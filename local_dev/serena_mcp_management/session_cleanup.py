"""Native session cleanup operations for Codex and Claude Code."""
from __future__ import annotations

import fcntl
import os
import secrets
import stat
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NoReturn

from local_dev.serena_mcp_management.session_inventory import (
    ActiveSessionScanError,
    AgentInventory,
    ClaudeSessionPath,
    CodexCleanupTarget,
    FileFingerprint,
    FileIdentity,
    snapshot_active_claude_sessions,
    snapshot_claude_session_roots,
    snapshot_open_rollouts,
)


CLAUDE_RETENTION_JSON = '{"cleanupPeriodDays":5}'
DELETE_TIMEOUT_SECONDS = 30
_CLOSE_ON_EXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOSE_ON_EXEC
)
_FILE_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | _CLOSE_ON_EXEC
_QUARANTINE_PREFIX = ".claude-cleanup-"
_QUARANTINE_CREATE_ATTEMPTS = 32
_DESCRIPTOR_PATH_BUFFER_SIZE = 1024


RunCommand = Callable[..., subprocess.CompletedProcess[str]]
OpenFileSnapshot = Callable[[tuple[Path, ...]], frozenset[FileIdentity]]


@dataclass(frozen=True)
class CleanupResult:
    deleted: int = 0
    preserved_running: int = 0
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class _DirectoryAnchor:
    parent_fd: int
    name: str
    directory_fd: int
    identity: FileIdentity
    path: Path


@dataclass(frozen=True)
class _QuarantineEvidence:
    name: str
    identity: FileIdentity | None
    path: Path


def claude_retention_args(args: list[str]) -> list[str]:
    if any(arg == "--settings" or arg.startswith("--settings=") for arg in args):
        return list(args)
    return ["--settings", CLAUDE_RETENTION_JSON, *args]


