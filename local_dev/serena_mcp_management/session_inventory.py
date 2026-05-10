"""Session and memory inventory for the local agent launcher."""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentStatePaths:
    client: str
    sessions_dir: Path
    memory_dir: Path
    criteria: str


@dataclass(frozen=True)
class CountStats:
    total: int
    to_delete: int = 0
    to_keep: int = 0
    to_reset: int = 0


@dataclass(frozen=True)
class AgentInventory:
    client: str
    sessions: CountStats
    memory: CountStats
    criteria: str
    sessions_dir: Path
    memory_dir: Path
    session_delete_paths: list[Path]
    memory_reset: bool


def encode_claude_project_path(path: str) -> str:
    return path.replace("/", "-")


def agent_paths(
    *,
    client: str,
    cwd: str,
    project_root: str,
    home: Path,
    codex_home: Path,
) -> AgentStatePaths:
    if client == "claude":
        session_key = encode_claude_project_path(cwd)
        memory_key = encode_claude_project_path(project_root or cwd)
        return AgentStatePaths(
            client=client,
            sessions_dir=home / ".claude" / "projects" / session_key,
            memory_dir=home / ".claude" / "projects" / memory_key / "memory",
            criteria="sessions: this project + older than 3d . memory: reset all",
        )
    if client != "codex":
        raise ValueError(f"unsupported client: {client}")
    if not codex_home.is_absolute():
        raise ValueError("codex_home must be absolute")
    return AgentStatePaths(
        client="codex",
        sessions_dir=codex_home / "sessions",
        memory_dir=codex_home / "memories",
        criteria="sessions: same cwd + older than 3d . memory: reset all",
    )


def _is_old(path: Path, now: float, days: int = 3) -> bool:
    try:
        return path.stat().st_mtime < now - days * 86400
    except OSError:
        return False


def _count_files(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def _codex_session_matches_cwd(path: Path, cwd: str) -> bool:
    try:
        with path.open(encoding="utf-8") as session_file:
            for line in session_file:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") != "session_meta":
                    continue
                payload = row.get("payload")
                if isinstance(payload, dict) and payload.get("cwd") == cwd:
                    return True
    except (OSError, UnicodeDecodeError):
        return False
    return False


def _scan_codex_sessions(
    sessions_dir: Path,
    cwd: str,
    now: float,
) -> tuple[CountStats, list[Path]]:
    total = 0
    delete_paths: list[Path] = []

    for path in sorted(sessions_dir.rglob("*.jsonl")):
        if not path.is_file() or not _codex_session_matches_cwd(path, cwd):
            continue
        total += 1
        if _is_old(path, now):
            delete_paths.append(path)

    return (
        CountStats(
            total=total,
            to_delete=len(delete_paths),
            to_keep=total - len(delete_paths),
        ),
        delete_paths,
    )


def _scan_claude_sessions(
    sessions_dir: Path,
    now: float,
) -> tuple[CountStats, list[Path]]:
    paths = [path for path in sorted(sessions_dir.glob("*.jsonl")) if path.is_file()]
    delete_paths = [path for path in paths if _is_old(path, now)]
    return (
        CountStats(
            total=len(paths),
            to_delete=len(delete_paths),
            to_keep=len(paths) - len(delete_paths),
        ),
        delete_paths,
    )


def scan_inventory(
    *,
    client: str,
    cwd: str,
    project_root: str,
    home: Path,
    codex_home: Path,
    now: float | None = None,
) -> AgentInventory:
    scan_time = time.time() if now is None else now
    paths = agent_paths(
        client=client,
        cwd=cwd,
        project_root=project_root,
        home=home,
        codex_home=codex_home,
    )

    if paths.client == "claude":
        sessions, session_delete_paths = _scan_claude_sessions(paths.sessions_dir, scan_time)
    else:
        sessions, session_delete_paths = _scan_codex_sessions(paths.sessions_dir, cwd, scan_time)

    memory_total = _count_files(paths.memory_dir)
    memory = CountStats(total=memory_total, to_reset=memory_total, to_keep=0)

    return AgentInventory(
        client=paths.client,
        sessions=sessions,
        memory=memory,
        criteria=paths.criteria,
        sessions_dir=paths.sessions_dir,
        memory_dir=paths.memory_dir,
        session_delete_paths=session_delete_paths,
        memory_reset=paths.memory_dir.is_dir(),
    )


def cleanup_inventory(
    *,
    client: str,
    cwd: str,
    project_root: str,
    home: Path,
    codex_home: Path,
    now: float | None = None,
) -> AgentInventory:
    inventory = scan_inventory(
        client=client,
        cwd=cwd,
        project_root=project_root,
        home=home,
        codex_home=codex_home,
        now=now,
    )

    for path in inventory.session_delete_paths:
        path.unlink(missing_ok=True)
        if inventory.client == "claude":
            uuid_dir = path.with_suffix("")
            if uuid_dir.is_dir():
                shutil.rmtree(uuid_dir)

    if inventory.memory_reset:
        shutil.rmtree(inventory.memory_dir)

    return inventory
