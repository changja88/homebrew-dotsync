"""dotsync config persistence.

Layout:
  ~/.dotsync                       single-line pointer file containing the
                                   absolute path of the user's sync folder.
  <sync-folder>/dotsync.toml       real config (apps + options).

The folder doesn't record its own location, so dotsync.toml omits `dir`.
"""
from __future__ import annotations
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

SUPPORTED_APPS = {"claude", "ghostty", "bettertouchtool", "zsh"}
DEFAULT_BACKUP_DIR = "~/.local/share/dotsync/backups"
DEFAULT_BACKUP_KEEP = 10
DEFAULT_BTT_PRESET = "Master_bt"

POINTER_FILENAME = ".dotsync"
FOLDER_CONFIG_FILENAME = "dotsync.toml"


class ConfigError(Exception):
    """Raised when config is missing or invalid."""


@dataclass
class Config:
    dir: Path
    apps: List[str]
    backup_dir: Path = field(default_factory=lambda: Path(DEFAULT_BACKUP_DIR).expanduser())
    backup_keep: int = DEFAULT_BACKUP_KEEP
    bettertouchtool_preset: str = DEFAULT_BTT_PRESET


def pointer_path() -> Path:
    return Path.home() / POINTER_FILENAME


def folder_config_path(folder: Path) -> Path:
    return folder / FOLDER_CONFIG_FILENAME


def read_pointer() -> Optional[Path]:
    p = pointer_path()
    if not p.exists():
        return None
    raw = p.read_text().strip()
    if not raw:
        return None
    return Path(raw)


def write_pointer(folder: Path) -> None:
    pointer_path().write_text(f"{folder}\n")


def load_config() -> Config:
    folder = read_pointer()
    if folder is None:
        raise ConfigError("dotsync is not initialized — run `dotsync init` first.")
    if not folder.is_absolute():
        raise ConfigError(f"pointer must be an absolute path, got: {folder}")
    if not folder.exists():
        raise ConfigError(
            f"sync folder not found at {folder}. "
            f"Run `dotsync init --dir <path> --yes` to repoint."
        )
    cfg_file = folder_config_path(folder)
    if not cfg_file.exists():
        raise ConfigError(
            f"dotsync.toml missing in {folder}. "
            f"Run `dotsync init --dir {folder} --yes` to recreate."
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
    backup_dir_raw = options.get("backup_dir", DEFAULT_BACKUP_DIR)
    backup_dir = Path(backup_dir_raw).expanduser()
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
    """Write the pointer (~/.dotsync) and the folder's dotsync.toml."""
    cfg.dir.mkdir(parents=True, exist_ok=True)
    write_pointer(cfg.dir)
    lines = [
        "apps = [" + ", ".join(f'"{a}"' for a in cfg.apps) + "]",
        "",
        "[options]",
        f'backup_dir = "{cfg.backup_dir}"',
        f"backup_keep = {cfg.backup_keep}",
        f'bettertouchtool_preset = "{cfg.bettertouchtool_preset}"',
        "",
    ]
    folder_config_path(cfg.dir).write_text("\n".join(lines))
