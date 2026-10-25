"""Where the sessions commands look. Read at call time, never at import, so
tests can point every location at a temp dir."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path


def claude_home() -> Path:
    # CLAUDE_CONFIG_DIR is ignored on purpose: dotsync always scans ~/.claude.
    return Path.home() / ".claude"


def claude_temp_root() -> Path | None:
    """Claude's temp tree, or None when CLAUDE_CODE_TMPDIR moves it: the layout
    under that directory is not documented, and unknown means keep."""
    if os.environ.get("CLAUDE_CODE_TMPDIR"):
        return None
    return Path(f"/private/tmp/claude-{os.getuid()}")


def codex_home() -> Path:
    override = os.environ.get("CODEX_HOME")
    return Path(override) if override else Path.home() / ".codex"


def codex_sqlite_home() -> Path:
    """Where Codex keeps its SQLite files: `sqlite_home` in config.toml, then
    $CODEX_SQLITE_HOME, then the Codex home. An unreadable config is skipped."""
    home = codex_home()
    try:
        with open(home / "config.toml", "rb") as f:
            configured = tomllib.load(f).get("sqlite_home")
    except (OSError, ValueError):  # tomllib.TOMLDecodeError is a ValueError
        configured = None
    if isinstance(configured, str) and configured:
        return home / Path(configured).expanduser()  # an absolute path wins
    from_env = os.environ.get("CODEX_SQLITE_HOME")
    return Path(from_env) if from_env else home


def log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "dotsync"
