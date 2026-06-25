"""Backup directory management. Per-session timestamped subdirs with rotation."""

from __future__ import annotations
import re
import shutil
from datetime import datetime
from pathlib import Path

_BACKUP_NAME_RE = re.compile(r"^\d{8}_\d{6}(?:-\d{2})?$")


def new_backup_session(root: Path, *, now: datetime | None = None) -> Path:
    """Create and return a fresh timestamped backup directory under `root`.

    `now` is a test seam — production passes None and uses datetime.now().
    """
    if root.is_symlink():
        raise RuntimeError(
            f"{root} is a symlink; refusing to create backups through symlinks"
        )
    root.mkdir(parents=True, exist_ok=True)
    ts = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    session = root / ts
    for i in range(100):
        candidate = session if i == 0 else root / f"{ts}-{i:02d}"
        try:
            candidate.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError(f"could not create a unique backup session under {root}")


def rotate_backups(root: Path, keep: int) -> None:
    """Delete oldest backup dirs (by name = timestamp), keeping `keep` newest. keep=0 disables."""
    if keep <= 0 or not root.exists():
        return
    sessions = sorted(
        (p for p in root.iterdir() if p.is_dir() and _BACKUP_NAME_RE.match(p.name)),
        key=lambda p: p.name,
    )
    excess = len(sessions) - keep
    if excess <= 0:
        return
    for old in sessions[:excess]:
        shutil.rmtree(old, ignore_errors=True)
