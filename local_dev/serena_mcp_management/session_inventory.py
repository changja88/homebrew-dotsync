"""Fast, non-destructive session inventory for the local agent launcher."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .agent_paths import canonical_codex_homes
from .memory_management import ClientProcess, running_client_processes


RETENTION_DAYS = 5
RETENTION_SECONDS = RETENTION_DAYS * 86400
SessionPolicy = Literal["retention_5d", "all_inactive"]


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
class ClaudeSessionPath:
    path: Path
    fingerprint: FileFingerprint
    is_directory: bool


@dataclass(frozen=True)
class ClaudeCleanupTarget:
    session_id: str
    roots: tuple[Path, ...]
    manifest: tuple[ClaudeSessionPath, ...]


@dataclass(frozen=True)
class AgentInventory:
    client: str
    sessions: CountStats
    criteria: str
    policy: SessionPolicy = "retention_5d"
    records: CountStats | None = None
    codex_targets: tuple[CodexCleanupTarget, ...] = ()
    claude_targets: tuple[ClaudeCleanupTarget, ...] = ()
    active_sessions: int = 0
    scanned_paths: tuple[Path, ...] = ()
    session_dirs: tuple[Path, ...] = ()
    claude_config_dir: Path | None = None
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


def _fingerprint_from_stat(stat_result: os.stat_result) -> FileFingerprint:
    return FileFingerprint(
        identity=FileIdentity(
            device=stat_result.st_dev,
            inode=stat_result.st_ino,
        ),
        size=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
    )


def _fingerprint(path: Path) -> FileFingerprint:
    return _fingerprint_from_stat(path.stat())


def snapshot_claude_manifest(
    roots: tuple[Path, ...],
) -> tuple[ClaudeSessionPath, ...]:
    """Snapshot exact Claude bundle roots without following symlinks."""
    pending = list(roots)
    seen: set[Path] = set()
    manifest: list[ClaudeSessionPath] = []

    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        try:
            stat_result = path.lstat()
        except OSError as exc:
            raise ActiveSessionScanError(
                f"cannot inspect Claude session path {path}: {exc}"
            ) from exc

        mode = stat_result.st_mode
        if stat.S_ISLNK(mode):
            raise ActiveSessionScanError(
                f"unsafe Claude session symlink: {path}"
            )
        is_directory = stat.S_ISDIR(mode)
        if not is_directory and not stat.S_ISREG(mode):
            raise ActiveSessionScanError(
                f"unsupported Claude session path type: {path}"
            )

        manifest.append(
            ClaudeSessionPath(
                path=path,
                fingerprint=_fingerprint_from_stat(stat_result),
                is_directory=is_directory,
            )
        )
        if not is_directory:
            continue
        try:
            pending.extend(path.iterdir())
        except OSError as exc:
            raise ActiveSessionScanError(
                f"cannot enumerate Claude session path {path}: {exc}"
            ) from exc

    return tuple(sorted(manifest, key=lambda entry: str(entry.path)))


def snapshot_active_claude_sessions(
    config_dir: Path,
    *,
    processes: tuple[ClientProcess, ...] | None = None,
    run_command: RunCommand = subprocess.run,
) -> frozenset[str]:
    """Return live Claude session IDs from validated process markers."""
    if processes is None:
        processes = running_client_processes(
            "claude",
            run_command=run_command,
        )
    processes_by_pid = {process.pid: process for process in processes}
    active: set[str] = set()
    marker_dir = config_dir / "sessions"
    if marker_dir.is_symlink():
        raise ActiveSessionScanError(
            f"unsafe Claude marker directory: {marker_dir}"
        )
    if not marker_dir.is_dir():
        return frozenset()

    for marker in sorted(marker_dir.glob("*.json")):
        if marker.is_symlink():
            raise ActiveSessionScanError(
                f"unsafe Claude session marker: {marker}"
            )
        try:
            payload = json.loads(marker.read_text())
            session_id = str(uuid.UUID(payload["sessionId"]))
            pid = payload["pid"]
            proc_start = payload["procStart"]
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            raise ActiveSessionScanError(
                f"invalid Claude session marker {marker}: {exc}"
            ) from exc
        if isinstance(pid, bool) or not isinstance(pid, int):
            raise ActiveSessionScanError(
                f"invalid Claude marker pid: {marker}"
            )
        if not isinstance(proc_start, str) or not proc_start.strip():
            raise ActiveSessionScanError(
                f"invalid Claude marker start: {marker}"
            )
        if pid not in processes_by_pid:
            continue
        command = ["/bin/ps", "-p", str(pid), "-o", "lstart="]
        result = run_command(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if (
            result.returncode == 1
            and not result.stdout.strip()
            and not result.stderr.strip()
        ):
            continue
        if result.returncode != 0 or not result.stdout.strip():
            detail = result.stderr.strip() or f"ps exited {result.returncode}"
            raise ActiveSessionScanError(detail)
        if result.stdout.strip() == proc_start.strip():
            active.add(session_id)
    return frozenset(active)


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
    group_id_set = set(group_ids)
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

        def group_depth(session_id: str) -> int:
            depth = 0
            current = session_id
            while True:
                parent = parents.get(current)
                if parent is None or parent not in group_id_set:
                    return depth
                depth += 1
                current = parent

        local_delete_ids = tuple(
            sorted(
                local_ids,
                key=lambda session_id: (-group_depth(session_id), session_id),
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
    policy: SessionPolicy,
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
    active_sessions = 0

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
        if (
            policy == "retention_5d"
            and max(item.fingerprint.mtime_ns for item in group_files)
            >= cutoff_ns
        ):
            continue
        if any(item.fingerprint.identity in open_ids for item in group_files):
            if policy == "all_inactive":
                active_sessions += 1
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
    criteria = (
        "sessions: all known homes + all inactive; running preserved"
        if policy == "all_inactive"
        else "sessions: all known homes + inactive longer than 5d"
    )
    return AgentInventory(
        client="codex",
        sessions=CountStats(
            total=total,
            to_delete=delete_count,
            to_keep=total - delete_count,
        ),
        criteria=criteria,
        policy=policy,
        records=CountStats(
            total=record_total,
            to_delete=records_to_delete,
            to_keep=record_total - records_to_delete,
        ),
        codex_targets=tuple(targets),
        active_sessions=active_sessions,
        scanned_paths=scanned_paths,
        session_dirs=session_dirs,
        warnings=tuple(warnings),
    )


def _canonical_uuid(value: str) -> str | None:
    normalized = _normalized_uuid(value)
    if normalized != value:
        return None
    return normalized


def _safe_claude_directory_entries(
    directory: Path,
    warnings: list[str],
) -> tuple[Path, ...]:
    try:
        stat_result = directory.lstat()
    except FileNotFoundError:
        return ()
    except OSError as exc:
        warnings.append(f"cannot inspect Claude artifact directory {directory}: {exc}")
        return ()
    if stat.S_ISLNK(stat_result.st_mode):
        warnings.append(f"unsafe Claude artifact directory symlink: {directory}")
        return ()
    if not stat.S_ISDIR(stat_result.st_mode):
        warnings.append(f"unsafe Claude artifact directory type: {directory}")
        return ()
    try:
        return tuple(sorted(directory.iterdir(), key=str))
    except OSError as exc:
        warnings.append(
            f"cannot enumerate Claude artifact directory {directory}: {exc}"
        )
        return ()


def _claude_discovery_stat(
    path: Path,
    warnings: list[str],
) -> os.stat_result | None:
    try:
        return path.lstat()
    except OSError as exc:
        warnings.append(f"cannot inspect Claude artifact path {path}: {exc}")
        return None


def _add_claude_root(
    roots_by_id: dict[str, list[Path]],
    session_id: str,
    path: Path,
) -> None:
    roots = roots_by_id.setdefault(session_id, [])
    if path not in roots:
        roots.append(path)


def _discover_claude_roots(
    config_dir: Path,
) -> tuple[dict[str, tuple[Path, ...]], list[str]]:
    warnings: list[str] = []
    roots_by_id: dict[str, list[Path]] = {}

    try:
        config_stat = config_dir.lstat()
    except FileNotFoundError:
        return {}, warnings
    except OSError as exc:
        warnings.append(f"cannot inspect Claude config directory {config_dir}: {exc}")
        return {}, warnings
    if stat.S_ISLNK(config_stat.st_mode):
        warnings.append(f"unsafe Claude config directory symlink: {config_dir}")
        return {}, warnings
    if not stat.S_ISDIR(config_stat.st_mode):
        warnings.append(f"unsafe Claude config directory type: {config_dir}")
        return {}, warnings

    projects_dir = config_dir / "projects"
    for project_dir in _safe_claude_directory_entries(projects_dir, warnings):
        project_stat = _claude_discovery_stat(project_dir, warnings)
        if project_stat is None:
            continue
        if stat.S_ISLNK(project_stat.st_mode):
            warnings.append(f"unsafe Claude project directory symlink: {project_dir}")
            continue
        if not stat.S_ISDIR(project_stat.st_mode):
            continue
        for path in _safe_claude_directory_entries(project_dir, warnings):
            path_stat = _claude_discovery_stat(path, warnings)
            if path_stat is None:
                continue
            if path.suffix == ".jsonl":
                session_id = _canonical_uuid(path.stem)
                if session_id is None:
                    continue
                if stat.S_ISREG(path_stat.st_mode) or stat.S_ISLNK(
                    path_stat.st_mode
                ):
                    _add_claude_root(roots_by_id, session_id, path)
                else:
                    warnings.append(f"unsafe Claude transcript type: {path}")
                continue
            session_id = _canonical_uuid(path.name)
            if session_id is None:
                continue
            if stat.S_ISDIR(path_stat.st_mode) or stat.S_ISLNK(
                path_stat.st_mode
            ):
                _add_claude_root(roots_by_id, session_id, path)

    for directory_name in ("file-history", "session-env", "tasks"):
        directory = config_dir / directory_name
        for path in _safe_claude_directory_entries(directory, warnings):
            session_id = _canonical_uuid(path.name)
            if session_id is None:
                continue
            path_stat = _claude_discovery_stat(path, warnings)
            if path_stat is None:
                continue
            if stat.S_ISDIR(path_stat.st_mode) or stat.S_ISLNK(
                path_stat.st_mode
            ):
                _add_claude_root(roots_by_id, session_id, path)

    debug_dir = config_dir / "debug"
    for path in _safe_claude_directory_entries(debug_dir, warnings):
        if path.suffix != ".txt":
            continue
        session_id = _canonical_uuid(path.stem)
        if session_id is None:
            continue
        path_stat = _claude_discovery_stat(path, warnings)
        if path_stat is None:
            continue
        if stat.S_ISREG(path_stat.st_mode) or stat.S_ISLNK(path_stat.st_mode):
            _add_claude_root(roots_by_id, session_id, path)
        else:
            warnings.append(f"unsafe Claude debug artifact type: {path}")

    return (
        {
            session_id: tuple(sorted(roots, key=str))
            for session_id, roots in sorted(roots_by_id.items())
        },
        warnings,
    )


def snapshot_claude_session_roots(
    config_dir: Path,
) -> dict[str, tuple[Path, ...]]:
    """Rediscover exact canonical Claude roots or fail on uncertainty."""
    roots_by_id, warnings = _discover_claude_roots(config_dir)
    if warnings:
        raise ActiveSessionScanError(
            "cannot safely rediscover Claude session roots: "
            + "; ".join(warnings)
        )
    return roots_by_id


def _scan_claude_all_inactive(
    *,
    config_dir: Path,
    open_file_identities: frozenset[FileIdentity] | None,
    active_claude_session_ids: frozenset[str] | None,
) -> AgentInventory:
    roots_by_id, warnings = _discover_claude_roots(config_dir)
    manifests: dict[str, tuple[ClaudeSessionPath, ...]] = {}
    for session_id, roots in roots_by_id.items():
        try:
            manifests[session_id] = snapshot_claude_manifest(roots)
        except ActiveSessionScanError as exc:
            warnings.append(str(exc))

    if active_claude_session_ids is None:
        active_ids = snapshot_active_claude_sessions(config_dir)
    else:
        active_ids = active_claude_session_ids

    if open_file_identities is None:
        try:
            open_ids = snapshot_open_rollouts((config_dir / "projects",))
        except ActiveSessionScanError as exc:
            warnings.append(f"active session scan unavailable: {exc}")
            open_ids = frozenset(
                entry.fingerprint.identity
                for manifest in manifests.values()
                for entry in manifest
            )
    else:
        open_ids = open_file_identities

    targets: list[ClaudeCleanupTarget] = []
    active_sessions = 0
    for session_id, roots in roots_by_id.items():
        manifest = manifests.get(session_id)
        if manifest is None:
            continue
        is_open = any(
            entry.fingerprint.identity in open_ids for entry in manifest
        )
        if session_id in active_ids or is_open:
            active_sessions += 1
            continue
        targets.append(
            ClaudeCleanupTarget(
                session_id=session_id,
                roots=roots,
                manifest=manifest,
            )
        )

    scanned_paths = tuple(
        sorted(
            {
                entry.path
                for manifest in manifests.values()
                for entry in manifest
            },
            key=str,
        )
    )
    records_to_delete = sum(len(target.manifest) for target in targets)
    total = len(roots_by_id)
    delete_count = len(targets)
    return AgentInventory(
        client="claude",
        sessions=CountStats(
            total=total,
            to_delete=delete_count,
            to_keep=total - delete_count,
        ),
        criteria="sessions: all projects + all inactive; running preserved",
        policy="all_inactive",
        records=CountStats(
            total=len(scanned_paths),
            to_delete=records_to_delete,
            to_keep=len(scanned_paths) - records_to_delete,
        ),
        claude_targets=tuple(targets),
        active_sessions=active_sessions,
        scanned_paths=scanned_paths,
        session_dirs=(config_dir / "projects",),
        claude_config_dir=config_dir,
        warnings=tuple(warnings),
    )


def _scan_claude_inventory(
    *,
    home: Path,
    claude_config_dir: Path | None,
    now: float,
    policy: SessionPolicy,
    open_file_identities: frozenset[FileIdentity] | None,
    active_claude_session_ids: frozenset[str] | None,
) -> AgentInventory:
    config_dir = claude_config_dir or home / ".claude"
    if not config_dir.is_absolute():
        raise ValueError("claude_config_dir must be absolute")
    if policy == "all_inactive":
        return _scan_claude_all_inactive(
            config_dir=config_dir,
            open_file_identities=open_file_identities,
            active_claude_session_ids=active_claude_session_ids,
        )

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
        policy=policy,
        records=stats,
        scanned_paths=paths,
        session_dirs=(config_dir / "projects",),
        claude_config_dir=config_dir,
    )


def scan_inventory(
    *,
    client: str,
    home: Path,
    codex_home: Path,
    claude_config_dir: Path | None = None,
    orca_codex_home: Path | None = None,
    now: float | None = None,
    policy: SessionPolicy = "retention_5d",
    open_file_identities: frozenset[FileIdentity] | None = None,
    active_claude_session_ids: frozenset[str] | None = None,
) -> AgentInventory:
    """Build one immutable inventory snapshot for preflight and cleanup."""
    if policy not in {"retention_5d", "all_inactive"}:
        raise ValueError(f"unsupported session policy: {policy}")
    scan_time = time.time() if now is None else now
    if client == "claude":
        return _scan_claude_inventory(
            home=home,
            claude_config_dir=claude_config_dir,
            now=scan_time,
            policy=policy,
            open_file_identities=open_file_identities,
            active_claude_session_ids=active_claude_session_ids,
        )
    if client == "codex":
        return _scan_codex_inventory(
            home=home,
            codex_home=codex_home,
            orca_codex_home=orca_codex_home,
            now=scan_time,
            policy=policy,
            open_file_identities=open_file_identities,
        )
    raise ValueError(f"unsupported client: {client}")
