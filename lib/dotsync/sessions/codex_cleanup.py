"""The Codex cleanup: the orphans it finds and the run that cleans.

`orphans` and `dry_run_payload` are read-only: what the stores hold for ids
outside the keep-set. The run (`run_cleanup`) is the one place dotsync writes
inside Codex's own databases and history files. It runs only while nothing
uses ~/.codex (`codex_blockers`), after the support check and the keep-set
(`codex_keepset`), under Codex's own coordination lock, and deletes only
what `orphans` reports."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
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
from dotsync.sessions import codex, codex_blockers, codex_keepset, log, roots, safety
from dotsync.sessions.stop import StopSignal


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


def orphans(keep: codex_keepset.KeepSet) -> Orphans:
    """Read-only. Raises Refusal(codex_db_failed) when a store cannot be read:
    that is never a count of 0. A missing `history.jsonl`,
    `session_index.jsonl` or cache folder holds nothing."""

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
        raise codex_keepset.Refusal("codex_db_failed", f"cannot read {codex.CACHE_DIR}: {e}") from e
    for entry in entries:
        try:
            if entry.is_file(follow_symlinks=False):
                yield entry.name
        except OSError:
            continue


def dry_run_payload(progress: Callable[[int, int], None] | None = None) -> dict:
    """`detect()`'s fields plus the four items in spec order, what the history
    database weighs, whether it can be vacuumed, and what a vacuum could give
    back now. Blockers are reported, not a refusal: nothing is changed. A
    refusal gives `items: []`, and the keep-set walk is skipped when the
    support check already refuses."""
    payload = codex_blockers.detect()
    refusal, vacuum_available = codex_keepset.support_check()
    found: Orphans | None = None
    if refusal is None:
        keep = codex_keepset.keep_set(progress)
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
    """The history database's free pages in bytes (`freelist_count` ×
    `page_size`), read-only: what a vacuum alone could give back. None when
    the database cannot be read, since an unknown amount is no reason to quit
    ChatGPT for a vacuum."""
    try:
        with contextlib.closing(codex.open_ro(roots.codex_sqlite_home() / codex.HISTORY_DB)) as con:
            (pages,) = con.execute("PRAGMA freelist_count").fetchone()
            (size,) = con.execute("PRAGMA page_size").fetchone()
    except sqlite3.Error:
        return None
    return pages * size


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
            refusal, available = codex_keepset.support_check()
        except Exception as e:
            return _failed_vacuum(before, e)
        if refusal is not None:
            return _failed_vacuum(before, refusal)
        if not available:
            why = "unavailable"
        else:
            return reclaim(path, stop=stop, detect=codex_blockers.detect, progress=progress)
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
    refusal, _vacuum_available = codex_keepset.support_check()
    if refusal is not None:
        raise refusal
    keep = codex_keepset.keep_set(progress)
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
    progress: Callable[[int, int], None],
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
