"""Codex sessions: root threads read from Codex's SQLite state (read-only),
their children folded in, how big their rollout files are, whether any
process still holds them, and deleting them through Codex's own command."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sqlite3
import stat
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from dotsync import locks
from dotsync.cli_json import JSONCommandError, now_iso
from dotsync.sessions import grouping, log, procs, roots, safety
from dotsync.sessions.stop import StopSignal

STATE_DB = "state_5.sqlite"
QUEUE_DB = "queue_1.sqlite"
HISTORY_DB = "thread_history_1.sqlite"
ROLLOUT_TREES = ("sessions", "archived_sessions")  # under the Codex home
_UUID = r"[0-9a-fA-F-]{36}"
# Group 1 is the thread id; group 2, when there, the rollout id.
ROLLOUT_NAME = re.compile(
    rf"^rollout-\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}-\d{{2}}-\d{{2}}-({_UUID})(?:_({_UUID}))?\.jsonl(?:\.zst)?$"
)


@dataclass
class Thread:
    id: str
    name: str | None
    title: str
    cwd: str
    archived: bool
    rollout_path: str
    updated_at_ms: int


def open_ro(path: Path) -> sqlite3.Connection:
    """The database at `path`, which this process cannot write to."""
    con = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, timeout=5)
    con.execute("PRAGMA query_only = 1")
    return con


def _is_subagent_source(source: object) -> bool:
    # Plain sources are `cli`, `exec`, `vscode`; sub-agents are JSON objects.
    try:
        parsed = json.loads(source) if isinstance(source, str) else None
    except ValueError:
        return False
    return isinstance(parsed, dict) and "subagent" in parsed


def read_tree(con: sqlite3.Connection) -> tuple[list[Thread], dict[str, list[str]]]:
    """The root threads, and for each root id the ids of all its descendants."""
    edges: dict[str, list[str]] = {}
    children: set[str] = set()
    for parent, child in con.execute(
        "SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges"
    ):
        edges.setdefault(parent, []).append(child)
        children.add(child)
    rows = con.execute(
        "SELECT id, name, title, cwd, archived, rollout_path, updated_at_ms, source FROM threads"
    )
    threads = [
        Thread(r[0], r[1], r[2] or "", r[3] or "", bool(r[4]), r[5] or "", r[6] or 0)
        for r in rows
        if r[0] not in children and not _is_subagent_source(r[7])
    ]
    return threads, {t.id: _descendants(t.id, edges) for t in threads}


def _descendants(root: str, edges: dict[str, list[str]]) -> list[str]:
    seen = {root}
    found: list[str] = []
    queue = deque([root])
    while queue:
        for child in edges.get(queue.popleft(), ()):
            if child not in seen:
                seen.add(child)
                found.append(child)
                queue.append(child)
    return found


def rollout_files(*, strict: bool = False) -> dict[str, list[Path]]:
    """Thread id → its rollout files, active and archived, plain and zstd.
    Symlinks are not followed; names with a non-canonical id are ignored. A
    folder that cannot be listed is skipped, or with `strict` raises OSError
    (a missing one is fine either way)."""

    def fail(error: OSError) -> None:
        if not isinstance(error, FileNotFoundError):
            raise error

    found: dict[str, list[Path]] = {}
    for top in ROLLOUT_TREES:
        walk = os.walk(roots.codex_home() / top, followlinks=False, onerror=fail if strict else None)
        for dirpath, _dirs, names in walk:
            for name in sorted(names):
                m = ROLLOUT_NAME.match(name)
                if not m or not safety.is_valid_id(m.group(1)):
                    continue
                if m.group(2) and not safety.is_valid_id(m.group(2)):
                    continue
                path = Path(dirpath) / name
                if not path.is_symlink():
                    found.setdefault(m.group(1), []).append(path)
    return found


def _state_db() -> Path:
    return roots.codex_sqlite_home() / STATE_DB


def cwds() -> list[str]:
    """The distinct `threads.cwd` values; none when Codex has no state yet."""
    if not _state_db().exists():
        return []
    try:
        with contextlib.closing(open_ro(_state_db())) as con:
            return [r[0] for r in con.execute("SELECT DISTINCT cwd FROM threads") if r[0]]
    except sqlite3.Error as e:
        raise JSONCommandError("codex_db_failed", f"cannot read Codex's state: {e}") from e


@dataclass
class _Held:
    """What the process scan says; `paths` is None when the scan failed."""

    paths: set[str] | None

    def holds(self, lock: Path) -> bool:
        return self.paths is not None and os.path.realpath(lock) in self.paths


def _open_by_others() -> _Held:
    try:
        scan = procs.lsof_scan()
    except Exception:  # any failure, not only ToolFailure: unknown means keep
        return _Held(None)
    me = os.getpid()
    return _Held(
        {os.path.realpath(f) for pid, o in scan.items() if pid != me for f in o.files}
    )


def _state_of(ids: list[str], held: _Held) -> tuple[str, str | None]:
    if not all(safety.is_valid_id(i) for i in ids):
        return "unknown", "invalid_id"
    if held.paths is None:
        return "unknown", "scan_failed"
    lock_dir = roots.codex_home() / "thread-writer-locks"
    try:
        locks = [safety.child(lock_dir, f"{i}.lock") for i in ids]
    except safety.UnsafePath:
        return "unknown", "unsafe_path"
    if any(held.holds(lock) for lock in locks):
        return "running", None
    return "idle", None


def _size_and_mtime(paths: list[Path]) -> tuple[int, float | None]:
    total, newest = 0, None
    for p in paths:
        try:
            st = os.lstat(p)
        except OSError:
            continue
        total += st.st_size
        newest = st.st_mtime if newest is None else max(newest, st.st_mtime)
    return total, newest


def _history_bytes() -> int:
    base = roots.codex_sqlite_home() / HISTORY_DB
    total = 0
    for p in (base, base.with_name(base.name + "-wal")):
        try:
            total += os.lstat(p).st_size
        except OSError:
            pass
    return total


def list_payload(extra_cwds: Iterable[str] = ()) -> dict:
    """The `sessions list codex --json` object, shaped like Claude's.
    `extra_cwds` are the other tool's cwds."""
    now = time.time()
    threads, tree = _read_threads()
    items: list[dict] = []
    if threads:
        files = rollout_files()
        held = _open_by_others()
        for t in threads:
            ids = [t.id, *tree[t.id]]
            size, newest = _size_and_mtime([p for i in ids for p in files.get(i, ())])
            state, reason = _state_of(ids, held)
            item = {
                "id": t.id,
                "title": t.name or t.title,
                "folder": os.path.basename(t.cwd),
                "cwd": t.cwd,
                "last_activity": _iso(newest if newest is not None else t.updated_at_ms / 1000),
                "bytes": size,
                "state": state,
                "children": len(tree[t.id]),
                "archived": t.archived,
            }
            if reason:
                item["reason"] = reason
            items.append(item)

    locator = grouping.Locator.default()
    item_cwds = {i["cwd"] for i in items}
    places: dict[str, grouping.Place] = {}
    if items:
        known = locator.known_roots(item_cwds | set(extra_cwds))
        places = {cwd: locator.place(cwd, known) for cwd in item_cwds}
    return {
        "tool": "codex",
        "checked_at": _iso(now),
        "totals": {
            "sessions": len(items),
            "bytes": sum(i["bytes"] for i in items),
            "running": sum(i["state"] == "running" for i in items),
            "history_db_bytes": _history_bytes(),
        },
        "groups": grouping.groups(items, places),
    }


