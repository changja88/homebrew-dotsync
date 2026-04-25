"""dotsync config persistence.

Design goal: dotsync MUST NOT create any file or directory anywhere on the
user's machine outside the sync folder they explicitly chose. There is no
~/.config/dotsync, no ~/.dotsync pointer, nothing in $HOME.

How does dotsync know where the sync folder is then?
  1. $DOTSYNC_DIR environment variable (absolute path), if set, wins.
  2. Otherwise, walk up from cwd looking for a folder containing dotsync.toml
     (git-style). This means running dotsync from inside the sync folder
     (or any subdirectory) just works.

Real config lives at:
  <sync-folder>/dotsync.toml

Backups default to:
  <sync-folder>/.backups/<YYYYMMDD_HHMMSS>/<app>/
"""
from __future__ import annotations
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

SUPPORTED_APPS = {"claude", "ghostty", "bettertouchtool", "zsh"}
DEFAULT_BACKUP_KEEP = 10
DEFAULT_BTT_PRESET = "Master_bt"

ENV_VAR = "DOTSYNC_DIR"
FOLDER_CONFIG_FILENAME = "dotsync.toml"
DEFAULT_BACKUP_SUBDIR = ".backups"


class ConfigError(Exception):
    """Raised when config is missing or invalid."""


def folder_config_path(folder: Path) -> Path:
    return folder / FOLDER_CONFIG_FILENAME


def default_backup_dir(folder: Path) -> Path:
    return folder / DEFAULT_BACKUP_SUBDIR


@dataclass
class Config:
    dir: Path
    apps: List[str]
    backup_dir: Optional[Path] = None
    backup_keep: int = DEFAULT_BACKUP_KEEP
    bettertouchtool_preset: str = DEFAULT_BTT_PRESET

    def __post_init__(self):
        if self.backup_dir is None:
            self.backup_dir = default_backup_dir(self.dir)


def find_sync_folder() -> Optional[Path]:
    """Locate the user's sync folder.

    1. $DOTSYNC_DIR (must be absolute).
    2. Walk up from cwd looking for FOLDER_CONFIG_FILENAME.
    Returns None if neither succeeds.
    """
    env = os.environ.get(ENV_VAR)
    if env:
        p = Path(env).expanduser()
        return p  # validity (absolute, exists) is checked by load_config
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / FOLDER_CONFIG_FILENAME).exists():
            return parent
    return None


def load_config() -> Config:
    folder = find_sync_folder()
    if folder is None:
        raise ConfigError(
            "dotsync is not initialized in this context. Either:\n"
            f"  • set {ENV_VAR}=<absolute path to your sync folder>\n"
            "  • run dotsync from inside the sync folder (or any subdir)\n"
            "  • run `dotsync init --dir <path> --yes` to create a new one"
        )
    if not folder.is_absolute():
        raise ConfigError(f"{ENV_VAR} must be an absolute path, got: {folder}")
    if not folder.exists():
        raise ConfigError(
            f"sync folder not found at {folder}. "
            f"Run `dotsync init --dir <path> --yes` to create one, "
            f"or fix {ENV_VAR}."
        )
    cfg_file = folder_config_path(folder)
    if not cfg_file.exists():
        raise ConfigError(
            f"dotsync.toml missing in {folder}. "
            f"Run `dotsync init --dir {folder} --yes` to create it."
        )
    with cfg_file.open("rb") as f:
        data = tomllib.load(f)

    apps = data.get("apps") or []
    if not isinstance(apps, list):
        raise ConfigError(f"`apps` must be a list, got: {type(apps).__name__}")
    for app in apps:
        if app not in SUPPORTED_APPS:
            raise ConfigError(
                f"unknown app `{app}` in config (supported: {sorted(SUPPORTED_APPS)})"
            )

    options = data.get("options", {}) or {}
    backup_dir_raw = options.get("backup_dir")
    if backup_dir_raw:
        backup_dir = Path(backup_dir_raw).expanduser()
        if not backup_dir.is_absolute():
            backup_dir = folder / backup_dir
    else:
        backup_dir = default_backup_dir(folder)
    backup_keep = int(options.get("backup_keep", DEFAULT_BACKUP_KEEP))
    btt_preset = str(options.get("bettertouchtool_preset", DEFAULT_BTT_PRESET))

    return Config(
        dir=folder,
        apps=apps,
        backup_dir=backup_dir,
        backup_keep=backup_keep,
        bettertouchtool_preset=btt_preset,
    )


def save_config(cfg: Config) -> None:
    """Write the sync folder's dotsync.toml. Touches no other location."""
    cfg.dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "apps = [" + ", ".join(f'"{a}"' for a in cfg.apps) + "]",
        "",
        "[options]",
    ]
    # Only persist backup_dir if it's not the default (keeps the file portable
    # — moving the folder to another machine still uses default location).
    default_bd = default_backup_dir(cfg.dir)
    if cfg.backup_dir is not None and cfg.backup_dir != default_bd:
        lines.append(f'backup_dir = "{cfg.backup_dir}"')
    lines.append(f"backup_keep = {cfg.backup_keep}")
    lines.append(f'bettertouchtool_preset = "{cfg.bettertouchtool_preset}"')
    lines.append("")

    folder_config_path(cfg.dir).write_text("\n".join(lines))
