"""Path and scope helpers for scoped Serena MCP runtime state."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CLIENT_TYPES = {"codex", "claude"}
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
    client_type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", self.project_root.resolve())
        if self.client_type not in CLIENT_TYPES:
            raise ValueError(f"unsupported client type: {self.client_type}")

    @property
    def key(self) -> str:
        return f"{self.project_root}::{self.client_type}"


def find_project_root(cwd: Path) -> Path:
    """Find a project root from a current working directory."""

    current = cwd.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".serena" / "project.yml").is_file():
            return candidate
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate
    return current


def state_dir_for(scope: Scope) -> Path:
    """Return the per-scope runtime state directory."""

    return scope.project_root / ".serena" / "dotsync-mcp" / scope.client_type
