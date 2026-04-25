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
    DEFAULT_BTT_PRESET,
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
        'bettertouchtool_preset = "Foo"\n'
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    cfg = load_config()
    assert cfg.dir == folder
    assert cfg.apps == ["zsh", "claude"]
    assert cfg.backup_keep == 7
    assert cfg.bettertouchtool_preset == "Foo"
    # default backup_dir is inside sync folder
    assert cfg.backup_dir == folder / ".backups"


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
    assert loaded.bettertouchtool_preset == DEFAULT_BTT_PRESET


def test_bettertouchtool_preset_roundtrip(monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    cfg = Config(dir=folder, apps=["bettertouchtool"], bettertouchtool_preset="MyCustomPreset")
    save_config(cfg)
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    loaded = load_config()
    assert loaded.bettertouchtool_preset == "MyCustomPreset"


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
