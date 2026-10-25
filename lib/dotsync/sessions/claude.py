"""Claude sessions on disk: which files belong to each session, how big they
are, what the session is called, whether it is still in use, and deleting
the ones that are not."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import json
import os
import re
import shutil
import stat
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dotsync import locks
from dotsync.cli_json import now_iso
from dotsync.sessions import grouping, log, procs, roots, safety
from dotsync.sessions.stop import StopSignal

QUARANTINE_SUFFIX = ".dotsync-deleting"
TITLE_LIMIT = 120
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_TRANSCRIPT = re.compile(rf"^({_UUID})\.jsonl({re.escape(QUARANTINE_SUFFIX)})?$")
AGENTS_TIMEOUT = 30  # seconds, for `claude agents`
RECENT_SECONDS = 30 * 60
_PID_FILE = re.compile(r"^(\d+)\.json$")
# Directories under claude_home() that hold one entry per session id.
_HOME_DIRS = ("file-history", "session-env", "uploads", "dev-mods", "image-cache")


def encode_cwd(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


@dataclass
class ClaudeSession:
    id: str
    transcripts: list[Path]  # active <id>.jsonl
    quarantined: list[Path]  # <id>.jsonl.dotsync-deleting
    project_dirs: list[Path]
    cwd: str
    title: str
    last_activity: float  # epoch seconds
    bytes: int
    subagents: int
    paths: list[Path]  # owned paths that exist
    unsafe: bool = field(default=False)  # an owned path failed safety.child


def _owned(
    sid: str, project_dirs: list[Path], cwds: list[str]
) -> tuple[list[Path], bool]:
    """Every path the session owns, and whether any could not be built safely
    (those are left out)."""
    paths: list[Path] = []
    unsafe = False

    def add(root: Path, *names: str) -> None:
        nonlocal unsafe
        try:
            path = root
            for name in names:
                path = safety.child(path, name)
        except safety.UnsafePath:
            unsafe = True
            return
        paths.append(path)

    orphan = re.compile(rf"^{re.escape(sid)}\.orphaned-.+\.jsonl$")
    superseded = re.compile(rf"^{re.escape(sid)}\.jsonl\.superseded-.+$")
    for p in project_dirs:
        add(p, f"{sid}.jsonl")
        add(p, f"{sid}.jsonl{QUARANTINE_SUFFIX}")
        for name in _listdir(p):
            if orphan.match(name) or superseded.match(name):
                add(p, name)
        for sub in ("subagents", "tool-results", "mcp-tasks"):
            add(p, sid, sub)

    home = roots.claude_home()
    for directory in _HOME_DIRS:
        add(home, directory, sid)
    add(home, "debug", f"{sid}.txt")

    temp = roots.claude_temp_root()
    if temp is not None:
        encoded = {p.name for p in project_dirs} | {encode_cwd(c) for c in cwds}
        for enc in sorted(encoded):
            add(temp, enc, sid)
    return paths, unsafe


def owned_paths(sid: str, project_dirs: list[Path], cwds: list[str]) -> list[Path]:
    """The session's files and directories, existing or not. Raises
    safety.UnsafePath when any of them is not directly inside its root."""
    paths, unsafe = _owned(sid, project_dirs, cwds)
    if unsafe:
        raise safety.UnsafePath(f"session {sid} has a path outside its root")
    return paths


def _listdir(directory: Path) -> list[str]:
    try:
        return os.listdir(directory)
    except OSError:
        return []


def read_title_and_cwd(path: Path, window: int = 2 * 1024 * 1024) -> tuple[str, str]:
    """Title and cwd of a transcript, from at most `window` bytes at each end
    (lines that do not parse, such as a half-written last one, are skipped)."""
    head, tail = _read_windows(path, window)
    cwd = next((r["cwd"] for r in head if isinstance(r.get("cwd"), str) and r["cwd"]), "")
    for kind, key in (("custom-title", "customTitle"), ("ai-title", "aiTitle")):
        for record in [*reversed(tail), *reversed(head)]:
            if record.get("type") == kind:
                title = _tidy(record.get(key))
                if title:
                    return title, cwd
    for record in head:
        if record.get("type") == "user" and not record.get("isMeta"):
            title = _tidy(_first_text(record.get("message")))
            if title:
                return title, cwd
    return "", cwd


def _read_windows(path: Path, window: int) -> tuple[list[dict], list[dict]]:
    with path.open("rb") as f:
        size = os.fstat(f.fileno()).st_size
        head = f.read(window)
        if size <= window:
            return _records(head), []
        f.seek(size - window)  # may overlap the head: no line is lost between them
        tail = f.read(window)
    # The tail may start mid-line; the head may end mid-line. Neither parses.
    return _records(head), _records(tail)


def _records(chunk: bytes) -> list[dict]:
    records: list[dict] = []
    for line in chunk.split(b"\n"):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _first_text(message: object) -> str:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                return text if isinstance(text, str) else ""
    return ""


def _tidy(value: object) -> str:
    if not isinstance(value, str):
        return ""
    for line in value.splitlines():
        collapsed = " ".join(line.split())
        if collapsed:
            return collapsed[:TITLE_LIMIT]
    return ""


def _files_under(path: Path) -> Iterator[os.stat_result]:
    """lstat of every non-directory entry at or below `path`, never following
    a symlink."""
    try:
        info = path.lstat()
    except OSError:
        return
    if not _is_dir(info):
        yield info
        return
    stack = [path]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if _is_dir(st):
                        stack.append(Path(entry.path))
                    else:
                        yield st
        except OSError:
            continue


def _is_dir(st: os.stat_result) -> bool:
    return (st.st_mode & 0o170000) == 0o040000


def scan() -> list[ClaudeSession]:
    return [_build(sid, entry) for sid, entry in sorted(_transcripts_by_id().items())]


def _scan_one(sid: str) -> ClaudeSession | None:
    """The session `sid` as it is on disk now; None when no transcript is left."""
    entry = _transcripts_by_id().get(sid)
    return _build(sid, entry) if entry else None


def _transcripts_by_id() -> dict[str, dict[str, list[Path]]]:
    found: dict[str, dict[str, list[Path]]] = {}
    projects = roots.claude_home() / "projects"
    for folder in sorted(_listdir(projects)):
        p = projects / folder
        if p.is_symlink() or not p.is_dir():
            continue
        for name in sorted(_listdir(p)):
            m = _TRANSCRIPT.match(name)
            if m:
                kind = "quarantined" if m.group(2) else "transcripts"
                entry = found.setdefault(m.group(1), {"transcripts": [], "quarantined": []})
                entry[kind].append(p / name)
    return found


def _build(sid: str, entry: dict[str, list[Path]]) -> ClaudeSession:
    transcripts, quarantined = entry["transcripts"], entry["quarantined"]
    project_dirs = sorted({t.parent for t in transcripts + quarantined})
    newest = max(transcripts or quarantined, key=_mtime)
    try:
        title, cwd = read_title_and_cwd(newest)
    except OSError:
        title, cwd = "", ""

    paths, unsafe = _owned(sid, project_dirs, [cwd] if cwd else [])
    existing = [p for p in paths if os.path.lexists(p)]
    sizes = [st for p in existing for st in _files_under(p)]
    return ClaudeSession(
        id=sid,
        transcripts=transcripts,
        quarantined=quarantined,
        project_dirs=project_dirs,
        cwd=cwd,
        title=title,
        last_activity=max((st.st_mtime for st in sizes), default=0.0),
        bytes=sum(st.st_size for st in sizes),
        subagents=_count_subagents(sid, project_dirs),
        paths=existing,
        unsafe=unsafe,
    )


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _count_subagents(sid: str, project_dirs: list[Path]) -> int:
    count = 0
    for p in project_dirs:
        try:
            directory = safety.child(safety.child(p, sid), "subagents")
        except safety.UnsafePath:
            continue
        count += sum(1 for name in _listdir(directory) if name.endswith(".jsonl"))
    return count


@dataclass(frozen=True)
class ClaudeProc:
    pid: int
    started: float  # epoch seconds
    cwd: str | None


@dataclass
class Liveness:
    agents: list[dict] | None = None  # None when `claude agents` gave no answer
    error: str | None = None  # agents_missing | agents_failed | agents_incomplete | process_scan_failed
    procs: list[ClaudeProc] = field(default_factory=list)
    agent_pids: set[int] = field(default_factory=set)
    known_cwds: set[str] = field(default_factory=set)  # agents' cwds plus process cwds


def claude_binary() -> str | None:
    homebrew = "/opt/homebrew/bin/claude"
    if os.access(homebrew, os.X_OK):
        return homebrew
    return shutil.which("claude")


def gather_liveness(now: float) -> Liveness:
    live = Liveness()
    live.agents, live.error = _read_agents()
    # An entry only explains its pid when it names a valid session.
    live.agent_pids = {
        a["pid"]
        for a in live.agents or []
        if isinstance(a.get("pid"), int) and safety.is_valid_id(a.get("sessionId"))
    }
    try:
        table = procs.ps_table()
        scan = procs.lsof_scan(fds="cwd,txt")
    except procs.ToolFailure:
        live.error = live.error or "process_scan_failed"
        table, scan = {}, {}
    for pid, proc in table.items():
        if procs.exe_name(pid, table, scan) == "claude":
            opened = scan.get(pid)
            live.procs.append(
                ClaudeProc(pid, now - proc.elapsed, opened.cwd if opened else None)
            )
    if live.agents is not None and live.error is None:
        pid_files = _live_pids(table)
        anonymous_background = any(
            a.get("kind") == "background" and not safety.is_valid_id(a.get("sessionId"))
            for a in live.agents
        )
        if pid_files is None or not pid_files <= live.agent_pids or anonymous_background:
            live.error = "agents_incomplete"
    live.known_cwds = {a["cwd"] for a in live.agents or [] if isinstance(a.get("cwd"), str)}
    live.known_cwds |= {p.cwd for p in live.procs if p.cwd}
    return live


def _read_agents() -> tuple[list[dict] | None, str | None]:
    binary = claude_binary()
    if binary is None:
        return None, "agents_missing"
    # CLAUDE_CONFIG_DIR would point `claude` at another home than the one dotsync scans.
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    try:
        result = procs.run(
            [binary, "agents", "--json", "--all"],
            timeout=AGENTS_TIMEOUT,
            cwd=Path.home(),
            env=env,
        )
    except procs.ToolFailure as exc:
        return None, "agents_missing" if exc.code == "missing" else "agents_failed"
    if result.returncode != 0:
        return None, "agents_failed"
    try:
        agents = json.loads(result.stdout)
    except ValueError:
        return None, "agents_failed"
    if not isinstance(agents, list) or not all(isinstance(a, dict) for a in agents):
        return None, "agents_failed"
    return agents, None


def _live_pids(table: dict[int, procs.Proc]) -> set[int] | None:
    """Pids named by ~/.claude/sessions/<pid>.json that are still running
    (names only: the files are never opened). None when the directory exists
    but cannot be listed: only a missing directory means no live sessions."""
    try:
        names = os.listdir(roots.claude_home() / "sessions")
    except FileNotFoundError:
        return set()
    except OSError:
        return None
    named = (_PID_FILE.match(n) for n in names)
    return {int(m.group(1)) for m in named if m and int(m.group(1)) in table}


def state_of(s: ClaudeSession, live: Liveness, now: float) -> tuple[str, str]:
    """`(state, reason)`; anything that cannot be shown to be unused is unknown."""
    desktop = Path.home() / "Library" / "Application Support" / "Claude"
    if s.cwd == str(desktop) or s.cwd.startswith(f"{desktop}/"):
        return "desktop", "desktop"
    mine = [a for a in live.agents or [] if a.get("sessionId") == s.id]
    if any(a.get("kind") == "background" for a in mine):
        return "background", "background"
    if any(isinstance(a.get("pid"), int) for a in mine):
        return "running", "running"
    if s.unsafe:
        return "unknown", "unsafe_path"
    if live.error:
        return "unknown", live.error
    if now - s.last_activity < RECENT_SECONDS:
        return "unknown", "recent"
    if _has_unexplained_process(s, live):
        return "unknown", "unexplained_process"
    if s.quarantined:
        return "interrupted", "interrupted"
    return "idle", "idle"


def _has_unexplained_process(s: ClaudeSession, live: Liveness) -> bool:
    folders = {p.name for p in s.project_dirs}
    return any(
        p.pid not in live.agent_pids
        # a process whose cwd lsof could not map may belong to any session
        and (p.cwd is None or p.cwd == s.cwd or encode_cwd(p.cwd) in folders)
        and p.started < s.last_activity
        for p in live.procs
    )


def list_payload(extra_cwds: Iterable[str] = ()) -> dict:
    """The `sessions list claude --json` object. `extra_cwds` are the other
    tool's cwds: a root found by either tool counts for both."""
    now = time.time()
    sessions = scan()
    live = gather_liveness(now)
    items: list[dict] = []
    for s in sessions:
        state, reason = state_of(s, live, now)
        item = {
            "id": s.id,
            "title": s.title,
            "folder": os.path.basename(s.cwd),
            "cwd": s.cwd,
            "last_activity": _iso(s.last_activity),
            "bytes": s.bytes,
            "state": state,
            "subagents": s.subagents,
        }
        if state == "unknown":
            item["reason"] = reason
        items.append(item)

    locator = grouping.Locator.default()
    cwds = {i["cwd"] for i in items}
    known = locator.known_roots(cwds | live.known_cwds | set(extra_cwds))
    places = {cwd: locator.place(cwd, known) for cwd in cwds}
    payload = {
        "tool": "claude",
        "checked_at": _iso(now),
        "totals": {
            "sessions": len(items),
            "bytes": sum(i["bytes"] for i in items),
            "running": sum(i["state"] == "running" for i in items),
        },
        "groups": grouping.groups(items, places),
    }
    if live.error:
        payload["agents_error"] = live.error
    return payload


