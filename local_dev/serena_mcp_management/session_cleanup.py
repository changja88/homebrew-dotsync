"""Native session cleanup operations for Codex and Claude Code."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from local_dev.serena_mcp_management.session_inventory import (
    ActiveSessionScanError,
    AgentInventory,
    ClaudeSessionPath,
    CodexCleanupTarget,
    FileFingerprint,
    FileIdentity,
    snapshot_active_claude_sessions,
    snapshot_claude_manifest,
    snapshot_open_rollouts,
)


CLAUDE_RETENTION_JSON = '{"cleanupPeriodDays":5}'
DELETE_TIMEOUT_SECONDS = 30


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
    strict = inventory.policy == "all_inactive"
    warnings = list(inventory.warnings)
    if inventory.client != "codex":
        message = f"cannot run Codex cleanup for {inventory.client} inventory"
        if strict:
            return CleanupResult(warnings=tuple(warnings), error=message)
        warnings.append(message)
        return CleanupResult(warnings=tuple(warnings))
    if strict and warnings:
        return CleanupResult(
            warnings=tuple(warnings),
            error="cannot safely inventory every inactive Codex session",
        )
    if not inventory.codex_targets:
        return CleanupResult(warnings=tuple(warnings))

    try:
        current_paths = _current_session_paths(inventory.session_dirs)
    except OSError as exc:
        message = f"cannot revalidate Codex session paths: {exc}"
        if strict:
            return CleanupResult(warnings=tuple(warnings), error=message)
        warnings.append(f"{message}; cleanup skipped")
        return CleanupResult(warnings=tuple(warnings))
    if current_paths != inventory.scanned_paths:
        message = "Codex session paths changed after inventory"
        if strict:
            return CleanupResult(warnings=tuple(warnings), error=message)
        warnings.append(f"{message}; cleanup skipped")
        return CleanupResult(warnings=tuple(warnings))

    for target in inventory.codex_targets:
        if not _target_unchanged(target):
            message = f"Codex session {target.root_id} changed after inventory"
            if strict:
                return CleanupResult(warnings=tuple(warnings), error=message)
            warnings.append(f"{message}; cleanup skipped")
            return CleanupResult(warnings=tuple(warnings))

    try:
        open_identities = open_file_snapshot(inventory.session_dirs)
    except (ActiveSessionScanError, OSError) as exc:
        message = f"active session scan unavailable: {exc}"
        if strict:
            return CleanupResult(warnings=tuple(warnings), error=message)
        warnings.append(f"{message}; cleanup skipped")
        return CleanupResult(warnings=tuple(warnings))

    safe_targets: list[CodexCleanupTarget] = []
    preserved_running = 0
    for target in inventory.codex_targets:
        if any(
            session_file.fingerprint.identity in open_identities
            for session_file in target.files
        ):
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
        for target in safe_targets:
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
        return CleanupResult(
            deleted=deleted,
            preserved_running=preserved_running,
            warnings=tuple(warnings),
        )

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


def cleanup_claude_inventory(
    inventory: AgentInventory,
    *,
    active_session_snapshot: Callable[[Path], frozenset[str]] = (
        snapshot_active_claude_sessions
    ),
    open_file_snapshot: OpenFileSnapshot = snapshot_open_rollouts,
    remove_tree: Callable[[Path], None] = shutil.rmtree,
    unlink: Callable[[Path], None] = Path.unlink,
) -> CleanupResult:
    """Delete only exact, revalidated inactive Claude session bundles."""
    if inventory.client != "claude":
        return CleanupResult(
            error=f"cannot run Claude cleanup for {inventory.client} inventory"
        )
    if inventory.policy != "all_inactive":
        return CleanupResult(error="Claude cleanup requires all_inactive inventory")
    if inventory.warnings:
        return CleanupResult(
            warnings=inventory.warnings,
            error="cannot safely inventory every inactive Claude session",
        )

    config_dir = inventory.claude_config_dir
    if config_dir is None or not config_dir.is_absolute():
        return CleanupResult(error="Claude inventory has no absolute config directory")

    try:
        active_session_ids = active_session_snapshot(config_dir)
    except (ActiveSessionScanError, OSError) as exc:
        return CleanupResult(
            error=f"cannot scan active Claude sessions in {config_dir}: {exc}"
        )
    try:
        open_identities = open_file_snapshot(inventory.session_dirs)
    except (ActiveSessionScanError, OSError) as exc:
        paths = ", ".join(str(path) for path in inventory.session_dirs)
        return CleanupResult(
            error=f"cannot scan open Claude transcripts in {paths}: {exc}"
        )

    preserved_running = 0
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

    for target in inactive_targets:
        try:
            current_manifest = snapshot_claude_manifest(target.roots)
        except (ActiveSessionScanError, OSError) as exc:
            return CleanupResult(
                preserved_running=preserved_running,
                error=(
                    f"cannot validate Claude session {target.session_id} "
                    f"at {target.roots}: {exc}"
                ),
            )
        if current_manifest != target.manifest:
            return CleanupResult(
                preserved_running=preserved_running,
                error=(
                    f"Claude session {target.session_id} changed after inventory "
                    f"at {target.roots}"
                ),
            )

    deleted = 0
    for target in inactive_targets:
        roots = sorted(
            target.roots,
            key=lambda path: (len(path.parts), str(path)),
            reverse=True,
        )
        entries_by_path = {entry.path: entry for entry in target.manifest}
        for root in roots:
            expected_manifest = _manifest_below_root(target.manifest, root)
            try:
                current_manifest = snapshot_claude_manifest((root,))
            except (ActiveSessionScanError, OSError) as exc:
                return CleanupResult(
                    deleted=deleted,
                    preserved_running=preserved_running,
                    error=f"cannot revalidate Claude session path {root}: {exc}",
                )
            if current_manifest != expected_manifest:
                return CleanupResult(
                    deleted=deleted,
                    preserved_running=preserved_running,
                    error=f"Claude session path changed before delete: {root}",
                )

            root_entry = entries_by_path[root]
            try:
                if root_entry.is_directory:
                    remove_tree(root)
                else:
                    unlink(root)
            except OSError as exc:
                return CleanupResult(
                    deleted=deleted,
                    preserved_running=preserved_running,
                    error=f"failed to remove Claude session path {root}: {exc}",
                )
            try:
                root.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                return CleanupResult(
                    deleted=deleted,
                    preserved_running=preserved_running,
                    error=f"cannot verify removal of Claude session path {root}: {exc}",
                )
            return CleanupResult(
                deleted=deleted,
                preserved_running=preserved_running,
                error=f"Claude session path still exists after delete: {root}",
            )
        deleted += 1

    return CleanupResult(
        deleted=deleted,
        preserved_running=preserved_running,
    )
