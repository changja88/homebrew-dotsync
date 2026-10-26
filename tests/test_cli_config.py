import json

from dotsync.cli import main
from dotsync.config import Config, load_config, save_config


def _write_rc(home, folder):
    from dotsync.shellrc import MARKER, export_line

    rc = home / ".zshrc"
    rc.write_text(f"alias ll='ls -l'\n\n{MARKER}\n{export_line(folder)}\n")
    return rc


def _app_context(monkeypatch, home):
    """What a process started from Finder sees: no $DOTSYNC_DIR, a cwd
    outside the sync folder, and the folder named only by the rc line."""
    monkeypatch.delenv("DOTSYNC_DIR", raising=False)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.chdir(home)


def test_config_dir_updates_marked_rc_line(fake_home, monkeypatch, capsys):
    from dotsync.shellrc import marked_dir

    old, new = fake_home / "old", fake_home / "new"
    save_config(Config(dir=old, apps=["zsh"]))
    rc = _write_rc(fake_home, old)
    _app_context(monkeypatch, fake_home)

    assert main(["config", "dir", "--json", "--", str(new)]) == 0

    assert marked_dir(rc) == new.resolve()
    assert (new / "dotsync.toml").exists()
    assert load_config().dir == new.resolve()


def test_config_dir_json(fake_home, monkeypatch, capsys):
    old, new = fake_home / "old", fake_home / "new"
    save_config(Config(dir=old, apps=["zsh"]))
    rc = _write_rc(fake_home, old)
    _app_context(monkeypatch, fake_home)

    # The argv the dotsync app sends (SyncService.setDir).
    assert main(["config", "dir", "--json", "--", str(new)]) == 0

    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out) == {
        "dir": str(new.resolve()),
        "dir_source": "rc",
        "rc": {"action": "updated", "path": str(rc)},
    }


