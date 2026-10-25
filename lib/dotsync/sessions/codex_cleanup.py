"""The Codex cleanup: its read-only checks, and the run that cleans.

What is using ~/.codex: the cleanup may run only when nothing is, so `detect`
finds every process that runs a Codex binary or holds a file under the Codex
home. It only looks: it never stops or signals anything, and it reports
executable basenames, never arguments, environment or file paths.

Whether this is a Codex the cleanup knows (`support_check`), and every id that
something of Codex still refers to (`keep_set`). Each cleanup item deletes
only data of ids outside that set, so whatever the scan cannot read refuses
the whole cleanup (`scan_refusal`).

The run (`run_cleanup`) is the one place dotsync writes inside Codex's own
databases and history files. It runs only while nothing uses ~/.codex, under
Codex's own coordination lock, and deletes only what `orphans` reports."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from dotsync import locks
from dotsync.cli_json import JSONCommandError, now_iso
from dotsync.sessions import codex, codex_migrations, log, procs, roots, safety
from dotsync.sessions.stop import StopSignal

CHATGPT_MAIN_SUFFIX = "ChatGPT.app/Contents/MacOS/ChatGPT"
CODEX_NAMES = ("codex", "codex-code-mode-host")
LOCK_SUFFIX = ".lock"


def detect() -> dict:
    """{"blockers", "chatgpt_helpers", "chatgpt_open_threads"}. When `ps` or
    `lsof` cannot answer, the one blocker is a `detection_failed` entry: not
    knowing is never reported as nothing running."""
    try:
        table = procs.ps_table()
    except Exception:  # any failure, not only ToolFailure: unknown means keep
        return _failed("ps")
    try:
        scan = procs.lsof_scan()
    except Exception:
        return _failed("lsof")

    me = os.getpid()
    pids = sorted((set(table) | set(scan)) - {me})
    exe_paths = {pid: _exe_path(pid, table, scan) for pid in pids}
    chatgpt_mains = {pid for pid in pids if exe_paths[pid].endswith(CHATGPT_MAIN_SUFFIX)}
    code_dirs = [os.path.realpath(roots.codex_home()), os.path.realpath(roots.codex_sqlite_home())]
    lock_dir = os.path.join(code_dirs[0], "thread-writer-locks")

    blockers: list[dict] = []
    helpers: list[dict] = []
    chatgpt_locks: set[str] = set()
    for pid in pids:
        under_chatgpt = pid in chatgpt_mains or any(
            a in chatgpt_mains for a in procs.ancestors(pid, table)
        )
        files = scan[pid].files if pid in scan else []
        if under_chatgpt:
            chatgpt_locks |= _thread_locks(files, lock_dir)
        name = procs.exe_name(pid, table, scan)
        if pid in chatgpt_mains or _runs_codex(name, exe_paths[pid]) or _holds_file(files, code_dirs):
            blockers.append(_entry(pid, _kind(pid, name, chatgpt_mains, under_chatgpt), name, table))
        elif under_chatgpt:
            helpers.append(_entry(pid, "chatgpt_helper", name, table))
    return {
        "blockers": blockers,
        "chatgpt_helpers": helpers,
        "chatgpt_open_threads": len(chatgpt_locks),
    }


def _failed(tool: str) -> dict:
    entry = {"pid": 0, "kind": "detection_failed", "name": tool, "elapsed": 0}
    return {"blockers": [entry], "chatgpt_helpers": [], "chatgpt_open_threads": 0}


def _entry(pid: int, kind: str, name: str, table: dict[int, procs.Proc]) -> dict:
    proc = table.get(pid)
    return {"pid": pid, "kind": kind, "name": name, "elapsed": proc.elapsed if proc else 0}


def _kind(pid: int, name: str, chatgpt_mains: set[int], under_chatgpt: bool) -> str:
    if pid in chatgpt_mains:
        return "chatgpt_app"
    if under_chatgpt:
        return "chatgpt_child"
    return "terminal_codex" if name == "codex" else "other"


def _exe_path(pid: int, table: dict[int, procs.Proc], scan: dict[int, procs.OpenFiles]) -> str:
    """The executable's full path: lsof's first txt mapping, else `comm`
    (which `ps` prints as a path)."""
    opened = scan.get(pid)
    if opened is not None and opened.txt:
        return opened.txt[0]
    proc = table.get(pid)
    return proc.comm if proc is not None else ""


def _runs_codex(name: str, exe_path: str) -> bool:
    if name in CODEX_NAMES:
        return True
    resolved = os.path.realpath(exe_path) if exe_path else ""
    if name == "SkyComputerUseService":
        return _is_under(resolved, os.path.realpath(roots.codex_home() / "computer-use"))
    if name == "node":
        app_support = Path.home() / "Library" / "Application Support" / "Codex"
        return _is_under(resolved, os.path.realpath(app_support))
    return False


def _holds_file(files: list[str], directories: list[str]) -> bool:
    return any(_is_under(os.path.realpath(f), d) for f in files for d in directories)


def _thread_locks(files: list[str], lock_dir: str) -> set[str]:
    """The `thread-writer-locks/<thread-id>.lock` files among `files`."""
    found: set[str] = set()
    for f in files:
        real = os.path.realpath(f)
        if os.path.dirname(real) == lock_dir and real.endswith(LOCK_SUFFIX):
            if safety.is_valid_id(os.path.basename(real)[: -len(LOCK_SUFFIX)]):
                found.add(real)
    return found


def _is_under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip("/") + "/")


# The support check

MIGRATORS = {  # database → its migrator in codex_migrations.KNOWN
    codex.STATE_DB: "state",
    codex.QUEUE_DB: "queue",
    codex.HISTORY_DB: "thread_history",
}
ROLLOUT_MIGRATIONS = "rollout-migrations"
PENDING_SUFFIX = ".pending"
_STAGING = re.compile(r"\..*\.tmp", re.DOTALL)  # `.<name>.<suffix>.tmp`, before Codex renames it


class Refusal(JSONCommandError):
    """Why the cleanup cannot run at all. It is a JSONCommandError, so a run
    can raise it as it is, and a preview can show its code and message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message)
        self.message = message


