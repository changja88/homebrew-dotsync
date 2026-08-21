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
import json
import os
import secrets
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

DEFAULT_BACKUP_KEEP = 10
# BetterTouchTool can sync multiple presets at once. The default list ships
# with one entry to match BTT's stock starter preset; new installs without
# any explicit configuration fall back to this.
DEFAULT_BTT_PRESETS: tuple[str, ...] = ("Master_bt",)

ENV_VAR = "DOTSYNC_DIR"
FOLDER_CONFIG_FILENAME = "dotsync.toml"
DEFAULT_BACKUP_SUBDIR = ".backups"


def supported_apps() -> set[str]:
    """Return the set of registered app names. Lazy import keeps this module
    importable without firing apps/__init__.py at top of file."""
    from dotsync.apps import APP_NAMES

    return set(APP_NAMES)


class ConfigError(Exception):
    """Raised when config is missing or invalid."""


class ConfigWriteUncertain(OSError):
    """Raised after config replacement when directory durability is uncertain."""


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
    # TODO: remove `bettertouchtool_presets` once one release has passed with
    # `app_options` as the canonical home. BTT now reads from
    # `cfg.app_options["bettertouchtool"]["presets"]` via from_config(); this
    # field exists only as a legacy fallback for dotsync.toml files saved
    # before Phase 6/7. When removed, also drop the legacy fallback in
    # BetterTouchToolApp.from_config and the legacy migration in _read_btt_presets.
    bettertouchtool_presets: List[str] = field(
        default_factory=lambda: list(DEFAULT_BTT_PRESETS)
    )
    app_options: dict = field(default_factory=dict)

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
    return load_config_from(folder)


def load_config_from(folder: Path) -> Config:
    """Load one explicitly selected sync folder without global discovery."""
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
    backup_dir = _read_backup_dir(folder, options.get("backup_dir"))
    backup_keep = _read_backup_keep(options.get("backup_keep", DEFAULT_BACKUP_KEEP))
    btt_presets = _read_btt_presets(options)
    # tomllib materializes [options.x] as nested dict values within `options`.
    app_options = {k: v for k, v in options.items() if isinstance(v, dict)}

    return Config(
        dir=folder,
        apps=apps,
        backup_dir=backup_dir,
        backup_keep=backup_keep,
        bettertouchtool_presets=btt_presets,
        app_options=app_options,
    )


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


def _read_backup_dir(folder: Path, value) -> Path:
    raw = Path(".backups") if value is None else None
    if value is not None and not isinstance(value, str):
        raise ConfigError(
            f"`backup_dir` must point inside the sync folder, "
            f"got: {type(value).__name__}"
        )
    if isinstance(value, str):
        if not value:
            raise ConfigError("`backup_dir` must point inside the sync folder")
        raw = Path(value).expanduser()
    assert raw is not None
    folder_real = folder.resolve()
    backup_dir = raw.resolve() if raw.is_absolute() else (folder / raw).resolve()
    if backup_dir != folder_real and folder_real not in backup_dir.parents:
        raise ConfigError("`backup_dir` must stay inside the sync folder")
    return backup_dir


