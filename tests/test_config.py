from pathlib import Path
import pytest
from dotsync.config import (
    Config,
    ConfigError,
    load_config,
    save_config,
    pointer_path,
    read_pointer,
    write_pointer,
    folder_config_path,
    DEFAULT_BACKUP_DIR,
    DEFAULT_BACKUP_KEEP,
    DEFAULT_BTT_PRESET,
)


def test_pointer_path_is_dot_dotsync_in_home(fake_home):
    assert pointer_path() == fake_home / ".dotsync"


def test_folder_config_path_is_dotsync_toml(tmp_path):
    assert folder_config_path(tmp_path) == tmp_path / "dotsync.toml"


def test_load_missing_pointer_raises(fake_home):
    with pytest.raises(ConfigError, match="dotsync init"):
        load_config()


def test_load_pointer_to_missing_folder_raises(fake_home, tmp_path):
    write_pointer(tmp_path / "does-not-exist")
    with pytest.raises(ConfigError, match="not found"):
        load_config()


def test_load_pointer_to_folder_without_dotsync_toml_raises(fake_home, tmp_path):
    folder = tmp_path / "empty-folder"
    folder.mkdir()
    write_pointer(folder)
    with pytest.raises(ConfigError, match="dotsync.toml"):
        load_config()


def test_save_writes_pointer_and_folder_toml(fake_home, tmp_path):
    folder = tmp_path / "myfolder"
    folder.mkdir()
    cfg = Config(dir=folder, apps=["zsh"])
    save_config(cfg)

    # pointer was written
    pointer = fake_home / ".dotsync"
    assert pointer.exists()
    assert pointer.read_text().strip() == str(folder)

    # folder config was written
    toml_path = folder / "dotsync.toml"
    assert toml_path.exists()
    text = toml_path.read_text()
    assert 'apps = ["zsh"]' in text
    # folder doesn't record its own location at top level (pointer holds it)
    import re
    assert re.search(r'^dir\s*=', text, re.MULTILINE) is None


def test_save_then_load_roundtrip(fake_home, tmp_path):
    folder = tmp_path / "configs"
    folder.mkdir()
    cfg = Config(dir=folder, apps=["claude", "zsh"])
    save_config(cfg)

    loaded = load_config()
    assert loaded.dir == folder
    assert loaded.apps == ["claude", "zsh"]
    assert loaded.backup_dir == Path(DEFAULT_BACKUP_DIR).expanduser()
    assert loaded.backup_keep == DEFAULT_BACKUP_KEEP
    assert loaded.bettertouchtool_preset == DEFAULT_BTT_PRESET


def test_bettertouchtool_preset_roundtrip(fake_home, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    cfg = Config(dir=folder, apps=["bettertouchtool"], bettertouchtool_preset="MyCustomPreset")
    save_config(cfg)
    loaded = load_config()
    assert loaded.bettertouchtool_preset == "MyCustomPreset"


def test_load_rejects_unknown_app(fake_home, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    write_pointer(folder)
    (folder / "dotsync.toml").write_text('apps = ["nonsense"]\n')
    with pytest.raises(ConfigError, match="unknown app"):
        load_config()


def test_load_rejects_relative_pointer(fake_home):
    # write a relative path directly to the pointer file
    (fake_home / ".dotsync").write_text("relative/path\n")
    with pytest.raises(ConfigError, match="absolute"):
        load_config()


def test_save_creates_folder_if_missing(fake_home, tmp_path):
    folder = tmp_path / "new-folder-not-yet-existing"
    cfg = Config(dir=folder, apps=["zsh"])
    save_config(cfg)
    assert folder.exists()
    assert (folder / "dotsync.toml").exists()