def _current_session_paths(session_dirs: tuple[Path, ...]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for session_dir in session_dirs:
        if not session_dir.is_dir():
            continue
        paths.extend(
            path
            for path in sorted(session_dir.rglob("*.jsonl"))
            if path.is_file()
        )
    return tuple(paths)


def _current_fingerprint(path: Path) -> FileFingerprint | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return FileFingerprint(
        identity=FileIdentity(device=stat.st_dev, inode=stat.st_ino),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def _target_unchanged(target: CodexCleanupTarget) -> bool:
    return all(
        _current_fingerprint(session_file.path) == session_file.fingerprint
        for session_file in target.files
    )


def _codex_target_is_open(
    target: CodexCleanupTarget,
    open_identities: frozenset[FileIdentity],
) -> bool:
    return any(
        session_file.fingerprint.identity in open_identities
        for session_file in target.files
    )


def _codex_target_revalidation_error(
    inventory: AgentInventory,
    target: CodexCleanupTarget,
    completed_paths: frozenset[Path],
) -> str | None:
    try:
        current_paths = set(_current_session_paths(inventory.session_dirs))
    except OSError as exc:
        return f"cannot revalidate Codex session paths: {exc}"

    scanned_paths = set(inventory.scanned_paths)
    required_paths = scanned_paths - completed_paths
    if not required_paths.issubset(current_paths) or not current_paths.issubset(
        scanned_paths
    ):
        return "Codex session paths changed after inventory"
    if not _target_unchanged(target):
        return f"Codex session {target.root_id} changed after inventory"
    return None


def _command_detail(result: subprocess.CompletedProcess[str]) -> str:
    return (
        (result.stderr or "").strip()
        or (result.stdout or "").strip()
        or f"exit {result.returncode}"
    )


def _run_codex_command(
    command: list[str],
    *,
    codex_home: Path,
    runner: RunCommand,
) -> tuple[bool, str]:
    environment = {**os.environ, "CODEX_HOME": str(codex_home)}
    try:
        result = runner(
            command,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=DELETE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {DELETE_TIMEOUT_SECONDS}s"
    except OSError as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, _command_detail(result)
    return True, ""


def cleanup_codex_inventory(
    inventory: AgentInventory,
    *,
    codex_binary: str,
    runner: RunCommand = subprocess.run,
    open_file_snapshot: OpenFileSnapshot = snapshot_open_rollouts,
) -> CleanupResult:
    """Delete eligible Codex groups through the official CLI only."""
    strict = inventory.policy == "all_inactive"
    warnings = list(inventory.warnings)
    preserved_running = inventory.active_sessions
    if inventory.client != "codex":
        message = f"cannot run Codex cleanup for {inventory.client} inventory"
        if strict:
            return CleanupResult(
                preserved_running=preserved_running,
                warnings=tuple(warnings),
                error=message,
            )
        warnings.append(message)
        return CleanupResult(
            preserved_running=preserved_running,
            warnings=tuple(warnings),
        )
    if strict and warnings:
        return CleanupResult(
            preserved_running=preserved_running,
            warnings=tuple(warnings),
            error="cannot safely inventory every inactive Codex session",
        )
    if not inventory.codex_targets:
        return CleanupResult(
            preserved_running=preserved_running,
            warnings=tuple(warnings),
        )

    try:
        current_paths = _current_session_paths(inventory.session_dirs)
    except OSError as exc:
        message = f"cannot revalidate Codex session paths: {exc}"
        if strict:
            return CleanupResult(
                preserved_running=preserved_running,
                warnings=tuple(warnings),
                error=message,
            )
        warnings.append(f"{message}; cleanup skipped")
        return CleanupResult(
            preserved_running=preserved_running,
            warnings=tuple(warnings),
        )
    if current_paths != inventory.scanned_paths:
        message = "Codex session paths changed after inventory"
        if strict:
            return CleanupResult(
                preserved_running=preserved_running,
                warnings=tuple(warnings),
                error=message,
            )
        warnings.append(f"{message}; cleanup skipped")
        return CleanupResult(
            preserved_running=preserved_running,
            warnings=tuple(warnings),
        )

    for target in inventory.codex_targets:
        if not _target_unchanged(target):
            message = f"Codex session {target.root_id} changed after inventory"
            if strict:
                return CleanupResult(
                    preserved_running=preserved_running,
                    warnings=tuple(warnings),
                    error=message,
                )
            warnings.append(f"{message}; cleanup skipped")
            return CleanupResult(
                preserved_running=preserved_running,
                warnings=tuple(warnings),
            )

    try:
        open_identities = open_file_snapshot(inventory.session_dirs)
    except (ActiveSessionScanError, OSError) as exc:
        message = f"active session scan unavailable: {exc}"
        if strict:
            return CleanupResult(
                preserved_running=preserved_running,
                warnings=tuple(warnings),
                error=message,
            )
        warnings.append(f"{message}; cleanup skipped")
        return CleanupResult(
            preserved_running=preserved_running,
            warnings=tuple(warnings),
        )

    safe_targets: list[CodexCleanupTarget] = []
    for target in inventory.codex_targets:
        if _codex_target_is_open(target, open_identities):
            preserved_running += 1
            warnings.append(
                f"Codex session {target.root_id} is currently open; cleanup skipped"
            )
            continue
        safe_targets.append(target)
    if not safe_targets:
        return CleanupResult(
            preserved_running=preserved_running,
            warnings=tuple(warnings),
        )

    first_home = safe_targets[0].owners[0].codex_home
    supported, detail = _run_codex_command(
        [codex_binary, "delete", "--help"],
        codex_home=first_home,
        runner=runner,
    )
    if not supported:
        message = f"Codex CLI does not support session delete: {detail}"
        if strict:
            return CleanupResult(
                preserved_running=preserved_running,
                warnings=tuple(warnings),
                error=message,
            )
        warnings.append(message)
        return CleanupResult(
            preserved_running=preserved_running,
            warnings=tuple(warnings),
        )

    deleted = 0
    if strict:
        completed_paths: set[Path] = set()
        for target in safe_targets:
            revalidation_error = _codex_target_revalidation_error(
                inventory,
                target,
                frozenset(completed_paths),
            )
            if revalidation_error is not None:
                return CleanupResult(
                    deleted=deleted,
                    preserved_running=preserved_running,
                    warnings=tuple(warnings),
                    error=revalidation_error,
                )
            try:
                current_open_identities = open_file_snapshot(
                    inventory.session_dirs
                )
            except (ActiveSessionScanError, OSError) as exc:
                return CleanupResult(
                    deleted=deleted,
                    preserved_running=preserved_running,
                    warnings=tuple(warnings),
                    error=f"active session scan unavailable: {exc}",
                )
            if _codex_target_is_open(target, current_open_identities):
                preserved_running += 1
                warnings.append(
                    f"Codex session {target.root_id} is currently open; "
                    "cleanup skipped"
                )
                continue
            for owner in target.owners:
                for local_delete_id in owner.local_delete_ids:
                    succeeded, detail = _run_codex_command(
                        [codex_binary, "delete", "--force", local_delete_id],
                        codex_home=owner.codex_home,
                        runner=runner,
                    )
                    if not succeeded:
                        return CleanupResult(
                            deleted=deleted,
                            preserved_running=preserved_running,
                            warnings=tuple(warnings),
                            error=(
                                f"Codex session {local_delete_id} delete failed in "
                                f"{owner.codex_home}: {detail}"
                            ),
                        )
            deleted += 1
            completed_paths.update(
                session_file.path for session_file in target.files
            )
        return CleanupResult(
            deleted=deleted,
            preserved_running=preserved_running,
            warnings=tuple(warnings),
        )

    completed_paths = set()
    for target in safe_targets:
        revalidation_error = _codex_target_revalidation_error(
            inventory,
            target,
            frozenset(completed_paths),
        )
        if revalidation_error is not None:
            warnings.append(f"{revalidation_error}; cleanup skipped")
            return CleanupResult(
                deleted=deleted,
                preserved_running=preserved_running,
                warnings=tuple(warnings),
            )
        try:
            current_open_identities = open_file_snapshot(inventory.session_dirs)
        except (ActiveSessionScanError, OSError) as exc:
            warnings.append(f"active session scan unavailable: {exc}; cleanup skipped")
            return CleanupResult(
                deleted=deleted,
                preserved_running=preserved_running,
                warnings=tuple(warnings),
            )
        if _codex_target_is_open(target, current_open_identities):
            preserved_running += 1
            warnings.append(
                f"Codex session {target.root_id} is currently open; cleanup skipped"
            )
            continue
        group_succeeded = True
        source_failed = False
        for owner in target.owners:
            if owner.is_orca and source_failed:
                group_succeeded = False
                warnings.append(
                    f"Codex session {target.root_id} source delete failed; "
                    "Orca copy preserved"
                )
                continue
            for local_delete_id in owner.local_delete_ids:
                succeeded, detail = _run_codex_command(
                    [codex_binary, "delete", "--force", local_delete_id],
                    codex_home=owner.codex_home,
                    runner=runner,
                )
                if succeeded:
                    continue
                group_succeeded = False
                if not owner.is_orca:
                    source_failed = True
                warnings.append(
                    f"Codex session {local_delete_id} delete failed in "
                    f"{owner.codex_home}: {detail}"
                )
                break
        if group_succeeded:
            deleted += 1
            completed_paths.update(
                session_file.path for session_file in target.files
            )

    return CleanupResult(
        deleted=deleted,
        preserved_running=preserved_running,
        warnings=tuple(warnings),
    )


def _manifest_below_root(
    target_manifest: tuple[ClaudeSessionPath, ...],
    root: Path,
) -> tuple[ClaudeSessionPath, ...]:
    return tuple(
        entry
        for entry in target_manifest
        if entry.path == root or root in entry.path.parents
    )


def _fingerprint_from_stat_result(
    stat_result: os.stat_result,
) -> FileFingerprint:
    return FileFingerprint(
        identity=FileIdentity(
            device=stat_result.st_dev,
            inode=stat_result.st_ino,
        ),
        size=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
    )


def _claude_entry_from_stat(
    path: Path,
    stat_result: os.stat_result,
) -> ClaudeSessionPath:
    mode = stat_result.st_mode
    if stat.S_ISLNK(mode):
        raise ActiveSessionScanError(f"unsafe Claude session symlink: {path}")
    is_directory = stat.S_ISDIR(mode)
    if not is_directory and not stat.S_ISREG(mode):
        raise ActiveSessionScanError(
            f"unsupported Claude session path type: {path}"
        )
    return ClaudeSessionPath(
        path=path,
        fingerprint=_fingerprint_from_stat_result(stat_result),
        is_directory=is_directory,
    )


def _open_directory_anchor(
    parent_fd: int,
    name: str,
    path: Path,
) -> _DirectoryAnchor:
    directory_fd = os.open(
        name,
        _DIRECTORY_OPEN_FLAGS,
        dir_fd=parent_fd,
    )
    try:
        stat_result = os.fstat(directory_fd)
        if not stat.S_ISDIR(stat_result.st_mode):
            raise ActiveSessionScanError(
                f"unsafe Claude directory type: {path}"
            )
        return _DirectoryAnchor(
            parent_fd=parent_fd,
            name=name,
            directory_fd=directory_fd,
            identity=_fingerprint_from_stat_result(stat_result).identity,
            path=path,
        )
    except BaseException:
        os.close(directory_fd)
        raise


def _validate_directory_anchors(
    anchors: tuple[_DirectoryAnchor, ...],
) -> None:
    for anchor in anchors:
        stat_result = os.stat(
            anchor.name,
            dir_fd=anchor.parent_fd,
            follow_symlinks=False,
        )
        current = _claude_entry_from_stat(anchor.path, stat_result)
        descriptor_stat = os.fstat(anchor.directory_fd)
        descriptor_identity = _fingerprint_from_stat_result(
            descriptor_stat
        ).identity
        if (
            not current.is_directory
            or current.fingerprint.identity != anchor.identity
            or descriptor_identity != anchor.identity
        ):
            raise ActiveSessionScanError(
                f"Claude directory changed during cleanup: {anchor.path}"
            )


@contextmanager
def _open_absolute_directory_no_follow(
    path: Path,
) -> Iterator[tuple[int, tuple[_DirectoryAnchor, ...]]]:
    if not path.is_absolute() or path == Path("/"):
        raise ActiveSessionScanError(
            f"Claude config directory must be a bounded absolute path: {path}"
        )

    root_fd = os.open("/", _DIRECTORY_OPEN_FLAGS)
    opened_fds = [root_fd]
    anchors: list[_DirectoryAnchor] = []
    current_fd = root_fd
    current_path = Path("/")
    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise ActiveSessionScanError(
                    f"unsafe Claude config path component: {path}"
                )
            current_path /= component
            anchor = _open_directory_anchor(
                current_fd,
                component,
                current_path,
            )
            anchors.append(anchor)
            opened_fds.append(anchor.directory_fd)
            current_fd = anchor.directory_fd
        anchor_tuple = tuple(anchors)
        _validate_directory_anchors(anchor_tuple)
        yield current_fd, anchor_tuple
    finally:
        for descriptor in reversed(opened_fds):
            os.close(descriptor)


@contextmanager
def _open_relative_directories_no_follow(
    base_fd: int,
    base_path: Path,
    components: tuple[str, ...],
    base_anchors: tuple[_DirectoryAnchor, ...],
) -> Iterator[tuple[int, tuple[_DirectoryAnchor, ...]]]:
    opened_fds: list[int] = []
    anchors = list(base_anchors)
    current_fd = base_fd
    current_path = base_path
    try:
        for component in components:
            if component in {"", ".", ".."}:
                raise ActiveSessionScanError(
                    f"unsafe Claude relative path below {base_path}"
                )
            current_path /= component
            anchor = _open_directory_anchor(
                current_fd,
                component,
                current_path,
            )
            anchors.append(anchor)
            opened_fds.append(anchor.directory_fd)
            current_fd = anchor.directory_fd
        anchor_tuple = tuple(anchors)
        _validate_directory_anchors(anchor_tuple)
        yield current_fd, anchor_tuple
    finally:
        for descriptor in reversed(opened_fds):
            os.close(descriptor)


def _claude_root_parts(config_dir: Path, root: Path) -> tuple[str, ...]:
    try:
        relative = root.relative_to(config_dir)
    except ValueError as exc:
        raise ActiveSessionScanError(
            f"Claude session root escapes config directory: {root}"
        ) from exc
    if not relative.parts or any(
        component in {"", ".", ".."} for component in relative.parts
    ):
        raise ActiveSessionScanError(f"unsafe Claude session root: {root}")
    return relative.parts


def _snapshot_entry_no_follow(
    parent_fd: int,
    name: str,
    path: Path,
    anchors: tuple[_DirectoryAnchor, ...],
) -> tuple[ClaudeSessionPath, ...]:
    _validate_directory_anchors(anchors)
    stat_result = os.stat(
        name,
        dir_fd=parent_fd,
        follow_symlinks=False,
    )
    entry = _claude_entry_from_stat(path, stat_result)
    if not entry.is_directory:
        file_fd = os.open(name, _FILE_OPEN_FLAGS, dir_fd=parent_fd)
        try:
            descriptor_entry = _claude_entry_from_stat(path, os.fstat(file_fd))
            if descriptor_entry != entry:
                raise ActiveSessionScanError(
                    f"Claude session file changed during cleanup: {path}"
                )
            _validate_directory_anchors(anchors)
            current_entry = _claude_entry_from_stat(
                path,
                os.stat(
                    name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                ),
            )
            if current_entry != entry:
                raise ActiveSessionScanError(
                    f"Claude session file changed during cleanup: {path}"
                )
        finally:
            os.close(file_fd)
        return (entry,)

    anchor = _open_directory_anchor(parent_fd, name, path)
    child_anchors = (*anchors, anchor)
    try:
        descriptor_entry = _claude_entry_from_stat(
            path,
            os.fstat(anchor.directory_fd),
        )
        if descriptor_entry != entry:
            raise ActiveSessionScanError(
                f"Claude session directory changed during cleanup: {path}"
            )
        names = tuple(sorted(os.listdir(anchor.directory_fd)))
        manifest = [entry]
        for child_name in names:
            manifest.extend(
                _snapshot_entry_no_follow(
                    anchor.directory_fd,
                    child_name,
                    path / child_name,
                    child_anchors,
                )
            )
        _validate_directory_anchors(child_anchors)
        if tuple(sorted(os.listdir(anchor.directory_fd))) != names:
            raise ActiveSessionScanError(
                f"Claude session directory changed during cleanup: {path}"
            )
        final_entry = _claude_entry_from_stat(
            path,
            os.fstat(anchor.directory_fd),
        )
        if final_entry != entry:
            raise ActiveSessionScanError(
                f"Claude session directory changed during cleanup: {path}"
            )
        return tuple(manifest)
    finally:
        os.close(anchor.directory_fd)


def _snapshot_target_no_follow(
    config_fd: int,
    config_dir: Path,
    config_anchors: tuple[_DirectoryAnchor, ...],
    roots: tuple[Path, ...],
) -> tuple[ClaudeSessionPath, ...]:
    manifest: list[ClaudeSessionPath] = []
    for root in roots:
        parts = _claude_root_parts(config_dir, root)
        with _open_relative_directories_no_follow(
            config_fd,
            config_dir,
            parts[:-1],
            config_anchors,
        ) as (parent_fd, anchors):
            manifest.extend(
                _snapshot_entry_no_follow(
                    parent_fd,
                    parts[-1],
                    root,
                    anchors,
                )
            )
    paths = [entry.path for entry in manifest]
    if len(paths) != len(set(paths)):
        raise ActiveSessionScanError("overlapping Claude cleanup roots")
    return tuple(sorted(manifest, key=lambda entry: str(entry.path)))


def _verify_entry_matches(
    current: ClaudeSessionPath,
    expected: ClaudeSessionPath,
) -> None:
    if current != expected:
        raise ActiveSessionScanError(
            f"Claude session path changed before delete: {expected.path}"
        )


def _remove_partial_quarantine(
    parent_fd: int,
    anchors: tuple[_DirectoryAnchor, ...],
    quarantine: _QuarantineEvidence,
) -> None:
    if quarantine.identity is None:
        raise ActiveSessionScanError(
            "partial quarantine identity is unavailable; cleanup not attempted"
        )
    _validate_directory_anchors(anchors)
    current_stat = os.stat(
        quarantine.name,
        dir_fd=parent_fd,
        follow_symlinks=False,
    )
    current_identity = _fingerprint_from_stat_result(current_stat).identity
    if (
        not stat.S_ISDIR(current_stat.st_mode)
        or current_identity != quarantine.identity
    ):
        raise ActiveSessionScanError(
            "partial quarantine identity changed; cleanup not attempted"
        )
    os.rmdir(quarantine.name, dir_fd=parent_fd)
    _validate_directory_anchors(anchors)


def _raise_quarantine_creation_failure(
    parent_fd: int,
    anchors: tuple[_DirectoryAnchor, ...],
    quarantine: _QuarantineEvidence,
    entry_name: str,
    primary_error: Exception,
    *,
    descriptor_provenance_established: bool,
) -> NoReturn:
    diagnostic = _quarantine_recovery_diagnostic(
        quarantine,
        entry_name,
        isolated_stat=None,
        last_lexical_path=quarantine.path,
        path_was_verified=False,
    )
    if not descriptor_provenance_established:
        raise ActiveSessionScanError(
            "quarantine initialization failed after mkdir: "
            f"{primary_error}; session entry was not moved into quarantine; "
            "quarantine provenance=unverified before descriptor-backed open; "
            "public quarantine name was not removed; "
            f"{diagnostic}"
        ) from primary_error
    try:
        _remove_partial_quarantine(parent_fd, anchors, quarantine)
    except (ActiveSessionScanError, OSError) as cleanup_error:
        raise ActiveSessionScanError(
            "quarantine initialization failed after mkdir: "
            f"{primary_error}; session entry was not moved into quarantine; "
            "partial quarantine cleanup also failed: "
            f"{cleanup_error}; {diagnostic}"
        ) from primary_error
    raise ActiveSessionScanError(
        "quarantine initialization failed after mkdir: "
        f"{primary_error}; session entry was not moved into quarantine; "
        f"partial quarantine cleanup completed; {diagnostic}"
    ) from primary_error


def _create_private_quarantine(
    parent_fd: int,
    parent_path: Path,
    anchors: tuple[_DirectoryAnchor, ...],
    entry_name: str,
) -> _DirectoryAnchor:
    _validate_directory_anchors(anchors)
    for _ in range(_QUARANTINE_CREATE_ATTEMPTS):
        name = f"{_QUARANTINE_PREFIX}{secrets.token_hex(16)}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        break
    else:
        raise ActiveSessionScanError(
            f"cannot allocate private cleanup quarantine below {parent_path}"
        )

    quarantine_path = parent_path / name
    quarantine_evidence = _QuarantineEvidence(
        name=name,
        identity=None,
        path=quarantine_path,
    )
    try:
        created_stat = os.stat(
            name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        _raise_quarantine_creation_failure(
            parent_fd,
            anchors,
            quarantine_evidence,
            entry_name,
            exc,
            descriptor_provenance_established=False,
        )
    quarantine_evidence = _QuarantineEvidence(
        name=name,
        identity=_fingerprint_from_stat_result(created_stat).identity,
        path=quarantine_path,
    )
    try:
        quarantine = _open_directory_anchor(
            parent_fd,
            name,
            quarantine_path,
        )
    except (ActiveSessionScanError, OSError) as exc:
        _raise_quarantine_creation_failure(
            parent_fd,
            anchors,
            quarantine_evidence,
            entry_name,
            exc,
            descriptor_provenance_established=False,
        )
    try:
        quarantine_stat = os.fstat(quarantine.directory_fd)
        parent_stat = os.fstat(parent_fd)
        if (
            quarantine.identity != quarantine_evidence.identity
            or quarantine_stat.st_dev != parent_stat.st_dev
            or quarantine_stat.st_uid != os.geteuid()
            or stat.S_IMODE(quarantine_stat.st_mode) & 0o077
        ):
            raise ActiveSessionScanError(
                f"unsafe Claude cleanup quarantine: {quarantine.path}"
            )
        _validate_directory_anchors((*anchors, quarantine))
    except (ActiveSessionScanError, OSError) as exc:
        os.close(quarantine.directory_fd)
        _raise_quarantine_creation_failure(
            parent_fd,
            anchors,
            quarantine_evidence,
            entry_name,
            exc,
            descriptor_provenance_established=True,
        )
    except BaseException:
        os.close(quarantine.directory_fd)
        raise
    return quarantine


def _remove_empty_quarantine(
    parent_fd: int,
    anchors: tuple[_DirectoryAnchor, ...],
    quarantine: _DirectoryAnchor,
) -> None:
    quarantine_anchors = (*anchors, quarantine)
    _validate_directory_anchors(quarantine_anchors)
    if os.listdir(quarantine.directory_fd):
        raise ActiveSessionScanError(
            f"Claude cleanup quarantine is not empty: {quarantine.path}"
        )
    os.rmdir(quarantine.name, dir_fd=parent_fd)
    _validate_directory_anchors(anchors)


def _descriptor_directory_path(
    quarantine: _DirectoryAnchor,
) -> Path:
    raw_path = fcntl.fcntl(
        quarantine.directory_fd,
        fcntl.F_GETPATH,
        bytes(_DESCRIPTOR_PATH_BUFFER_SIZE),
    )
    if not isinstance(raw_path, bytes) or b"\0" not in raw_path:
        raise ActiveSessionScanError(
            "descriptor recovery path was unavailable or truncated"
        )
    encoded_path = raw_path.split(b"\0", 1)[0]
    if not encoded_path:
        raise ActiveSessionScanError("descriptor recovery path was empty")
    quarantine_path = Path(os.fsdecode(encoded_path))
    if not quarantine_path.is_absolute():
        raise ActiveSessionScanError(
            "descriptor recovery path was not absolute"
        )

    current_quarantine_stat = os.stat(
        quarantine_path,
        follow_symlinks=False,
    )
    current_quarantine_identity = _fingerprint_from_stat_result(
        current_quarantine_stat
    ).identity
    if (
        not stat.S_ISDIR(current_quarantine_stat.st_mode)
        or current_quarantine_identity != quarantine.identity
    ):
        raise ActiveSessionScanError(
            "descriptor recovery directory identity changed"
        )

    return quarantine_path


def _descriptor_recovery_path(
    quarantine: _DirectoryAnchor,
    name: str,
    isolated_stat: os.stat_result,
) -> Path:
    quarantine_path = _descriptor_directory_path(quarantine)
    recovery_path = quarantine_path / name
    current_isolated_stat = os.stat(
        recovery_path,
        follow_symlinks=False,
    )
    if (
        stat.S_IFMT(current_isolated_stat.st_mode)
        != stat.S_IFMT(isolated_stat.st_mode)
        or _fingerprint_from_stat_result(current_isolated_stat)
        != _fingerprint_from_stat_result(isolated_stat)
    ):
        raise ActiveSessionScanError(
            "descriptor recovery entry identity changed"
        )
    return recovery_path


def _quarantine_recovery_diagnostic(
    quarantine: _DirectoryAnchor | _QuarantineEvidence,
    name: str,
    *,
    isolated_stat: os.stat_result | None,
    last_lexical_path: Path,
    path_was_verified: bool,
    lookup_failure: Exception | None = None,
) -> str:
    path_label = (
        "last-verified lexical path"
        if path_was_verified
        else "last-known lexical path"
    )
    details = [
        f"{path_label}={last_lexical_path}",
        f"quarantine name={quarantine.name}",
    ]
    if quarantine.identity is None:
        details.append("quarantine identity=unavailable")
    else:
        details.append(
            "quarantine identity="
            f"device:{quarantine.identity.device},"
            f"inode:{quarantine.identity.inode}"
        )
    details.append(f"isolated entry name={name}")
    if isolated_stat is None:
        details.append("isolated identity/fingerprint=unavailable")
    else:
        fingerprint = _fingerprint_from_stat_result(isolated_stat)
        details.extend(
            (
                (
                    "isolated identity="
                    f"device:{fingerprint.identity.device},"
                    f"inode:{fingerprint.identity.inode}"
                ),
                (
                    "isolated fingerprint="
                    f"size:{fingerprint.size},mtime_ns:{fingerprint.mtime_ns}"
                ),
            )
        )
    if lookup_failure is not None:
        details.append(
            f"current-path lookup/F_GETPATH failed: {lookup_failure}"
        )
    details.append("current namespace location is not guaranteed")
    return "; ".join(details)


def _delete_entry_via_quarantine(
    parent_fd: int,
    name: str,
    path: Path,
    anchors: tuple[_DirectoryAnchor, ...],
    expected: ClaudeSessionPath,
    opened_fd: int,
) -> None:
    quarantine = _create_private_quarantine(
        parent_fd,
        path.parent,
        anchors,
        name,
    )
    quarantine_anchors = (*anchors, quarantine)
    try:
        _validate_directory_anchors(quarantine_anchors)
        os.rename(
            name,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=quarantine.directory_fd,
        )
        _validate_directory_anchors(quarantine_anchors)

        isolated = _claude_entry_from_stat(
            path,
            os.stat(
                name,
                dir_fd=quarantine.directory_fd,
                follow_symlinks=False,
            ),
        )
        descriptor_entry = _claude_entry_from_stat(path, os.fstat(opened_fd))
        if expected.is_directory:
            expected_identity = expected.fingerprint.identity
            if (
                not isolated.is_directory
                or not descriptor_entry.is_directory
                or isolated.fingerprint.identity != expected_identity
                or descriptor_entry.fingerprint.identity != expected_identity
            ):
                raise ActiveSessionScanError(
                    f"Claude session directory changed during isolation: {path}"
                )
            if os.listdir(opened_fd):
                raise ActiveSessionScanError(
                    f"Claude session directory changed before removal: {path}"
                )
            os.rmdir(name, dir_fd=quarantine.directory_fd)
        else:
            _verify_entry_matches(isolated, expected)
            _verify_entry_matches(descriptor_entry, expected)
            os.unlink(name, dir_fd=quarantine.directory_fd)

        try:
            os.stat(
                name,
                dir_fd=quarantine.directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise ActiveSessionScanError(
                f"Claude session path still exists in quarantine: {path}"
            )

        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ActiveSessionScanError(
                f"Claude session path replacement was preserved: {path}"
            )
        _remove_empty_quarantine(parent_fd, anchors, quarantine)
    except (ActiveSessionScanError, OSError) as exc:
        try:
            isolated_stat = os.stat(
                name,
                dir_fd=quarantine.directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            try:
                _remove_empty_quarantine(parent_fd, anchors, quarantine)
            except (ActiveSessionScanError, OSError) as cleanup_exc:
                try:
                    leftover_path = _descriptor_directory_path(quarantine)
                except (ActiveSessionScanError, OSError) as recovery_exc:
                    diagnostic = _quarantine_recovery_diagnostic(
                        quarantine,
                        name,
                        isolated_stat=None,
                        last_lexical_path=quarantine.path,
                        path_was_verified=False,
                        lookup_failure=recovery_exc,
                    )
                    raise ActiveSessionScanError(
                        f"{exc}; quarantine cleanup also failed: "
                        f"{cleanup_exc}; {diagnostic}"
                    ) from exc
                diagnostic = _quarantine_recovery_diagnostic(
                    quarantine,
                    name,
                    isolated_stat=None,
                    last_lexical_path=leftover_path,
                    path_was_verified=True,
                )
                raise ActiveSessionScanError(
                    f"{exc}; quarantine cleanup also failed: {cleanup_exc}; "
                    f"{diagnostic}"
                ) from exc
            diagnostic = _quarantine_recovery_diagnostic(
                quarantine,
                name,
                isolated_stat=None,
                last_lexical_path=quarantine.path,
                path_was_verified=False,
            )
            raise ActiveSessionScanError(
                f"{exc}; isolated entry was not found in quarantine and the "
                f"empty quarantine cleanup completed; {diagnostic}"
            ) from exc
        except OSError as inspection_exc:
            try:
                quarantine_path = _descriptor_directory_path(quarantine)
            except (ActiveSessionScanError, OSError) as recovery_exc:
                diagnostic = _quarantine_recovery_diagnostic(
                    quarantine,
                    name,
                    isolated_stat=None,
                    last_lexical_path=quarantine.path,
                    path_was_verified=False,
                    lookup_failure=recovery_exc,
                )
                raise ActiveSessionScanError(
                    f"{exc}; cannot inspect isolated entry: {inspection_exc}; "
                    f"{diagnostic}"
                ) from exc
            diagnostic = _quarantine_recovery_diagnostic(
                quarantine,
                name,
                isolated_stat=None,
                last_lexical_path=quarantine_path,
                path_was_verified=True,
            )
            raise ActiveSessionScanError(
                f"{exc}; cannot inspect isolated entry: {inspection_exc}; "
                f"{diagnostic}"
            ) from exc
        else:
            try:
                recovery_path = _descriptor_recovery_path(
                    quarantine,
                    name,
                    isolated_stat,
                )
            except (ActiveSessionScanError, OSError) as recovery_exc:
                diagnostic = _quarantine_recovery_diagnostic(
                    quarantine,
                    name,
                    isolated_stat=isolated_stat,
                    last_lexical_path=quarantine.path / name,
                    path_was_verified=False,
                    lookup_failure=recovery_exc,
                )
                raise ActiveSessionScanError(
                    f"{exc}; isolated entry was observed through the held "
                    f"quarantine descriptor; {diagnostic}"
                ) from exc
            diagnostic = _quarantine_recovery_diagnostic(
                quarantine,
                name,
                isolated_stat=isolated_stat,
                last_lexical_path=recovery_path,
                path_was_verified=True,
            )
            raise ActiveSessionScanError(
                f"{exc}; isolated entry was verified before diagnostic "
                f"reporting; {diagnostic}"
            ) from exc
        raise
    finally:
        os.close(quarantine.directory_fd)


def _delete_entry_no_follow(
    parent_fd: int,
    name: str,
    path: Path,
    anchors: tuple[_DirectoryAnchor, ...],
    expected_by_path: dict[Path, ClaudeSessionPath],
) -> None:
    expected = expected_by_path[path]
    _validate_directory_anchors(anchors)
    current = _claude_entry_from_stat(
        path,
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False),
    )
    _verify_entry_matches(current, expected)

    if not expected.is_directory:
        file_fd = os.open(name, _FILE_OPEN_FLAGS, dir_fd=parent_fd)
        try:
            descriptor_entry = _claude_entry_from_stat(path, os.fstat(file_fd))
            _verify_entry_matches(descriptor_entry, expected)
            _validate_directory_anchors(anchors)
            current = _claude_entry_from_stat(
                path,
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False),
            )
            _verify_entry_matches(current, expected)
            _delete_entry_via_quarantine(
                parent_fd,
                name,
                path,
                anchors,
                expected,
                file_fd,
            )
        finally:
            os.close(file_fd)
    else:
        anchor = _open_directory_anchor(parent_fd, name, path)
        child_anchors = (*anchors, anchor)
        try:
            descriptor_entry = _claude_entry_from_stat(
                path,
                os.fstat(anchor.directory_fd),
            )
            _verify_entry_matches(descriptor_entry, expected)
            expected_names = {
                entry_path.name
                for entry_path in expected_by_path
                if entry_path.parent == path
            }
            current_names = set(os.listdir(anchor.directory_fd))
            if current_names != expected_names:
                raise ActiveSessionScanError(
                    f"Claude session directory changed before delete: {path}"
                )
            for child_name in sorted(current_names):
                _delete_entry_no_follow(
                    anchor.directory_fd,
                    child_name,
                    path / child_name,
                    child_anchors,
                    expected_by_path,
                )
            _validate_directory_anchors(child_anchors)
            if os.listdir(anchor.directory_fd):
                raise ActiveSessionScanError(
                    f"Claude session directory changed before removal: {path}"
                )
            current_stat = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            current_identity = _fingerprint_from_stat_result(
                current_stat
            ).identity
            if (
                not stat.S_ISDIR(current_stat.st_mode)
                or current_identity != expected.fingerprint.identity
            ):
                raise ActiveSessionScanError(
                    f"Claude session directory replaced before removal: {path}"
                )
            _delete_entry_via_quarantine(
                parent_fd,
                name,
                path,
                anchors,
                expected,
                anchor.directory_fd,
            )
        finally:
            os.close(anchor.directory_fd)

    _validate_directory_anchors(anchors)
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ActiveSessionScanError(
        f"Claude session path still exists after delete: {path}"
    )


def _delete_root_no_follow(
    config_fd: int,
    config_dir: Path,
    config_anchors: tuple[_DirectoryAnchor, ...],
    root: Path,
    expected_manifest: tuple[ClaudeSessionPath, ...],
) -> None:
    parts = _claude_root_parts(config_dir, root)
    with _open_relative_directories_no_follow(
        config_fd,
        config_dir,
        parts[:-1],
        config_anchors,
    ) as (parent_fd, anchors):
        current_manifest = tuple(
            sorted(
                _snapshot_entry_no_follow(
                    parent_fd,
                    parts[-1],
                    root,
                    anchors,
                ),
                key=lambda entry: str(entry.path),
            )
        )
        if current_manifest != expected_manifest:
            raise ActiveSessionScanError(
                f"Claude session path changed before delete: {root}"
            )
        expected_by_path = {
            entry.path: entry for entry in expected_manifest
        }
        _delete_entry_no_follow(
            parent_fd,
            parts[-1],
            root,
            anchors,
            expected_by_path,
        )


def cleanup_claude_inventory(
    inventory: AgentInventory,
    *,
    active_session_snapshot: Callable[[Path], frozenset[str]] = (
        snapshot_active_claude_sessions
    ),
    open_file_snapshot: OpenFileSnapshot = snapshot_open_rollouts,
) -> CleanupResult:
    """Delete only exact, revalidated inactive Claude session bundles."""
    preserved_running = inventory.active_sessions
    if not inventory.claude_targets:
        return CleanupResult(preserved_running=preserved_running)
    if inventory.client != "claude":
        return CleanupResult(
            preserved_running=preserved_running,
            error=f"cannot run Claude cleanup for {inventory.client} inventory"
        )
    if inventory.policy != "all_inactive":
        return CleanupResult(
            preserved_running=preserved_running,
            error="Claude cleanup requires all_inactive inventory",
        )
    if inventory.warnings:
        return CleanupResult(
            preserved_running=preserved_running,
            warnings=inventory.warnings,
            error="cannot safely inventory every inactive Claude session",
        )

    config_dir = inventory.claude_config_dir
    if config_dir is None or not config_dir.is_absolute():
        return CleanupResult(
            preserved_running=preserved_running,
            error="Claude inventory has no absolute config directory",
        )

    try:
        active_session_ids = active_session_snapshot(config_dir)
    except (ActiveSessionScanError, OSError) as exc:
        return CleanupResult(
            preserved_running=preserved_running,
            error=f"cannot scan active Claude sessions in {config_dir}: {exc}"
        )
    try:
        open_identities = open_file_snapshot(inventory.session_dirs)
    except (ActiveSessionScanError, OSError) as exc:
        paths = ", ".join(str(path) for path in inventory.session_dirs)
        return CleanupResult(
            preserved_running=preserved_running,
            error=f"cannot scan open Claude transcripts in {paths}: {exc}"
        )

    inactive_targets = []
    for target in inventory.claude_targets:
        is_open = any(
            entry.fingerprint.identity in open_identities
            for entry in target.manifest
        )
        if target.session_id in active_session_ids or is_open:
            preserved_running += 1
            continue
        inactive_targets.append(target)

    if not inactive_targets:
        return CleanupResult(preserved_running=preserved_running)

    deleted = 0
    try:
        with _open_absolute_directory_no_follow(config_dir) as (
            config_fd,
            config_anchors,
        ):
            try:
                prevalidated_roots = snapshot_claude_session_roots(config_dir)
            except (ActiveSessionScanError, OSError) as exc:
                return CleanupResult(
                    preserved_running=preserved_running,
                    error=(
                        f"cannot prevalidate Claude session roots in "
                        f"{config_dir}: {exc}"
                    ),
                )
            for target in inactive_targets:
                if prevalidated_roots.get(target.session_id, ()) != target.roots:
                    return CleanupResult(
                        preserved_running=preserved_running,
                        error=(
                            f"Claude session {target.session_id} roots changed "
                            "after inventory"
                        ),
                    )

            for target in inactive_targets:
                current_manifest = _snapshot_target_no_follow(
                    config_fd,
                    config_dir,
                    config_anchors,
                    target.roots,
                )
                if current_manifest != target.manifest:
                    return CleanupResult(
                        preserved_running=preserved_running,
                        error=(
                            f"Claude session {target.session_id} changed after "
                            f"inventory at {target.roots}"
                        ),
                    )

            for target in inactive_targets:
                try:
                    current_active_session_ids = active_session_snapshot(
                        config_dir
                    )
                except (ActiveSessionScanError, OSError) as exc:
                    return CleanupResult(
                        deleted=deleted,
                        preserved_running=preserved_running,
                        error=(
                            f"cannot refresh active Claude sessions in "
                            f"{config_dir}: {exc}"
                        ),
                    )
                if target.session_id in current_active_session_ids:
                    preserved_running += 1
                    continue
                try:
                    current_open_identities = open_file_snapshot(
                        inventory.session_dirs
                    )
                except (ActiveSessionScanError, OSError) as exc:
                    paths = ", ".join(
                        str(path) for path in inventory.session_dirs
                    )
                    return CleanupResult(
                        deleted=deleted,
                        preserved_running=preserved_running,
                        error=(
                            f"cannot refresh open Claude transcripts in "
                            f"{paths}: {exc}"
                        ),
                    )
                if any(
                    entry.fingerprint.identity in current_open_identities
                    for entry in target.manifest
                ):
                    preserved_running += 1
                    continue
                try:
                    current_roots = snapshot_claude_session_roots(config_dir).get(
                        target.session_id,
                        (),
                    )
                except (ActiveSessionScanError, OSError) as exc:
                    return CleanupResult(
                        deleted=deleted,
                        preserved_running=preserved_running,
                        error=(
                            f"cannot refresh Claude session roots in "
                            f"{config_dir}: {exc}"
                        ),
                    )
                if current_roots != target.roots:
                    return CleanupResult(
                        deleted=deleted,
                        preserved_running=preserved_running,
                        error=(
                            f"Claude session {target.session_id} roots changed "
                            "after inventory"
                        ),
                    )
                current_manifest = _snapshot_target_no_follow(
                    config_fd,
                    config_dir,
                    config_anchors,
                    current_roots,
                )
                if current_manifest != target.manifest:
                    return CleanupResult(
                        deleted=deleted,
                        preserved_running=preserved_running,
                        error=(
                            f"Claude session {target.session_id} changed after "
                            "inventory before delete"
                        ),
                    )
                roots = sorted(
                    target.roots,
                    key=lambda path: (len(path.parts), str(path)),
                    reverse=True,
                )
                for root in roots:
                    _delete_root_no_follow(
                        config_fd,
                        config_dir,
                        config_anchors,
                        root,
                        _manifest_below_root(target.manifest, root),
                    )
                deleted += 1
    except (ActiveSessionScanError, OSError) as exc:
        return CleanupResult(
            deleted=deleted,
            preserved_running=preserved_running,
            error=f"failed to remove Claude session path safely: {exc}",
        )

    return CleanupResult(
        deleted=deleted,
        preserved_running=preserved_running,
    )