def _read_threads() -> tuple[list[Thread], dict[str, list[str]]]:
    """No state database means Codex was never used: nothing to list."""
    if not _state_db().exists():
        return [], {}
    try:
        with contextlib.closing(open_ro(_state_db())) as con:
            return read_tree(con)
    except sqlite3.Error as e:
        raise JSONCommandError("codex_db_failed", f"cannot read Codex's state: {e}") from e


def _iso(epoch: float) -> str:
    """UTC with whole seconds, as Swift's .iso8601 decoding expects."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Delete

CODEX_TIMEOUT = 180  # seconds, for `codex delete`
CACHE_DIR = "tui-thread-reference-capabilities"
PARTS = ("rollout", "state", "history")  # what a result's `remaining` can name
_HISTORY_TABLES = (
    "thread_items",
    "thread_realtime_items",
    "thread_turns",
    "thread_history_projection_state",
)
# The 0.164.0 CLI prints one generic line for every failure. Its app-server's
# own wording is matched only in case it does reach stderr.
_IN_USE = ("already has an active writer", "already has a live local writer")
_REFERENCED = "forked history still references it"


def codex_binary() -> str | None:
    homebrew = "/opt/homebrew/bin/codex"
    if os.access(homebrew, os.X_OK):
        return homebrew
    return shutil.which("codex")


@dataclass(frozen=True)
class Remains:
    """What is left of some threads, in enough detail that two looks are
    equal only when nothing of it changed in between. A part whose check
    could not finish is in `unchecked`, and counts as left: unknown means
    keep."""

    rollout_files: frozenset[Path]
    thread_rows: frozenset[str]  # the ids that have a `threads` row
    edges: int  # spawn edges that name one of the ids
    history_rows: tuple[int, ...]  # per history table
    unchecked: frozenset[str]

    @property
    def rollouts(self) -> bool:
        return bool(self.rollout_files) or "rollout" in self.unchecked

    @property
    def state(self) -> bool:
        return bool(self.thread_rows) or self.edges > 0 or "state" in self.unchecked

    @property
    def history(self) -> bool:
        return any(self.history_rows) or "history" in self.unchecked

    def parts(self) -> list[str]:
        """The parts left, in report order."""
        left = {"rollout": self.rollouts, "state": self.state, "history": self.history}
        return [part for part in PARTS if left[part]]


def inspect(ids: list[str], rollout_ids: set[str]) -> Remains:
    """What is left of the threads `ids`, read-only: their rollout files,
    their state rows, and the history rows keyed by `rollout_ids`, by `ids`,
    or by a rollout id a file still there is named with. A database that does
    not exist holds no rows; one that cannot be read leaves its part
    unchecked."""
    unchecked: set[str] = set()
    found: frozenset[Path] = frozenset()
    try:
        files = rollout_files(strict=True)
        found = frozenset(p for i in ids for p in files.get(i, ()))
    except OSError:
        unchecked.add("rollout")
    rows, edges = frozenset(), 0
    try:
        rows, edges = _state_rows(ids)
    except (sqlite3.Error, OSError):
        unchecked.add("state")
    history = (0,) * len(_HISTORY_TABLES)
    try:
        history = _history_rows(set(ids) | rollout_ids | {_rollout_id(p) for p in found})
    except (sqlite3.Error, OSError):
        unchecked.add("history")
    return Remains(found, rows, edges, history, frozenset(unchecked))


def _state_rows(ids: list[str]) -> tuple[frozenset[str], int]:
    if not ids or not _exists(_state_db()):
        return frozenset(), 0
    marks = ",".join("?" * len(ids))
    with contextlib.closing(open_ro(_state_db())) as con:
        rows = frozenset(
            r[0] for r in con.execute(f"SELECT id FROM threads WHERE id IN ({marks})", ids)
        )
        (edges,) = con.execute(
            "SELECT COUNT(*) FROM thread_spawn_edges"
            f" WHERE parent_thread_id IN ({marks}) OR child_thread_id IN ({marks})",
            [*ids, *ids],
        ).fetchone()
    return rows, edges


def _history_rows(keys: set[str]) -> tuple[int, ...]:
    """Rows per history table whose `thread_id` (a rollout id) is in `keys`."""
    db = roots.codex_sqlite_home() / HISTORY_DB
    if not keys or not _exists(db):
        return (0,) * len(_HISTORY_TABLES)
    ordered = sorted(keys)
    marks = ",".join("?" * len(ordered))
    with contextlib.closing(open_ro(db)) as con:
        return tuple(
            con.execute(f"SELECT COUNT(*) FROM {table} WHERE thread_id IN ({marks})", ordered)
            .fetchone()[0]
            for table in _HISTORY_TABLES
        )


def _exists(path: Path) -> bool:
    """Only "no such file" means it is not there. Any other error raises
    OSError: what is there is then unknown."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    return True


