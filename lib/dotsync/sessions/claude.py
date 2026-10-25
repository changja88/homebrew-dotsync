"""Claude sessions on disk: which files belong to each session, how big they
are, what the session is called, and whether it is still in use."""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from dotsync.sessions import procs, roots, safety

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
    return [_build(sid, entry) for sid, entry in sorted(found.items())]


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
    live.agent_pids = {
        a["pid"] for a in live.agents or [] if isinstance(a.get("pid"), int)
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
        if not _live_pids(table) <= live.agent_pids:
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


def _live_pids(table: dict[int, procs.Proc]) -> set[int]:
    """Pids named by ~/.claude/sessions/<pid>.json that are still running
    (names only: the files are never opened)."""
    named = (_PID_FILE.match(n) for n in _listdir(roots.claude_home() / "sessions"))
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
        and p.cwd is not None
        and (p.cwd == s.cwd or encode_cwd(p.cwd) in folders)
        and p.started < s.last_activity
        for p in live.procs
    )