def support_check() -> tuple[Refusal | None, bool]:
    """(refusal, vacuum_available). It refuses unless the state, queue and
    history databases have applied exactly Codex 0.164.0's migrations, and no
    rollout migration is pending or being staged. Vacuum is available when the
    history database has `auto_vacuum` = 2 (incremental); it is False with a
    refusal."""
    try:
        return None, _check_support()
    except Refusal as refusal:
        return refusal, False


def _check_support() -> bool:
    """Whether the history database can be vacuumed, or the first Refusal."""
    _check_config()
    vacuum_available = False
    for db_name, migrator in MIGRATORS.items():
        with _reading(roots.codex_sqlite_home() / db_name) as con:
            applied = _applied_migrations(con)
            if db_name == codex.HISTORY_DB:
                vacuum_available = con.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
        known = [(v, 1, checksum) for v, checksum in codex_migrations.KNOWN[migrator].items()]
        if Counter(applied) != Counter(known):
            raise Refusal(
                "codex_version",
                f"the migrations in {db_name} are not Codex {codex_migrations.VERSION}'s",
            )
    _check_rollout_migrations()
    return vacuum_available


def _check_config() -> None:
    """A config.toml that is there but cannot be read may move the databases
    (`sqlite_home`), and roots.codex_sqlite_home() would quietly fall back
    past it: the cleanup could then check databases Codex does not use."""
    try:
        with open(roots.codex_home() / "config.toml", "rb") as f:
            tomllib.load(f)
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:  # tomllib.TOMLDecodeError is a ValueError
        raise Refusal("codex_db_failed", f"cannot read config.toml: {e}") from e


@contextlib.contextmanager
def _reading(db: Path) -> Iterator[sqlite3.Connection]:
    """A read-only connection to one of Codex's databases. One that is not
    there has applied no migrations, so it is not a Codex this knows
    (`codex_version`); one that cannot be opened or read is
    `codex_db_failed`."""
    try:
        os.lstat(db)
    except FileNotFoundError:
        raise Refusal("codex_version", f"{db.name} is missing") from None
    except OSError as e:
        raise Refusal("codex_db_failed", f"cannot read {db.name}: {e}") from e
    try:
        with contextlib.closing(codex.open_ro(db)) as con:
            yield con
    except sqlite3.Error as e:
        raise Refusal("codex_db_failed", f"cannot read {db.name}: {e}") from e


def _applied_migrations(con: sqlite3.Connection) -> list[tuple]:
    """`(version, success, checksum in upper-case hex)` per `_sqlx_migrations`
    row; none when there is no such table."""
    has_table = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = '_sqlx_migrations'"
    ).fetchone()
    if not has_table:
        return []
    rows = con.execute("SELECT version, success, checksum FROM _sqlx_migrations")
    return [(v, ok, c.hex().upper() if isinstance(c, bytes) else c) for v, ok, c in rows]


def _check_rollout_migrations() -> None:
    """Refuse while Codex has a rollout migration pending or half-staged."""
    try:
        names = os.listdir(roots.codex_home() / ROLLOUT_MIGRATIONS)
    except FileNotFoundError:
        return
    except OSError as e:
        raise Refusal("codex_version", f"cannot check Codex's rollout migrations: {e}") from e
    if any(name.endswith(PENDING_SUFFIX) for name in names):
        raise Refusal("codex_version", "Codex is migrating rollouts")
    if any(_STAGING.fullmatch(name) for name in names):
        raise Refusal("codex_version", "Codex is starting a rollout migration")


# The keep-set

ZSTD_TIMEOUT = 10  # seconds per file
_LOOKS_LIKE_ROLLOUT = re.compile(r"rollout-.*\.jsonl(?:\.zst)?", re.DOTALL)
# What Codex refuses to find before the SessionMeta; every other record, and
# every line it cannot parse, it skips (codex-rs rollout/src/list.rs).
_NOT_BEFORE_META = ("response_item", "inter_agent_communication")


class MetaUnreadable(Exception):
    """A rollout whose SessionMeta cannot be read."""


