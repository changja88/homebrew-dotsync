import os
import stat
import threading

import pytest
import dotsync.config as config_module
from dotsync.config import (
    Config,
    ConfigError,
    load_config,
    save_config,
    find_sync_folder,
    folder_config_path,
    default_backup_dir,
    initialize_config_file,
    DEFAULT_BACKUP_KEEP,
    DEFAULT_BTT_PRESETS,
)


def test_folder_config_path_is_dotsync_toml(tmp_path):
    assert folder_config_path(tmp_path) == tmp_path / "dotsync.toml"


def test_default_backup_dir_is_inside_sync_folder(tmp_path):
    assert default_backup_dir(tmp_path) == tmp_path / ".backups"


# ----- find_sync_folder ------------------------------------------------------


def test_find_sync_folder_uses_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("DOTSYNC_DIR", str(tmp_path))
    assert find_sync_folder() == tmp_path


def test_find_sync_folder_ascends_cwd(monkeypatch, tmp_path):
    folder = tmp_path / "myfolder"
    folder.mkdir()
    (folder / "dotsync.toml").write_text("apps = []\n")
    deep = folder / "a" / "b" / "c"
    deep.mkdir(parents=True)
    monkeypatch.delenv("DOTSYNC_DIR", raising=False)
    monkeypatch.chdir(deep)
    assert find_sync_folder() == folder


