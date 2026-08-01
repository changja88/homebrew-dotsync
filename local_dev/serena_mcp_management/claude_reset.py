"""Full reset of local Claude Code CLI conversation state."""
from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .agent_paths import lexical_claude_config_dir
from .memory_management import (
    ClientProcess,
    running_client_processes,
    scan_memory_inventory,
)
from .serena_mcp.health import pid_is_alive, process_identity
from .serena_mcp.termination import terminate_pid


RunCommand = Callable[..., subprocess.CompletedProcess[str]]
ProcessScanner = Callable[..., tuple[ClientProcess, ...]]
IdentityReader = Callable[[int], str | None]
ProcessTerminator = Callable[..., None]
ProcessAlive = Callable[[int], bool]

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
class _PinnedRuntime:
    process: ClientProcess
    identity: str


@dataclass(frozen=True)
class _RuntimeTermination:
    terminated: int = 0
    warnings: tuple[str, ...] = ()
    error: str | None = None


def _config_root_error(config_dir: Path, *, home: Path) -> str | None:
    broad_paths = {Path("/"), home.absolute(), *home.absolute().parents}
    if config_dir in broad_paths:
        return f"Claude config path is unsafe and too broad: {config_dir}"
    return None


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
) -> ClaudeResetResult:
    """Preflight a full local Claude Code reset."""

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

    memory_inventory = scan_memory_inventory(
        client="claude",
        home=home,
        codex_home=home / ".codex",
        claude_config_dir=config_dir,
    )
    if memory_inventory.warnings:
        return ClaudeResetResult(
            error="Claude memory scan unsafe: "
            + "; ".join(memory_inventory.warnings)
        )

    _, global_config_error = _snapshot_global_config(
        home=home,
        config_dir=config_dir,
        custom_config=claude_config_dir is not None,
    )
    if global_config_error is not None:
        return ClaudeResetResult(error=global_config_error)

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
        return ClaudeResetResult(error=capability_error)
    return ClaudeResetResult()