def _iso(epoch: float) -> str:
    """UTC with whole seconds, as Swift's .iso8601 decoding expects."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Delete

DELETABLE = ("idle", "interrupted")
_REF = re.compile(rf"/({_UUID})/tool-results/".encode())
_REF_MARK = b"/tool-results/"
_CHUNK = 8 * 1024 * 1024
_OVERLAP = 64  # longer than a whole reference, so none is cut in two
_RENAME_EXCL = 0x00000004  # <sys/stdio.h>


def tool_result_refs(files: Iterable[Path]) -> dict[str, set[str]]:
    """Referenced id → the ids whose transcripts name `/<id>/tool-results/`.
    A byte search: nothing else is taken from a transcript. A file that cannot
    be read raises OSError, since what it references is then unknown."""
    refs: dict[str, set[str]] = {}
    for path in files:
        owner = _transcript_owner(path)
        for ref in _refs_in(path):
            refs.setdefault(ref, set()).add(owner)
    return refs


def _refs_in(path: Path) -> set[str]:
    found: set[str] = set()
    # O_NOFOLLOW: a symlink in a transcript's place is not read through.
    with open(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as f:
        carry = b""
        while chunk := f.read(_CHUNK):
            data = carry + chunk
            if _REF_MARK in data:  # a cheap test first: most chunks hold none
                found.update(m.group(1).decode() for m in _REF.finditer(data))
            carry = data[-_OVERLAP:]
    return found


def _transcript_owner(path: Path) -> str:
    """The id a transcript belongs to, or "" when its path does not say: its
    references then count against every id."""
    if path.parent.name == "subagents" and safety.is_valid_id(path.parent.parent.name):
        return path.parent.parent.name
    m = _TRANSCRIPT.match(path.name)
    return m.group(1) if m else ""


def _transcript_files() -> list[Path]:
    """Every top-level transcript (active or quarantined) and every subagent
    transcript. Unlike scan(), this fails instead of skipping: a folder that
    cannot be listed, or a symlink where a folder should be, raises OSError."""
    projects = roots.claude_home() / "projects"
    files: list[Path] = []
    for folder in _strict_listdir(projects):
        p = projects / folder
        if not _is_real_dir(p):
            continue
        for name in _strict_listdir(p):
            if _TRANSCRIPT.match(name):
                files.append(p / name)
            elif safety.is_valid_id(name) and _is_real_dir(p / name):
                sub = p / name / "subagents"
                if _is_real_dir(sub):
                    files.extend(sub / n for n in _strict_listdir(sub) if n.endswith(".jsonl"))
    return files


def _strict_listdir(directory: Path) -> list[str]:
    """Like _listdir, but only a missing folder gives []: any other error
    raises."""
    try:
        return sorted(os.listdir(directory))
    except FileNotFoundError:
        return []


def _is_real_dir(path: Path) -> bool:
    """Whether `path` is a directory. A symlink raises OSError: it is not
    followed, so what is behind it is unknown."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(mode):
        raise OSError(errno.ELOOP, "symlink not followed", str(path))
    return stat.S_ISDIR(mode)


