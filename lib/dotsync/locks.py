"""Locks shared by the terminal and the dotsync app.

They live in ~/Library/Application Support/dotsync/locks/ so a command started
from either place waits for the other:

- ``codex``: session deletes and the Codex cleanup hold it exclusive; Codex
  sync (which runs the ``codex`` CLI) holds it shared.
- ``claude-sessions``: Claude session deletes hold it exclusive.

~/.claude.json writers use accounts.locked() instead, the lock the account
commands already take.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import time
from pathlib import Path
from typing import Iterator, Literal

LockName = Literal["claude-sessions", "codex"]


def lock_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "dotsync" / "locks"


class LockBusy(RuntimeError):
    """Another dotsync command holds the lock past the wait."""

    code = "busy"

    def __init__(self, name: str) -> None:
        super().__init__(f"another dotsync command is using {name} — try again shortly")
        self.name = name


@contextlib.contextmanager
def tool_lock(name: LockName, *, shared: bool, wait: float = 10.0) -> Iterator[None]:
    """Hold <lock_dir>/<name>.lock shared or exclusive, polling every 0.1 s
    for up to `wait` seconds before raising LockBusy."""
    directory = lock_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    with open(directory / f"{name}.lock", "a") as handle:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(handle, mode | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockBusy(name) from None
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
