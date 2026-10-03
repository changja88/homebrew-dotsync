import pytest
from dotsync.config import (
    Config,
    ConfigError,
    load_config,
    save_config,
    find_sync_folder,
    folder_config_path,
)


def test_folder_config_path_is_dotsync_toml(tmp_path):
    assert folder_config_path(tmp_path) == tmp_path / "dotsync.toml"


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
        'apps = ["zsh", "claude"]\n'
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))
    cfg = load_config()
    assert cfg.dir == folder
    assert cfg.apps == ["zsh", "claude"]


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


def test_save_creates_folder_if_missing(tmp_path):
    folder = tmp_path / "new-folder-not-yet-existing"
    cfg = Config(dir=folder, apps=["zsh"])
    save_config(cfg)
    assert folder.exists()
    assert (folder / "dotsync.toml").exists()


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
        'apps = ["bettertouchtool"]\n\n'
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


@pytest.mark.parametrize(
    "line, key",
    [
        ("backup_keep = 10", "backup_keep"),
        ('backup_dir = ".backups"', "backup_dir"),
        ('bettertouchtool_presets = ["A"]', "bettertouchtool_presets"),
        ('bettertouchtool_preset = "A"', "bettertouchtool_preset"),
    ],
)
def test_load_rejects_options_that_are_not_app_tables(monkeypatch, tmp_path, line, key):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(f'apps = ["zsh"]\n\n[options]\n{line}\n')
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))

    with pytest.raises(ConfigError, match=key):
        load_config()


def test_save_writes_only_apps_and_app_option_tables(tmp_path):
    cfg = Config(
        dir=tmp_path,
        apps=["bettertouchtool"],
        app_options={"bettertouchtool": {"presets": ["A"]}},
    )

    save_config(cfg)

    assert (tmp_path / "dotsync.toml").read_text() == (
        'apps = ["bettertouchtool"]\n\n[options.bettertouchtool]\npresets = ["A"]\n'
    )