@dataclass
class _Batch:
    """What one delete gathers once, before the first id."""

    sessions: dict[str, ClaudeSession]
    live: Liveness
    now: float
    refs: dict[str, set[str]] | None  # None: a transcript could not be read
    cwds: set[str]  # of listed sessions, agents and claude processes


def _gather() -> _Batch:
    now = time.time()
    sessions = {s.id: s for s in scan()}
    live = gather_liveness(now)
    refs: dict[str, set[str]] | None
    try:
        refs = tool_result_refs(_transcript_files())
    except OSError:
        refs = None
    cwds = {s.cwd for s in sessions.values() if s.cwd} | live.known_cwds
    return _Batch(sessions, live, now, refs, cwds)


def delete(
    ids: list[str], *, stop: StopSignal, progress: Callable[[int, int], None]
) -> dict:
    """Delete each session that is still idle or interrupted once its
    transcripts are set aside, keeping what other sessions use. The report,
    plus the paths removed, goes to the result log."""
    for sid in ids:
        safety.validate_id(sid)  # one bad id and nothing is touched
    # Once per id: a repeat would be judged on the batch-start snapshot again,
    # after its first turn may have found it running.
    ids = list(dict.fromkeys(ids))
    started_at = now_iso()
    results: list[dict] = []
    report = {"tool": "claude", "results": results, "stopped": False}
    removed: dict[str, list[str]] = {}
    with locks.tool_lock("claude-sessions", shared=False):
        try:
            batch = _gather()
            for done, sid in enumerate(ids, start=1):
                if stop.requested:
                    report["stopped"] = True
                    results.extend(_skipped(rest, "stopped") for rest in ids[done - 1 :])
                    break
                result, gone = _delete_one(sid, batch)
                results.append(result)
                if gone:
                    removed[sid] = [str(p) for p in gone]
                progress(done, len(ids))
        finally:
            log.write("delete", "claude", started_at, {**report, "removed": removed})
    return report


