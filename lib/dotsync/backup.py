"""Backup directory management. Per-session timestamped subdirs with rotation."""
from __future__ import annotations
import re
import shutil
from datetime import datetime
from pathlib import Path

_BACKUP_NAME_RE = re.compile(r"^\d{8}_\d{6}$")


def new_backup_session(root: Path, *, now: datetime | None = None) -> Path:
    """Create and return a fresh timestamped backup directory under `root`.

    `now` is a test seam — production passes None and uses datetime.now().
    """
    root.mkdir(parents=True, exist_ok=True)
    ts = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    session = root / ts
    session.mkdir(exist_ok=False)
    return session


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
