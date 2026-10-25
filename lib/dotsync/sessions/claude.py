"""Claude sessions on disk: which files belong to each session, how big they
are, and what the session is called."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from dotsync.sessions import roots, safety

QUARANTINE_SUFFIX = ".dotsync-deleting"
TITLE_LIMIT = 120
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_TRANSCRIPT = re.compile(rf"^({_UUID})\.jsonl({re.escape(QUARANTINE_SUFFIX)})?$")
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
