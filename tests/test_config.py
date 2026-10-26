import pytest
from dotsync.config import (
    Config,
    ConfigError,
    load_config,
    read_folder_config,
    save_config,
    locate_sync_folder,
    folder_config_path,
)


def test_folder_config_path_is_dotsync_toml(tmp_path):
    assert folder_config_path(tmp_path) == tmp_path / "dotsync.toml"


# ----- locate_sync_folder ----------------------------------------------------


def test_locate_sync_folder_reads_the_marked_rc_line(point_at, tmp_path):
    folder = tmp_path / "sync"
    save_config(Config(dir=folder, apps=[]))
    point_at(folder)

    assert locate_sync_folder() == folder


def test_locate_sync_folder_returns_none_when_nothing_found(fake_home):
    assert locate_sync_folder() is None


def test_locate_sync_folder_ignores_dotsync_dir_and_the_cwd(point_at, monkeypatch, tmp_path):
    # Only the rc line counts: it is what dotsync.app's folder pick writes.
    folders = {}
    for name in ("rc", "env", "cwd"):
        folders[name] = tmp_path / name
        save_config(Config(dir=folders[name], apps=[]))
    monkeypatch.setenv("DOTSYNC_DIR", str(folders["env"]))
    monkeypatch.chdir(folders["cwd"])

    assert locate_sync_folder() is None
    point_at(folders["rc"])
    assert locate_sync_folder() == folders["rc"]


# ----- load_config -----------------------------------------------------------


def test_load_without_a_sync_folder_points_at_the_app(fake_home):
    with pytest.raises(ConfigError, match="dotsync.app"):
        load_config()


def test_read_folder_config_of_a_missing_folder_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        read_folder_config(tmp_path / "does-not-exist")


def test_read_folder_config_without_dotsync_toml_raises(tmp_path):
    with pytest.raises(ConfigError, match="dotsync.toml"):
        read_folder_config(tmp_path)


