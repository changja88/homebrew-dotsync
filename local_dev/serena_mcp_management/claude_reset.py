"""Full reset of local Claude Code CLI conversation state."""
from __future__ import annotations

import json
import os
import plistlib
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .agent_paths import (
    is_unsafe_shared_storage_root,
    lexical_claude_config_dir,
)
from .memory_management import (
    ClientProcess,
    MemoryDeleteResult,
    MemoryInventory,
    configured_memory_path_from_document,
    delete_all_memory,
    running_client_processes,
    scan_memory_inventory,
)
from .safe_delete import (
    SafeDeleteError,
    delete_directory_tree,
    directory_is_empty_no_follow,
    read_file_bytes_no_follow,
    read_json_file_no_follow,
    remove_json_object_key,
    tree_digest_no_follow,
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
ManagedPolicyChecker = Callable[..., str | None]
GitCheckoutRootsResolver = Callable[
    [Path],
    tuple[tuple[Path, ...], str | None],
]

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

_PRESERVED_USER_DATA_NAMES = (
    ".credentials.json",
    "credentials.json",
    ".mcp.json",
    "mcp.json",
    "settings.local.json",
    "CLAUDE.md",
    "keybindings.json",
    "policy-limits.json",
    "remote-settings.json",
    "stats-cache.json",
    "plugins/data",
    "skills",
    "commands",
    "hooks",
    "agents",
    "rules",
    "output-styles",
    "themes",
    "workflows",
)

_VOLATILE_GLOBAL_CONFIG_KEYS = frozenset(
    {
        "cachedExperimentData",
        "cachedExperimentFeatures",
        "cachedGrowthBookFeatures",
        "cachedGrowthBookFeaturesAt",
    }
)

_PURGE_NO_MATCH_EXIT_CODE = 1

_MACOS_MANAGED_POLICY_DIR = Path("/Library/Application Support/ClaudeCode")


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
    project_paths: tuple[Path, ...] = ()


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


@dataclass(frozen=True)
class _BackupSanitization:
    sanitized: int = 0
    error: str | None = None


@dataclass(frozen=True)
class _BackupSnapshot:
    path: Path
    non_project_values: dict[str, object]
    project_paths: tuple[Path, ...]


@dataclass(frozen=True)
class _PreservedPathSnapshot:
    path: Path
    digest: str


def _read_macos_managed_defaults() -> object:
    command = [
        "/usr/bin/defaults",
        "export",
        "com.anthropic.claudecode",
        "-",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or b"").decode(errors="replace").strip()
        if "does not exist" in detail.lower():
            return {}
        raise RuntimeError(
            "cannot inspect Claude managed preferences: "
            + (detail or f"exit {result.returncode}")
        )
    try:
        return plistlib.loads(result.stdout)
    except (plistlib.InvalidFileException, ValueError) as exc:
        raise RuntimeError(f"invalid Claude managed preferences: {exc}") from exc


def _policy_contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        if key in value:
            return True
        return any(_policy_contains_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_policy_contains_key(child, key) for child in value)
    if isinstance(value, str) and value.lstrip().startswith("{"):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return False
        return _policy_contains_key(decoded, key)
    return False


def _read_managed_policy_json(path: Path) -> object:
    try:
        return read_json_file_no_follow(path)
    except SafeDeleteError as exc:
        raise RuntimeError(f"invalid Claude managed policy {path}: {exc}") from exc


def _managed_auto_memory_policy_error(
    *,
    config_dir: Path | None = None,
    policy_dir: Path = _MACOS_MANAGED_POLICY_DIR,
    defaults_reader: Callable[[], object] = _read_macos_managed_defaults,
) -> str | None:
    """Fail closed when higher-precedence policy can redirect auto-memory."""

    documents: list[tuple[str, object]] = []
    base = policy_dir / "managed-settings.json"
    try:
        base.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError(
            f"cannot inspect Claude managed policy {base}: {exc}"
        ) from exc
    else:
        documents.append((str(base), _read_managed_policy_json(base)))

    drop_in_dir = policy_dir / "managed-settings.d"
    try:
        drop_in_mode = drop_in_dir.lstat().st_mode
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError(
            f"cannot inspect Claude managed policy directory {drop_in_dir}: {exc}"
        ) from exc
    else:
        if stat.S_ISLNK(drop_in_mode) or not stat.S_ISDIR(drop_in_mode):
            raise RuntimeError(
                f"unsafe Claude managed policy directory: {drop_in_dir}"
            )
        try:
            entries = sorted(drop_in_dir.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise RuntimeError(
                f"cannot read Claude managed policy directory {drop_in_dir}: {exc}"
            ) from exc
        for entry in entries:
            if entry.name.startswith(".") or entry.suffix != ".json":
                continue
            documents.append((str(entry), _read_managed_policy_json(entry)))

    documents.append(("macOS managed preferences", defaults_reader()))
    if config_dir is not None:
        remote_cache = config_dir / "remote-settings.json"
        try:
            remote_cache.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeError(
                "cannot inspect Claude server-managed settings cache "
                f"{remote_cache}: {exc}"
            ) from exc
        else:
            try:
                remote_document = _read_managed_policy_json(remote_cache)
            except RuntimeError as exc:
                raise RuntimeError(
                    "cannot inspect Claude server-managed settings cache "
                    f"{remote_cache}: {exc}"
                ) from exc
            documents.append(
                (
                    f"Claude server-managed settings cache {remote_cache}",
                    remote_document,
                )
            )
    for source, document in documents:
        if _policy_contains_key(document, "policyHelper"):
            return (
                "Claude managed policyHelper is active; effective "
                "autoMemoryDirectory cannot be verified safely: "
                f"{source}"
            )
        if _policy_contains_key(document, "plansDirectory"):
            return (
                "Claude managed plansDirectory is active; custom project-relative "
                f"plan stores cannot be reset safely: {source}"
            )
        if _policy_contains_key(document, "autoMemoryDirectory"):
            return (
                "Claude managed autoMemoryDirectory is active and cannot be "
                f"reset safely: {source}"
            )
    return None


def _project_paths_from_mapping(
    projects: dict[str, object],
    *,
    source: Path,
) -> tuple[tuple[Path, ...], str | None]:
    paths: list[Path] = []
    for raw_path in projects:
        if not isinstance(raw_path, str):
            return (), f"invalid Claude project key in {source}: expected string"
        project_path = Path(raw_path)
        if not project_path.is_absolute():
            return (), f"invalid Claude project key in {source}: {raw_path!r}"
        paths.append(project_path.absolute())
    return tuple(paths), None


def _settings_plans_directory_error(path: Path, *, label: str) -> str | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"cannot inspect {label} {path}: {exc}"
    try:
        payload = read_json_file_no_follow(path)
    except SafeDeleteError as exc:
        return f"invalid {label} {path}: {exc}"
    if not isinstance(payload, dict):
        return f"invalid {label} {path}: expected object"
    if "plansDirectory" in payload:
        return (
            f"Claude plansDirectory is configured in {label} {path}; "
            "custom project-relative plan stores cannot be proven complete "
            "and reset safely"
        )
    return None


def _settings_snapshot_plans_directory_error(
    snapshot: _FileSnapshot,
    *,
    label: str,
) -> str | None:
    if not snapshot.existed:
        return None
    try:
        payload = json.loads(snapshot.content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        return f"invalid {label} {snapshot.path}: {exc}"
    if not isinstance(payload, dict):
        return f"invalid {label} {snapshot.path}: expected object"
    if "plansDirectory" in payload:
        return (
            f"Claude plansDirectory is configured in {label} {snapshot.path}; "
            "custom project-relative plan stores cannot be proven complete "
            "and reset safely"
        )
    return None


def _known_project_plans_directory_error(
    project_paths: tuple[Path, ...],
) -> str | None:
    for project_path in dict.fromkeys(project_paths):
        for filename in ("settings.json", "settings.local.json"):
            settings_path = project_path / ".claude" / filename
            error = _settings_plans_directory_error(
                settings_path,
                label="Claude project settings",
            )
            if error is not None:
                return error
    return None


def _git_checkout_roots(
    project_path: Path,
) -> tuple[tuple[Path, ...], str | None]:
    try:
        result = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(project_path),
                "rev-parse",
                "--path-format=absolute",
                "--show-toplevel",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return (), f"cannot inspect git roots for {project_path}: {exc}"
    if result.returncode != 0:
        git_marker = next(
            (
                candidate / ".git"
                for candidate in (project_path, *project_path.parents)
                if (candidate / ".git").exists()
                or (candidate / ".git").is_symlink()
            ),
            None,
        )
        if git_marker is not None:
            detail = (result.stderr or "").strip() or (
                f"exit {result.returncode}"
            )
            return (), (
                f"cannot resolve git checkout roots for {project_path} "
                f"despite marker {git_marker}: {detail}"
            )
        return (), None
    lines = tuple(
        line.strip()
        for line in (result.stdout or "").splitlines()
        if line.strip()
    )
    if len(lines) != 2:
        return (), f"git returned unexpected checkout roots for {project_path}"
    worktree_root = Path(lines[0])
    common_dir = Path(lines[1])
    if not worktree_root.is_absolute() or not common_dir.is_absolute():
        return (), f"git returned relative checkout roots for {project_path}"
    return (
        tuple(
            dict.fromkeys(
                (worktree_root.absolute(), common_dir.absolute().parent)
            )
        ),
        None,
    )


def _expand_known_project_paths(
    project_paths: tuple[Path, ...],
    *,
    git_checkout_roots_resolver: GitCheckoutRootsResolver,
) -> tuple[tuple[Path, ...], str | None]:
    expanded: list[Path] = []
    for project_path in dict.fromkeys(project_paths):
        expanded.append(project_path)
        checkout_roots, error = git_checkout_roots_resolver(project_path)
        if error is not None:
            return (), error
        expanded.extend(checkout_roots)
    return tuple(dict.fromkeys(expanded)), None


def _config_root_error(config_dir: Path, *, home: Path) -> str | None:
    broad_paths = {Path("/"), home.absolute(), *home.absolute().parents}
    if config_dir in broad_paths or is_unsafe_shared_storage_root(
        config_dir,
        home=home,
    ):
        return f"Claude config path is unsafe and too broad: {config_dir}"
    try:
        mode = config_dir.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"cannot inspect Claude config directory {config_dir}: {exc}"
    if stat.S_ISLNK(mode):
        return f"Claude config directory is a symlink: {config_dir}"
    if not stat.S_ISDIR(mode):
        return f"Claude config path is not a directory: {config_dir}"
    try:
        owner = config_dir.stat(follow_symlinks=False).st_uid
    except OSError as exc:
        return f"cannot inspect Claude config directory {config_dir}: {exc}"
    if owner != os.geteuid():
        return f"Claude config directory is not owned by this user: {config_dir}"
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
        stat_result = target.path.lstat()
        mode = stat_result.st_mode
    except FileNotFoundError:
        return "missing", None
    except OSError as exc:
        return None, f"cannot inspect Claude supplemental target {target.path}: {exc}"
    if stat.S_ISDIR(mode):
        if stat_result.st_uid != os.geteuid():
            return (
                None,
                "Claude supplemental target is not owned by this user: "
                f"{target.path}",
            )
        return "directory", None
    if stat.S_ISLNK(mode):
        return "symlink", None
    return None, f"Claude supplemental target is not a directory: {target.path}"


def _delete_supplemental_targets(
    targets: tuple[_SupplementalTarget, ...],
    *,
    remove_tree: Callable[[Path], None] | None = None,
    unlink_path: Callable[[Path], None] | None = None,
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
                if unlink_path is None:
                    delete_directory_tree(
                        target.path,
                        allow_final_symlink=True,
                    )
                else:
                    unlink_path(target.path)
            else:
                (remove_tree or delete_directory_tree)(target.path)
        except (OSError, SafeDeleteError) as exc:
            return _SupplementalDeletion(
                deleted=deleted,
                error=f"cannot delete Claude supplemental target {target.path}: {exc}",
            )
        deleted += 1
    return _SupplementalDeletion(deleted=deleted)


def _backup_snapshots(
    config_dir: Path,
) -> tuple[tuple[_BackupSnapshot, ...], str | None]:
    backups = config_dir / "backups"
    try:
        backup_stat = backups.lstat()
    except FileNotFoundError:
        return (), None
    except OSError as exc:
        return (), f"cannot inspect Claude backup directory {backups}: {exc}"
    if stat.S_ISLNK(backup_stat.st_mode) or not stat.S_ISDIR(backup_stat.st_mode):
        return (), f"unsafe Claude backup directory: {backups}"
    if backup_stat.st_uid != os.geteuid():
        return (), f"Claude backup directory is not owned by this user: {backups}"
    try:
        entries = sorted(backups.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        return (), f"cannot read Claude backup directory {backups}: {exc}"

    snapshots: list[_BackupSnapshot] = []
    prefix = ".claude.json.backup."
    for entry in entries:
        if not entry.name.startswith(prefix) or entry.name == prefix:
            continue
        try:
            payload = read_json_file_no_follow(entry)
        except SafeDeleteError as exc:
            return (), f"invalid Claude global config backup {entry}: {exc}"
        if not isinstance(payload, dict):
            return (), f"invalid Claude global config backup {entry}: expected object"
        projects = payload.get("projects")
        if projects is not None and not isinstance(projects, dict):
            return (), (
                f"invalid Claude global config backup {entry}: "
                "projects must be an object"
            )
        project_paths, project_paths_error = _project_paths_from_mapping(
            projects or {},
            source=entry,
        )
        if project_paths_error is not None:
            return (), project_paths_error
        snapshots.append(
            _BackupSnapshot(
                path=entry,
                non_project_values={
                    key: value
                    for key, value in payload.items()
                    if key != "projects"
                },
                project_paths=project_paths,
            )
        )
    return tuple(snapshots), None


def _backup_targets(
    config_dir: Path,
) -> tuple[tuple[Path, ...], str | None]:
    snapshots, error = _backup_snapshots(config_dir)
    return tuple(snapshot.path for snapshot in snapshots), error


def _snapshot_unrelated_backup_data(
    config_dir: Path,
) -> tuple[tuple[_PreservedPathSnapshot, ...], str | None]:
    backups = config_dir / "backups"
    try:
        entries = sorted(backups.iterdir(), key=lambda path: path.name)
    except FileNotFoundError:
        return (), None
    except OSError as exc:
        return (), f"cannot read Claude backup directory {backups}: {exc}"
    prefix = ".claude.json.backup."
    snapshots: list[_PreservedPathSnapshot] = []
    for entry in entries:
        if entry.name.startswith(prefix) and entry.name != prefix:
            continue
        try:
            digest = tree_digest_no_follow(entry)
        except SafeDeleteError as exc:
            return (), f"cannot snapshot unrelated Claude backup {entry}: {exc}"
        snapshots.append(_PreservedPathSnapshot(entry, digest))
    return tuple(snapshots), None


def _backup_preservation_errors(
    snapshots: tuple[_BackupSnapshot, ...],
) -> tuple[str, ...]:
    errors: list[str] = []
    for snapshot in snapshots:
        try:
            snapshot.path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(
                f"cannot inspect Claude global config backup {snapshot.path}: {exc}"
            )
            continue
        try:
            payload = read_json_file_no_follow(snapshot.path)
        except SafeDeleteError as exc:
            errors.append(
                f"invalid Claude global config backup {snapshot.path}: {exc}"
            )
            continue
        if not isinstance(payload, dict):
            errors.append(
                f"invalid Claude global config backup {snapshot.path}: expected object"
            )
            continue
        for key, value in snapshot.non_project_values.items():
            if key not in payload or payload[key] != value:
                errors.append(
                    "Claude global config backup changed preserved value "
                    f"{key!r}: {snapshot.path}"
                )
    return tuple(errors)


def _snapshot_preserved_user_data(
    config_dir: Path,
) -> tuple[tuple[_PreservedPathSnapshot, ...], str | None]:
    snapshots: list[_PreservedPathSnapshot] = []
    for name in _PRESERVED_USER_DATA_NAMES:
        path = config_dir / name
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            return (), f"cannot inspect preserved Claude user data {path}: {exc}"
        try:
            digest = tree_digest_no_follow(path)
        except SafeDeleteError as exc:
            return (), f"cannot snapshot preserved Claude user data {path}: {exc}"
        snapshots.append(_PreservedPathSnapshot(path=path, digest=digest))
    return tuple(snapshots), None


def _preserved_user_data_errors(
    snapshots: tuple[_PreservedPathSnapshot, ...],
) -> tuple[str, ...]:
    errors: list[str] = []
    for snapshot in snapshots:
        try:
            snapshot.path.lstat()
        except FileNotFoundError:
            errors.append(
                f"preserved Claude user data disappeared: {snapshot.path}"
            )
            continue
        except OSError as exc:
            errors.append(
                f"cannot inspect preserved Claude user data {snapshot.path}: {exc}"
            )
            continue
        try:
            digest = tree_digest_no_follow(snapshot.path)
        except SafeDeleteError as exc:
            errors.append(
                f"cannot verify preserved Claude user data {snapshot.path}: {exc}"
            )
            continue
        if digest != snapshot.digest:
            errors.append(f"preserved Claude user data changed: {snapshot.path}")
    return tuple(errors)


def _sanitize_backup_project_entries(config_dir: Path) -> _BackupSanitization:
    targets, error = _backup_targets(config_dir)
    if error is not None:
        return _BackupSanitization(error=error)
    sanitized = 0
    for target in targets:
        try:
            changed = remove_json_object_key(target, key="projects")
        except SafeDeleteError as exc:
            return _BackupSanitization(
                sanitized=sanitized,
                error=str(exc),
            )
        if changed:
            sanitized += 1
    return _BackupSanitization(sanitized=sanitized)


def _backup_project_residuals(config_dir: Path) -> tuple[str, ...]:
    snapshots, error = _backup_snapshots(config_dir)
    if error is not None:
        return (error,)
    return tuple(
        "Claude global config backup still contains project entries: "
        f"{snapshot.path}"
        for snapshot in snapshots
        if snapshot.project_paths
    )


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
        payload = read_json_file_no_follow(path)
    except SafeDeleteError as exc:
        return None, f"invalid Claude global config {path}: {exc}"
    if not isinstance(payload, dict):
        return None, f"invalid Claude global config {path}: expected an object"
    projects = payload.get("projects")
    if projects is not None and not isinstance(projects, dict):
        return None, f"invalid Claude global config {path}: projects must be an object"
    project_paths, project_paths_error = _project_paths_from_mapping(
        projects or {},
        source=path,
    )
    if project_paths_error is not None:
        return None, project_paths_error
    return (
        _GlobalConfigSnapshot(
            path=path,
            existed=True,
            non_project_values={
                key: value
                for key, value in payload.items()
                if key != "projects" and key not in _VOLATILE_GLOBAL_CONFIG_KEYS
            },
            project_paths=project_paths,
        ),
        None,
    )


def _snapshot_file(
    path: Path,
    *,
    label: str,
) -> tuple[_FileSnapshot | None, str | None]:
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
        content = read_file_bytes_no_follow(path)
    except SafeDeleteError as exc:
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


def _verify_global_config(
    snapshot: _GlobalConfigSnapshot,
    *,
    check_preserved_values: bool = True,
) -> str | None:
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
        if current.project_paths:
            return (
                "Claude global config still contains project entries: "
                f"{snapshot.path}"
            )
        if not check_preserved_values:
            return None
        for key, value in snapshot.non_project_values.items():
            if (
                key not in current.non_project_values
                or current.non_project_values[key] != value
            ):
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
            if not directory_is_empty_no_follow(path):
                residuals.append(f"official purge target is not empty: {path}")
        except SafeDeleteError as exc:
            residuals.append(
                f"cannot safely read official purge target {path}: {exc}"
            )

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
    current_project_root: Path | None = None,
    run_command: RunCommand = subprocess.run,
    _process_scanner: ProcessScanner = running_client_processes,
    _identity_reader: IdentityReader = process_identity,
    _process_terminator: ProcessTerminator = terminate_pid,
    _process_alive: ProcessAlive = pid_is_alive,
    _session_scanner: SessionScanner = scan_claude_inventory,
    _memory_scanner: MemoryScanner = scan_memory_inventory,
    _memory_deleter: MemoryDeleter = delete_all_memory,
    _remove_tree: Callable[[Path], None] | None = None,
    _unlink_path: Callable[[Path], None] | None = None,
    _managed_policy_checker: ManagedPolicyChecker = (
        _managed_auto_memory_policy_error
    ),
    _git_checkout_roots_resolver: GitCheckoutRootsResolver = (
        _git_checkout_roots
    ),
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
    if current_project_root is not None and not current_project_root.is_absolute():
        return ClaudeResetResult(error="current Claude project root must be absolute")

    try:
        managed_policy_error = _managed_policy_checker(config_dir=config_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        return ClaudeResetResult(
            error=f"cannot verify Claude managed policy: {exc}"
        )
    if managed_policy_error is not None:
        return ClaudeResetResult(error=managed_policy_error)

    settings_snapshot, settings_error = _snapshot_file(
        config_dir / "settings.json",
        label="Claude user settings",
    )
    if settings_error is not None:
        return ClaudeResetResult(error=settings_error)
    assert settings_snapshot is not None
    plans_directory_error = _settings_snapshot_plans_directory_error(
        settings_snapshot,
        label="Claude user settings",
    )
    if plans_directory_error is not None:
        return ClaudeResetResult(error=plans_directory_error)

    if settings_snapshot.existed:
        try:
            settings_document = json.loads(
                settings_snapshot.content.decode("utf-8")
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            return ClaudeResetResult(
                error=(
                    f"invalid Claude user settings {settings_snapshot.path}: {exc}"
                )
            )
    else:
        settings_document = {}
    configured_path_warnings: list[str] = []
    expected_configured_memory_path = configured_memory_path_from_document(
        settings_document,
        home=home,
        config_dir=config_dir,
        warnings=configured_path_warnings,
        settings_path=settings_snapshot.path,
    )
    if configured_path_warnings:
        return ClaudeResetResult(
            error="Claude memory settings unsafe: "
            + "; ".join(configured_path_warnings)
        )

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
    configured_memory_stores = tuple(
        store
        for store in memory_inventory.stores
        if store.source == "claude-settings"
    )
    if len(configured_memory_stores) > 1 or any(
        expected_configured_memory_path is None
        or store.path != expected_configured_memory_path
        for store in configured_memory_stores
    ):
        return ClaudeResetResult(
            error=(
                "Claude autoMemoryDirectory changed during preflight; "
                "no memory store was deleted"
            )
        )
    configured_memory_inventory = MemoryInventory(
        client="claude",
        stores=configured_memory_stores,
        file_count=sum(store.file_count for store in configured_memory_stores),
        scope=memory_inventory.scope,
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
    backup_snapshots, backup_error = _backup_snapshots(config_dir)
    if backup_error is not None:
        return ClaudeResetResult(error=backup_error)
    unrelated_backup_data, unrelated_backup_error = (
        _snapshot_unrelated_backup_data(config_dir)
    )
    if unrelated_backup_error is not None:
        return ClaudeResetResult(error=unrelated_backup_error)
    preserved_user_data, preserved_user_data_error = (
        _snapshot_preserved_user_data(config_dir)
    )
    if preserved_user_data_error is not None:
        return ClaudeResetResult(error=preserved_user_data_error)
    known_project_paths = (
        (() if current_project_root is None else (current_project_root.absolute(),))
        + global_config_snapshot.project_paths
        + tuple(
        project_path
        for backup_snapshot in backup_snapshots
        for project_path in backup_snapshot.project_paths
        )
    )
    known_project_paths, common_root_error = _expand_known_project_paths(
        known_project_paths,
        git_checkout_roots_resolver=_git_checkout_roots_resolver,
    )
    if common_root_error is not None:
        return ClaudeResetResult(error=common_root_error)
    project_plans_error = _known_project_plans_directory_error(
        known_project_paths
    )
    if project_plans_error is not None:
        return ClaudeResetResult(error=project_plans_error)

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
    purge_no_match_error: str | None = None
    if purge_result.returncode != 0:
        detail = (purge_result.stderr or "").strip() or (
            f"exit {purge_result.returncode}"
        )
        purge_error = f"Claude project purge failed: {detail}"
        if purge_result.returncode != _PURGE_NO_MATCH_EXIT_CODE:
            return ClaudeResetResult(
                discovered_sessions=discovered_sessions,
                terminated_processes=termination.terminated,
                warnings=tuple(warnings),
                error=purge_error,
            )
        purge_no_match_error = purge_error

    try:
        managed_policy_error = _managed_policy_checker(config_dir=config_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error=f"cannot reverify Claude managed policy: {exc}",
        )
    if managed_policy_error is not None:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error=managed_policy_error,
        )
    project_plans_error = _known_project_plans_directory_error(
        known_project_paths
    )
    if project_plans_error is not None:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error=project_plans_error,
        )

    official_errors = list(_official_residuals(config_dir))
    global_official_error = _verify_global_config(
        global_config_snapshot,
        check_preserved_values=False,
    )
    if global_official_error is not None:
        official_errors.append(global_official_error)
    if official_errors:
        if purge_no_match_error is not None:
            official_errors.insert(0, purge_no_match_error)
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error="; ".join(official_errors),
        )
    deleted_sessions = discovered_sessions

    preservation_errors: list[str] = []
    global_error = _verify_global_config(global_config_snapshot)
    if global_error is not None:
        preservation_errors.append(global_error)
    settings_changed = _file_unchanged_error(
        settings_snapshot,
        label="Claude user settings",
    )
    if settings_changed is not None:
        preservation_errors.append(settings_changed)
    preservation_errors.extend(_backup_preservation_errors(backup_snapshots))
    preservation_errors.extend(
        _preserved_user_data_errors(unrelated_backup_data)
    )
    preservation_errors.extend(_preserved_user_data_errors(preserved_user_data))
    if preservation_errors:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            deleted_sessions=deleted_sessions,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error="; ".join(preservation_errors),
        )

    backup_result = _sanitize_backup_project_entries(config_dir)
    if backup_result.error is not None:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            deleted_sessions=deleted_sessions,
            deleted_residual_targets=backup_result.sanitized,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error=backup_result.error,
        )

    try:
        memory_delete_kwargs = {
            "client": "claude",
            "home": home,
            "codex_home": home / ".codex",
            "claude_config_dir": config_dir,
            "run_command": run_command,
            "inventory": configured_memory_inventory,
        }
        if _remove_tree is not None:
            memory_delete_kwargs["remove_tree"] = _remove_tree
        memory_result = _memory_deleter(
            **memory_delete_kwargs,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            deleted_sessions=deleted_sessions,
            deleted_residual_targets=backup_result.sanitized,
            terminated_processes=termination.terminated,
            warnings=tuple(warnings),
            error=f"Claude memory deletion failed: {exc}",
        )
    if not memory_result.succeeded:
        return ClaudeResetResult(
            discovered_sessions=discovered_sessions,
            deleted_sessions=deleted_sessions,
            deleted_memory_stores=memory_result.deleted_stores,
            deleted_residual_targets=backup_result.sanitized,
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
            deleted_residual_targets=(
                backup_result.sanitized + supplemental_result.deleted
            ),
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
    verification_errors.extend(_backup_project_residuals(config_dir))
    verification_errors.extend(_backup_preservation_errors(backup_snapshots))
    verification_errors.extend(
        _preserved_user_data_errors(unrelated_backup_data)
    )
    verification_errors.extend(
        _preserved_user_data_errors(preserved_user_data)
    )

    final_memory_verified_empty = False
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
        if not final_memory.warnings and not final_memory.stores:
            final_memory_verified_empty = True

    final_global_error = _verify_global_config(global_config_snapshot)
    if final_global_error is not None:
        verification_errors.append(final_global_error)
    final_settings_error = _file_unchanged_error(
        settings_snapshot,
        label="Claude user settings",
    )
    if final_settings_error is not None:
        verification_errors.append(final_settings_error)

    try:
        final_managed_policy_error = _managed_policy_checker(
            config_dir=config_dir
        )
    except (OSError, RuntimeError, ValueError) as exc:
        verification_errors.append(
            f"cannot finally verify Claude managed policy: {exc}"
        )
    else:
        if final_managed_policy_error is not None:
            verification_errors.append(final_managed_policy_error)

    final_project_plans_error = _known_project_plans_directory_error(
        known_project_paths
    )
    if final_project_plans_error is not None:
        verification_errors.append(final_project_plans_error)

    try:
        final_processes = _process_scanner(
            "claude",
            run_command=run_command,
            current_pid=os.getpid(),
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        verification_errors.append(
            f"cannot reverify final Claude process state: {exc}"
        )
    else:
        if final_processes:
            verification_errors.append(
                f"{len(final_processes)} Claude process(es) remain after reset"
            )

    return ClaudeResetResult(
        discovered_sessions=discovered_sessions,
        deleted_sessions=deleted_sessions,
        deleted_memory_stores=(
            len(memory_inventory.stores)
            if final_memory_verified_empty
            else memory_result.deleted_stores
        ),
        deleted_residual_targets=(
            backup_result.sanitized + supplemental_result.deleted
        ),
        terminated_processes=termination.terminated,
        warnings=tuple(warnings),
        error="; ".join(verification_errors) if verification_errors else None,
    )
