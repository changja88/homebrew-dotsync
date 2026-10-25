import json

from dotsync.cli import main
from dotsync.config import Config, load_config, save_config


def test_config_show(fake_home, monkeypatch, tmp_path, capsys):
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    rc = main(["config", "show"])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(target) in out
    assert "zsh" in out


def test_config_show_prints_app_options(fake_home, monkeypatch, tmp_path, capsys):
    target = tmp_path / "configs"
    target.mkdir()
    save_config(
        Config(
            dir=target,
            apps=["bettertouchtool"],
            app_options={"bettertouchtool": {"presets": ["Foo", "Bar"]}},
        )
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    rc = main(["config", "show"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "app_options" in out
    assert "Foo" in out
    assert "Bar" in out


def test_config_btt_presets_rejects_unsafe_names(
    fake_home, monkeypatch, tmp_path, capsys
):
    target = tmp_path / "configs"
    target.mkdir()
    save_config(
        Config(
            dir=target,
            apps=["bettertouchtool"],
            app_options={"bettertouchtool": {"presets": ["Safe"]}},
        )
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    rc = main(["config", "btt-presets", 'Bad"Name'])

    assert rc == 2
    err = capsys.readouterr().err
    assert "preset" in err
    assert load_config().app_options["bettertouchtool"]["presets"] == ["Safe"]


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

    assert main(["config", "dir", str(new)]) == 0

    assert marked_dir(rc) == new.resolve()
    assert (new / "dotsync.toml").exists()
    assert "updated" in capsys.readouterr().out
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


def test_config_apps_json_rejects_unknown(fake_home, monkeypatch, capsys):
    target = fake_home / "configs"
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    assert main(["config", "apps", "zsh,nope", "--json"]) == 1

    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "unknown_apps"
    assert "nope" in error["message"]
    assert load_config().apps == ["zsh"]


def test_config_show_json(fake_home, monkeypatch, capsys):
    target = fake_home / "configs"
    save_config(
        Config(
            dir=target,
            apps=["bettertouchtool"],
            app_options={"bettertouchtool": {"presets": ["Foo"]}},
        )
    )
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    assert main(["config", "show", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "dir": str(target),
        "dir_source": "env",
        "apps": ["bettertouchtool"],
        "app_options": {"bettertouchtool": {"presets": ["Foo"]}},
    }


def test_config_apps_json_keeps_the_existing_order(fake_home, monkeypatch, capsys):
    # The dotsync app sends every tracked app in its table's order when one
    # switch flips; the order in dotsync.toml stays the person's.
    target = fake_home / "configs"
    save_config(Config(dir=target, apps=["zsh", "bettertouchtool", "claude"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    assert main(["config", "apps", "claude,codex,bettertouchtool,zsh", "--json"]) == 0

    assert load_config().apps == ["zsh", "bettertouchtool", "claude", "codex"]
    assert json.loads(capsys.readouterr().out)["apps"] == ["zsh", "bettertouchtool", "claude", "codex"]