@dataclass
class KeepSet:
    """Every id something of Codex still refers to. `unreadable` counts what
    the scan could not read, and `staging` the half-written rollout files it
    found; `zstd_missing` says `.zst` rollouts were left unread for want of
    zstd (they count as unreadable too). Any of them makes the set
    incomplete: see `scan_refusal`."""

    ids: set[str]
    unreadable: int
    staging: int
    zstd_missing: bool = False


def zstd_binary() -> str | None:
    homebrew = "/opt/homebrew/bin/zstd"
    if os.access(homebrew, os.X_OK):
        return homebrew
    return shutil.which("zstd")


def keep_set(progress: Callable[[int, int], None] | None = None) -> KeepSet:
    """The ids of every thread and spawn edge in the state database, and of
    every rollout file, active or archived, plain or `.zst`: the thread and
    rollout ids in its name, and its SessionMeta's id and
    `history_base.thread_id`. A `threads.rollout_path` adds the ids in its
    name too, whether or not its file is still there. `progress(done, total)`
    follows the rollouts.
    Raises Refusal when the state database is missing or cannot be read, or
    holds an id that is not canonical."""
    ids, rollout_paths = _read_state()
    named, unreadable = _named_rollout_ids(rollout_paths)
    ids |= named
    rollouts, unwalkable, staging = _rollout_files()
    unreadable += unwalkable
    zstd_missing = any(p.name.endswith(".zst") for p, _ in rollouts) and zstd_binary() is None
    for done, (path, name_ids) in enumerate(rollouts, start=1):
        ids |= name_ids
        try:
            ids |= _meta_ids(first_session_meta(path))
        except MetaUnreadable:
            unreadable += 1
        if progress is not None:
            progress(done, len(rollouts))
    return KeepSet(ids, unreadable, staging, zstd_missing)


def scan_refusal(keep: KeepSet) -> Refusal | None:
    """Why the keep-set cannot be trusted, if it cannot."""
    if keep.unreadable or keep.zstd_missing:
        why = " (zstd is not installed)" if keep.zstd_missing else ""
        return Refusal(
            "incomplete_scan", f"could not read {keep.unreadable} of the rollout files{why}"
        )
    if keep.staging:
        return Refusal("codex_version", f"Codex has half-written rollout files ({keep.staging})")
    return None


def _read_state() -> tuple[set[str], list[object]]:
    """Every `threads.id` and both ids of every spawn edge, and every
    `threads.rollout_path`."""
    with _reading(roots.codex_sqlite_home() / codex.STATE_DB) as con:
        threads = con.execute("SELECT id, rollout_path FROM threads").fetchall()
        found = [thread_id for thread_id, _path in threads]
        for parent, child in con.execute(
            "SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges"
        ):
            found += [parent, child]
    if not all(safety.is_valid_id(i) for i in found):
        raise Refusal("codex_version", f"{codex.STATE_DB} has a thread id that is not canonical")
    return set(found), [path for _thread_id, path in threads]


def _named_rollout_ids(paths: list[object]) -> tuple[set[str], int]:
    """The thread and rollout ids in the names of the rollout files the state
    database points at, and how many of its paths cannot be trusted. A file
    that is gone (a Codex delete that stopped halfway leaves its row) still
    names a rollout id whose history rows must stay. A path whose real path
    is outside the rollout trees counts as unreadable: the walk never reads
    that file, so its `history_base` would be missed. An empty path names no
    file."""
    trees = [os.path.realpath(roots.codex_home() / top) for top in codex.ROLLOUT_TREES]
    ids: set[str] = set()
    unreadable = 0
    for path in paths:
        if path is None or path == "":
            continue
        try:
            real = os.path.realpath(path) if isinstance(path, str) else None
        except (OSError, ValueError):  # ValueError: a NUL byte
            real = None
        name_ids = None
        if real is not None and any(_is_under(real, tree) for tree in trees):
            name_ids = _name_ids(os.path.basename(path))
        if name_ids is None:
            unreadable += 1
        else:
            ids |= name_ids
    return ids, unreadable


def _rollout_files() -> tuple[list[tuple[Path, set[str]]], int, int]:
    """Every rollout file in the rollout trees, with the ids in its name; how
    many entries there are that cannot be read as a rollout; how many staging
    files. Only a tree that is not there at all is fine to miss: a folder that
    cannot be listed, any symlink (what it points to is not scanned), and a
    rollout-like name that is not a regular file with canonical ids all count
    as unreadable."""
    found: list[tuple[Path, set[str]]] = []
    unreadable = staging = 0
    folders: list[Path] = []
    for top in codex.ROLLOUT_TREES:
        tree = roots.codex_home() / top
        try:
            os.lstat(tree)
        except FileNotFoundError:
            continue  # no such tree, so nothing in it to keep
        except OSError:
            unreadable += 1
            continue
        folders.append(tree)
    while folders:
        folder = folders.pop()
        try:
            with os.scandir(folder) as listing:
                entries = sorted(listing, key=lambda e: e.name)
        except OSError:
            unreadable += 1
            continue
        for entry in entries:
            try:
                if _STAGING.fullmatch(entry.name):
                    staging += 1
                elif entry.is_symlink():
                    unreadable += 1
                elif _LOOKS_LIKE_ROLLOUT.fullmatch(entry.name):
                    name_ids = _name_ids(entry.name)
                    if name_ids is None or not entry.is_file(follow_symlinks=False):
                        unreadable += 1
                    else:
                        found.append((Path(entry.path), name_ids))
                elif entry.is_dir(follow_symlinks=False):
                    folders.append(Path(entry.path))
            except OSError:
                unreadable += 1
    return found, unreadable, staging


