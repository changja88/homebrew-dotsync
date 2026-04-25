"""dotsync config file management at ~/.config/dotsync/config.toml."""
from __future__ import annotations
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

SUPPORTED_APPS = {"claude", "ghostty", "bettertouchtool", "zsh"}
DEFAULT_BACKUP_DIR = "~/.local/share/dotsync/backups"
DEFAULT_BACKUP_KEEP = 10
DEFAULT_BTT_PRESET = "Master_bt"


class ConfigError(Exception):
    """Raised when config is missing or invalid."""


@dataclass
class Config:
    dir: Path
    apps: List[str]
    backup_dir: Path = field(default_factory=lambda: Path(DEFAULT_BACKUP_DIR).expanduser())
    backup_keep: int = DEFAULT_BACKUP_KEEP
    bettertouchtool_preset: str = DEFAULT_BTT_PRESET


def config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "dotsync" / "config.toml"


def load_config() -> Config:
    path = config_path()
    if not path.exists():
        raise ConfigError(
            f"config not found at {path}. Run `dotsync init` first."
        )
    with path.open("rb") as f:
        data = tomllib.load(f)

    raw_dir = data.get("dir")
    if not raw_dir:
        raise ConfigError(f"`dir` missing in {path}")
    dir_path = Path(raw_dir)
    if not dir_path.is_absolute():
        raise ConfigError(f"`dir` must be an absolute path, got: {raw_dir}")

    apps = data.get("apps") or []
    if not isinstance(apps, list):
        raise ConfigError(f"`apps` must be a list, got: {type(apps).__name__}")
    for app in apps:
        if app not in SUPPORTED_APPS:
            raise ConfigError(f"unknown app `{app}` in config (supported: {sorted(SUPPORTED_APPS)})")

    options = data.get("options", {}) or {}
    backup_dir_raw = options.get("backup_dir", DEFAULT_BACKUP_DIR)
    backup_dir = Path(backup_dir_raw).expanduser()
    backup_keep = int(options.get("backup_keep", DEFAULT_BACKUP_KEEP))
    btt_preset = str(options.get("bettertouchtool_preset", DEFAULT_BTT_PRESET))

    return Config(
        dir=dir_path,
        apps=apps,
        backup_dir=backup_dir,
        backup_keep=backup_keep,
        bettertouchtool_preset=btt_preset,
    )


def save_config(cfg: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'dir = "{cfg.dir}"',
        "apps = [" + ", ".join(f'"{a}"' for a in cfg.apps) + "]",
        "",
        "[options]",
        f'backup_dir = "{cfg.backup_dir}"',
        f"backup_keep = {cfg.backup_keep}",
        f'bettertouchtool_preset = "{cfg.bettertouchtool_preset}"',
        "",
    ]
    path.write_text("\n".join(lines))
