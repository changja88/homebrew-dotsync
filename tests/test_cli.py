from pathlib import Path
from unittest.mock import patch
import pytest
from dotsync.cli import main
from dotsync.config import Config, save_config


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "0.1.0" in out


def test_init_writes_config_noninteractive(fake_home, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "myconfigs"
    rc = main(["init", "--dir", str(target), "--apps", "zsh,ghostty", "--yes"])
    assert rc == 0
    cfg_file = fake_home / ".config" / "dotsync" / "config.toml"
    assert cfg_file.exists()
    assert "zsh" in cfg_file.read_text()
    assert target.exists()


def test_init_with_btt_preset_flag(fake_home, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "myconfigs"
    rc = main([
        "init", "--dir", str(target),
        "--apps", "bettertouchtool",
        "--btt-preset", "MyPreset",
        "--yes",
    ])
    assert rc == 0
    cfg_text = (fake_home / ".config" / "dotsync" / "config.toml").read_text()
    assert "MyPreset" in cfg_text


def test_config_show(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    rc = main(["config", "show"])
    assert rc == 0
    out = capsys.readouterr().out
    assert str(target) in out
    assert "zsh" in out


def test_from_single_app_calls_sync_from(fake_home, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    (fake_home / ".zshrc").write_text("X")

    rc = main(["from", "zsh"])
    assert rc == 0
    assert (target / "zsh" / ".zshrc").read_text() == "X"


def test_to_all_iterates_registered_apps(fake_home, monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("Z")
    save_config(Config(dir=target, apps=["zsh"], backup_dir=tmp_path / "bk"))

    rc = main(["to", "--all"])
    assert rc == 0
    assert (fake_home / ".zshrc").read_text() == "Z"


def test_no_config_shows_init_hint(fake_home, monkeypatch, capsys):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    rc = main(["from", "--all"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "dotsync init" in err


def test_status_reports_diff(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("STORED")
    (fake_home / ".zshrc").write_text("LOCAL")
    save_config(Config(dir=target, apps=["zsh"]))

    rc = main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "zsh" in out
    assert "dirty" in out


def test_runtime_error_caught_with_friendly_exit(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("Z")
    save_config(Config(dir=target, apps=["zsh"], backup_dir=tmp_path / "bk"))

    with patch("dotsync.apps.zsh.shutil.copy2", side_effect=RuntimeError("disk full")):
        rc = main(["to", "zsh"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "disk full" in err