def _rollout_id(path: Path) -> str:
    """What a rollout file's history rows are keyed by: the rollout id in its
    name, else its thread id."""
    m = ROLLOUT_NAME.match(path.name)
    return m.group(2) or m.group(1)


@dataclass
class _Batch:
    """What one delete reads once, before the first id."""

    binary: str | None
    tree: dict[str, list[str]]  # root → descendants: gone once Codex deletes the root
    held: _Held  # what other processes hold open


def _gather() -> _Batch:
    binary = codex_binary()
    _threads, tree = _read_threads()
    return _Batch(binary, tree, _open_by_others())


def delete(
    ids: list[str],
    *,
    stop: StopSignal,
    progress: Callable[[int, int], None],
    log_command: str = "delete",
) -> dict:
    """Delete each root thread, with its spawn subtree, through Codex's own
    `codex delete`, then the reference-cache files Codex leaves behind. When
    Codex does not finish, the result says what is left. The report, plus the
    paths involved, goes to the result log under `log_command`."""
    for tid in ids:
        safety.validate_id(tid)  # one bad id and nothing is touched
    ids = list(dict.fromkeys(ids))
    started_at = now_iso()
    results: list[dict] = []
    report = {"tool": "codex", "results": results, "stopped": False}
    paths: dict[str, dict] = {}
    with locks.tool_lock("codex", shared=False):
        try:
            batch = _gather()
            for done, tid in enumerate(ids, start=1):
                if stop.requested:
                    report["stopped"] = True
                    results.extend(
                        _result(rest, "skipped", "stopped", len(batch.tree.get(rest, ())))
                        for rest in ids[done - 1 :]
                    )
                    break
                result, used = _delete_one(tid, batch)
                results.append(result)
                if used:
                    paths[tid] = used
                progress(done, len(ids))
        finally:
            log.write(log_command, "codex", started_at, {**report, "paths": paths})
    return report


