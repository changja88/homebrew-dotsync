"""Abstract base for app sync modules."""
from __future__ import annotations
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Tuple

StatusState = Literal["clean", "dirty", "missing", "unknown"]


@dataclass
class AppStatus:
    state: StatusState
    details: str = ""
    direction: str = ""  # "local-newer" | "folder-newer" | "diverged" | ""


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def diff_files(pairs: Iterable[Tuple[Path, Path]]) -> AppStatus:
    """Compare (local, stored) file pairs by sha256, with a mtime-based direction hint.

    Returns:
      missing — at least one side absent
      dirty   — every file present but at least one pair differs (with direction)
      clean   — every pair byte-identical
    """
    pairs = list(pairs)
    if not pairs:
        return AppStatus(state="unknown")
    missing: list[str] = []
    differs: list[Tuple[Path, Path, str]] = []  # (local, stored, name)
    for local, stored in pairs:
        if not local.exists() or not stored.exists():
            missing.append(local.name)
            continue
        if _hash(local) != _hash(stored):
            differs.append((local, stored, local.name))
    if missing:
        return AppStatus(state="missing", details=", ".join(missing))
    if differs:
        local_newer = sum(1 for l, s, _ in differs if l.stat().st_mtime > s.stat().st_mtime)
        folder_newer = len(differs) - local_newer
        if local_newer and folder_newer:
            direction = "diverged"
        elif local_newer:
            direction = "local-newer"
        else:
            direction = "folder-newer"
        return AppStatus(
            state="dirty",
            details=", ".join(name for _, _, name in differs),
            direction=direction,
        )
    return AppStatus(state="clean")


class App(ABC):
    """One concrete subclass per supported app."""

    name: str = ""           # short id, must match config and dir/<name>/
    description: str = ""    # human-readable

    @abstractmethod
    def sync_from(self, target_dir: Path) -> None:
        """Local app config → target_dir/<self.name>/"""

    @abstractmethod
    def sync_to(self, target_dir: Path, backup_dir: Path) -> None:
        """target_dir/<self.name>/ → local app config, after backing up local to backup_dir/<self.name>/"""

    def status(self, target_dir: Path) -> AppStatus:
        """Optional: report local-vs-target state. Default: unknown.

        Concrete apps should override and return diff_files(...) over their tracked files.
        """
        return AppStatus(state="unknown")