def test_load_through_the_rc_line(point_at, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(
        'apps = ["zsh", "claude"]\n'
    )
    point_at(folder)
    cfg = load_config()
    assert cfg.dir == folder
    assert cfg.apps == ["zsh", "claude"]


def test_load_ignores_a_relative_rc_line(fake_home, monkeypatch, tmp_path):
    from dotsync.shellrc import MARKER

    monkeypatch.chdir(tmp_path)
    save_config(Config(dir=tmp_path / "relative", apps=[]))
    (fake_home / ".zshrc").write_text(f"{MARKER}\nexport DOTSYNC_DIR=relative\n")
    with pytest.raises(ConfigError):
        load_config()


def test_load_rejects_unknown_app(point_at, monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text('apps = ["nonsense"]\n')
    point_at(folder)
    with pytest.raises(ConfigError, match="unknown app"):
        load_config()


@pytest.mark.parametrize("value", ["false", "0", '""'])
def test_load_rejects_falsey_apps_when_not_list(point_at, monkeypatch, tmp_path, value):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(f"apps = {value}\n")
    point_at(folder)
    with pytest.raises(ConfigError, match="apps"):
        load_config()


def test_load_rejects_non_string_app_name(point_at, monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text("apps = [1]\n")
    point_at(folder)
    with pytest.raises(ConfigError, match="apps"):
        load_config()


def test_load_rejects_options_when_not_table(point_at, monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text('apps = ["zsh"]\noptions = "bad"\n')
    point_at(folder)
    with pytest.raises(ConfigError, match="options"):
        load_config()


@pytest.mark.parametrize("value", ["false", "0", "[]"])
def test_load_rejects_falsey_options_when_not_table(point_at, monkeypatch, tmp_path, value):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(f'apps = ["zsh"]\noptions = {value}\n')
    point_at(folder)
    with pytest.raises(ConfigError, match="options"):
        load_config()


# ----- save_config -----------------------------------------------------------


def test_save_writes_only_dotsync_toml_no_other_files(fake_home, tmp_path):
    """save_config must NOT create any file outside the sync folder."""
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


def test_save_then_load_roundtrip(point_at, monkeypatch, tmp_path):
    folder = tmp_path / "configs"
    folder.mkdir()
    cfg = Config(dir=folder, apps=["claude", "zsh"])
    save_config(cfg)

    point_at(folder)
    loaded = load_config()
    assert loaded.dir == folder
    assert loaded.apps == ["claude", "zsh"]


def test_save_creates_folder_if_missing(tmp_path):
    folder = tmp_path / "new-folder-not-yet-existing"
    cfg = Config(dir=folder, apps=["zsh"])
    save_config(cfg)
    assert folder.exists()
    assert (folder / "dotsync.toml").exists()


def test_load_corrupted_toml_raises_config_error(point_at, monkeypatch, tmp_path):
    """A hand-mangled dotsync.toml must surface as ConfigError, not raw
    TOMLDecodeError, so cli.py's friendly handler catches it."""
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "dotsync.toml").write_text('apps = ["zsh"\n[options\nbroken = ')
    point_at(folder)
    with pytest.raises(ConfigError, match="dotsync.toml"):
        load_config()


def test_config_app_options_default_is_empty_dict(tmp_path):
    cfg = Config(dir=tmp_path, apps=["zsh"])
    assert cfg.app_options == {}


def test_load_reads_app_options_subtables(point_at, monkeypatch, tmp_path):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(
        'apps = ["bettertouchtool"]\n\n'
        "[options.bettertouchtool]\n"
        'presets = ["A", "B"]\n'
    )
    point_at(folder)
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


def test_save_escapes_strings_for_toml(point_at, monkeypatch, tmp_path):
    folder = tmp_path / "quoted"
    folder.mkdir()
    cfg = Config(
        dir=folder,
        apps=["bettertouchtool"],
        app_options={"bettertouchtool": {"presets": ['Preset "Q"', "Back\\slash"]}},
    )
    save_config(cfg)

    point_at(folder)
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
def test_load_rejects_options_that_are_not_app_tables(point_at, monkeypatch, tmp_path, line, key):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "dotsync.toml").write_text(f'apps = ["zsh"]\n\n[options]\n{line}\n')
    point_at(folder)

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


# ----- the marked DOTSYNC_DIR line in the shell rc ---------------------------


def _marked_rc(home, folder, name=".zshrc"):
    from dotsync.shellrc import MARKER, export_line

    rc = home / name
    rc.write_text(f"export PATH=/x:$PATH\n\n{MARKER}\n{export_line(folder)}\n")
    return rc


def test_rc_line_ignored_without_dotsync_toml(fake_home, tmp_path):
    folder = tmp_path / "sync"
    folder.mkdir()
    _marked_rc(fake_home, folder)

    with pytest.raises(ConfigError):
        load_config()


def test_unmarked_export_line_is_ignored(fake_home, tmp_path):
    from dotsync.shellrc import export_line

    folder = tmp_path / "sync"
    save_config(Config(dir=folder, apps=[]))
    (fake_home / ".zshrc").write_text(export_line(folder) + "\n")

    with pytest.raises(ConfigError):
        load_config()


def test_rc_path_with_spaces_and_korean_round_trips(tmp_path):
    from dotsync.shellrc import MARKER, export_line, marked_dir

    folder = tmp_path / "Mobile Documents" / "it's dotsync 설정"
    rc = tmp_path / ".zshrc"
    rc.write_text(f"{MARKER}\n{export_line(folder)}\n")

    assert marked_dir(rc) == folder


def test_marked_dir_missing_file_or_line(tmp_path):
    from dotsync.shellrc import MARKER, marked_dir

    assert marked_dir(tmp_path / "nope") is None
    rc = tmp_path / ".zshrc"
    rc.write_text(f"{MARKER}\necho hi\n")
    assert marked_dir(rc) is None


def test_rc_found_when_shell_unset(fake_home, monkeypatch, tmp_path):
    monkeypatch.delenv("SHELL", raising=False)
    folder = tmp_path / "sync"
    save_config(Config(dir=folder, apps=[]))
    _marked_rc(fake_home, folder, ".zshrc")

    assert load_config().dir == folder


def test_rc_candidates_order_and_dedup(tmp_path):
    from dotsync.shellrc import rc_candidates

    assert rc_candidates(shell="/bin/zsh", home=tmp_path) == [
        tmp_path / ".zshrc",
        tmp_path / ".bash_profile",
        tmp_path / ".bashrc",
    ]
    assert rc_candidates(shell="", home=tmp_path)[0] == tmp_path / ".zshrc"
