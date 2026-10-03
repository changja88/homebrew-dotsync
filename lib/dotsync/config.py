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
"""

from __future__ import annotations
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

ENV_VAR = "DOTSYNC_DIR"
FOLDER_CONFIG_FILENAME = "dotsync.toml"


def supported_apps() -> set[str]:
    """Return the set of registered app names. Lazy import keeps this module
    importable without firing apps/__init__.py at top of file."""
    from dotsync.apps import APP_NAMES

    return set(APP_NAMES)


class ConfigError(Exception):
    """Raised when config is missing or invalid."""


def folder_config_path(folder: Path) -> Path:
    return folder / FOLDER_CONFIG_FILENAME


@dataclass
class Config:
    dir: Path
    apps: List[str]
    # [options.<app>] tables, each parsed by that app's from_config().
    app_options: dict = field(default_factory=dict)


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
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"dotsync.toml at {cfg_file} is malformed: {e}") from e

    apps = data.get("apps", [])
    if not isinstance(apps, list):
        raise ConfigError(f"`apps` must be a list, got: {type(apps).__name__}")
    known = supported_apps()
    for app in apps:
        if not isinstance(app, str):
            raise ConfigError(
                f"`apps` entries must be strings, got: {type(app).__name__}"
            )
        if app not in known:
            raise ConfigError(
                f"unknown app `{app}` in config (supported: {sorted(known)})"
            )

    options = data.get("options", {})
    if not isinstance(options, dict):
        raise ConfigError(f"`options` must be a table, got: {type(options).__name__}")
    # tomllib materializes [options.x] as nested dict values within `options`;
    # anything else there is a leftover key from an older dotsync.
    stray = sorted(k for k, v in options.items() if not isinstance(v, dict))
    if stray:
        raise ConfigError(
            f"unsupported option {', '.join(stray)} in [options] of {cfg_file}; "
            "only [options.<app>] tables are allowed — remove the line"
        )

    return Config(dir=folder, apps=apps, app_options=options)


def _toml_value(v) -> str:
    """Minimal TOML value serializer for app_options (str | int | float | bool | list of those)."""
    if isinstance(v, bool):
        # bool MUST come before int — bool is a subclass of int in Python.
        return "true" if v else "false"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"unsupported app_options value type: {type(v).__name__}")


def save_config(cfg: Config) -> None:
    """Write the sync folder's dotsync.toml. Touches no other location."""
    cfg.dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "apps = [" + ", ".join(_toml_value(a) for a in cfg.apps) + "]",
        "",
    ]

    for app_name, opts in cfg.app_options.items():
        if not opts:
            continue
        lines.append(f"[options.{app_name}]")
        for key, val in opts.items():
            lines.append(f"{key} = {_toml_value(val)}")
        lines.append("")

    folder_config_path(cfg.dir).write_text("\n".join(lines))