def _delete_one(tid: str, batch: _Batch) -> tuple[dict, dict]:
    """One id's result, and the paths it involved, for the log."""
    ids = [tid, *batch.tree.get(tid, ())]
    children = len(ids) - 1
    reason = _not_now(ids, batch)
    if reason:
        return _result(tid, "skipped", reason, children), {}
    before = inspect(ids, set())
    if before.unchecked:
        return _result(tid, "skipped", "inspect_failed", children), {}
    if tid not in batch.tree and tid in before.thread_rows:
        # a child, or a thread made after the tree was read: its subtree is unknown
        return _result(tid, "skipped", "not_root", children), {}
    if not before.parts():
        return _leftovers(tid, ids)
    return _run_codex(tid, ids, before, batch.binary)


def _not_now(ids: list[str], batch: _Batch) -> str | None:
    """Why the batch's own checks keep these threads from Codex, if they do."""
    if batch.binary is None:
        return "codex_missing"
    state, reason = _state_of(ids, batch.held)
    return "in_use" if state == "running" else reason  # invalid_id, scan_failed, unsafe_path


def _leftovers(tid: str, ids: list[str]) -> tuple[dict, dict]:
    """Nothing of the threads is left but, from a run that could not remove
    them, maybe their reference-cache files: those go now."""
    children = len(ids) - 1
    try:
        caches = _cache_files(ids)
    except safety.UnsafePath:
        return _result(tid, "skipped", "unsafe_path", children), {}
    except OSError:
        return _result(tid, "skipped", "inspect_failed", children), {}
    if not caches:
        return _result(tid, "skipped", "not_found", children), {}
    freed, removed, failed = _remove_files(caches)
    result = _result(tid, "partial" if failed else "deleted", None, children, freed)
    if failed:
        result["failed"] = failed
    return result, _log_paths([], removed)


def _run_codex(
    tid: str, ids: list[str], before: Remains, binary: str
) -> tuple[dict, dict]:
    """Run `codex delete` on the root, then judge by what is left."""
    children = len(ids) - 1
    sizes = {p: _size(p) for p in before.rollout_files}
    answer = _ask_codex(binary, tid)
    if answer.error is not None and answer.error.code == "missing":
        return _result(tid, "skipped", "codex_missing", children), {}  # it never started
    after = inspect(ids, {_rollout_id(p) for p in before.rollout_files})
    outcome, reason, extra = _judge(ids, before, after, answer)
    freed = sum(size for p, size in sizes.items() if _gone(p))
    removed: list[Path] = []
    if not after.rollouts and not after.state:  # Codex no longer has the threads
        more, removed, failed = _clear_caches(ids)
        freed += more
        if failed:
            outcome = "partial"
            extra["failed"] = failed
    result = {**_result(tid, outcome, reason, children, freed), **extra}
    return result, _log_paths(sorted(sizes, key=str), removed)


@dataclass(frozen=True)
class _Answer:
    """How `codex delete` ended: an exit code, or the ToolFailure of a run
    that timed out (the child is killed) or could not start."""

    exit0: bool
    stderr: str = ""
    error: procs.ToolFailure | None = None


