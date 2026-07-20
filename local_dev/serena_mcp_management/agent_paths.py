"""Canonical product paths shared by local launcher inventories."""
from __future__ import annotations

from pathlib import Path


ORCA_CODEX_HOME = Path(
    "Library/Application Support/orca/codex-runtime-home/home"
)


def canonical_codex_homes(
    *,
    home: Path,
    codex_home: Path,
    orca_codex_home: Path | None = None,
) -> tuple[tuple[Path, ...], Path, Path]:
    active_home = codex_home.expanduser()
    if not active_home.is_absolute():
        raise ValueError("codex_home must be absolute")
    default_home = (home / ".codex").resolve(strict=False)
    active_home = active_home.resolve(strict=False)
    orca_home = (orca_codex_home or home / ORCA_CODEX_HOME).expanduser()
    if not orca_home.is_absolute():
        raise ValueError("orca_codex_home must be absolute")
    orca_home = orca_home.resolve(strict=False)
    homes = tuple(dict.fromkeys((default_home, active_home, orca_home)))
    return homes, default_home, orca_home


def effective_claude_config_dir(
    *,
    home: Path,
    claude_config_dir: Path | None = None,
) -> Path:
    candidate = (claude_config_dir or home / ".claude").expanduser()
    if not candidate.is_absolute():
        raise ValueError("claude_config_dir must be absolute")
    return candidate.resolve(strict=False)
