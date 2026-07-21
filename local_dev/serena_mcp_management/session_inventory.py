"""Fast, non-destructive session inventory for the local agent launcher."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .agent_paths import canonical_codex_homes


RETENTION_DAYS = 5
RETENTION_SECONDS = RETENTION_DAYS * 86400


class ActiveSessionScanError(RuntimeError):
    """Raised when open Codex rollout files cannot be identified safely."""


@dataclass(frozen=True)
class CountStats:
    total: int
    to_delete: int = 0
    to_keep: int = 0


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int


@dataclass(frozen=True)
class FileFingerprint:
    identity: FileIdentity
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class CodexSessionFile:
    session_id: str
    parent_id: str | None
    path: Path
    codex_home: Path
    fingerprint: FileFingerprint


@dataclass(frozen=True)
class OwnerDeletePlan:
    codex_home: Path
    local_delete_ids: tuple[str, ...]
    is_orca: bool


@dataclass(frozen=True)
class CodexCleanupTarget:
    root_id: str
    files: tuple[CodexSessionFile, ...]
    owners: tuple[OwnerDeletePlan, ...]


@dataclass(frozen=True)
class AgentInventory:
    client: str
    sessions: CountStats
    criteria: str
    records: CountStats | None = None
    codex_targets: tuple[CodexCleanupTarget, ...] = ()
    scanned_paths: tuple[Path, ...] = ()
    session_dirs: tuple[Path, ...] = ()
    warnings: tuple[str, ...] = ()


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def snapshot_open_rollouts(
    session_dirs: tuple[Path, ...],
    *,
    runner: RunCommand = subprocess.run,
) -> frozenset[FileIdentity]:
    """Return identities of open rollout files below the supplied directories."""
    existing_dirs = tuple(path for path in session_dirs if path.is_dir())
    if not existing_dirs:
        return frozenset()

    lsof = shutil.which("lsof")
    if lsof is None:
        fallback = Path("/usr/sbin/lsof")
        if fallback.is_file() and os.access(fallback, os.X_OK):
            lsof = str(fallback)
    if lsof is None:
        raise ActiveSessionScanError("lsof is unavailable")

    command = [lsof, "-n", "-F", "n"]
    for session_dir in existing_dirs:
        command.extend(("+D", str(session_dir)))
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ActiveSessionScanError(str(exc)) from exc

    stderr = result.stderr.strip()
    if result.returncode not in {0, 1} or (result.returncode == 1 and stderr):
        detail = stderr or f"lsof exited with {result.returncode}"
        raise ActiveSessionScanError(detail)

    identities: set[FileIdentity] = set()
    for line in result.stdout.splitlines():
        if not line.startswith("n"):
            continue
        path = Path(line[1:])
        if path.suffix != ".jsonl":
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        identities.add(FileIdentity(device=stat.st_dev, inode=stat.st_ino))
    return frozenset(identities)


def _fingerprint(path: Path) -> FileFingerprint:
    stat = path.stat()
    return FileFingerprint(
        identity=FileIdentity(device=stat.st_dev, inode=stat.st_ino),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def _normalized_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _find_parent_thread_id(value: object) -> str | None:
    if isinstance(value, dict):
        parent = _normalized_uuid(value.get("parent_thread_id"))
        if parent is not None:
            return parent
        for child in value.values():
            parent = _find_parent_thread_id(child)
            if parent is not None:
                return parent
    elif isinstance(value, list):
        for child in value:
            parent = _find_parent_thread_id(child)
            if parent is not None:
                return parent
    return None


def _read_codex_session_files(
    homes: tuple[Path, ...],
) -> tuple[list[CodexSessionFile], tuple[Path, ...], list[str], int]:
    files: list[CodexSessionFile] = []
    scanned_paths: list[Path] = []
    warnings: list[str] = []
    malformed_count = 0

    for codex_home in homes:
        sessions_dir = codex_home / "sessions"
        if not sessions_dir.is_dir():
            continue
        for path in sorted(sessions_dir.rglob("*.jsonl")):
            if not path.is_file():
                continue
            scanned_paths.append(path)
            try:
                with path.open(encoding="utf-8") as session_file:
                    first_line = session_file.readline()
                row = json.loads(first_line)
                payload = row.get("payload")
                if row.get("type") != "session_meta" or not isinstance(payload, dict):
                    raise ValueError("first row is not session_meta")
                session_id = _normalized_uuid(payload.get("id"))
                if session_id is None:
                    raise ValueError("session UUID is missing or invalid")
                files.append(
                    CodexSessionFile(
                        session_id=session_id,
                        parent_id=_find_parent_thread_id(payload),
                        path=path,
                        codex_home=codex_home,
                        fingerprint=_fingerprint(path),
                    )
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                malformed_count += 1
                warnings.append(f"malformed session metadata: {path.name}: {exc}")

    return files, tuple(scanned_paths), warnings, malformed_count


def _group_codex_files(
    files: list[CodexSessionFile],
    warnings: list[str],
) -> tuple[dict[str, list[str]], dict[str, str | None], set[str]]:
    files_by_id: dict[str, list[CodexSessionFile]] = {}
    ids_by_identity: dict[FileIdentity, set[str]] = {}
    for session_file in files:
        files_by_id.setdefault(session_file.session_id, []).append(session_file)
        ids_by_identity.setdefault(session_file.fingerprint.identity, set()).add(
            session_file.session_id
        )

    invalid: set[str] = set()
    parents: dict[str, str | None] = {}
    for session_id, copies in files_by_id.items():
        copy_parents = {copy.parent_id for copy in copies}
        if len(copy_parents) != 1:
            invalid.add(session_id)
            warnings.append(f"conflicting parent for session {session_id}")
            continue
        parents[session_id] = next(iter(copy_parents))

    for identity_ids in ids_by_identity.values():
        if len(identity_ids) <= 1:
            continue
        invalid.update(identity_ids)
        warnings.append(
            "conflicting session UUIDs share one file identity: "
            + ", ".join(sorted(identity_ids))
        )

    roots: dict[str, str] = {}

    def resolve_root(session_id: str) -> str | None:
        trail: list[str] = []
        positions: dict[str, int] = {}
        current = session_id
        while True:
            if current in roots:
                root = roots[current]
                for item in trail:
                    roots[item] = root
                return root
            if current in invalid:
                invalid.update(trail)
                return None
            if current in positions:
                cycle = trail[positions[current] :]
                invalid.update(trail)
                warnings.append("parent cycle: " + " -> ".join(cycle + [current]))
                return None
            positions[current] = len(trail)
            trail.append(current)
            parent = parents.get(current)
            if parent is None:
                for item in trail:
                    roots[item] = current
                return current
            if parent not in files_by_id:
                for item in trail:
                    roots[item] = parent
                return parent
            current = parent

    for session_id in sorted(files_by_id):
        resolve_root(session_id)

    groups: dict[str, list[str]] = {}
    for session_id, root_id in roots.items():
        if session_id in invalid:
            continue
        groups.setdefault(root_id, []).append(session_id)
    for members in groups.values():
        members.sort()
    return groups, parents, invalid


def _owner_delete_plans(
    *,
    group_ids: list[str],
    files_by_id: dict[str, list[CodexSessionFile]],
    parents: dict[str, str | None],
    homes: tuple[Path, ...],
    default_home: Path,
    orca_home: Path,
) -> tuple[OwnerDeletePlan, ...]:
    plans: list[OwnerDeletePlan] = []
    for codex_home in homes:
        local_ids = {
            session_id
            for session_id in group_ids
            if any(
                session_file.codex_home == codex_home
                for session_file in files_by_id[session_id]
            )
        }
        if not local_ids:
            continue

        def local_depth(session_id: str) -> int:
            depth = 0
            current = session_id
            while True:
                parent = parents.get(current)
                if parent is None or parent not in local_ids:
                    return depth
                depth += 1
                current = parent

        local_delete_ids = tuple(
            sorted(
                local_ids,
                key=lambda session_id: (-local_depth(session_id), session_id),
            )
        )
        plans.append(
            OwnerDeletePlan(
                codex_home=codex_home,
                local_delete_ids=local_delete_ids,
                is_orca=codex_home == orca_home and codex_home != default_home,
            )
        )
    return tuple(plans)


def _scan_codex_inventory(
    *,
    home: Path,
    codex_home: Path,
    orca_codex_home: Path | None,
    now: float,
    open_file_identities: frozenset[FileIdentity] | None,
) -> AgentInventory:
    homes, default_home, orca_home = canonical_codex_homes(
        home=home,
        codex_home=codex_home,
        orca_codex_home=orca_codex_home,
    )
    session_dirs = tuple(codex_home / "sessions" for codex_home in homes)
    files, scanned_paths, warnings, malformed_count = _read_codex_session_files(
        homes
    )

    if open_file_identities is None:
        try:
            open_ids = snapshot_open_rollouts(session_dirs)
        except ActiveSessionScanError as exc:
            warnings.append(f"active session scan unavailable: {exc}")
            open_ids = frozenset(
                session_file.fingerprint.identity for session_file in files
            )
    else:
        open_ids = open_file_identities

    files_by_id: dict[str, list[CodexSessionFile]] = {}
    for session_file in files:
        files_by_id.setdefault(session_file.session_id, []).append(session_file)
    groups, parents, invalid = _group_codex_files(files, warnings)
    cutoff_ns = int((now - RETENTION_SECONDS) * 1_000_000_000)
    targets: list[CodexCleanupTarget] = []

    for root_id, group_ids in sorted(groups.items()):
        group_files = tuple(
            sorted(
                (
                    session_file
                    for session_id in group_ids
                    for session_file in files_by_id[session_id]
                ),
                key=lambda session_file: str(session_file.path),
            )
        )
        if max(item.fingerprint.mtime_ns for item in group_files) >= cutoff_ns:
            continue
        if any(item.fingerprint.identity in open_ids for item in group_files):
            continue
        targets.append(
            CodexCleanupTarget(
                root_id=root_id,
                files=group_files,
                owners=_owner_delete_plans(
                    group_ids=group_ids,
                    files_by_id=files_by_id,
                    parents=parents,
                    homes=homes,
                    default_home=default_home,
                    orca_home=orca_home,
                ),
            )
        )

    total = len(groups) + len(invalid) + malformed_count
    delete_count = len(targets)
    record_total = len(scanned_paths)
    records_to_delete = sum(len(target.files) for target in targets)
    return AgentInventory(
        client="codex",
        sessions=CountStats(
            total=total,
            to_delete=delete_count,
            to_keep=total - delete_count,
        ),
        criteria="sessions: all known homes + inactive longer than 5d",
        records=CountStats(
            total=record_total,
            to_delete=records_to_delete,
            to_keep=record_total - records_to_delete,
        ),
        codex_targets=tuple(targets),
        scanned_paths=scanned_paths,
        session_dirs=session_dirs,
        warnings=tuple(warnings),
    )


def _scan_claude_inventory(
    *,
    home: Path,
    claude_config_dir: Path | None,
    now: float,
) -> AgentInventory:
    config_dir = claude_config_dir or home / ".claude"
    if not config_dir.is_absolute():
        raise ValueError("claude_config_dir must be absolute")

    paths = tuple(
        path
        for path in sorted((config_dir / "projects").glob("*/*.jsonl"))
        if path.is_file()
    )
    cutoff_ns = int((now - RETENTION_SECONDS) * 1_000_000_000)
    delete_count = sum(path.stat().st_mtime_ns < cutoff_ns for path in paths)
    total = len(paths)
    stats = CountStats(
        total=total,
        to_delete=delete_count,
        to_keep=total - delete_count,
    )
    return AgentInventory(
        client="claude",
        sessions=stats,
        criteria="sessions: all projects + native retention 5d",
        records=stats,
        scanned_paths=paths,
        session_dirs=(config_dir / "projects",),
    )


def scan_inventory(
    *,
    client: str,
    home: Path,
    codex_home: Path,
    claude_config_dir: Path | None = None,
    orca_codex_home: Path | None = None,
    now: float | None = None,
    open_file_identities: frozenset[FileIdentity] | None = None,
) -> AgentInventory:
    """Build one immutable inventory snapshot for preflight and cleanup."""
    scan_time = time.time() if now is None else now
    if client == "claude":
        return _scan_claude_inventory(
            home=home,
            claude_config_dir=claude_config_dir,
            now=scan_time,
        )
    if client == "codex":
        return _scan_codex_inventory(
            home=home,
            codex_home=codex_home,
            orca_codex_home=orca_codex_home,
            now=scan_time,
            open_file_identities=open_file_identities,
        )
    raise ValueError(f"unsupported client: {client}")