def _name_ids(name: str) -> set[str] | None:
    """The thread id and rollout id in a rollout's name, or None when the name
    is not Codex's or an id in it is not canonical."""
    m = codex.ROLLOUT_NAME.match(name)
    if m is None:
        return None
    ids = {i for i in m.groups() if i is not None}
    return ids if all(safety.is_valid_id(i) for i in ids) else None


def _meta_ids(payload: dict) -> set[str]:
    """`payload.id` and, when there is a history base, its `thread_id` (a
    rollout id). Each must be a canonical id."""
    ids = [payload.get("id")]
    base = payload.get("history_base")
    if base is not None:
        if not isinstance(base, dict):
            raise MetaUnreadable("history_base is not an object")
        ids.append(base.get("thread_id"))
    if not all(safety.is_valid_id(i) for i in ids):
        raise MetaUnreadable("a SessionMeta id is not a canonical id")
    return set(ids)


def first_session_meta(path: Path) -> dict:
    """The payload of the rollout's first SessionMeta record, found the way
    Codex finds it: blank lines, lines that do not parse and other records
    are skipped; a response item or inter-agent message before it, or the end
    of the file, is an error. Nothing past that record is read. A `.jsonl.zst`
    is streamed through zstd, which is killed once the record is found.
    MetaUnreadable when there is no such record or the file cannot be read."""
    if path.name.endswith(".zst"):
        zstd = zstd_binary()
        if zstd is None:
            raise MetaUnreadable("zstd is not installed")
        try:
            with contextlib.closing(
                procs.stream_lines([zstd, "-dc", "--", str(path)], timeout=ZSTD_TIMEOUT)
            ) as lines:
                return _meta_in(lines)
        except procs.ToolFailure as e:
            raise MetaUnreadable(f"zstd: {e}") from e
    try:
        # No symlink, and no waiting on a FIFO put in a rollout's place.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise MetaUnreadable("not a regular file")
            return _meta_in(f)
    except OSError as e:
        raise MetaUnreadable(str(e)) from e


def _meta_in(lines: Iterable[bytes]) -> dict:
    for line in lines:
        if not line.strip():
            continue
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as e:
            raise MetaUnreadable("a line that is not UTF-8") from e
        try:
            record = json.loads(text)
        except (ValueError, RecursionError):
            if b'"session_meta"' in line:  # it may be the SessionMeta: unknown means keep
                raise MetaUnreadable("a SessionMeta-like line that does not parse") from None
            continue  # Codex skips a line that does not parse
        if not isinstance(record, dict):
            continue
        kind = record.get("type")
        if kind == "session_meta":
            payload = record.get("payload")
            if not isinstance(payload, dict):
                raise MetaUnreadable("a SessionMeta without a payload object")
            return payload
        if kind in _NOT_BEFORE_META:
            raise MetaUnreadable(f"a {kind} record before the SessionMeta")
    raise MetaUnreadable("no SessionMeta record")


# The orphans, and the dry run

HISTORY_JSONL = "history.jsonl"
SESSION_INDEX_JSONL = "session_index.jsonl"
_QUEUE_TABLES = ("queued_items", "queued_thread_revisions")


@dataclass
class Orphans:
    """What the stores hold for ids outside the keep-set. An id that is not
    canonical is ignored in every store, so it is never counted and never
    touched."""

    history_ids: set[str]  # the four history tables, keyed by rollout id
    history_lines: dict[str, int]  # id -> its `history.jsonl` lines
    index_ids: set[str]  # `session_index.jsonl`
    queue_rows: int
    queue_ids: set[str]
    cache_ids: set[str]  # names in `tui-thread-reference-capabilities/`


def orphans(keep: KeepSet) -> Orphans:
    """Read-only. Raises Refusal(codex_db_failed) when a store cannot be read:
    that is never a count of 0. A missing `history.jsonl`,
    `session_index.jsonl` or cache folder holds nothing."""

    def orphan(value: object) -> bool:
        return safety.is_valid_id(value) and value not in keep.ids

    history_ids: set[str] = set()
    with _reading(roots.codex_sqlite_home() / codex.HISTORY_DB) as con:
        for table in codex.HISTORY_TABLES:
            history_ids |= {i for (i,) in con.execute(f"SELECT DISTINCT thread_id FROM {table}") if orphan(i)}
    queue_rows = 0
    queue_ids: set[str] = set()
    with _reading(roots.codex_sqlite_home() / codex.QUEUE_DB) as con:
        for table in _QUEUE_TABLES:
            for thread_id, rows in con.execute(f"SELECT thread_id, COUNT(*) FROM {table} GROUP BY thread_id"):
                if orphan(thread_id):
                    queue_ids.add(thread_id)
                    queue_rows += rows
    history_lines = dict(Counter(_jsonl_ids(HISTORY_JSONL, "session_id", orphan)))
    index_ids = set(_jsonl_ids(SESSION_INDEX_JSONL, "id", orphan))
    cache_ids = {name for name in _cache_names() if orphan(name)}
    return Orphans(history_ids, history_lines, index_ids, queue_rows, queue_ids, cache_ids)


