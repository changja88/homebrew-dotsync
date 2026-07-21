"""Native session cleanup operations for Codex and Claude Code."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from local_dev.serena_mcp_management.session_inventory import (
    ActiveSessionScanError,
    AgentInventory,
    CodexCleanupTarget,
    FileFingerprint,
    FileIdentity,
    snapshot_open_rollouts,
)


CLAUDE_RETENTION_JSON = '{"cleanupPeriodDays":5}'
DELETE_TIMEOUT_SECONDS = 30


RunCommand = Callable[..., subprocess.CompletedProcess[str]]
OpenFileSnapshot = Callable[[tuple[Path, ...]], frozenset[FileIdentity]]


@dataclass(frozen=True)
class CleanupResult:
    deleted: int = 0
    native_eligible: int = 0
    warnings: tuple[str, ...] = ()


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
    warnings = list(inventory.warnings)
    if inventory.client != "codex":
        warnings.append(f"cannot run Codex cleanup for {inventory.client} inventory")
        return CleanupResult(warnings=tuple(warnings))
    if not inventory.codex_targets:
        return CleanupResult(warnings=tuple(warnings))

    if _current_session_paths(inventory.session_dirs) != inventory.scanned_paths:
        warnings.append("Codex session paths changed after inventory; cleanup skipped")
        return CleanupResult(warnings=tuple(warnings))

    for target in inventory.codex_targets:
        if not _target_unchanged(target):
            warnings.append(
                f"Codex session {target.root_id} changed after inventory; cleanup skipped"
            )
            return CleanupResult(warnings=tuple(warnings))

    try:
        open_identities = open_file_snapshot(inventory.session_dirs)
    except (ActiveSessionScanError, OSError) as exc:
        warnings.append(f"active session scan unavailable: {exc}; cleanup skipped")
        return CleanupResult(warnings=tuple(warnings))

    safe_targets: list[CodexCleanupTarget] = []
    for target in inventory.codex_targets:
        if any(
            session_file.fingerprint.identity in open_identities
            for session_file in target.files
        ):
            warnings.append(
                f"Codex session {target.root_id} is currently open; cleanup skipped"
            )
            continue
        safe_targets.append(target)
    if not safe_targets:
        return CleanupResult(warnings=tuple(warnings))

    first_home = safe_targets[0].owners[0].codex_home
    supported, detail = _run_codex_command(
        [codex_binary, "delete", "--help"],
        codex_home=first_home,
        runner=runner,
    )
    if not supported:
        warnings.append(f"Codex CLI does not support session delete: {detail}")
        return CleanupResult(warnings=tuple(warnings))

    deleted = 0
    for target in safe_targets:
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

    return CleanupResult(deleted=deleted, warnings=tuple(warnings))
