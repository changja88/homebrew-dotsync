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


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def diff_files(pairs: Iterable[Tuple[Path, Path]]) -> AppStatus:
    """Compare (local, stored) file pairs by sha256.

    Returns:
      missing — at least one side absent
      dirty   — every file present but at least one pair differs
      clean   — every pair byte-identical
    """
    pairs = list(pairs)
    if not pairs:
        return AppStatus(state="unknown")
    missing: list[str] = []
    differs: list[str] = []
    for local, stored in pairs:
        if not local.exists() or not stored.exists():
            missing.append(local.name)
            continue
        if _hash(local) != _hash(stored):
            differs.append(local.name)
    if missing:
        return AppStatus(state="missing", details=", ".join(missing))
    if differs:
        return AppStatus(state="dirty", details=", ".join(differs))
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