def _jsonl_ids(filename: str, key: str, wanted: Callable[[str], bool]) -> Iterator[str]:
    """For each line of a Codex JSONL file that belongs to a wanted id
    (`_line_owner`), that id."""
    path = roots.codex_home() / filename
    try:
        # No waiting on a FIFO put in the file's place.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return
    except OSError as e:
        raise Refusal("codex_db_failed", f"cannot read {filename}: {e}") from e
    try:
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise Refusal("codex_db_failed", f"{filename} is not a regular file")
            for line in f:
                owner = _line_owner(line, key, wanted)
                if owner is not None:
                    yield owner
    except OSError as e:
        raise Refusal("codex_db_failed", f"cannot read {filename}: {e}") from e


def _line_owner(line: bytes, key: str, wanted: Callable[[str], bool]) -> str | None:
    """The one definition of whose a JSONL line is: the line decodes as strict
    UTF-8 to a JSON object whose `key` is a canonical id that `wanted`
    accepts. Anything else (a line that does not decode or parse, a BOM,
    another JSON value, an id only mentioned in the text) is no one's. The dry
    run counts lines with it and the cleanup drops lines with it, so the two
    cannot disagree."""
    try:
        record = json.loads(line.decode("utf-8"))
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        return None
    if not isinstance(record, dict):
        return None
    value = record.get(key)
    return value if safety.is_valid_id(value) and wanted(value) else None


def _cache_names() -> Iterator[str]:
    """Names of the plain files in the reference-cache folder. A symlink, a
    folder or anything else in it is ignored."""
    folder = roots.codex_home() / codex.CACHE_DIR
    try:
        with os.scandir(folder) as listing:
            entries = list(listing)
    except FileNotFoundError:
        return
    except OSError as e:
        raise Refusal("codex_db_failed", f"cannot read {codex.CACHE_DIR}: {e}") from e
    for entry in entries:
        try:
            if entry.is_file(follow_symlinks=False):
                yield entry.name
        except OSError:
            continue


def dry_run_payload(progress: Callable[[int, int], None] | None = None) -> dict:
    """`detect()`'s fields plus the four items in spec order, what the history
    database weighs, and whether it can be vacuumed. Blockers are reported, not
    a refusal: nothing is changed. A refusal gives `items: []`, and the
    keep-set walk is skipped when the support check already refuses."""
    payload = detect()
    refusal, vacuum_available = support_check()
    found: Orphans | None = None
    if refusal is None:
        keep = keep_set(progress)
        refusal = scan_refusal(keep)
        if refusal is None:
            try:
                found = orphans(keep)
            except Refusal as e:
                refusal = e
    payload["items"] = _items(found) if found is not None else []
    payload["history_db_bytes"] = codex.history_bytes()
    payload["vacuum_available"] = vacuum_available
    payload["refusal"] = {"code": refusal.code, "message": refusal.message} if refusal else None
    return payload


def _items(found: Orphans) -> list[dict]:
    rows = [
        ("history_rows", "대화 저장소에 남은 내용", len(found.history_ids), {}),
        (
            "history_lines",
            "입력 기록에서 지운 세션의 줄",
            len(found.history_lines),
            {"lines": sum(found.history_lines.values())},
        ),
        ("session_index", "이름 목록에서 지운 세션", len(found.index_ids), {}),
        ("queue", "큐 기록", found.queue_rows, {}),
    ]
    return [
        {"id": item_id, "label": label, "count": count, **extra, "checked": count > 0}
        for item_id, label, count, extra in rows
    ]


# The cleanup

ITEMS = ("history_rows", "history_lines", "session_index", "queue")  # what --items picks among
CACHE_ITEM = "cache_files"  # not a choice: it always runs, last
COORDINATION_WAIT = 10.0  # seconds; a coordination lock held longer means a Codex is at work
JSONL_LOCK_WAIT = 5.0  # seconds for a JSONL file's own lock
WRITE_TIMEOUT = 5.0  # seconds a write waits on a locked database
BACKUP_INFIX = ".dotsync-backup-"
VACUUM_CAP = 600  # seconds in total (spec: 10 minutes)
VACUUM_BATCH_PAGES = 25600


