"""Whether this is a Codex the cleanup knows, and what it still refers to.

`support_check` says whether the Codex data model is one the cleanup knows,
and `keep_set` collects every id that something of Codex still refers to.
Each cleanup item deletes only data of ids outside that set, so whatever the
scan cannot read refuses the whole cleanup (`scan_refusal`)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import stat
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from compression import zstd
from dataclasses import dataclass
from pathlib import Path

from dotsync.cli_json import JSONCommandError
from dotsync.sessions import codex, codex_migrations, roots, safety


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
    history databases have applied exactly Codex 0.165.0's migrations, and no
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
        with reading(roots.codex_sqlite_home() / db_name) as con:
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
def reading(db: Path) -> Iterator[sqlite3.Connection]:
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
    found. Either makes the set incomplete: see `scan_refusal`."""

    ids: set[str]
    unreadable: int
    staging: int


def keep_set(progress: Callable[[int, int], None] | None = None) -> KeepSet:
    """The ids of every thread, attached parent and spawn edge in the state
    database, and of every rollout file, active or archived, plain or `.zst`:
    the thread and rollout ids in its name, and its SessionMeta's id,
    `history_base.thread_id` and attached `forked_from_id`. A
    `threads.rollout_path` adds the ids in its name too, whether or not its
    file is still there. `progress(done, total)` follows the rollouts.
    Raises Refusal when the state database is missing or cannot be read, or
    holds an id that is not canonical."""
    ids, rollout_paths = _read_state()
    named, unreadable = _named_rollout_ids(rollout_paths)
    ids |= named
    rollouts, unwalkable, staging = _rollout_files()
    unreadable += unwalkable
    for done, (path, name_ids) in enumerate(rollouts, start=1):
        ids |= name_ids
        try:
            ids |= _meta_ids(first_session_meta(path))
        except MetaUnreadable:
            unreadable += 1
        if progress is not None:
            progress(done, len(rollouts))
    return KeepSet(ids, unreadable, staging)


def scan_refusal(keep: KeepSet) -> Refusal | None:
    """Why the keep-set cannot be trusted, if it cannot."""
    if keep.unreadable:
        return Refusal("incomplete_scan", f"could not read {keep.unreadable} of the rollout files")
    if keep.staging:
        return Refusal("codex_version", f"Codex has half-written rollout files ({keep.staging})")
    return None


def _read_state() -> tuple[set[str], list[object]]:
    """Every `threads.id` and `threads.attached_thread_parent_id`, both ids of
    every spawn edge, and every `threads.rollout_path`."""
    with reading(roots.codex_sqlite_home() / codex.STATE_DB) as con:
        threads = con.execute(
            "SELECT id, rollout_path, attached_thread_parent_id FROM threads"
        ).fetchall()
        found = [thread_id for thread_id, _path, _attached in threads]
        found += [attached for _id, _path, attached in threads if attached is not None]
        for parent, child in con.execute(
            "SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges"
        ):
            found += [parent, child]
    if not all(safety.is_valid_id(i) for i in found):
        raise Refusal("codex_version", f"{codex.STATE_DB} has a thread id that is not canonical")
    return set(found), [path for _thread_id, path, _attached in threads]


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
        if real is not None and any(safety.is_under(real, tree) for tree in trees):
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
    """`payload.id`; when there is a history base, its `thread_id` (a rollout
    id); and when the rollout is an attached thread, the `forked_from_id` it
    is attached to (a plain fork's source is no reference, as in Codex's
    rollout_reference_index.rs). Each must be a canonical id."""
    ids = [payload.get("id")]
    base = payload.get("history_base")
    if base is not None:
        if not isinstance(base, dict):
            raise MetaUnreadable("history_base is not an object")
        ids.append(base.get("thread_id"))
    attached = payload.get("attached_thread", False)
    if not isinstance(attached, bool):
        raise MetaUnreadable("attached_thread is not a boolean")
    if attached and payload.get("forked_from_id") is not None:
        ids.append(payload["forked_from_id"])
    if not all(safety.is_valid_id(i) for i in ids):
        raise MetaUnreadable("a SessionMeta id is not a canonical id")
    return set(ids)


def first_session_meta(path: Path) -> dict:
    """The payload of the rollout's first SessionMeta record, found the way
    Codex finds it: blank lines, lines that do not parse and other records
    are skipped; a response item or inter-agent message before it, or the end
    of the file, is an error. Reading stops at that record. A `.jsonl.zst` is
    decompressed in this process as it is read: one zstd process per file
    made reading thousands of rollouts take minutes.
    MetaUnreadable when there is no such record or the file cannot be read
    or decompressed."""
    try:
        # No symlink, and no waiting on a FIFO put in a rollout's place.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise MetaUnreadable("not a regular file")
            if path.name.endswith(".zst"):
                with zstd.ZstdFile(f) as lines:
                    return _meta_in(lines)
            return _meta_in(f)
    except (OSError, EOFError, zstd.ZstdError) as e:  # EOFError: a frame cut short
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