def _delete_one(sid: str, batch: _Batch) -> tuple[dict, list[Path]]:
    """One id's result, and the paths it removed."""
    s = batch.sessions.get(sid)
    if s is None:
        return _skipped(sid, "not_found"), []
    # Already in use when the batch started: not even renamed.
    reason = _not_deletable(s, batch.live, batch.now)
    if reason:
        return _skipped(sid, reason), []
    moved, failed = _quarantine(s.transcripts)
    if failed:
        return _skipped(sid, "rename_failed", failed), []
    now = time.time()
    live = gather_liveness(now)
    # Scanned after the liveness check, so it sees every write until now,
    # including one from a process still writing to the renamed file.
    fresh = _scan_one(sid)
    reason = _not_deletable(fresh, live, now) if fresh else "not_found"
    if reason is None and fresh.transcripts:
        # created after the rename: a transcript not set aside is never deleted
        reason = "unknown:new_transcript"
    if reason:
        return _skipped(sid, reason, _restore(moved)), []
    return _remove(fresh, batch.refs, batch.cwds | live.known_cwds)


def _not_deletable(s: ClaudeSession, live: Liveness, now: float) -> str | None:
    """None when `s` may be deleted, else the reason it is skipped."""
    state, reason = state_of(s, live, now)
    if state in DELETABLE:
        return None
    return f"unknown:{reason}" if state == "unknown" else state


