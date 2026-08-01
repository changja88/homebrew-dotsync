"""Full reset of local Claude Code CLI conversation state."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .agent_paths import lexical_claude_config_dir
from .memory_management import (
    ClientProcess,
    MemoryDeleteResult,
    MemoryInventory,
    delete_all_memory,
    running_client_processes,
    scan_memory_inventory,
)
from .serena_mcp.health import pid_is_alive, process_identity
from .serena_mcp.termination import terminate_pid
from .session_inventory import AgentInventory, scan_claude_inventory


RunCommand = Callable[..., subprocess.CompletedProcess[str]]
ProcessScanner = Callable[..., tuple[ClientProcess, ...]]
IdentityReader = Callable[[int], str | None]
ProcessTerminator = Callable[..., None]
ProcessAlive = Callable[[int], bool]
SessionScanner = Callable[..., AgentInventory]
MemoryScanner = Callable[..., MemoryInventory]
MemoryDeleter = Callable[..., MemoryDeleteResult]

_SUPPLEMENTAL_DIRECTORY_NAMES = (
    "agent-memory",
    "plans",
    "paste-cache",
    "image-cache",
    "session-env",
    "shell-snapshots",
    "sessions",
    "feedback-bundles",
    "todos",
    "logs",
)

_OFFICIAL_DIRECTORY_NAMES = (
    "projects",
    "tasks",
    "debug",
    "file-history",
)


@dataclass(frozen=True)
class ClaudeResetResult:
    discovered_sessions: int = 0
    deleted_sessions: int = 0
    deleted_memory_stores: int = 0
    deleted_residual_targets: int = 0
    terminated_processes: int = 0
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class _GlobalConfigSnapshot:
    path: Path
    existed: bool
    non_project_values: dict[str, object]


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    existed: bool
    content: bytes


@dataclass(frozen=True)
class _PinnedRuntime:
    process: ClientProcess
    identity: str


@dataclass(frozen=True)
class _RuntimeTermination:
    terminated: int = 0
    warnings: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class _SupplementalTarget:
    path: Path
    allowed_root: Path


@dataclass(frozen=True)
class _SupplementalDeletion:
    deleted: int = 0
    error: str | None = None


def _config_root_error(config_dir: Path, *, home: Path) -> str | None:
    broad_paths = {Path("/"), home.absolute(), *home.absolute().parents}
    if config_dir in broad_paths:
        return f"Claude config path is unsafe and too broad: {config_dir}"
    return None


def _discover_supplemental_targets(
    config_dir: Path,
) -> tuple[_SupplementalTarget, ...]:
    return tuple(
        _SupplementalTarget(
            path=config_dir / name,
            allowed_root=config_dir,
        )
        for name in _SUPPLEMENTAL_DIRECTORY_NAMES
    )


def _supplemental_targets_error(
    targets: tuple[_SupplementalTarget, ...],
) -> str | None:
    for target in targets:
        _, error = _supplemental_target_kind(target)
        if error is not None:
            return error
    return None


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(mode):
            return True
    return False


def _supplemental_target_kind(
    target: _SupplementalTarget,
) -> tuple[str | None, str | None]:
    if (
        target.path.parent != target.allowed_root
        or target.path.name not in _SUPPLEMENTAL_DIRECTORY_NAMES
    ):
        return None, f"invalid Claude supplemental target: {target.path}"
    try:
        if _has_symlink_component(target.allowed_root):
            return (
                None,
                f"Claude supplemental root contains a symlink: {target.allowed_root}",
            )
        mode = target.path.lstat().st_mode
    except FileNotFoundError:
        return "missing", None
    except OSError as exc:
        return None, f"cannot inspect Claude supplemental target {target.path}: {exc}"
    if stat.S_ISDIR(mode):
        return "directory", None
    if stat.S_ISLNK(mode):
        return "symlink", None
    return None, f"Claude supplemental target is not a directory: {target.path}"


def _delete_supplemental_targets(
    targets: tuple[_SupplementalTarget, ...],
    *,
    remove_tree: Callable[[Path], None] = shutil.rmtree,
    unlink_path: Callable[[Path], None] = os.unlink,
) -> _SupplementalDeletion:
    validation_error = _supplemental_targets_error(targets)
    if validation_error is not None:
        return _SupplementalDeletion(error=validation_error)

    deleted = 0
    for target in targets:
        kind, error = _supplemental_target_kind(target)
        if error is not None:
            return _SupplementalDeletion(deleted=deleted, error=error)
        if kind == "missing":
            continue
        try:
            if kind == "symlink":
                unlink_path(target.path)
            else:
                remove_tree(target.path)
        except OSError as exc:
            return _SupplementalDeletion(
                deleted=deleted,
                error=f"cannot delete Claude supplemental target {target.path}: {exc}",
            )
        deleted += 1
    return _SupplementalDeletion(deleted=deleted)


def _probe_purge_capability(
    real_claude_binary: str,
    *,
    run_command: RunCommand,
    environment: dict[str, str],
) -> str | None:
    command = [real_claude_binary, "project", "purge", "--help"]
    try:
        result = run_command(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"cannot probe Claude project purge: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or f"exit {result.returncode}"
        return f"cannot probe Claude project purge: {detail}"
    output = f"{result.stdout or ''}\n{result.stderr or ''}"
    missing = [flag for flag in ("--all", "--yes") if flag not in output]
    if missing:
        return (
            "Claude project purge is missing required option(s): "
            + ", ".join(missing)
        )
    return None


def _snapshot_global_config(
    *,
    home: Path,
    config_dir: Path,
    custom_config: bool,
) -> tuple[_GlobalConfigSnapshot | None, str | None]:
    path = config_dir / ".claude.json" if custom_config else home / ".claude.json"
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return _GlobalConfigSnapshot(path, False, {}), None
    except OSError as exc:
        return None, f"cannot inspect Claude global config {path}: {exc}"
    if stat.S_ISLNK(mode):
        return None, f"Claude global config is a symlink: {path}"
    if not stat.S_ISREG(mode):
        return None, f"Claude global config is not a regular file: {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"invalid Claude global config {path}: {exc}"
    if not isinstance(payload, dict):
        return None, f"invalid Claude global config {path}: expected an object"
    projects = payload.get("projects")
    if projects is not None and not isinstance(projects, dict):
        return None, f"invalid Claude global config {path}: projects must be an object"
    return (
        _GlobalConfigSnapshot(
            path=path,
            existed=True,
            non_project_values={
                key: value for key, value in payload.items() if key != "projects"
            },
        ),
        None,
    )


def _snapshot_file(path: Path, *, label: str) -> tuple[_FileSnapshot | None, str | None]:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return _FileSnapshot(path, False, b""), None
    except OSError as exc:
        return None, f"cannot inspect {label} {path}: {exc}"
    if stat.S_ISLNK(mode):
        return None, f"{label} is a symlink: {path}"
    if not stat.S_ISREG(mode):
        return None, f"{label} is not a regular file: {path}"
    try:
        content = path.read_bytes()
    except OSError as exc:
        return None, f"cannot read {label} {path}: {exc}"
    return _FileSnapshot(path, True, content), None


def _file_unchanged_error(snapshot: _FileSnapshot, *, label: str) -> str | None:
    current, error = _snapshot_file(snapshot.path, label=label)
    if error is not None:
        return error
    assert current is not None
    if current.existed != snapshot.existed or current.content != snapshot.content:
        return f"{label} changed during Claude reset: {snapshot.path}"
    return None


def _verify_global_config(snapshot: _GlobalConfigSnapshot) -> str | None:
    current, error = _snapshot_global_config(
        home=snapshot.path.parent,
        config_dir=snapshot.path.parent,
        custom_config=True,
    )
    if error is not None:
        return error
    assert current is not None
    if snapshot.existed and not current.existed:
        return f"Claude global config disappeared during reset: {snapshot.path}"
    if current.existed:
        try:
            payload = json.loads(snapshot.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return f"invalid Claude global config {snapshot.path}: {exc}"
        projects = payload.get("projects")
        if isinstance(projects, dict) and projects:
            return f"Claude global config still contains project entries: {snapshot.path}"
        if projects is not None and not isinstance(projects, dict):
            return f"invalid Claude global config {snapshot.path}: projects must be an object"
        for key, value in snapshot.non_project_values.items():
            if key not in payload or payload[key] != value:
                return (
                    "Claude global config changed preserved value "
                    f"{key!r}: {snapshot.path}"
                )
    return None


def _official_residuals(config_dir: Path) -> tuple[str, ...]:
    residuals: list[str] = []
    for name in _OFFICIAL_DIRECTORY_NAMES:
        path = config_dir / name
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            residuals.append(f"cannot inspect official purge target {path}: {exc}")
            continue
        if not stat.S_ISDIR(mode):
            residuals.append(f"official purge target has wrong type: {path}")
            continue
        try:
            with os.scandir(path) as entries:
                if next(entries, None) is not None:
                    residuals.append(f"official purge target is not empty: {path}")
        except OSError as exc:
            residuals.append(f"cannot read official purge target {path}: {exc}")

    history = config_dir / "history.jsonl"
    try:
        history.lstat()
    except FileNotFoundError:
        return tuple(residuals)
    except OSError as exc:
        residuals.append(f"cannot inspect official purge target {history}: {exc}")
    else:
        residuals.append(f"official purge target remains: {history}")
    return tuple(residuals)


def _supplemental_residuals(
    targets: tuple[_SupplementalTarget, ...],
) -> tuple[str, ...]:
    residuals: list[str] = []
    for target in targets:
        try:
            target.path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            residuals.append(
                f"cannot inspect Claude supplemental target {target.path}: {exc}"
            )
        else:
            residuals.append(f"Claude supplemental target remains: {target.path}")
    return tuple(residuals)


def _terminate_claude_runtimes(
    *,
    real_claude_binary: str,
    environment: dict[str, str],
    run_command: RunCommand,
    process_scanner: ProcessScanner = running_client_processes,
    identity_reader: IdentityReader = process_identity,
    process_terminator: ProcessTerminator = terminate_pid,
    process_alive: ProcessAlive = pid_is_alive,
) -> _RuntimeTermination:
    warnings: list[str] = []
    daemon_command = [real_claude_binary, "daemon", "stop", "--any"]
    try:
        daemon_result = run_command(
            daemon_command,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        warnings.append(f"could not stop Claude daemon: {exc}")
    else:
        if daemon_result.returncode != 0:
            detail = (daemon_result.stderr or "").strip() or (
                f"exit {daemon_result.returncode}"
            )
            warnings.append(f"could not stop Claude daemon: {detail}")

    terminated = 0
    for _ in range(4):
        try:
            processes = process_scanner(
                "claude",
                run_command=run_command,
                current_pid=os.getpid(),
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return _RuntimeTermination(
                terminated=terminated,
                warnings=tuple(warnings),
                error=f"cannot inspect running Claude processes: {exc}",
            )
        if not processes:
            return _RuntimeTermination(
                terminated=terminated,
                warnings=tuple(warnings),
            )

        pinned: list[_PinnedRuntime] = []
        for process in processes:
            try:
                identity = identity_reader(process.pid)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                return _RuntimeTermination(
                    terminated=terminated,
                    warnings=tuple(warnings),
                    error=(
                        "cannot inspect Claude process identity for PID "
                        f"{process.pid}: {exc}"
                    ),
                )
            if identity is None:
                return _RuntimeTermination(
                    terminated=terminated,
                    warnings=tuple(warnings),
                    error=(
                        "cannot pin Claude process identity for PID "
                        f"{process.pid}"
                    ),
                )
            pinned.append(_PinnedRuntime(process, identity))

        try:
            current_processes = process_scanner(
                "claude",
                run_command=run_command,
                current_pid=os.getpid(),
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return _RuntimeTermination(
                terminated=terminated,
                warnings=tuple(warnings),
                error=f"cannot revalidate Claude processes: {exc}",
            )
        current_by_pid = {process.pid: process for process in current_processes}
        for runtime in pinned:
            pid = runtime.process.pid
            if pid not in current_by_pid:
                continue
            try:
                current_identity = identity_reader(pid)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                return _RuntimeTermination(
                    terminated=terminated,
                    warnings=tuple(warnings),
                    error=(
                        "cannot revalidate Claude process identity for PID "
                        f"{pid}: {exc}"
                    ),
                )
            if current_identity != runtime.identity:
                return _RuntimeTermination(
                    terminated=terminated,
                    warnings=tuple(warnings),
                    error=(
                        f"Claude process {pid} identity changed before "
                        "termination"
                    ),
                )
            try:
                process_terminator(
                    pid,
                    expected_identity=runtime.identity,
                )
            except OSError as exc:
                return _RuntimeTermination(
                    terminated=terminated,
                    warnings=tuple(warnings),
                    error=f"cannot terminate Claude process {pid}: {exc}",
                )
            try:
                still_alive = process_alive(pid)
            except (OSError, RuntimeError) as exc:
                return _RuntimeTermination(
                    terminated=terminated,
                    warnings=tuple(warnings),
                    error=(
                        "cannot verify Claude process liveness for PID "
                        f"{pid}: {exc}"
                    ),
                )
            if still_alive:
                return _RuntimeTermination(
                    terminated=terminated,
                    warnings=tuple(warnings),
                    error=(
                        f"Claude process {pid} is still running after "
                        "termination"
                    ),
                )
            terminated += 1

    return _RuntimeTermination(
        terminated=terminated,
        warnings=tuple(warnings),
        error="Claude processes kept respawning during reset quiescence check",
    )


def reset_all_claude_data(
    *,
    home: Path,
    claude_config_dir: Path | None,
    real_claude_binary: str,
    run_command: RunCommand = subprocess.run,
    _process_scanner: ProcessScanner = running_client_processes,
    _identity_reader: IdentityReader = process_identity,
    _process_terminator: ProcessTerminator = terminate_pid,
    _process_alive: ProcessAlive = pid_is_alive,
    _session_scanner: SessionScanner = scan_claude_inventory,
    _memory_scanner: MemoryScanner = scan_memory_inventory,
    _memory_deleter: MemoryDeleter = delete_all_memory,
    _remove_tree: Callable[[Path], None] = shutil.rmtree,
    _unlink_path: Callable[[Path], None] = os.unlink,
) -> ClaudeResetResult:
    """Delete every known local Claude Code conversation trace."""

    try:
        config_dir = lexical_claude_config_dir(
            home=home,
            claude_config_dir=claude_config_dir,
        )
    except ValueError as exc:
        return ClaudeResetResult(error=str(exc))

    root_error = _config_root_error(config_dir, home=home)
    if root_error is not None:
        return ClaudeResetResult(error=root_error)

    settings_snapshot, settings_error = _snapshot_file(
        config_dir / "settings.json",
        label="Claude user settings",
    )
    if settings_error is not None:
        return ClaudeResetResult(error=settings_error)
    assert settings_snapshot is not None

    try:
        memory_inventory = _memory_scanner(
            client="claude",
            home=home,
            codex_home=home / ".codex",
            claude_config_dir=config_dir,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return ClaudeResetResult(error=f"cannot scan Claude memory state: {exc}")
    if memory_inventory.warnings:
        return ClaudeResetResult(
            error="Claude memory scan unsafe: "
            + "; ".join(memory_inventory.warnings)
        )

    global_config_snapshot, global_config_error = _snapshot_global_config(
        home=home,
        config_dir=config_dir,
        custom_config=claude_config_dir is not None,
    )
    if global_config_error is not None:
        return ClaudeResetResult(error=global_config_error)
    assert global_config_snapshot is not None

    supplemental_targets = _discover_supplemental_targets(config_dir)
    supplemental_error = _supplemental_targets_error(supplemental_targets)
    if supplemental_error is not None:
        return ClaudeResetResult(error=supplemental_error)

    warnings: list[str] = []
    discovered_sessions = 0
    try:
        session_inventory = _session_scanner(
            home=home,
            claude_config_dir=config_dir,
            policy="all_inactive",
        )
    except (OSError, RuntimeError, ValueError) as exc:
        warnings.append(f"cannot count Claude sessions before reset: {exc}")
    else:
        discovered_sessions = session_inventory.sessions.total
        warnings.extend(session_inventory.warnings)

    environment = dict(os.environ)
    if claude_config_dir is None:
        environment.pop("CLAUDE_CONFIG_DIR", None)
    else:
        environment["CLAUDE_CONFIG_DIR"] = str(config_dir)
    capability_error = _probe_purge_capability(
        real_claude_binary,
        run_command=run_command,
        environment=environment,
    )
    if capability_error is not None:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            warnings=tuple(warnings),
            error=capability_error,
        )

    termination = _terminate_claude_runtimes(
        real_claude_binary=real_claude_binary,
        environment=environment,
        run_command=run_command,
        process_scanner=_process_scanner,
        identity_reader=_identity_reader,
        process_terminator=_process_terminator,
        process_alive=_process_alive,
    )
    warnings.extend(termination.warnings)
    if termination.error is not None:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error=termination.error,
        )

    purge_command = [
        real_claude_binary,
        "project",
        "purge",
        "--all",
        "--yes",
    ]
    try:
        purge_result = run_command(
            purge_command,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error=f"Claude project purge failed: {exc}",
        )
    if purge_result.returncode != 0:
        detail = (purge_result.stderr or "").strip() or (
            f"exit {purge_result.returncode}"
        )
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error=f"Claude project purge failed: {detail}",
        )

    official_errors = list(_official_residuals(config_dir))
    global_error = _verify_global_config(global_config_snapshot)
    if global_error is not None:
        official_errors.append(global_error)
    settings_changed = _file_unchanged_error(
        settings_snapshot,
        label="Claude user settings",
    )
    if settings_changed is not None:
        official_errors.append(settings_changed)
    if official_errors:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error="; ".join(official_errors),
        )
    deleted_sessions = discovered_sessions

    try:
        memory_result = _memory_deleter(
            client="claude",
            home=home,
            codex_home=home / ".codex",
            claude_config_dir=config_dir,
            run_command=run_command,
            remove_tree=_remove_tree,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            deleted_sessions=deleted_sessions,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error=f"Claude memory deletion failed: {exc}",
        )
    if not memory_result.succeeded:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            deleted_sessions=deleted_sessions,
            deleted_memory_stores=memory_result.deleted_stores,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error=memory_result.error or "Claude memory deletion failed",
        )

    supplemental_result = _delete_supplemental_targets(
        supplemental_targets,
        remove_tree=_remove_tree,
        unlink_path=_unlink_path,
    )
    if supplemental_result.error is not None:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            deleted_sessions=deleted_sessions,
            deleted_memory_stores=memory_result.deleted_stores,
            deleted_residual_targets=supplemental_result.deleted,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error=supplemental_result.error,
        )

    verification_errors: list[str] = []
    try:
        remaining_processes = _process_scanner(
            "claude",
            run_command=run_command,
            current_pid=os.getpid(),
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        verification_errors.append(
            f"cannot verify final Claude process state: {exc}"
        )
    else:
        if remaining_processes:
            verification_errors.append(
                f"{len(remaining_processes)} Claude process(es) remain after reset"
            )

    verification_errors.extend(_official_residuals(config_dir))
    verification_errors.extend(_supplemental_residuals(supplemental_targets))

    try:
        final_memory = _memory_scanner(
            client="claude",
            home=home,
            codex_home=home / ".codex",
            claude_config_dir=config_dir,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        verification_errors.append(f"cannot verify Claude memory state: {exc}")
    else:
        if final_memory.warnings:
            verification_errors.append(
                "Claude memory verification unsafe: "
                + "; ".join(final_memory.warnings)
            )
        if final_memory.stores:
            verification_errors.append(
                f"{len(final_memory.stores)} Claude memory store(s) remain after reset"
            )

    final_global_error = _verify_global_config(global_config_snapshot)
    if final_global_error is not None:
        verification_errors.append(final_global_error)
    final_settings_error = _file_unchanged_error(
        settings_snapshot,
        label="Claude user settings",
    )
    if final_settings_error is not None:
        verification_errors.append(final_settings_error)

    return ClaudeResetResult(
        discovered_sessions=discovered_sessions,
        deleted_sessions=deleted_sessions,
        deleted_memory_stores=memory_result.deleted_stores,
        deleted_residual_targets=supplemental_result.deleted,
        terminated_processes=termination.terminated,
        warnings=tuple(warnings),
        error="; ".join(verification_errors) if verification_errors else None,
    )
