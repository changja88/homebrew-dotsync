"""The Codex cleanup's checks, all read-only.

What is using ~/.codex: the cleanup may run only when nothing is, so `detect`
finds every process that runs a Codex binary or holds a file under the Codex
home. It only looks: it never stops or signals anything, and it reports
executable basenames, never arguments, environment or file paths.

Whether this is a Codex the cleanup knows (`support_check`), and every id that
something of Codex still refers to (`keep_set`). Each cleanup item deletes
only data of ids outside that set, so whatever the scan cannot read refuses
the whole cleanup (`scan_refusal`)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sqlite3
import stat
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from dotsync.cli_json import JSONCommandError
from dotsync.sessions import codex, codex_migrations, procs, roots, safety

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
    `history_base.thread_id`. `progress(done, total)` follows the rollouts.
    Raises Refusal when the state database is missing or cannot be read, or
    holds an id that is not canonical."""
    ids = _state_ids()
    rollouts, unreadable, staging = _rollout_files()
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


def _state_ids() -> set[str]:
    """Every `threads.id` and both ids of every spawn edge."""
    with _reading(roots.codex_sqlite_home() / codex.STATE_DB) as con:
        found = [r[0] for r in con.execute("SELECT id FROM threads")]
        for parent, child in con.execute(
            "SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges"
        ):
            found += [parent, child]
    if not all(safety.is_valid_id(i) for i in found):
        raise Refusal("codex_version", f"{codex.STATE_DB} has a thread id that is not canonical")
    return set(found)


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
            record = json.loads(line)
        except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
            continue
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