def run_cleanup(
    items: list[str],
    vacuum: bool,
    *,
    stop: StopSignal,
    progress: Callable[[int, int], None],
) -> dict:
    """Remove what deleted Codex sessions left behind: the chosen `items`, in
    spec order whatever order they come in, then the cache files.

    Under the `codex` lock, and only while nothing uses ~/.codex
    (`codex_running`). Codex's coordination lock is taken before the support
    check and the keep-set walk and held to the end, so no Codex can make a
    thread the walk did not see. Each item deletes only what `orphans()`
    found. Before each item, SIGTERM stops the run (`stopped`) and a Codex
    that started stops it (`codex_started`); an item that fails does not stop
    the others. Every run that got the lock is logged, refusals too: ids and
    counts, never contents."""
    chosen = _chosen(items)
    started_at = now_iso()
    report: dict = {"items": [], "vacuum": None, "stopped": False}
    removed: dict[str, object] = {}  # item -> the ids it removed, for the log
    error: dict | None = None
    with locks.tool_lock("codex", shared=False):
        try:
            blockers = detect()["blockers"]
            if blockers:
                raise _codex_running(blockers)
            with _coordination_lock():
                found = _orphans_to_clean(progress)
                _run_items([*chosen, CACHE_ITEM], found, report, removed, stop, progress)
                if vacuum:
                    report["vacuum"] = _vacuum(report, stop, progress)
        except BaseException as e:
            code = e.code if isinstance(e, JSONCommandError) else "failed"
            error = {"code": code, "message": str(e) or type(e).__name__}
            raise
        finally:
            logged = {**report, "ids": removed}
            if error is not None:
                logged["error"] = error
            log.write("cleanup", "codex", started_at, logged)
    return report


def _vacuum(report: dict, stop: StopSignal, progress: Callable[[int, int], None]) -> dict:
    """The `--vacuum` result, run while both locks are still held. It starts
    only when the items ended normally; a stop or a Codex that started leaves
    the database as it is, and the result says so."""
    path = roots.codex_sqlite_home() / codex.HISTORY_DB
    before = codex.db_bytes(path)
    reasons = {item.get("reason") for item in report["items"]}
    if report["stopped"] or stop.requested:
        why = "stopped"
    elif "codex_started" in reasons:
        why = "blocker"
    else:
        try:
            refusal, available = support_check()
        except Exception as e:
            return _failed_vacuum(before, e)
        if refusal is not None:
            return _failed_vacuum(before, refusal)
        if not available:
            why = "unavailable"
        else:
            return reclaim(path, stop=stop, detect=detect, progress=progress)
    return {"stopped_because": why, "bytes_before": before, "bytes_after": before}


def _failed_vacuum(before: int, error: object) -> dict:
    """A vacuum that failed is a result like any other: the items above it
    were applied and must still be reported."""
    return {
        "stopped_because": "error",
        "message": _one_line(error),
        "bytes_before": before,
        "bytes_after": before,
    }


def _chosen(items: list[str]) -> list[str]:
    unknown = [item for item in items if item not in ITEMS]
    if unknown:
        raise JSONCommandError(
            "bad_items",
            f"unknown cleanup items {unknown!r}; choose among {', '.join(ITEMS)}",
        )
    return [item for item in ITEMS if item in items]


def _codex_running(blockers: list[dict]) -> JSONCommandError:
    if any(b["kind"] == "detection_failed" for b in blockers):
        return JSONCommandError("codex_running", "cannot tell what uses ~/.codex")
    names = ", ".join(sorted({b["name"] for b in blockers}))
    return JSONCommandError("codex_running", f"quit what uses ~/.codex first: {names}")


@contextlib.contextmanager
def _coordination_lock() -> Iterator[None]:
    """Codex's `thread-writer-locks/.coordination.lock`, held exclusive: a
    Codex that starts meanwhile waits on it before it can create or resume a
    thread, and the kernel lets go of it if dotsync dies. Its folder is made
    when missing, but never the Codex home itself."""
    folder = roots.codex_home() / "thread-writer-locks"
    try:
        folder.mkdir(exist_ok=True)
    except FileNotFoundError:
        raise JSONCommandError("codex_version", f"{roots.codex_home()} is missing") from None
    with open(folder / ".coordination.lock", "a") as handle:
        if not _flock(handle.fileno(), COORDINATION_WAIT):
            raise JSONCommandError("codex_running", "a Codex holds its coordination lock")
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _flock(fd: int, wait: float) -> bool:
    """Take flock(LOCK_EX) on `fd`, polling every 0.1 s for up to `wait`
    seconds. Whether it was taken."""
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)


def _orphans_to_clean(progress: Callable[[int, int], None]) -> Orphans:
    """The support check, then the keep-set walk; either refusal raises."""
    refusal, _vacuum_available = support_check()
    if refusal is not None:
        raise refusal
    keep = keep_set(progress)
    refusal = scan_refusal(keep)
    if refusal is not None:
        raise refusal
    return orphans(keep)


def _run_items(
    order: list[str],
    found: Orphans,
    report: dict,
    removed: dict[str, object],
    stop: StopSignal,
    progress: Callable[[int, int], None],
) -> None:
    results: list[dict] = report["items"]
    for position, item in enumerate(order):
        if stop.requested:
            report["stopped"] = True
            why = "stopped"
        elif detect()["blockers"]:
            why = "codex_started"
        else:
            result, ids = _clean(item, found, stop, progress)
            results.append(result)
            if ids:
                removed[item] = ids
            continue
        results.extend(_item(rest, "skipped", 0, reason=why) for rest in order[position:])
        return


