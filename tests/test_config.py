from pathlib import Path
import pytest
from dotsync.config import (
    Config,
    ConfigError,
    load_config,
    save_config,
    find_sync_folder,
    folder_config_path,
    default_backup_dir,
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
        'backup_keep = 7\n'
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
    assert 'bettertouchtool_preset =' not in text


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
    folder = tmp_path / "x"; folder.mkdir()
    (folder / "dotsync.toml").write_text(
        'apps = ["bettertouchtool"]\n\n[options]\n'
        'backup_keep = 5\n\n'
        '[options.bettertouchtool]\n'
        'presets = ["A", "B"]\n'
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    cfg = load_config()
    assert cfg.app_options.get("bettertouchtool") == {"presets": ["A", "B"]}


def test_save_persists_app_options_as_subtables(tmp_path):
    folder = tmp_path / "fresh"; folder.mkdir()
    cfg = Config(
        dir=folder,
        apps=["bettertouchtool"],
        app_options={"bettertouchtool": {"presets": ["X", "Y"]}},
    )
    save_config(cfg)
    text = (folder / "dotsync.toml").read_text()
    assert "[options.bettertouchtool]" in text
    assert 'presets = ["X", "Y"]' in text