def _skipped(sid: str, reason: str, failed: list[dict] | None = None) -> dict:
    result: dict = {"id": sid, "outcome": "skipped", "reason": reason}
    if failed:
        result["failed"] = failed
    result["freed_bytes"] = 0
    return result


def _quarantine(transcripts: list[Path]) -> tuple[list[tuple[Path, Path]], list[dict]]:
    """Rename every transcript to its quarantine name, so `claude --resume`
    finds nothing to open. On a failure the ones already renamed go back and
    the failures are returned instead."""
    moved: list[tuple[Path, Path]] = []
    for path in transcripts:
        aside = path.with_name(path.name + QUARANTINE_SUFFIX)
        try:
            _rename_no_replace(path, aside)
        except OSError as e:
            return [], [_failure(path, e), *_restore(moved)]
        moved.append((path, aside))
    return moved, []


def _restore(moved: list[tuple[Path, Path]]) -> list[dict]:
    """Rename quarantined transcripts back. A name taken in the meantime is
    reported, and both files are left as they are."""
    failed: list[dict] = []
    for path, aside in moved:
        try:
            _rename_no_replace(aside, path)
        except OSError as e:
            failed.append(_failure(aside, e))
    return failed


def _rename_no_replace(src: Path, dst: Path) -> None:
    """Rename in one step, or raise FileExistsError when `dst` exists.
    os.rename would replace it: an older quarantined copy, or a transcript
    Claude created again after the first rename."""
    rename = ctypes.CDLL(None, use_errno=True).renamex_np
    rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    if rename(os.fsencode(src), os.fsencode(dst), _RENAME_EXCL) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(src), None, str(dst))