def _ask_codex(binary: str, tid: str) -> _Answer:
    # --no-daemon: a shared app-server that has the thread loaded would stop it first.
    argv = [binary, "--no-daemon", "delete", tid, "--force"]
    try:
        done = procs.run(argv, timeout=CODEX_TIMEOUT, cwd=Path.home())
    except procs.ToolFailure as e:
        return _Answer(False, error=e)
    return _Answer(done.returncode == 0, done.stderr or "")


def _judge(
    ids: list[str], before: Remains, after: Remains, answer: _Answer
) -> tuple[str, str | None, dict]:
    """The outcome, its reason, and the rest of the result, from what is left
    after Codex ran. Never a guess: a part that could not be checked is left."""
    if after.unchecked:
        unchecked = [part for part in PARTS if part in after.unchecked]
        return "partial", "failed", {"remaining": after.parts(), "unchecked": unchecked}
    left = after.parts()
    if not left:
        return "deleted", None, {}
    if left == ["history"]:
        # Codex refuses a thread without a rollout; "남은 데이터 정리" removes these rows.
        return "partial", "no_rollout", {"remaining": left}
    if answer.exit0:
        return "partial", "failed", {"remaining": left}  # done, Codex said, yet this is left
    if after == before:  # Codex refused before changing anything
        reason, extra = _refusal(ids, answer)
        return "skipped", reason, extra
    timed_out = answer.error is not None and answer.error.code == "timeout"
    return "partial", "timeout" if timed_out else "failed", {"remaining": left}


def _refusal(ids: list[str], answer: _Answer) -> tuple[str, dict]:
    """Why Codex refused. Its stderr rarely says, so a writer lock of the
    root or a descendant held open now is the main sign of one in use."""
    if _state_of(ids, _open_by_others())[0] == "running":
        return "in_use", {}
    if _REFERENCED in answer.stderr:
        return "referenced", {}
    if any(words in answer.stderr for words in _IN_USE):
        return "in_use", {}
    message = str(answer.error) if answer.error else _last_line(answer.stderr)
    return "refused", {"message": message}


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _cache_files(ids: list[str]) -> list[Path]:
    """The reference-cache files of `ids` that exist. UnsafePath when their
    folder is not where it should be; OSError when one cannot be checked."""
    folder = roots.codex_home() / CACHE_DIR
    return [p for p in (safety.child(folder, i) for i in ids) if _exists(p)]


def _clear_caches(ids: list[str]) -> tuple[int, list[Path], list[dict]]:
    try:
        files = _cache_files(ids)
    except (safety.UnsafePath, OSError) as e:
        return 0, [], [_failure(roots.codex_home() / CACHE_DIR, e)]
    return _remove_files(files)


def _remove_files(paths: list[Path]) -> tuple[int, list[Path], list[dict]]:
    """Bytes freed, the paths removed, and the failures. Files only: a folder
    in a cache file's place is not what Codex leaves, so it stays."""
    freed, removed, failed = 0, [], []
    for path in paths:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError as e:
            failed.append(_failure(path, e))
            continue
        if stat.S_ISDIR(info.st_mode):
            failed.append(_failure(path, "a folder, not a cache file"))
            continue
        error = safety.remove(path)
        if error:
            failed.append(_failure(path, error))
        else:
            freed += info.st_size
            removed.append(path)
    return freed, removed, failed


def _size(path: Path) -> int:
    try:
        return os.lstat(path).st_size
    except OSError:
        return 0


def _gone(path: Path) -> bool:
    """Whether `path` is known to be gone."""
    try:
        return not _exists(path)
    except OSError:
        return False


def _result(tid: str, outcome: str, reason: str | None, children: int, freed: int = 0) -> dict:
    result: dict = {"id": tid, "outcome": outcome}
    if reason:
        result["reason"] = reason
    result["children"] = children
    result["freed_bytes"] = freed
    return result


def _failure(path: Path, error: object) -> dict:
    return {"path": str(path), "error": str(error).replace("\n", " ")}


def _log_paths(rollouts: list[Path], removed: list[Path]) -> dict:
    """What one id involved: the rollout files Codex was asked to delete, and
    the cache files dotsync removed."""
    used: dict[str, list[str]] = {}
    if rollouts:
        used["rollouts"] = [str(p) for p in rollouts]
    if removed:
        used["removed"] = [str(p) for p in removed]
    return used
