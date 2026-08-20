"""Path and scope helpers for shared Serena MCP runtime state."""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

SHARED_CONTEXT_PROFILE = "dotsync-shared-cli-v1"
RUNTIME_ROOT_ENV = "SERENA_AGENT_RUNTIME_ROOT"
PROJECT_MARKERS = (
    "AGENTS.md",
    "CLAUDE.md",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "Makefile",
)


@dataclass(frozen=True, slots=True)
class Scope:
    """A Serena server sharing scope."""

    project_root: Path
    context_profile: str = SHARED_CONTEXT_PROFILE

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", self.project_root.resolve())
        if self.context_profile != SHARED_CONTEXT_PROFILE:
            raise ValueError(f"unsupported context profile: {self.context_profile}")

    @property
    def key(self) -> str:
        return f"{self.project_root}::{self.context_profile}"


def find_project_root(cwd: Path) -> Path:
    """Find a project root from a current working directory."""

    current = cwd.resolve()
    candidates = (current, *current.parents)
    for candidate in candidates:
        if (
            (candidate / ".serena" / "project.yml").is_file()
            or (candidate / ".git").exists()
        ):
            return candidate
    for candidate in candidates:
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate
    return current


def serena_opted_in(project_root: Path) -> bool:
    """Return whether a selected project root explicitly enables Serena."""

    return (project_root.resolve() / ".serena" / "project.yml").is_file()


def shared_context_path() -> Path:
    """Return the launcher-owned shared Serena context file."""

    path = Path(__file__).resolve().with_name("contexts") / "oaicompat-agent.yml"
    if not path.is_file():
        raise FileNotFoundError(f"bundled Serena context not found: {path}")
    return path


def state_dir_for(scope: Scope) -> Path:
    """Return the per-scope runtime state directory."""

    runtime_root = _runtime_root_path()
    resolved_runtime_root = runtime_root.resolve(strict=False)
    if (
        resolved_runtime_root == scope.project_root
        or resolved_runtime_root.is_relative_to(scope.project_root)
    ):
        raise ValueError("launcher runtime root must remain outside project root")
    scope_hash = hashlib.sha256(scope.key.encode("utf-8")).hexdigest()
    return runtime_root / scope.context_profile / scope_hash


def runtime_root_path() -> Path:
    """Return the shared launcher runtime root selected for this user."""

    return _runtime_root_path()


def ensure_private_runtime_directory(path: Path) -> None:
    """Create and verify an owner-only, non-symlink runtime directory path."""

    runtime_root = _runtime_root_path()
    if path != runtime_root and not path.is_relative_to(runtime_root):
        raise ValueError(f"runtime path escapes launcher root: {path}")
    _ensure_private_directory(runtime_root)
    relative = path.relative_to(runtime_root)
    current = runtime_root
    for component in relative.parts:
        current /= component
        _ensure_private_directory(current)


def validate_private_runtime_directory(path: Path) -> None:
    """Validate an existing private runtime directory path without creating it."""

    runtime_root = _runtime_root_path()
    if not runtime_root.is_absolute():
        raise ValueError(f"private runtime directory must be absolute: {runtime_root}")
    if path != runtime_root and not path.is_relative_to(runtime_root):
        raise ValueError(f"runtime path escapes launcher root: {path}")
    _validate_private_directory(runtime_root)
    relative = path.relative_to(runtime_root)
    current = runtime_root
    for component in relative.parts:
        current /= component
        _validate_private_directory(current)


def open_private_runtime_file(path: Path, *, append: bool = False) -> TextIO:
    """Open an owner-only regular runtime file without following symlinks."""

    ensure_private_runtime_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_APPEND if append else os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"runtime file is not regular: {path}")
        if info.st_uid != os.geteuid():
            raise PermissionError(f"runtime file is not owned by this user: {path}")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise PermissionError(f"runtime file is not mode 0600: {path}")
        return os.fdopen(fd, "a" if append else "w")
    except BaseException:
        os.close(fd)
        raise


def _runtime_root_path() -> Path:
    override = os.environ.get(RUNTIME_ROOT_ENV)
    if override is not None:
        if not override:
            raise ValueError(f"{RUNTIME_ROOT_ENV} must not be empty")
        root = Path(override).expanduser()
        if not root.is_absolute():
            raise ValueError(f"{RUNTIME_ROOT_ENV} must be an absolute path")
        return root

    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        base = Path(xdg_runtime).expanduser()
        if base.is_absolute():
            return base / "dotsync-serena-mcp"

    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        base = Path(xdg_cache).expanduser()
        if base.is_absolute():
            return base / "dotsync" / "serena-mcp"
    return Path.home() / ".cache" / "dotsync" / "serena-mcp"


def _ensure_private_directory(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError(f"private runtime directory must be absolute: {path}")
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except FileNotFoundError:
        _ensure_parent_directories(path.parent)
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
    _validate_private_directory(path)


def _validate_private_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise OSError(f"runtime path is not a non-symlink directory: {path}")
    if info.st_uid != os.geteuid():
        raise PermissionError(f"runtime directory is not owned by this user: {path}")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise PermissionError(f"runtime directory is not mode 0700: {path}")


def _ensure_parent_directories(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while not os.path.lexists(current):
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for directory in reversed(missing):
        try:
            os.mkdir(directory, 0o700)
        except FileExistsError:
            pass
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or directory.is_symlink():
            raise OSError(
                f"runtime parent is not a non-symlink directory: {directory}"
            )
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise PermissionError(f"runtime parent is not owner-private: {directory}")