def _failure(path: Path, error: object) -> dict:
    return {"path": str(path), "error": str(error).replace("\n", " ")}


def _remove(
    s: ClaudeSession, refs: dict[str, set[str]] | None, cwds: set[str]
) -> tuple[dict, list[Path]]:
    """Remove what `s` owns except what other sessions use. Its quarantined
    transcripts go last, and only when everything else went: otherwise it
    stays listed as interrupted, and deleting it again retries what is left."""
    kept_paths, kept = _kept(s, refs, cwds)
    others = [p for p in s.paths if p not in kept_paths and p not in s.quarantined]
    freed, removed, failed = _remove_all(others)
    for folder in s.project_dirs:
        with contextlib.suppress(OSError, safety.UnsafePath):
            os.rmdir(safety.child(folder, s.id))  # only once it is empty
    if not failed:
        more, removed_last, failed = _remove_all(s.quarantined)
        freed += more
        removed += removed_last
    result: dict = {"id": s.id, "outcome": "partial" if failed else "deleted"}
    if kept:
        result["kept"] = kept
    if failed:
        result["failed"] = failed
    result["freed_bytes"] = freed
    return result, removed


def _remove_all(paths: Iterable[Path]) -> tuple[int, list[Path], list[dict]]:
    """Bytes freed, the paths removed, and the failures."""
    freed, removed, failed = 0, [], []
    for path in paths:
        try:
            safety.child(path.parent, path.name)  # no symlink crept into its parents
        except safety.UnsafePath as e:
            failed.append(_failure(path, e))
            continue
        before = _bytes_under(path)
        error = safety.remove(path)
        if error:
            freed += before - _bytes_under(path)
            failed.append(_failure(path, error))
        else:
            freed += before
            removed.append(path)
    return freed, removed, failed


def _bytes_under(path: Path) -> int:
    return sum(st.st_size for st in _files_under(path))


def _kept(
    s: ClaudeSession, refs: dict[str, set[str]] | None, cwds: set[str]
) -> tuple[set[Path], list[str]]:
    """Owned paths other sessions may still use, and the parts they are:
    `tool-results` another transcript references (every one, when the
    references are unknown), and a temp dir a known cwd is inside. A
    reference from another session in the same batch still counts."""
    paths: set[Path] = set()
    parts: list[str] = []
    if refs is None or refs.get(s.id, set()) - {s.id}:
        results = {folder / s.id / "tool-results" for folder in s.project_dirs} & set(s.paths)
        if results:
            paths |= results
            parts.append("tool_results")
    temp_root = roots.claude_temp_root()
    temps = {
        p
        for p in s.paths
        if temp_root is not None and p.parent.parent == temp_root and _holds_any(p, cwds)
    }
    if temps:
        paths |= temps
        parts.append("temp_dir")
    return paths, parts


def _holds_any(directory: Path, cwds: Iterable[str]) -> bool:
    """Whether a cwd is `directory` or below it, as written or with symlinks
    such as /tmp → /private/tmp resolved."""
    tops = {str(directory), os.path.realpath(directory)}
    for cwd in cwds:
        spellings = {cwd}
        if cwd.startswith("/"):
            with contextlib.suppress(ValueError):  # a NUL byte
                spellings.add(os.path.realpath(cwd))
        if any(c == t or c.startswith(t + "/") for c in spellings for t in tops):
            return True
    return False
