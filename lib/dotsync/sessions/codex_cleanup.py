"""The Codex cleanup: the orphans it finds and the run that cleans.

`orphans` and `dry_run_payload` are read-only: what the stores hold for ids
outside the keep-set, Codex's own and the leftovers around them (the ChatGPT
app's files, logs, shell snapshots, generated images). The run
(`run_cleanup`) is the one place dotsync writes inside Codex's own databases
and history files. It runs only while nothing uses ~/.codex
(`codex_blockers`), after the support check and the keep-set
(`codex_keepset`), under Codex's own coordination lock, and deletes only
what `orphans` reports."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import re
import sqlite3
import stat
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from dotsync import locks
from dotsync.cli_json import JSONCommandError, now_iso
from dotsync.sessions import codex, codex_blockers, codex_desktop, codex_keepset, log, roots, safety
from dotsync.sessions.stop import StopSignal


# The orphans, and the dry run

HISTORY_JSONL = "history.jsonl"
SESSION_INDEX_JSONL = "session_index.jsonl"
_QUEUE_TABLES = ("queued_items", "queued_thread_revisions")
LOGS_DB = "logs_2.sqlite"
SNAPSHOTS_DIR = "shell_snapshots"  # <thread id>.<stamp>.sh
IMAGES_DIR = "generated_images"  # <thread id>/


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
    catalog_ids: set[str]  # the ChatGPT app's sidebar catalogs
    state_ids: set[str]  # the ChatGPT app's state file and its copy
    log_rows: dict[str, int]  # id -> its rows in the logs
    log_mentions: dict[int, set[str]]  # a thread-less log row -> the deleted sessions its text names
    snapshot_names: list[str]  # files in the shell snapshot folder
    image_bytes: dict[str, int]  # id -> what its generated image folder weighs


def orphans(keep: codex_keepset.KeepSet) -> Orphans:
    """Read-only. Raises Refusal(codex_db_failed) when a store cannot be read:
    that is never a count of 0. A missing `history.jsonl`,
    `session_index.jsonl`, folder or leftover file holds nothing, and a
    ChatGPT app file of another shape is left alone (`codex_desktop`)."""

    def orphan(value: object) -> bool:
        return safety.is_valid_id(value) and value not in keep.ids

    history_ids: set[str] = set()
    with codex_keepset.reading(roots.codex_sqlite_home() / codex.HISTORY_DB) as con:
        for table in codex.HISTORY_TABLES:
            history_ids |= {i for (i,) in con.execute(f"SELECT DISTINCT thread_id FROM {table}") if orphan(i)}
    queue_rows = 0
    queue_ids: set[str] = set()
    with codex_keepset.reading(roots.codex_sqlite_home() / codex.QUEUE_DB) as con:
        for table in _QUEUE_TABLES:
            for thread_id, rows in con.execute(f"SELECT thread_id, COUNT(*) FROM {table} GROUP BY thread_id"):
                if orphan(thread_id):
                    queue_ids.add(thread_id)
                    queue_rows += rows
    history_lines = dict(Counter(_jsonl_ids(HISTORY_JSONL, "session_id", orphan)))
    index_ids = set(_jsonl_ids(SESSION_INDEX_JSONL, "id", orphan))
    cache_ids = {name for name in _cache_names() if orphan(name)}
    catalogs = _quietly(lambda: _read_catalogs(orphan), None)
    documents = _quietly(_read_documents, None)
    others = _others(catalogs, documents)
    documents = documents or []
    return Orphans(
        history_ids,
        history_lines,
        index_ids,
        queue_rows,
        queue_ids,
        cache_ids,
        catalog_ids=catalogs.listed if catalogs else set(),
        state_ids=_quietly(lambda: _state_ids(documents, others, orphan), set()),
        log_rows=_quietly(lambda: _log_rows(orphan), {}),
        log_mentions=_quietly(lambda: _log_mentions(orphan, others), {}),
        snapshot_names=_quietly(
            lambda: sorted(name for name in _plain_files(SNAPSHOTS_DIR) if orphan(name.split(".", 1)[0])), []
        ),
        image_bytes=_quietly(
            lambda: {name: _tree_bytes(roots.codex_home() / IMAGES_DIR / name)
                     for name in _folders(IMAGES_DIR) if orphan(name)},
            {},
        ),
    )


def _quietly[T](find: Callable[[], T], nothing: T) -> T:
    """A leftover store that cannot be read is left alone and the rest of the
    cleanup goes on: unlike Codex's own stores, none of them decides what to
    keep."""
    try:
        return find()
    except (codex_keepset.Refusal, OSError, sqlite3.Error, ValueError):  # UnicodeError is a ValueError
        return nothing


@dataclass
class _Catalogs:
    listed: set[str]  # the deleted sessions they still list
    others: set[str]  # the threads of every host but this machine
    complete: bool  # every catalog read, and no host runs Codex elsewhere


def _read_catalogs(orphan: Callable[[object], bool]) -> _Catalogs:
    """What the app's catalogs say. A catalog that is a link or in a linked
    folder (it may be another profile's), cannot be read, or is of another
    shape adds nothing, and makes the state impossible to judge
    (`_state_ids`), as does no active catalog at all: nothing then says
    whether the app knows remote hosts."""
    listed: set[str] = set()
    others: set[str] = set()
    if not _catalog_folder():
        return _Catalogs(listed, others, complete=False)
    complete = True
    active_read = False
    for path in codex_desktop.catalog_paths():
        if not _exists(path):
            continue
        if not _regular(path):
            complete = False
            continue
        try:
            with codex_keepset.reading(path) as con:
                if not codex_desktop.known_catalog(con):
                    complete = False
                    continue
                active = path.name == codex_desktop.ACTIVE_CATALOG
                listed |= {i for i in codex_desktop.listed_threads(con, active=active) if orphan(i)}
                others |= codex_desktop.other_hosts_threads(con)
                complete = complete and not codex_desktop.has_remote_hosts(con)
                active_read = active_read or active
        except (codex_keepset.Refusal, sqlite3.Error):
            complete = False
    return _Catalogs(listed, others, complete and active_read)


def _catalog_folder() -> bool:
    """Whether the catalogs' folder is a folder, not a link (to another
    profile's, it may be)."""
    try:
        return stat.S_ISDIR(os.lstat(codex_desktop.catalog_paths()[0].parent).st_mode)
    except OSError:
        return False


@dataclass
class _AppDocument:
    """One of the app's JSON files the state item edits, and how."""

    path: Path
    parse: Callable[[bytes], object]
    ids: Callable[[object, Callable[[str], bool]], set[str]]
    without: Callable[[object, set[str]], object]
    remote: Callable[[object], set[str]]


def _app_documents() -> list[_AppDocument]:
    """The state file and its copy, then the daemon's loaded threads."""
    home = roots.codex_home()
    state = [
        _AppDocument(home / name, codex_desktop.parse_state, codex_desktop.state_ids,
                     codex_desktop.without, codex_desktop.remote_ids)
        for name in codex_desktop.STATE_FILES
    ]
    loaded = _AppDocument(home / codex_desktop.LOADED_THREADS, codex_desktop.parse_loaded, codex_desktop.loaded_ids,
                          codex_desktop.without_loaded, lambda _data: set())
    return [*state, loaded]


def _read_documents() -> list[tuple[_AppDocument, object]] | None:
    """The app's files that are there, read; None when one that is there
    cannot be read (`_read_document`): it may be the one that names a
    thread's other host."""
    read = []
    for doc in _app_documents():
        data = _read_document(doc)
        if data is not None:
            read.append((doc, data))
        elif os.path.lexists(doc.path):
            return None
    return read


def _others(catalogs: _Catalogs | None, documents: list[tuple[_AppDocument, object]] | None) -> set[str] | None:
    """The threads of every host but this machine: every other host's
    catalogued threads, and those a state file names with another host.
    None unless the catalogs and the app's files were all read and no host
    runs Codex threads elsewhere, since those are UUIDv7 too: nothing that
    needs telling them apart can then run."""
    if catalogs is None or not catalogs.complete or documents is None:
        return None
    return catalogs.others.union(*(doc.remote(data) for doc, data in documents))


def _state_ids(documents: list[tuple[_AppDocument, object]], others: set[str] | None,
               orphan: Callable[[object], bool]) -> set[str]:
    """The deleted sessions the app's state files and its daemon's list hold
    an entry for: a UUIDv7 (`codex_desktop.is_thread_id`) outside the
    keep-set and outside `others`."""
    if others is None:
        return set()

    def wanted(value: str) -> bool:
        return codex_desktop.is_thread_id(value) and orphan(value) and value not in others

    return set().union(*(doc.ids(data, wanted) for doc, data in documents))


def _read_document(doc: _AppDocument) -> object | None:
    """What one of the app's files holds; None, so it is left alone, when
    there is none, it or a folder on its way from the Codex home is a link
    (it may be another profile's), it cannot be read, or it is not the
    app's shape."""
    if not _real_folders(doc.path.parent):
        return None
    try:
        fd = os.open(doc.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return None
            raw = f.read()
    except OSError:
        return None
    return doc.parse(raw)


def _log_rows(orphan: Callable[[object], bool]) -> dict[str, int]:
    """id -> its rows in the logs; none without the logs or a `thread_id`."""
    path = roots.codex_sqlite_home() / LOGS_DB
    if not _exists(path) or not _regular(path):
        return {}
    with codex_keepset.reading(path) as con:
        if "thread_id" not in {row[1] for row in con.execute("PRAGMA table_info(logs)")}:
            return {}
        rows = con.execute("SELECT thread_id, COUNT(*) FROM logs WHERE thread_id IS NOT NULL GROUP BY thread_id")
        return {thread_id: count for thread_id, count in rows if orphan(thread_id)}


def _log_mentions(orphan: Callable[[object], bool], others: set[str] | None) -> dict[int, set[str]]:
    """The thread-less log rows whose text names a session dotsync's own
    logs show it deleted (`log.deleted_ids`) and that is gone, and names no
    kept session and no other host's thread: another UUID in a line may be a
    turn or a request, so only that evidence counts, and a line that is also
    a live session's stays. None without the catalogs (`_others`)."""
    path = roots.codex_sqlite_home() / LOGS_DB
    if others is None or not _exists(path) or not _regular(path):
        return {}
    deleted = {i for i in log.deleted_ids("codex") if orphan(i) and i not in others}
    if not deleted:
        return {}
    mentions: dict[int, set[str]] = {}
    with codex_keepset.reading(path) as con:
        if not {"id", "thread_id", "feedback_log_body"} <= {row[1] for row in con.execute("PRAGMA table_info(logs)")}:
            return {}
        rows = con.execute("SELECT id, feedback_log_body FROM logs WHERE thread_id IS NULL AND feedback_log_body IS NOT NULL")
        for row_id, body in rows:
            ids = set(_ID.findall(body)) if isinstance(body, str) else set()
            named = ids & deleted
            if named and not any(not orphan(i) or i in others for i in ids):
                mentions[row_id] = named
    return mentions


_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _real_folders(folder: Path) -> bool:
    """Whether `folder` and every folder between it and the Codex home are
    folders, not links."""
    home = roots.codex_home()
    while folder != home:
        try:
            if not stat.S_ISDIR(os.lstat(folder).st_mode):
                return False
        except OSError:
            return False
        if folder.parent == folder:
            return False
        folder = folder.parent
    return True


def _regular(path: Path) -> bool:
    """A plain file: a link is not followed, whatever it points to."""
    return stat.S_ISREG(os.lstat(path).st_mode)


def _exists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as e:
        raise codex_keepset.Refusal("codex_db_failed", f"cannot read {path.name}: {e}") from e
    return True


def _plain_files(folder: str) -> list[str]:
    return [entry.name for entry in _listing(folder) if _is(entry.is_file)]


def _folders(folder: str) -> list[str]:
    return [entry.name for entry in _listing(folder) if _is(entry.is_dir)]


def _is(check: Callable[..., bool]) -> bool:
    """A symlink is neither a file nor a folder here: what it points to is
    not Codex's."""
    try:
        return check(follow_symlinks=False)
    except OSError:
        return False


def _listing(folder: str) -> list[os.DirEntry]:
    """The entries of one of Codex's folders; none when it is missing or a
    link (what it points to is not Codex's)."""
    try:
        if stat.S_ISLNK(os.lstat(roots.codex_home() / folder).st_mode):
            return []
        with os.scandir(roots.codex_home() / folder) as listing:
            return list(listing)
    except FileNotFoundError:
        return []
    except OSError as e:
        raise codex_keepset.Refusal("codex_db_failed", f"cannot read {folder}: {e}") from e


def _tree_bytes(folder: Path) -> int:
    """What the files in a folder weigh, symlinks not followed; a file that
    cannot be read counts 0."""
    total = 0
    for top, _dirs, files in os.walk(folder):
        for name in files:
            with contextlib.suppress(OSError):
                info = os.lstat(os.path.join(top, name))
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
    return total


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
        raise codex_keepset.Refusal("codex_db_failed", f"cannot read {filename}: {e}") from e
    try:
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise codex_keepset.Refusal("codex_db_failed", f"{filename} is not a regular file")
            for line in f:
                owner = _line_owner(line, key, wanted)
                if owner is not None:
                    yield owner
    except OSError as e:
        raise codex_keepset.Refusal("codex_db_failed", f"cannot read {filename}: {e}") from e


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


def _cache_names() -> list[str]:
    """Names of the plain files in the reference-cache folder. A symlink, a
    folder or anything else in it is ignored."""
    return _plain_files(codex.CACHE_DIR)


# (done, total, phase): `read` the rollout files of the keep-set walk,
# `delete` the history rows, `vacuum` the pages. One callback, so a reader of
# the stream can tell the counts apart.
PhaseProgress = Callable[[int, int, str], None]


def _in_phase(progress: PhaseProgress | None, phase: str) -> Callable[[int, int], None] | None:
    return None if progress is None else lambda done, total: progress(done, total, phase)


def dry_run_payload(progress: PhaseProgress | None = None) -> dict:
    """`detect()`'s fields plus the four items in spec order, what the history
    database weighs, whether it can be vacuumed, and what a vacuum could give
    back now. Blockers are reported, not a refusal: nothing is changed. A
    refusal gives `items: []`, and the keep-set walk is skipped when the
    support check already refuses."""
    payload = codex_blockers.detect()
    refusal, vacuum_available = codex_keepset.support_check()
    found: Orphans | None = None
    if refusal is None:
        keep = codex_keepset.keep_set(_in_phase(progress, "read"))
        refusal = codex_keepset.scan_refusal(keep)
        if refusal is None:
            try:
                found = orphans(keep)
            except codex_keepset.Refusal as e:
                refusal = e
    payload["items"] = _items(found) if found is not None else []
    payload["history_db_bytes"] = codex.history_bytes()
    payload["vacuum_available"] = vacuum_available
    payload["vacuum_free_bytes"] = vacuum_free_bytes()
    payload["refusal"] = {"code": refusal.code, "message": refusal.message} if refusal else None
    return payload


def vacuum_free_bytes() -> int | None:
    """The free pages of the databases the vacuum rewrites, in bytes
    (`freelist_count` × `page_size`), read-only: what a vacuum alone could
    give back. None when one of them cannot be read, since an unknown amount
    is no reason to quit ChatGPT for a vacuum."""
    total = 0
    try:
        for path in _vacuum_paths():
            with contextlib.closing(codex.open_ro(path)) as con:
                (pages,) = con.execute("PRAGMA freelist_count").fetchone()
                (size,) = con.execute("PRAGMA page_size").fetchone()
            total += pages * size
    except sqlite3.Error:
        return None
    return total


def _vacuum_paths() -> list[Path]:
    """The databases deleting sessions and this cleanup leave free pages in,
    where a deleted session's bytes stay until they are rewritten: Codex's
    history and state, and, when they are there as plain files, its queue
    and logs and the app's catalogs (in a folder that is no link)."""
    sqlite_home = roots.codex_sqlite_home()
    optional = [sqlite_home / codex.QUEUE_DB, sqlite_home / LOGS_DB]
    if _catalog_folder():
        optional += codex_desktop.catalog_paths()
    return [sqlite_home / codex.HISTORY_DB, sqlite_home / codex.STATE_DB, *(p for p in optional if _plain(p))]


def _plain(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _log_ids(found: Orphans) -> set[str]:
    return set(found.log_rows).union(*found.log_mentions.values())


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
        ("app_list", "ChatGPT 앱 목록에 남은 지운 세션", len(found.catalog_ids), {}),
        ("app_state", "ChatGPT 앱 상태에 남은 지운 세션", len(found.state_ids), {}),
        ("logs", "로그에 남은 지운 세션", len(_log_ids(found)), {"lines": sum(found.log_rows.values()) + len(found.log_mentions)}),
        ("shell_snapshots", "지운 세션의 셸 스냅숏", len(found.snapshot_names), {}),
        ("images", "지운 세션의 생성 이미지", len(found.image_bytes), {"bytes": sum(found.image_bytes.values())}),
    ]
    return [
        {"id": item_id, "label": label, "count": count, **extra, "checked": count > 0}
        for item_id, label, count, extra in rows
    ]


# The cleanup

ITEMS = (  # what --items picks among
    "history_rows",
    "history_lines",
    "session_index",
    "queue",
    "app_list",
    "app_state",
    "logs",
    "shell_snapshots",
    "images",
)
CACHE_ITEM = "cache_files"  # not a choice: it always runs, last
COORDINATION_WAIT = 10.0  # seconds; a coordination lock held longer means a Codex is at work
JSONL_LOCK_WAIT = 5.0  # seconds for a JSONL file's own lock
WRITE_TIMEOUT = 5.0  # seconds a write waits on a locked database
BACKUP_INFIX = ".dotsync-backup-"
VACUUM_STOP_CHECK = 1000  # SQLite steps between two looks at SIGTERM while a VACUUM runs


def run_cleanup(
    items: list[str],
    vacuum: bool,
    *,
    stop: StopSignal,
    progress: PhaseProgress,
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
            blockers = codex_blockers.detect()["blockers"]
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


def _vacuum(report: dict, stop: StopSignal, progress: PhaseProgress) -> dict:
    """The `--vacuum` result, run while both locks are still held. It starts
    only when the items ended normally; a stop or a Codex that started leaves
    the databases as they are, and the result says so."""
    paths = _vacuum_paths()
    before = sum(codex.db_bytes(p) for p in paths)
    reasons = {item.get("reason") for item in report["items"]}
    if report["stopped"] or stop.requested:
        why = "stopped"
    elif "codex_started" in reasons:
        why = "blocker"
    else:
        try:
            refusal, available = codex_keepset.support_check()
        except Exception as e:
            return _failed_vacuum(before, e)
        if refusal is not None:
            return _failed_vacuum(before, refusal)
        if not available:
            why = "unavailable"
        else:
            return reclaim(paths, stop=stop, detect=codex_blockers.detect, progress=_in_phase(progress, "vacuum"))
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


def _orphans_to_clean(progress: PhaseProgress) -> Orphans:
    """The support check, then the keep-set walk; either refusal raises."""
    refusal, _vacuum_available = codex_keepset.support_check()
    if refusal is not None:
        raise refusal
    keep = codex_keepset.keep_set(_in_phase(progress, "read"))
    refusal = codex_keepset.scan_refusal(keep)
    if refusal is not None:
        raise refusal
    return orphans(keep)


def _run_items(
    order: list[str],
    found: Orphans,
    report: dict,
    removed: dict[str, object],
    stop: StopSignal,
    progress: PhaseProgress,
) -> None:
    results: list[dict] = report["items"]
    for position, item in enumerate(order):
        if stop.requested:
            report["stopped"] = True
            why = "stopped"
        elif codex_blockers.detect()["blockers"]:
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
    item: str, found: Orphans, stop: StopSignal, progress: PhaseProgress
) -> tuple[dict, object]:
    """One item's result, and the ids it removed. An item that raises is
    `failed` with its message."""
    try:
        if item == "history_rows":
            return _delete_history_rows(sorted(found.history_ids), stop, _in_phase(progress, "delete"))
        if item == "history_lines":
            return _drop_lines(item, HISTORY_JSONL, "session_id", set(found.history_lines))
        if item == "session_index":
            return _drop_lines(item, SESSION_INDEX_JSONL, "id", found.index_ids)
        if item == "queue":
            return _delete_queue(sorted(found.queue_ids))
        if item == "app_list":
            return _clear_catalogs(found.catalog_ids)
        if item == "app_state":
            return _drop_state(found.state_ids)
        if item == "logs":
            return _delete_logs(found.log_rows, found.log_mentions)
        if item == "shell_snapshots":
            return _remove_in(item, SNAPSHOTS_DIR, found.snapshot_names)
        if item == "images":
            return _remove_in(item, IMAGES_DIR, sorted(found.image_bytes))
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


def _clear_catalogs(ids: set[str]) -> tuple[dict, list[str]]:
    """In each catalog that lists one of `ids`, in one transaction: the
    active one gets a full check requested, so the app drops what is gone
    when it next starts (rows it marks itself, as it does after its own
    migrations); the old one, which no app here opens, loses their rows. A
    catalog that became a link or another shape is left alone. The count is
    the ids of the catalogs that changed, kept when a later one fails."""
    changed: set[str] = set()
    if not ids or not _catalog_folder():
        return _item("app_list", "done", 0), []
    try:
        for path in codex_desktop.catalog_paths():
            if not _exists(path) or not _regular(path):
                continue
            active = path.name == codex_desktop.ACTIVE_CATALOG
            with _writing(path) as con:
                if not codex_desktop.known_catalog(con):
                    continue
                with _transaction(con):
                    listed = codex_desktop.listed_threads(con, active=active) & ids
                    if listed and active:
                        codex_desktop.request_full_check(con)
                    elif listed:
                        codex_desktop.delete_threads(con, listed)
                changed |= listed
    except (codex_keepset.Refusal, sqlite3.Error, OSError) as e:
        return _item("app_list", "failed", len(changed), message=_one_line(e)), sorted(changed)
    return _item("app_list", "done", len(changed)), sorted(changed)


def _drop_state(ids: set[str]) -> tuple[dict, list[str]]:
    """Each of the app's files without the entries of `ids`, put in place
    whole (`_replace`); one that holds none, or is left alone
    (`_read_document`), is not touched. The count is the ids dropped from a
    file, kept when a later file fails."""
    changed: set[str] = set()
    try:
        for doc in _app_documents():
            data = _read_document(doc) if ids else None
            dropped = doc.ids(data, ids.__contains__) if data is not None else set()
            if not dropped:
                continue
            kept = codex_desktop.dump_state(doc.without(data, ids))
            try:
                _replace(doc.path, [kept], stat.S_IMODE(os.lstat(doc.path).st_mode))
            except OSError:
                if _holds(doc.path, kept):  # in place; only the folder's sync failed
                    changed |= dropped
                raise
            changed |= dropped
    except (OSError, ValueError) as e:  # UnicodeEncodeError is a ValueError
        return _item("app_state", "failed", len(changed), message=_one_line(e)), sorted(changed)
    return _item("app_state", "done", len(changed)), sorted(changed)


def _holds(path: Path, content: bytes) -> bool:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as f:
            return f.read() == content
    except OSError:
        return False


def _delete_logs(rows: dict[str, int], mentions: dict[int, set[str]]) -> tuple[dict, list[str]]:
    """In one transaction, the log rows of the ids, and the thread-less rows
    whose text names one; logs that became a link are left alone. The count
    is the ids that had a row deleted, and `lines` the rows, as the dry run
    counts them."""
    deleted: set[str] = set()
    lines = 0
    path = roots.codex_sqlite_home() / LOGS_DB
    if (rows or mentions) and _regular(path):
        with _writing(path) as con:
            with _transaction(con):
                for tid in sorted(rows):
                    count = con.execute("DELETE FROM logs WHERE thread_id = ?", (tid,)).rowcount
                    if count > 0:
                        deleted.add(tid)
                        lines += count
                for row_id, named in sorted(mentions.items()):
                    if con.execute("DELETE FROM logs WHERE id = ? AND thread_id IS NULL", (row_id,)).rowcount:
                        deleted |= named
                        lines += 1
    return _item("logs", "done", len(deleted), lines=lines), sorted(deleted)


def _remove_in(item: str, folder: str, names: list[str]) -> tuple[dict, list[str]]:
    """The named files or folders in one of Codex's folders. A symlink put in
    one's place since the scan is removed, never followed (`safety.remove`)."""
    root = roots.codex_home() / folder
    gone: list[str] = []
    errors: list[str] = []
    for name in names:
        error = safety.remove(safety.child(root, name))
        if error:
            errors.append(error)
        else:
            gone.append(name)
    if errors:
        return _item(item, "failed", len(gone), message=errors[0]), gone
    return _item(item, "done", len(gone)), gone


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
    paths: list[Path],
    *,
    stop: StopSignal,
    detect: Callable[[], dict],
    progress: Callable[[int, int], None] = lambda freed, total: None,
) -> dict:
    """Give the databases' free pages back to the disk by rewriting each one
    whole (`VACUUM`), and say why it stopped: `freelist_empty` (all given
    back), `blocker` (a Codex started), `stopped` (SIGTERM; it interrupts a
    running VACUUM, which leaves that database as it was), `busy` (a
    checkpoint could not finish, or a database stayed locked) or `error` (any
    other sqlite or OS error, with a `message`). Nothing else uses the
    databases then, so the rewrite contends with no writer; it keeps their
    auto_vacuum mode. A database with nothing free is not rewritten.
    Sizes are the databases plus their `-wal`. `progress(freed, total)` runs
    after each database, in pages."""
    before = sum(codex.db_bytes(p) for p in paths)
    why = "freelist_empty"
    try:
        with contextlib.ExitStack() as stack:
            cons = [stack.enter_context(_writing(p)) for p in paths]
            if not all(_checkpointed(con) for con in cons):
                return _reclaimed(paths, before, "busy")
            free = [con.execute("PRAGMA freelist_count").fetchall()[0][0] for con in cons]
            freed = 0
            for con, pages in zip(cons, free):
                if pages == 0:
                    continue
                if detect()["blockers"]:
                    why = "blocker"
                    break
                if stop.requested:
                    why = "stopped"
                    break
                con.set_progress_handler(lambda: stop.requested, VACUUM_STOP_CHECK)
                con.execute("VACUUM")
                freed += pages
                progress(freed, sum(free))
                if not _checkpointed(con):
                    why = "busy"
                    break
    except (sqlite3.Error, OSError) as e:
        message = str(e)
        if isinstance(e, sqlite3.OperationalError) and message == "interrupted":
            why = "stopped"
        elif isinstance(e, sqlite3.OperationalError) and ("locked" in message or "busy" in message):
            why = "busy"
        else:
            return {**_reclaimed(paths, before, "error"), "message": _one_line(e)}
    return _reclaimed(paths, before, why)


def _checkpointed(con: sqlite3.Connection) -> bool:
    """Whether `wal_checkpoint(TRUNCATE)` ran to the end (its first column,
    `busy`, is 0)."""
    return con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()[0][0] == 0


def _reclaimed(paths: list[Path], before: int, why: str) -> dict:
    return {"bytes_before": before, "bytes_after": sum(codex.db_bytes(p) for p in paths), "stopped_because": why}


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