def _clean(
    item: str, found: Orphans, stop: StopSignal, progress: Callable[[int, int], None]
) -> tuple[dict, object]:
    """One item's result, and the ids it removed. An item that raises is
    `failed` with its message."""
    try:
        if item == "history_rows":
            return _delete_history_rows(sorted(found.history_ids), stop, progress)
        if item == "history_lines":
            return _drop_lines(item, HISTORY_JSONL, "session_id", set(found.history_lines))
        if item == "session_index":
            return _drop_lines(item, SESSION_INDEX_JSONL, "id", found.index_ids)
        if item == "queue":
            return _delete_queue(sorted(found.queue_ids))
        return _remove_caches(sorted(found.cache_ids))
    except Exception as e:
        return _item(item, "failed", 0, message=_one_line(e)), None


def _item(item: str, outcome: str, count: int, **extra: object) -> dict:
    return {"id": item, "outcome": outcome, "count": count, **extra}


def _one_line(error: object) -> str:
    return str(error).replace("\n", " ")


def _delete_history_rows(
    ids: list[str], stop: StopSignal, progress: Callable[[int, int], None]
) -> tuple[dict, list[str]]:
    """One BEGIN IMMEDIATE transaction per id (a rollout id) over the four
    history tables. SIGTERM is honoured between ids, never inside one; the
    count is the ids whose transaction finished."""
    deleted: list[str] = []
    if not ids:
        return _item("history_rows", "done", 0), deleted
    try:
        with _writing(roots.codex_sqlite_home() / codex.HISTORY_DB) as con:
            for done, tid in enumerate(ids, start=1):
                if stop.requested:
                    return _item("history_rows", "skipped", len(deleted), reason="stopped"), deleted
                with _transaction(con):
                    for table in codex.HISTORY_TABLES:
                        con.execute(f"DELETE FROM {table} WHERE thread_id = ?", (tid,))
                deleted.append(tid)
                progress(done, len(ids))
    except (sqlite3.Error, OSError) as e:
        return _item("history_rows", "failed", len(deleted), message=_one_line(e)), deleted
    return _item("history_rows", "done", len(deleted)), deleted


def _delete_queue(ids: list[str]) -> tuple[dict, list[str]]:
    """In one transaction, the queued items of `ids`, then their revisions:
    Codex's trigger upserts a revision for every item deleted, so revisions
    go last. The count is the rows the ids had in both tables, as the dry run
    counts them."""
    if not ids:
        return _item("queue", "done", 0), []
    args = [(tid,) for tid in ids]
    with _writing(roots.codex_sqlite_home() / codex.QUEUE_DB) as con:
        with _transaction(con):
            count = sum(
                con.execute(f"SELECT COUNT(*) FROM {table} WHERE thread_id = ?", arg).fetchone()[0]
                for table in _QUEUE_TABLES
                for arg in args
            )
            con.executemany("DELETE FROM queued_items WHERE thread_id = ?", args)
            con.executemany("DELETE FROM queued_thread_revisions WHERE thread_id = ?", args)
    return _item("queue", "done", count), ids


@contextlib.contextmanager
def _writing(db: Path) -> Iterator[sqlite3.Connection]:
    """A connection that writes one of Codex's databases, opened the way any
    client opens it: the file must already exist, no journal mode or other
    setting is changed, every write is inside an explicit transaction, and a
    lock is waited on for WRITE_TIMEOUT seconds."""
    con = sqlite3.connect(
        f"file:{quote(str(db))}?mode=rw", uri=True, timeout=WRITE_TIMEOUT, isolation_level=None
    )
    try:
        yield con
    finally:
        con.close()


def reclaim(
    path: Path,
    *,
    stop: StopSignal,
    detect: Callable[[], dict],
    progress: Callable[[int, int], None] = lambda freed, total: None,
    now: Callable[[], float] = time.monotonic,
    cap: float = VACUUM_CAP,
    batch_pages: int = VACUUM_BATCH_PAGES,
) -> dict:
    """Give the history database's free pages back to the disk, `batch_pages`
    at a time (`PRAGMA incremental_vacuum`), and say why it stopped:
    `freelist_empty`, `no_progress` (a batch freed nothing), `blocker` (a
    Codex started), `stopped` (SIGTERM), `busy` (a checkpoint could not
    finish, or the database stayed locked), `error` (any other sqlite or OS
    error, with a `message`) or `time_cap` (`cap` seconds).
    Between batches it checkpoints, so the WAL does not grow with the file.
    Sizes are the database plus its `-wal`. `progress(freed, total)` runs
    after each batch, in pages."""
    deadline = now() + cap
    before = codex.db_bytes(path)
    why = "busy"
    try:
        with _writing(path) as con:
            if not _checkpointed(con):
                return _reclaimed(path, before, "busy")
            total = con.execute("PRAGMA freelist_count").fetchall()[0][0]
            free = total
            if free > 0 and detect()["blockers"]:
                return _reclaimed(path, before, "blocker")
            while True:
                if free == 0:
                    why = "freelist_empty"
                    break
                con.execute(f"PRAGMA incremental_vacuum({batch_pages})").fetchall()
                left = con.execute("PRAGMA freelist_count").fetchall()[0][0]
                progress(total - left, total)
                if left >= free:
                    why = "no_progress"
                    break
                free = left
                if detect()["blockers"]:
                    why = "blocker"
                    break
                if stop.requested:
                    why = "stopped"
                    break
                if not _checkpointed(con):
                    why = "busy"
                    break
                if now() >= deadline:
                    why = "time_cap"
                    break
    except (sqlite3.Error, OSError) as e:
        if isinstance(e, sqlite3.OperationalError) and ("locked" in str(e) or "busy" in str(e)):
            why = "busy"
        else:
            return {**_reclaimed(path, before, "error"), "message": _one_line(e)}
    return _reclaimed(path, before, why)