def test_config_dir_without_rc_file(fake_home, monkeypatch, capsys):
    old, new = fake_home / "old", fake_home / "new"
    save_config(Config(dir=old, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(old))
    monkeypatch.setenv("SHELL", "/bin/zsh")

    assert main(["config", "dir", str(new), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dir"] == str(new.resolve())
    assert payload["rc"] == {"action": "rc_missing", "path": None}
    # $DOTSYNC_DIR still names the old folder, so the next command will not
    # find the new one by itself.
    assert payload["dir_source"] == ""
    assert (new / "dotsync.toml").exists()
    assert not (fake_home / ".zshrc").exists()


def test_config_dir_keeps_the_folders_own_dotsync_toml(fake_home, monkeypatch, capsys):
    # Choosing a folder another Mac already syncs with must not replace its
    # tracked apps and options with this folder's.
    from dotsync.shellrc import marked_dir

    old, shared = fake_home / "old", fake_home / "shared"
    save_config(Config(dir=old, apps=["zsh"]))
    save_config(Config(dir=shared, apps=["codex", "bettertouchtool"],
                       app_options={"bettertouchtool": {"presets": ["Work"]}}))
    before = (shared / "dotsync.toml").read_bytes()
    rc = _write_rc(fake_home, old)
    _app_context(monkeypatch, fake_home)

    assert main(["config", "dir", "--json", "--", str(shared)]) == 0

    assert (shared / "dotsync.toml").read_bytes() == before
    assert marked_dir(rc) == shared.resolve()
    assert load_config().apps == ["codex", "bettertouchtool"]


def test_config_dir_json_starts_a_sync_folder_on_a_mac_without_one(fake_home, monkeypatch, capsys):
    # The first folder the dotsync app picks gets a dotsync.toml that tracks
    # nothing yet; the app's table turns the apps on.
    from dotsync.shellrc import marked_dir

    rc = fake_home / ".zshrc"
    rc.write_text("alias ll='ls -l'\n")
    _app_context(monkeypatch, fake_home)
    new = fake_home / "new"

    assert main(["config", "dir", "--json", "--", str(new)]) == 0

    assert json.loads(capsys.readouterr().out)["dir"] == str(new.resolve())
    assert marked_dir(rc) == new.resolve()
    cfg = load_config()
    assert (cfg.dir, cfg.apps, cfg.app_options) == (new.resolve(), [], {})


def test_config_dir_json_starts_over_when_the_rc_line_names_a_folder_gone(fake_home, monkeypatch, capsys):
    from dotsync.shellrc import marked_dir

    rc = _write_rc(fake_home, fake_home / "deleted")
    _app_context(monkeypatch, fake_home)
    new = fake_home / "new"

    assert main(["config", "dir", "--json", "--", str(new)]) == 0

    assert marked_dir(rc) == new.resolve()
    assert load_config().apps == []


def test_config_dir_refuses_a_folder_with_a_broken_dotsync_toml(fake_home, monkeypatch, capsys):
    from dotsync.shellrc import marked_dir

    old, broken = fake_home / "old", fake_home / "broken"
    save_config(Config(dir=old, apps=["zsh"]))
    broken.mkdir()
    (broken / "dotsync.toml").write_text("apps = [\n")
    rc = _write_rc(fake_home, old)
    _app_context(monkeypatch, fake_home)

    assert main(["config", "dir", "--json", "--", str(broken)]) == 1

    assert json.loads(capsys.readouterr().out)["error"]["code"] == "config"
    assert (broken / "dotsync.toml").read_text() == "apps = [\n"
    assert marked_dir(rc) == old.resolve()


def test_config_apps_json_saves_apps(fake_home, monkeypatch, capsys):
    target = fake_home / "configs"
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    assert main(["config", "apps", "zsh,claude", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "dir": str(target),
        "dir_source": "env",
        "apps": ["zsh", "claude"],
    }
    assert load_config().apps == ["zsh", "claude"]


def _discover_presets(monkeypatch, names):
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.discover_preset_names",
        classmethod(lambda cls: list(names)),
    )


def test_config_apps_json_finds_the_presets_of_bettertouchtool_turned_on(fake_home, monkeypatch, capsys):
    target = fake_home / "configs"
    save_config(Config(dir=target, apps=["zsh"]))
    _write_rc(fake_home, target)
    _app_context(monkeypatch, fake_home)
    _discover_presets(monkeypatch, ["Home", "Work"])

    assert main(["config", "apps", "zsh,bettertouchtool", "--json"]) == 0

    assert load_config().app_options == {"bettertouchtool": {"presets": ["Home", "Work"]}}


def test_config_apps_json_keeps_the_presets_of_bettertouchtool_left_on(fake_home, monkeypatch, capsys):
    target = fake_home / "configs"
    save_config(Config(dir=target, apps=["bettertouchtool"],
                       app_options={"bettertouchtool": {"presets": ["Mine"]}}))
    _write_rc(fake_home, target)
    _app_context(monkeypatch, fake_home)
    _discover_presets(monkeypatch, ["Other"])

    assert main(["config", "apps", "bettertouchtool,zsh", "--json"]) == 0

    assert load_config().app_options == {"bettertouchtool": {"presets": ["Mine"]}}


def test_config_apps_json_rejects_unknown(fake_home, monkeypatch, capsys):
    target = fake_home / "configs"
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    assert main(["config", "apps", "zsh,nope", "--json"]) == 1

    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "unknown_apps"
    assert "nope" in error["message"]
    assert load_config().apps == ["zsh"]


def test_config_apps_json_keeps_the_existing_order(fake_home, monkeypatch, capsys):
    # The dotsync app sends every tracked app in its table's order when one
    # switch flips; the order in dotsync.toml stays the person's.
    target = fake_home / "configs"
    save_config(Config(dir=target, apps=["zsh", "bettertouchtool", "claude"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    assert main(["config", "apps", "claude,codex,bettertouchtool,zsh", "--json"]) == 0

    assert load_config().apps == ["zsh", "bettertouchtool", "claude", "codex"]
    assert json.loads(capsys.readouterr().out)["apps"] == ["zsh", "bettertouchtool", "claude", "codex"]
