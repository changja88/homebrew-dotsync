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