def _checkpointed(con: sqlite3.Connection) -> bool:
    """Whether `wal_checkpoint(TRUNCATE)` ran to the end (its first column,
    `busy`, is 0)."""
    return con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()[0][0] == 0


def _reclaimed(path: Path, before: int, why: str) -> dict:
    return {"bytes_before": before, "bytes_after": codex.db_bytes(path), "stopped_because": why}


@contextlib.contextmanager
def _transaction(con: sqlite3.Connection) -> Iterator[None]:
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def _drop_lines(item: str, filename: str, key: str, ids: set[str]) -> tuple[dict, dict[str, int]]:
    """Rewrite one of Codex's JSONL files without the lines of `ids`, matched
    the way `orphans()` counted them. The count is the ids whose lines went;
    `history_lines` also reports the lines, as its dry-run row does."""
    dropped: Counter[str] = Counter()

    def drop(line: bytes) -> bool:
        owner = _line_owner(line, key, ids.__contains__)
        if owner is not None:
            dropped[owner] += 1
        return owner is not None

    lines = rewrite_jsonl(roots.codex_home() / filename, drop) if ids else 0
    result = _item(item, "done", len(dropped))
    if item == "history_lines":
        result["lines"] = lines
    return result, dict(dropped)


def _remove_caches(ids: list[str]) -> tuple[dict, list[str]]:
    _freed, removed, failed = codex.clear_caches(ids)
    gone = [path.name for path in removed]
    if failed:
        return _item(CACHE_ITEM, "failed", len(gone), message=failed[0]["error"]), gone
    return _item(CACHE_ITEM, "done", len(gone)), gone


def rewrite_jsonl(path: Path, drop: Callable[[bytes], bool]) -> int:
    """Remove the lines `drop` picks from a JSONL file, keeping every other
    line's bytes exactly: one that does not parse, a CRLF ending, a last line
    with no newline. The number of lines dropped; a missing file has none.

    Under flock(LOCK_EX) on the file. When nothing is dropped the file is not
    touched. Otherwise it is hard-linked to `<name>.dotsync-backup-<ts>`, the
    kept lines go to a uniquely named temp file in the same folder with the
    file's mode, synced to the drive (`_full_fsync`), renamed over the file,
    the folder is synced the same way, and the backup removed. A failure or crash leaves the old file or the new one,
    whole, plus the backup; the temp file never stays. A symlink in the
    file's place is refused, not followed."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return 0
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise OSError(f"{path.name} is not a regular file")
        if not _flock(f.fileno(), JSONL_LOCK_WAIT):
            raise OSError(f"{path.name} is locked by another process")
        info = os.fstat(f.fileno())
        now = os.lstat(path)
        if (now.st_dev, now.st_ino) != (info.st_dev, info.st_ino):
            raise OSError(f"{path.name} was replaced while it was being opened")
        lines = list(f)
        kept = [line for line in lines if not drop(line)]
        if len(kept) == len(lines):
            return 0
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}{BACKUP_INFIX}{stamp}")
        os.link(path, backup, follow_symlinks=False)
        _replace(path, kept, stat.S_IMODE(info.st_mode))
        # The new file is in place: a backup that cannot be removed is only a stray file.
        with contextlib.suppress(OSError):
            os.unlink(backup)
    return len(lines) - len(kept)


def _replace(path: Path, lines: list[bytes], mode: int) -> None:
    """Put `lines` in `path`'s place through a temp file in the same folder,
    which is removed if anything fails before the rename."""
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".dotsync-tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            os.fchmod(out.fileno(), mode)
            out.writelines(lines)
            out.flush()
            _full_fsync(out.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)
        raise
    folder = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        _full_fsync(folder)
    finally:
        os.close(folder)


_NO_FULLFSYNC = (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP)  # the filesystem cannot


def _full_fsync(fd: int) -> None:
    """Get `fd`'s data onto the drive itself. On macOS fsync(2) only hands it
    to the drive, which may write it later and out of order (`man 2 fsync`);
    F_FULLFSYNC waits until it is written. A filesystem that does not support
    F_FULLFSYNC gets plain fsync; any other error raises."""
    if hasattr(fcntl, "F_FULLFSYNC"):
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except OSError as e:
            if e.errno not in _NO_FULLFSYNC:
                raise
    os.fsync(fd)
