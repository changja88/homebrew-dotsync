"""Discovery and safe deletion of Codex and Claude auto-memory stores."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .agent_paths import (
    lexical_claude_config_dir,
    lexical_codex_homes,
    paths_refer_to_same_file,
)


CODEX_SCOPE = "all known Codex homes"
CLAUDE_SCOPE = "all Claude auto-memory stores"


RunCommand = Callable[..., subprocess.CompletedProcess[str]]
RemoveTree = Callable[[Path], None]


@dataclass(frozen=True)
class MemoryStore:
    path: Path
    source: str
    file_count: int


@dataclass(frozen=True)
class MemoryInventory:
    client: str
    stores: tuple[MemoryStore, ...]
    file_count: int
    scope: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClientProcess:
    pid: int
    ppid: int
    command: str


@dataclass(frozen=True)
class MemoryDeleteResult:
    deleted_stores: int = 0
    deleted_files: int = 0
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


def running_client_processes(
    client: str,
    *,
    run_command: RunCommand = subprocess.run,
    current_pid: int | None = None,
) -> tuple[ClientProcess, ...]:
    if client not in {"codex", "claude"}:
        raise ValueError(f"unsupported client: {client}")

    command = ["/bin/ps", "-axo", "pid=,ppid=,comm=,args="]
    result = run_command(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or f"exit {result.returncode}"
        raise RuntimeError(f"cannot inspect running processes: {detail}")

    entries: list[tuple[ClientProcess, str]] = []
    parents: dict[int, int] = {}
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=3)
        if len(fields) < 3:
            continue
        try:
            pid = int(fields[0])
            ppid = int(fields[1])
        except ValueError:
            continue
        executable = fields[2]
        process_command = fields[3] if len(fields) == 4 else executable
        process = ClientProcess(
            pid=pid,
            ppid=ppid,
            command=process_command,
        )
        entries.append((process, executable))
        parents[pid] = ppid

    excluded_pids: set[int] = set()
    ancestor_pid = os.getpid() if current_pid is None else current_pid
    while ancestor_pid > 0 and ancestor_pid not in excluded_pids:
        excluded_pids.add(ancestor_pid)
        ancestor_pid = parents.get(ancestor_pid, 0)

    return tuple(
        process
        for process, executable in entries
        if process.pid not in excluded_pids
        and _matches_client_process(
            client,
            executable=executable,
            command=process.command,
        )
    )


def delete_all_memory(
    *,
    client: str,
    home: Path,
    codex_home: Path,
    claude_config_dir: Path | None = None,
    orca_codex_home: Path | None = None,
    run_command: RunCommand = subprocess.run,
    remove_tree: RemoveTree = shutil.rmtree,
) -> MemoryDeleteResult:
    inventory = scan_memory_inventory(
        client=client,
        home=home,
        codex_home=codex_home,
        claude_config_dir=claude_config_dir,
        orca_codex_home=orca_codex_home,
    )
    if inventory.warnings:
        return MemoryDeleteResult(
            error="memory scan unsafe: " + "; ".join(inventory.warnings)
        )

    try:
        conflicts = running_client_processes(
            client,
            run_command=run_command,
            current_pid=os.getpid(),
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return MemoryDeleteResult(error=f"process scan failed: {exc}")
    if conflicts:
        return MemoryDeleteResult(
            error=f"{len(conflicts)} running {client.title()} process(es)"
        )

    for store in inventory.stores:
        error = _store_validation_error(
            store=store,
            client=client,
            home=home,
            claude_config_dir=claude_config_dir,
        )
        if error is not None:
            return MemoryDeleteResult(error=error)

    deleted_stores = 0
    deleted_files = 0
    for store in inventory.stores:
        error = _store_validation_error(
            store=store,
            client=client,
            home=home,
            claude_config_dir=claude_config_dir,
        )
        if error is not None:
            return MemoryDeleteResult(
                deleted_stores=deleted_stores,
                deleted_files=deleted_files,
                error=error,
            )
        try:
            remove_tree(store.path)
        except OSError as exc:
            return MemoryDeleteResult(
                deleted_stores=deleted_stores,
                deleted_files=deleted_files,
                error=f"{store.path}: {exc}",
            )
        deleted_stores += 1
        deleted_files += store.file_count
    return MemoryDeleteResult(deleted_stores, deleted_files)


def _matches_client_process(
    client: str,
    *,
    executable: str,
    command: str,
) -> bool:
    tokens = _command_tokens(command)
    executable_paths = [executable]
    if tokens:
        executable_paths.append(tokens[0])

    if client == "claude" and any(
        "/claude.app/contents/" in path.lower()
        for path in executable_paths
    ):
        return False

    executable_names = {
        Path(path).name.lower()
        for path in executable_paths
        if path
    }
    if client == "codex" and any(
        name == "codex"
        or (
            name.startswith("codex-")
            and name.endswith("-apple-darwin")
        )
        for name in executable_names
    ):
        return True
    if client == "claude" and "claude" in executable_names:
        return True

    if not executable_names.intersection({"node", "nodejs"}):
        return False
    scripts = [token.lower() for token in tokens[1:]]
    if client == "codex":
        return any(
            "/@openai/codex/" in script
            and script.endswith("/codex.js")
            for script in scripts
        )
    return any(
        "/@anthropic-ai/claude-code/" in script
        and script.endswith("/cli.js")
        for script in scripts
    )


def _command_tokens(command: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(command))
    except ValueError:
        return tuple(command.split())


def _store_validation_error(
    *,
    store: MemoryStore,
    client: str,
    home: Path,
    claude_config_dir: Path | None,
) -> str | None:
    warnings: list[str] = []
    if _has_symlink_component(store.path, warnings):
        return "memory store unsafe: " + "; ".join(warnings)

    kind = _path_kind(
        store.path,
        label="memory store",
        warnings=warnings,
    )
    if kind == "missing":
        warnings.append(f"memory store is missing: {store.path}")
    if kind != "directory":
        return "memory store unsafe: " + "; ".join(warnings)

    if client == "codex":
        if store.source != "codex-home" or store.path.name != "memories":
            warnings.append(f"invalid Codex memory store: {store.path}")
    elif client == "claude":
        config_dir = lexical_claude_config_dir(
            home=home,
            claude_config_dir=claude_config_dir,
        ).resolve(strict=False)
        if _is_unsafe_broad_path(
            store.path,
            home=home.resolve(strict=False),
            config_dir=config_dir,
        ):
            warnings.append(f"unsafe broad memory store: {store.path}")
        elif store.source == "claude-project":
            projects_dir = config_dir / "projects"
            if (
                store.path.name != "memory"
                or store.path.parent.parent != projects_dir
            ):
                warnings.append(f"invalid Claude project memory store: {store.path}")
        elif store.source == "claude-settings":
            _valid_configured_store(store.path, warnings)
        else:
            warnings.append(f"invalid Claude memory store: {store.path}")
    else:
        warnings.append(f"unsupported client: {client}")

    _count_regular_files(store.path, warnings)
    if warnings:
        return "memory store unsafe: " + "; ".join(warnings)
    return None


def scan_memory_inventory(
    *,
    client: str,
    home: Path,
    codex_home: Path,
    claude_config_dir: Path | None = None,
    orca_codex_home: Path | None = None,
) -> MemoryInventory:
    if client == "codex":
        return _scan_codex_memory(
            home=home,
            codex_home=codex_home,
            orca_codex_home=orca_codex_home,
        )
    if client == "claude":
        return _scan_claude_memory(
            home=home,
            claude_config_dir=claude_config_dir,
        )
    raise ValueError(f"unsupported client: {client}")


def _scan_codex_memory(
    *,
    home: Path,
    codex_home: Path,
    orca_codex_home: Path | None,
) -> MemoryInventory:
    homes, _, _ = lexical_codex_homes(
        home=home,
        codex_home=codex_home,
        orca_codex_home=orca_codex_home,
    )
    warnings: list[str] = []
    discovered_stores: list[MemoryStore] = []
    seen_homes: list[Path] = []
    for candidate_home in homes:
        if _has_symlink_component(candidate_home, warnings):
            continue
        candidate_home = candidate_home.resolve(strict=False)
        if any(
            paths_refer_to_same_file(candidate_home, seen)
            for seen in seen_homes
        ):
            continue
        seen_homes.append(candidate_home)
        store = _inspect_store(
            candidate_home / "memories",
            source="codex-home",
            warnings=warnings,
        )
        if store is not None:
            discovered_stores.append(store)
    stores = tuple(discovered_stores)
    return MemoryInventory(
        client="codex",
        stores=stores,
        file_count=sum(store.file_count for store in stores),
        scope=CODEX_SCOPE,
        warnings=tuple(warnings),
    )


def _scan_claude_memory(
    *,
    home: Path,
    claude_config_dir: Path | None,
) -> MemoryInventory:
    config_dir = lexical_claude_config_dir(
        home=home,
        claude_config_dir=claude_config_dir,
    )
    warnings: list[str] = []
    if _has_symlink_component(config_dir, warnings):
        return MemoryInventory(
            client="claude",
            stores=(),
            file_count=0,
            scope=CLAUDE_SCOPE,
            warnings=tuple(warnings),
        )
    config_dir = config_dir.resolve(strict=False)
    stores: list[MemoryStore] = []

    for memory_path in _project_memory_paths(config_dir, warnings):
        store = _inspect_store(
            memory_path,
            source="claude-project",
            warnings=warnings,
        )
        if store is not None and not any(
            paths_refer_to_same_file(store.path, seen.path)
            for seen in stores
        ):
            stores.append(store)

    custom_path = _configured_memory_path(
        home=home,
        config_dir=config_dir,
        warnings=warnings,
    )
    if custom_path is not None:
        store = _inspect_store(
            custom_path,
            source="claude-settings",
            warnings=warnings,
        )
        if store is not None and not any(
            paths_refer_to_same_file(store.path, seen.path)
            for seen in stores
        ):
            stores.append(store)

    memory_stores = tuple(stores)
    return MemoryInventory(
        client="claude",
        stores=memory_stores,
        file_count=sum(store.file_count for store in memory_stores),
        scope=CLAUDE_SCOPE,
        warnings=tuple(warnings),
    )


def _project_memory_paths(
    config_dir: Path,
    warnings: list[str],
) -> tuple[Path, ...]:
    projects_dir = config_dir / "projects"
    kind = _path_kind(projects_dir, label="Claude projects", warnings=warnings)
    if kind == "missing":
        return ()
    if kind != "directory":
        return ()

    try:
        with os.scandir(projects_dir) as entries:
            projects = sorted(entries, key=lambda entry: entry.name)
    except OSError as exc:
        warnings.append(f"cannot read Claude projects {projects_dir}: {exc}")
        return ()

    paths: list[Path] = []
    for project in projects:
        try:
            if project.is_symlink():
                warnings.append(f"Claude project path is a symlink: {project.path}")
                continue
            if not project.is_dir(follow_symlinks=False):
                continue
        except OSError as exc:
            warnings.append(f"cannot inspect Claude project {project.path}: {exc}")
            continue
        paths.append(Path(project.path) / "memory")
    return tuple(paths)


def _configured_memory_path(
    *,
    home: Path,
    config_dir: Path,
    warnings: list[str],
) -> Path | None:
    settings_path = config_dir / "settings.json"
    kind = _path_kind(settings_path, label="Claude settings", warnings=warnings)
    if kind == "missing":
        return None
    if kind != "file":
        if kind == "directory":
            warnings.append(
                f"Claude settings is not a regular file: {settings_path}"
            )
        return None

    try:
        with settings_path.open(encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        warnings.append(f"invalid Claude settings {settings_path}: {exc}")
        return None

    if not isinstance(settings, dict):
        warnings.append(
            f"invalid Claude settings {settings_path}: expected an object"
        )
        return None
    if "autoMemoryDirectory" not in settings:
        return None

    raw_path = settings["autoMemoryDirectory"]
    if not isinstance(raw_path, str) or not raw_path:
        warnings.append("Claude autoMemoryDirectory must be absolute or start with ~/")
        return None
    if raw_path.startswith("~/"):
        remainder = raw_path[2:]
        relative_path = Path(remainder)
        if not remainder or relative_path.is_absolute():
            warnings.append(
                "Claude autoMemoryDirectory after ~/ must be a relative path"
            )
            return None
        candidate = home.absolute() / relative_path
    else:
        candidate = Path(raw_path)
    if not candidate.is_absolute():
        warnings.append("Claude autoMemoryDirectory must be absolute or start with ~/")
        return None

    if _has_parent_traversal(candidate, warnings):
        return None
    if _has_symlink_component(candidate, warnings):
        return None
    if _is_unsafe_broad_path(
        candidate,
        home=home.resolve(strict=False),
        config_dir=config_dir,
    ):
        warnings.append(
            f"Claude autoMemoryDirectory is an unsafe broad path: {candidate}"
        )
        return None
    return candidate


def _is_unsafe_broad_path(
    path: Path,
    *,
    home: Path,
    config_dir: Path,
) -> bool:
    projects_dir = config_dir / "projects"
    unsafe_paths = tuple(
        dict.fromkeys(
            (
                Path("/"),
                home,
                config_dir,
                projects_dir,
                *home.parents,
                *config_dir.parents,
                *projects_dir.parents,
            )
        )
    )
    if path in unsafe_paths:
        return True
    try:
        return any(
            paths_refer_to_same_file(path, unsafe_path)
            for unsafe_path in unsafe_paths
        )
    except (OSError, ValueError, UnicodeError):
        return True


def _inspect_store(
    path: Path,
    *,
    source: str,
    warnings: list[str],
) -> MemoryStore | None:
    if _has_symlink_component(path, warnings):
        return None
    kind = _path_kind(path, label="memory store", warnings=warnings)
    if kind == "missing":
        return None
    if kind != "directory":
        return None

    if source == "claude-settings" and not _valid_configured_store(
        path,
        warnings,
    ):
        return None
    file_count = _count_regular_files(path, warnings)
    return MemoryStore(path=path, source=source, file_count=file_count)


def _valid_configured_store(path: Path, warnings: list[str]) -> bool:
    try:
        with os.scandir(path) as entries:
            is_empty = next(entries, None) is None
    except OSError as exc:
        warnings.append(f"cannot read configured memory store {path}: {exc}")
        return False
    if is_empty:
        return True

    marker = path / "MEMORY.md"
    try:
        mode = marker.lstat().st_mode
    except (OSError, ValueError):
        mode = 0
    if stat.S_ISREG(mode):
        return True
    warnings.append(
        f"non-empty configured memory store requires MEMORY.md: {path}"
    )
    return False


def _has_symlink_component(path: Path, warnings: list[str]) -> bool:
    if _has_parent_traversal(path, warnings):
        return True
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return False
        except (OSError, ValueError, UnicodeError) as exc:
            warnings.append(f"cannot inspect memory path {current!r}: {exc}")
            return True
        if stat.S_ISLNK(mode):
            warnings.append(f"memory path contains a symlink: {current}")
            return True
    return False


def _has_parent_traversal(path: Path, warnings: list[str]) -> bool:
    if ".." not in path.parts:
        return False
    warnings.append(f"memory path contains unsafe parent traversal: {path}")
    return True


def _path_kind(
    path: Path,
    *,
    label: str,
    warnings: list[str],
) -> str:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError, UnicodeError) as exc:
        warnings.append(f"cannot inspect {label} {path!r}: {exc}")
        return "error"

    if stat.S_ISLNK(mode):
        warnings.append(f"{label} is a symlink: {path}")
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        if label != "Claude settings":
            warnings.append(f"{label} is not a directory: {path}")
        return "file"
    warnings.append(f"{label} has an unsupported file type: {path}")
    return "other"


def _count_regular_files(path: Path, warnings: list[str]) -> int:
    count = 0

    def record_walk_error(exc: OSError) -> None:
        warnings.append(f"cannot read memory store {exc.filename or path}: {exc}")

    for root, dirnames, filenames in os.walk(
        path,
        topdown=True,
        onerror=record_walk_error,
        followlinks=False,
    ):
        dirnames.sort()
        filenames.sort()
        safe_dirnames: list[str] = []
        for dirname in dirnames:
            directory = Path(root) / dirname
            kind = _path_kind(
                directory,
                label="memory entry",
                warnings=warnings,
            )
            if kind == "directory":
                safe_dirnames.append(dirname)
        dirnames[:] = safe_dirnames

        for filename in filenames:
            file_path = Path(root) / filename
            try:
                mode = file_path.lstat().st_mode
            except (OSError, ValueError) as exc:
                warnings.append(
                    f"cannot inspect memory entry {file_path!r}: {exc}"
                )
                continue
            if stat.S_ISLNK(mode):
                warnings.append(f"memory entry is a symlink: {file_path}")
            elif stat.S_ISREG(mode):
                count += 1
            else:
                warnings.append(
                    f"memory entry has an unsupported file type: {file_path}"
                )
    return count
