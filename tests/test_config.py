from pathlib import Path
import pytest
from dotsync.config import (
    Config,
    ConfigError,
    load_config,
    save_config,
    config_path,
    DEFAULT_BACKUP_DIR,
    DEFAULT_BACKUP_KEEP,
    DEFAULT_BTT_PRESET,
)


def test_config_path_uses_xdg_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_path() == tmp_path / "dotsync" / "config.toml"


def test_config_path_falls_back_to_home_config(fake_home, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config_path() == fake_home / ".config" / "dotsync" / "config.toml"


def test_load_missing_config_raises(fake_home, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    with pytest.raises(ConfigError, match="dotsync init"):
        load_config()


def test_save_then_load_roundtrip(fake_home, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    cfg = Config(dir=fake_home / "my-configs", apps=["claude", "zsh"])
    save_config(cfg)
    loaded = load_config()
    assert loaded.dir == fake_home / "my-configs"
    assert loaded.apps == ["claude", "zsh"]
    assert loaded.backup_dir == Path(DEFAULT_BACKUP_DIR).expanduser()
    assert loaded.backup_keep == DEFAULT_BACKUP_KEEP
    assert loaded.bettertouchtool_preset == DEFAULT_BTT_PRESET


def test_bettertouchtool_preset_roundtrip(fake_home, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    cfg = Config(
        dir=fake_home / "x",
        apps=["bettertouchtool"],
        bettertouchtool_preset="MyCustomPreset",
    )
    save_config(cfg)
    loaded = load_config()
    assert loaded.bettertouchtool_preset == "MyCustomPreset"


def test_load_rejects_relative_dir(fake_home, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    cfg_file = fake_home / ".config" / "dotsync" / "config.toml"
    cfg_file.parent.mkdir(parents=True)
    cfg_file.write_text('dir = "relative/path"\napps = []\n')
    with pytest.raises(ConfigError, match="absolute"):
        load_config()


def test_load_rejects_unknown_app(fake_home, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    cfg_file = fake_home / ".config" / "dotsync" / "config.toml"
    cfg_file.parent.mkdir(parents=True)
    cfg_file.write_text(f'dir = "{fake_home}/x"\napps = ["nonsense"]\n')
    with pytest.raises(ConfigError, match="unknown app"):
        load_config()


def test_save_creates_parent_dir(fake_home, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    cfg = Config(dir=fake_home / "x", apps=["zsh"])
    save_config(cfg)
    assert (fake_home / ".config" / "dotsync" / "config.toml").exists()