def _read_backup_keep(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(
            f"`backup_keep` must be a non-negative integer, got: {type(value).__name__}"
        )
    if value < 0:
        raise ConfigError("`backup_keep` must be a non-negative integer")
    return value


def _read_btt_presets(options: dict) -> List[str]:
    """Resolve BTT presets from on-disk options, with legacy migration.

    Precedence: new `bettertouchtool_presets` list → legacy
    `bettertouchtool_preset` single string → DEFAULT_BTT_PRESETS.
    The legacy single-string form is auto-migrated to a one-element list
    so users with old dotsync.toml files keep working without manual edits.
    """
    new_value = options.get("bettertouchtool_presets")
    if new_value is not None:
        if not isinstance(new_value, list):
            raise ConfigError(
                f"`bettertouchtool_presets` must be a list of strings, "
                f"got: {type(new_value).__name__}"
            )
        return [str(p) for p in new_value]
    legacy = options.get("bettertouchtool_preset")
    if legacy is not None:
        return [str(legacy)]
    return list(DEFAULT_BTT_PRESETS)


def save_config(cfg: Config) -> None:
    """Write the sync folder's dotsync.toml. Touches no other location."""
    cfg.dir.mkdir(parents=True, exist_ok=True)

    _atomic_write_config(folder_config_path(cfg.dir), _serialize_config(cfg))


def _serialize_config(cfg: Config) -> str:
    """Render a config without performing filesystem mutation."""

    lines = [
        "apps = [" + ", ".join(_toml_value(a) for a in cfg.apps) + "]",
        "",
        "[options]",
    ]
    # Only persist backup_dir if it's not the default (keeps the file portable
    # — moving the folder to another machine still uses default location).
    default_bd = default_backup_dir(cfg.dir)
    if cfg.backup_dir is not None and cfg.backup_dir != default_bd:
        lines.append(f"backup_dir = {_toml_value(str(cfg.backup_dir))}")
    lines.append(f"backup_keep = {cfg.backup_keep}")
    presets_repr = ", ".join(_toml_value(p) for p in cfg.bettertouchtool_presets)
    lines.append(f"bettertouchtool_presets = [{presets_repr}]")
    lines.append("")

    for app_name, opts in cfg.app_options.items():
        if not opts:
            continue
        lines.append(f"[options.{app_name}]")
        for key, val in opts.items():
            lines.append(f"{key} = {_toml_value(val)}")
        lines.append("")

    return "\n".join(lines)


def initialize_config_file(directory_fd: int, cfg: Config) -> None:
    """Create a missing config through one already-validated directory."""
    content = _serialize_config(cfg).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(
        FOLDER_CONFIG_FILENAME,
        flags,
        0o666,
        dir_fd=directory_fd,
    )
    initialized = False
    try:
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.fsync(directory_fd)
        initialized = True
    finally:
        if not initialized:
            _unlink_created_config_if_current(directory_fd, descriptor)
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            raise OSError("config write made no progress")
        offset += written


def _unlink_created_config_if_current(
    directory_fd: int,
    descriptor: int,
) -> None:
    created = os.fstat(descriptor)
    try:
        current = os.stat(
            FOLDER_CONFIG_FILENAME,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) != (created.st_dev, created.st_ino):
        return
    try:
        os.unlink(FOLDER_CONFIG_FILENAME, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except OSError:
        pass


def _atomic_write_config(path: Path, content: str) -> None:
    parent_fd = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY,
    )
    target_fd: int | None = None
    temporary_fd: int | None = None
    temporary_name: str | None = None
    try:
        target_fd = _open_existing_config(parent_fd, path.name)

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        for _ in range(100):
            temporary_name = f".dotsync.toml.{secrets.token_hex(16)}"
            try:
                temporary_fd = os.open(
                    temporary_name,
                    flags,
                    0o666,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                temporary_name = None
                continue
            break
        else:
            raise FileExistsError("could not create a temporary config file")

        try:
            assert temporary_fd is not None
            _write_all(temporary_fd, content.encode("utf-8"))
            os.fsync(temporary_fd)
            if not _config_target_identity_is_current(
                parent_fd,
                path.name,
                target_fd,
            ):
                raise ConfigError("dotsync.toml changed during save")
            if target_fd is not None:
                target_mode = stat.S_IMODE(os.fstat(target_fd).st_mode)
                os.fchmod(temporary_fd, target_mode)
                os.fsync(temporary_fd)
            os.close(temporary_fd)
            temporary_fd = None
            os.replace(
                temporary_name,
                path.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            temporary_name = None
            try:
                os.fsync(parent_fd)
            except OSError:
                raise ConfigWriteUncertain(
                    "config replacement durability is uncertain"
                ) from None
            final_fd = os.open(
                path.name,
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            try:
                if not stat.S_ISREG(os.fstat(final_fd).st_mode):
                    raise ConfigError("dotsync.toml must be a regular file")
            finally:
                os.close(final_fd)
        except BaseException:
            if temporary_fd is not None:
                os.close(temporary_fd)
                temporary_fd = None
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            raise
    finally:
        if target_fd is not None:
            os.close(target_fd)
        os.close(parent_fd)


def _open_existing_config(parent_fd: int, name: str) -> int | None:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as error:
        try:
            metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            raise error
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigError("dotsync.toml must be a regular file") from None
        raise
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ConfigError("dotsync.toml must be a regular file")
    return descriptor


def _config_target_identity_is_current(
    parent_fd: int,
    name: str,
    target_fd: int | None,
) -> bool:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return target_fd is None
    if target_fd is None or not stat.S_ISREG(current.st_mode):
        return False
    opened = os.fstat(target_fd)
    return (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino)
