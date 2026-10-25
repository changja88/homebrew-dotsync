"""Codex sessions: root threads read from Codex's SQLite state (read-only),
their children folded in, how big their rollout files are, and whether any
process still holds them."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from dotsync.cli_json import JSONCommandError
from dotsync.sessions import grouping, procs, roots, safety

STATE_DB = "state_5.sqlite"
HISTORY_DB = "thread_history_1.sqlite"
_UUID = r"[0-9a-fA-F-]{36}"
_ROLLOUT = re.compile(
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


def rollout_files() -> dict[str, list[Path]]:
    """Thread id → its rollout files, active and archived, plain and zstd.
    Symlinks are not followed; names with a non-canonical id are ignored."""
    found: dict[str, list[Path]] = {}
    for top in ("sessions", "archived_sessions"):
        for dirpath, _dirs, names in os.walk(roots.codex_home() / top, followlinks=False):
            for name in sorted(names):
                m = _ROLLOUT.match(name)
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
    except procs.ToolFailure:
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