def test_find_sync_folder_returns_none_when_nothing_found(monkeypatch, tmp_path):
    monkeypatch.delenv("DOTSYNC_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert find_sync_folder() is None


def test_find_sync_folder_env_takes_precedence_over_cwd(monkeypatch, tmp_path):
    env_folder = tmp_path / "env"
    env_folder.mkdir()
    cwd_folder = tmp_path / "cwd"
    cwd_folder.mkdir()
    (cwd_folder / "dotsync.toml").write_text("apps = []\n")
    monkeypatch.setenv("DOTSYNC_DIR", str(env_folder))
    monkeypatch.chdir(cwd_folder)
    assert find_sync_folder() == env_folder


# ----- load_config -----------------------------------------------------------


def test_load_no_env_no_cwd_raises_with_helpful_msg(fake_home, monkeypatch, tmp_path):
    monkeypatch.delenv("DOTSYNC_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as exc:
        load_config()
    msg = str(exc.value)
    assert "DOTSYNC_DIR" in msg or "dotsync init" in msg


def test_load_env_pointing_to_missing_folder_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("DOTSYNC_DIR", str(tmp_path / "does-not-exist"))
    with pytest.raises(ConfigError, match="not found"):
        load_config()


def test_load_env_pointing_to_folder_without_dotsync_toml_raises(monkeypatch, tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    with pytest.raises(ConfigError, match="dotsync.toml"):
        load_config()


def test_load_via_env(monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(
        'apps = ["zsh", "claude"]\n\n[options]\n'
        "backup_keep = 7\n"
        'bettertouchtool_presets = ["Foo", "Bar"]\n'
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    cfg = load_config()
    assert cfg.dir == folder
    assert cfg.apps == ["zsh", "claude"]
    assert cfg.backup_keep == 7
    assert cfg.bettertouchtool_presets == ["Foo", "Bar"]
    # default backup_dir is inside sync folder
    assert cfg.backup_dir == folder / ".backups"


def test_load_migrates_legacy_btt_preset_to_list(monkeypatch, tmp_path):
    """Legacy `bettertouchtool_preset = "X"` (single string) reads as ["X"]."""
    folder = tmp_path / "legacy"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(
        'apps = ["bettertouchtool"]\n\n[options]\n'
        'bettertouchtool_preset = "Master_bt"\n'
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    cfg = load_config()
    assert cfg.bettertouchtool_presets == ["Master_bt"]


def test_load_prefers_new_btt_presets_over_legacy(monkeypatch, tmp_path):
    """If both keys exist (transitional state), prefer the new list key."""
    folder = tmp_path / "both"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(
        'apps = ["bettertouchtool"]\n\n[options]\n'
        'bettertouchtool_preset = "Old"\n'
        'bettertouchtool_presets = ["New1", "New2"]\n'
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    cfg = load_config()
    assert cfg.bettertouchtool_presets == ["New1", "New2"]


def test_load_btt_presets_default_when_unset(monkeypatch, tmp_path):
    folder = tmp_path / "unset"
    folder.mkdir()
    (folder / "dotsync.toml").write_text('apps = ["zsh"]\n')
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    cfg = load_config()
    assert cfg.bettertouchtool_presets == list(DEFAULT_BTT_PRESETS)


def test_load_via_cwd_ascending(monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text('apps = ["zsh"]\n')
    monkeypatch.delenv("DOTSYNC_DIR", raising=False)
    monkeypatch.chdir(folder / "any" if (folder / "any").exists() else folder)
    cfg = load_config()
    assert cfg.dir == folder


def test_load_rejects_relative_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("DOTSYNC_DIR", "relative/path")
    with pytest.raises(ConfigError, match="absolute"):
        load_config()


def test_load_rejects_unknown_app(monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text('apps = ["nonsense"]\n')
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    with pytest.raises(ConfigError, match="unknown app"):
        load_config()


@pytest.mark.parametrize("value", ["false", "0", '""'])
def test_load_rejects_falsey_apps_when_not_list(monkeypatch, tmp_path, value):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(f"apps = {value}\n")
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    with pytest.raises(ConfigError, match="apps"):
        load_config()


def test_load_rejects_non_string_app_name(monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text("apps = [1]\n")
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    with pytest.raises(ConfigError, match="apps"):
        load_config()


def test_load_rejects_options_when_not_table(monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text('apps = ["zsh"]\noptions = "bad"\n')
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    with pytest.raises(ConfigError, match="options"):
        load_config()


@pytest.mark.parametrize("value", ["false", "0", "[]"])
def test_load_rejects_falsey_options_when_not_table(monkeypatch, tmp_path, value):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(f'apps = ["zsh"]\noptions = {value}\n')
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    with pytest.raises(ConfigError, match="options"):
        load_config()


def test_load_rejects_absolute_backup_dir(monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(
        f'apps = ["zsh"]\n\n[options]\nbackup_dir = "{tmp_path / "outside"}"\n'
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    with pytest.raises(ConfigError, match="backup_dir"):
        load_config()


def test_load_allows_absolute_backup_dir_inside_sync_folder(monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    backup_dir = folder / "custom-backups"
    (folder / "dotsync.toml").write_text(
        f'apps = ["zsh"]\n\n[options]\nbackup_dir = "{backup_dir}"\n'
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    assert load_config().backup_dir == backup_dir


def test_load_rejects_backup_dir_relative_escape(monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(
        'apps = ["zsh"]\n\n[options]\nbackup_dir = "../outside"\n'
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    with pytest.raises(ConfigError, match="backup_dir"):
        load_config()


def test_load_rejects_backup_dir_symlink_escape(monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (folder / "linked-backups").symlink_to(outside)
    (folder / "dotsync.toml").write_text(
        'apps = ["zsh"]\n\n[options]\nbackup_dir = "linked-backups"\n'
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    with pytest.raises(ConfigError, match="backup_dir"):
        load_config()


def test_load_rejects_default_backup_dir_symlink_escape(monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (folder / ".backups").symlink_to(outside, target_is_directory=True)
    (folder / "dotsync.toml").write_text('apps = ["zsh"]\n')
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    with pytest.raises(ConfigError, match="backup_dir"):
        load_config()


@pytest.mark.parametrize("value", ['["bad"]', "false", "0", '""'])
def test_load_rejects_backup_dir_non_string_or_empty(monkeypatch, tmp_path, value):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(
        f'apps = ["zsh"]\n\n[options]\nbackup_dir = {value}\n'
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    with pytest.raises(ConfigError, match="backup_dir"):
        load_config()


@pytest.mark.parametrize("value", ['"many"', "-1"])
def test_load_rejects_invalid_backup_keep(monkeypatch, tmp_path, value):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(
        f'apps = ["zsh"]\n\n[options]\nbackup_keep = {value}\n'
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    with pytest.raises(ConfigError, match="backup_keep"):
        load_config()


# ----- save_config -----------------------------------------------------------


def test_save_writes_only_dotsync_toml_no_other_files(fake_home, monkeypatch, tmp_path):
    """save_config must NOT create any file outside the sync folder."""
    monkeypatch.delenv("DOTSYNC_DIR", raising=False)
    folder = tmp_path / "myfolder"
    folder.mkdir()
    cfg = Config(dir=folder, apps=["zsh"])
    save_config(cfg)

    # dotsync.toml exists in the sync folder
    assert (folder / "dotsync.toml").exists()
    # NO pointer file in $HOME
    assert not (fake_home / ".dotsync").exists()
    # NO ~/.config/dotsync directory
    assert not (fake_home / ".config" / "dotsync").exists()


def test_save_then_load_roundtrip(monkeypatch, tmp_path):
    folder = tmp_path / "configs"
    folder.mkdir()
    cfg = Config(dir=folder, apps=["claude", "zsh"])
    save_config(cfg)

    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    loaded = load_config()
    assert loaded.dir == folder
    assert loaded.apps == ["claude", "zsh"]
    assert loaded.backup_dir == folder / ".backups"
    assert loaded.backup_keep == DEFAULT_BACKUP_KEEP
    assert loaded.bettertouchtool_presets == list(DEFAULT_BTT_PRESETS)


def test_save_preserves_existing_config_permission_mode(tmp_path):
    folder = tmp_path / "private-config"
    folder.mkdir()
    config_path = folder / "dotsync.toml"
    config_path.write_text('apps = ["zsh"]\n')
    config_path.chmod(0o600)

    save_config(Config(dir=folder, apps=["ghostty"]))

    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_save_preserves_latest_mode_changed_after_temporary_write(
    monkeypatch, tmp_path
):
    folder = tmp_path / "concurrent-mode-change"
    folder.mkdir()
    config_path = folder / "dotsync.toml"
    config_path.write_text('apps = ["zsh"]\n')
    config_path.chmod(0o600)
    temporary_write_fsynced = threading.Event()
    release_save = threading.Event()
    real_fsync = config_module.os.fsync

    def pause_after_temporary_write(descriptor: int) -> None:
        real_fsync(descriptor)
        if (
            stat.S_ISREG(config_module.os.fstat(descriptor).st_mode)
            and not temporary_write_fsynced.is_set()
        ):
            temporary_write_fsynced.set()
            assert release_save.wait(timeout=2.0)

    monkeypatch.setattr(config_module.os, "fsync", pause_after_temporary_write)
    errors: list[BaseException] = []

    def save() -> None:
        try:
            save_config(Config(dir=folder, apps=["ghostty"]))
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=save)
    thread.start()
    assert temporary_write_fsynced.wait(timeout=1.0)
    config_path.chmod(0o640)
    release_save.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert errors == []
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o640


def test_save_rejects_target_replaced_during_late_mode_transfer(
    monkeypatch, tmp_path
):
    folder = tmp_path / "late-target-replacement"
    folder.mkdir()
    config_path = folder / "dotsync.toml"
    displaced = folder / "original.toml"
    replacement = 'apps = ["concurrent"]\n'
    config_path.write_text('apps = ["zsh"]\n')
    config_path.chmod(0o600)
    real_fchmod = config_module.os.fchmod

    def replace_target(descriptor: int, mode: int) -> None:
        config_path.rename(displaced)
        config_path.write_text(replacement)
        real_fchmod(descriptor, mode)

    monkeypatch.setattr(config_module.os, "fchmod", replace_target)

    with pytest.raises(ConfigError, match="changed during save"):
        save_config(Config(dir=folder, apps=["ghostty"]))

    assert config_path.read_text() == replacement
    assert displaced.read_text() == 'apps = ["zsh"]\n'
    assert sorted(path.name for path in folder.iterdir()) == [
        "dotsync.toml",
        "original.toml",
    ]


def test_initializer_closes_staging_descriptor_when_cleanup_unlink_fails(
    monkeypatch, tmp_path
):
    folder = tmp_path / "initializer-close"
    folder.mkdir()
    directory_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    real_open = config_module.os.open
    real_close = config_module.os.close
    opened_staging: list[int] = []
    closed: list[int] = []

    def record_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if dir_fd == directory_fd:
            opened_staging.append(descriptor)
        return descriptor

    def fail_file_fsync(descriptor: int) -> None:
        if stat.S_ISREG(config_module.os.fstat(descriptor).st_mode):
            raise OSError("file fsync failed")

    def fail_cleanup_unlink(name: str, *, dir_fd=None) -> None:
        raise OSError("cleanup unlink failed")

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(config_module.os, "open", record_open)
    monkeypatch.setattr(config_module.os, "fsync", fail_file_fsync)
    monkeypatch.setattr(config_module.os, "unlink", fail_cleanup_unlink)
    monkeypatch.setattr(config_module.os, "close", record_close)

    try:
        with pytest.raises(OSError):
            initialize_config_file(
                directory_fd,
                Config(dir=folder, apps=["zsh"]),
            )

        assert len(opened_staging) == 1
        assert opened_staging[0] in closed
    finally:
        for descriptor in opened_staging:
            if descriptor not in closed:
                real_close(descriptor)
        real_close(directory_fd)


def test_save_applies_existing_set_id_mode_after_writing(monkeypatch, tmp_path):
    folder = tmp_path / "set-id-config"
    folder.mkdir()
    config_path = folder / "dotsync.toml"
    config_path.write_text('apps = ["zsh"]\n')
    config_path.chmod(0o755)
    target_identity = (config_path.stat().st_dev, config_path.stat().st_ino)
    real_fstat = config_module.os.fstat
    real_fchmod = config_module.os.fchmod
    real_write = config_module.os.write
    writes_completed = False
    applied_modes: list[int] = []

    def report_set_id_target_mode(descriptor: int):
        metadata = real_fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) != target_identity:
            return metadata
        values = list(metadata)
        values[stat.ST_MODE] |= stat.S_ISUID
        return os.stat_result(values)

    def record_write(descriptor: int, content: bytes) -> int:
        nonlocal writes_completed
        written = real_write(descriptor, content)
        writes_completed = True
        return written

    def apply_representable_mode(descriptor: int, mode: int) -> None:
        assert writes_completed
        applied_modes.append(mode)
        real_fchmod(descriptor, mode & ~stat.S_ISUID)

    monkeypatch.setattr(config_module.os, "fstat", report_set_id_target_mode)
    monkeypatch.setattr(config_module.os, "write", record_write)
    monkeypatch.setattr(config_module.os, "fchmod", apply_representable_mode)

    save_config(Config(dir=folder, apps=["ghostty"]))

    assert applied_modes == [0o4755]
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o755
    assert 'apps = ["ghostty"]' in config_path.read_text()


def test_save_new_config_uses_requested_mode_filtered_by_umask(tmp_path):
    folder = tmp_path / "new-config-mode"
    folder.mkdir()
    previous_umask = os.umask(0o027)
    try:
        save_config(Config(dir=folder, apps=["zsh"]))
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE((folder / "dotsync.toml").stat().st_mode) == 0o640


def test_save_and_load_roundtrip_through_sync_directory_symlink(
    monkeypatch, tmp_path
):
    real_folder = tmp_path / "real-sync"
    real_folder.mkdir()
    (real_folder / "dotsync.toml").write_text('apps = ["zsh"]\n')
    alias = tmp_path / "sync-alias"
    alias.symlink_to(real_folder, target_is_directory=True)
    monkeypatch.setenv("DOTSYNC_DIR", str(alias))

    loaded = load_config()
    loaded.apps = ["ghostty", "zsh"]
    save_config(loaded)
    roundtripped = load_config()

    assert roundtripped.dir == alias
    assert roundtripped.apps == ["ghostty", "zsh"]
    assert (real_folder / "dotsync.toml").is_file()


def test_save_rejects_a_symlinked_config_file(tmp_path):
    folder = tmp_path / "sync"
    folder.mkdir()
    outside = tmp_path / "outside.toml"
    outside.write_text("CONFIG_SYMLINK_SENTINEL")
    (folder / "dotsync.toml").symlink_to(outside)

    with pytest.raises(ConfigError, match="regular file"):
        save_config(Config(dir=folder, apps=["zsh"]))

    assert outside.read_text() == "CONFIG_SYMLINK_SENTINEL"


def test_bettertouchtool_presets_roundtrip(monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    cfg = Config(
        dir=folder,
        apps=["bettertouchtool"],
        bettertouchtool_presets=["MyCustomPreset", "Other"],
    )
    save_config(cfg)
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    loaded = load_config()
    assert loaded.bettertouchtool_presets == ["MyCustomPreset", "Other"]


def test_save_writes_new_btt_presets_key(tmp_path):
    """save_config must emit `bettertouchtool_presets = [...]` (new schema),
    not the legacy `bettertouchtool_preset = "..."` key."""
    folder = tmp_path / "fresh"
    folder.mkdir()
    cfg = Config(
        dir=folder,
        apps=["bettertouchtool"],
        bettertouchtool_presets=["A", "B"],
    )
    save_config(cfg)
    text = (folder / "dotsync.toml").read_text()
    assert 'bettertouchtool_presets = ["A", "B"]' in text
    assert "bettertouchtool_preset =" not in text


def test_save_creates_folder_if_missing(tmp_path):
    folder = tmp_path / "new-folder-not-yet-existing"
    cfg = Config(dir=folder, apps=["zsh"])
    save_config(cfg)
    assert folder.exists()
    assert (folder / "dotsync.toml").exists()


def test_config_backup_dir_defaults_to_sync_folder_subdir(tmp_path):
    cfg = Config(dir=tmp_path, apps=["zsh"])
    assert cfg.backup_dir == tmp_path / ".backups"


def test_config_backup_dir_explicit_override(tmp_path):
    custom = tmp_path / "custom-bk"
    cfg = Config(dir=tmp_path, apps=["zsh"], backup_dir=custom)
    assert cfg.backup_dir == custom


def test_load_corrupted_toml_raises_config_error(monkeypatch, tmp_path):
    """A hand-mangled dotsync.toml must surface as ConfigError, not raw
    TOMLDecodeError, so cli.py's friendly handler catches it."""
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "dotsync.toml").write_text('apps = ["zsh"\n[options\nbroken = ')
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    with pytest.raises(ConfigError, match="dotsync.toml"):
        load_config()


def test_config_app_options_default_is_empty_dict(tmp_path):
    cfg = Config(dir=tmp_path, apps=["zsh"])
    assert cfg.app_options == {}


def test_load_reads_app_options_subtables(monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(
        'apps = ["bettertouchtool"]\n\n[options]\n'
        "backup_keep = 5\n\n"
        "[options.bettertouchtool]\n"
        'presets = ["A", "B"]\n'
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    cfg = load_config()
    assert cfg.app_options.get("bettertouchtool") == {"presets": ["A", "B"]}


def test_save_persists_app_options_as_subtables(tmp_path):
    folder = tmp_path / "fresh"
    folder.mkdir()
    cfg = Config(
        dir=folder,
        apps=["bettertouchtool"],
        app_options={"bettertouchtool": {"presets": ["X", "Y"]}},
    )
    save_config(cfg)
    text = (folder / "dotsync.toml").read_text()
    assert "[options.bettertouchtool]" in text
    assert 'presets = ["X", "Y"]' in text


def test_save_escapes_strings_for_toml(monkeypatch, tmp_path):
    folder = tmp_path / "quoted"
    folder.mkdir()
    cfg = Config(
        dir=folder,
        apps=["bettertouchtool"],
        app_options={"bettertouchtool": {"presets": ['Preset "Q"', "Back\\slash"]}},
    )
    save_config(cfg)

    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    loaded = load_config()

    assert loaded.app_options["bettertouchtool"]["presets"] == [
        'Preset "Q"',
        "Back\\slash",
    ]
