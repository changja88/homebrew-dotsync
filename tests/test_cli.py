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


def test_init_writes_config_noninteractive(fake_home, tmp_path):
    target = tmp_path / "myconfigs"
    rc = main(["init", "--dir", str(target), "--apps", "zsh,ghostty", "--yes"])
    assert rc == 0
    cfg_file = target / "dotsync.toml"
    assert cfg_file.exists()
    assert "zsh" in cfg_file.read_text()
    assert target.exists()
    # NO file/dir created outside the sync folder
    assert not (fake_home / ".dotsync").exists()
    assert not (fake_home / ".config").exists()


def test_init_with_btt_preset_flag(fake_home, tmp_path):
    target = tmp_path / "myconfigs"
    rc = main([
        "init", "--dir", str(target),
        "--apps", "bettertouchtool",
        "--btt-preset", "MyPreset",
        "--yes",
    ])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert "MyPreset" in cfg_text


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


def test_from_single_app_calls_sync_from(fake_home, monkeypatch, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("X")

    rc = main(["from", "zsh"])
    assert rc == 0
    assert (target / "zsh" / ".zshrc").read_text() == "X"


def test_to_all_iterates_registered_apps(fake_home, monkeypatch, tmp_path):
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("Z")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    rc = main(["to", "--all"])
    assert rc == 0
    assert (fake_home / ".zshrc").read_text() == "Z"


def test_no_config_shows_init_hint(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)  # cwd has no dotsync.toml
    rc = main(["from", "--all"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "dotsync init" in err or "DOTSYNC_DIR" in err


def test_status_reports_diff(fake_home, monkeypatch, tmp_path, capsys):
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("STORED")
    (fake_home / ".zshrc").write_text("LOCAL")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    rc = main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "zsh" in out
    assert "dirty" in out


def test_runtime_error_caught_with_friendly_exit(fake_home, monkeypatch, tmp_path, capsys):
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("Z")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    with patch("dotsync.apps.zsh.shutil.copy2", side_effect=RuntimeError("disk full")):
        rc = main(["to", "zsh"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "disk full" in err


def _no_btt(monkeypatch, fake_home):
    """Make BTT detection return False regardless of host machine state."""
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.APP_PATH",
        fake_home / "no-btt.app",
    )


def test_init_yes_without_apps_uses_detected(fake_home, tmp_path, monkeypatch):
    (fake_home / ".zshrc").write_text("X")
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "settings.json").write_text("{}")
    _no_btt(monkeypatch, fake_home)

    target = tmp_path / "configs"
    rc = main(["init", "--dir", str(target), "--yes"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    apps_line = next(l for l in cfg_text.splitlines() if l.startswith("apps = "))
    assert "zsh" in apps_line
    assert "claude" in apps_line
    assert "bettertouchtool" not in apps_line
    assert "ghostty" not in apps_line


def test_init_yes_no_apps_and_no_detected_errors(fake_home, tmp_path, monkeypatch, capsys):
    _no_btt(monkeypatch, fake_home)
    target = tmp_path / "configs"
    rc = main(["init", "--dir", str(target), "--yes"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "no apps detected" in err.lower() or "no apps" in err.lower()


def test_init_yes_with_existing_dotsync_toml_reuses_it(fake_home, tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "dotsync.toml").write_text(
        'apps = ["claude"]\n\n[options]\n'
        'backup_keep = 10\n'
        'bettertouchtool_preset = "Existing"\n'
    )
    rc = main(["init", "--dir", str(target), "--yes"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert 'apps = ["claude"]' in cfg_text
    assert "Existing" in cfg_text
    # no other files created
    assert not (fake_home / ".dotsync").exists()
    assert not (fake_home / ".config").exists()


def test_init_yes_existing_toml_with_explicit_overrides(fake_home, tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "dotsync.toml").write_text(
        'apps = ["claude"]\n\n[options]\nbettertouchtool_preset = "Old"\n'
    )
    rc = main([
        "init", "--dir", str(target),
        "--apps", "zsh,ghostty",
        "--btt-preset", "New",
        "--yes",
    ])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert "zsh" in cfg_text and "ghostty" in cfg_text
    assert "claude" not in cfg_text  # overridden
    assert "New" in cfg_text


def test_init_interactive_uses_detected_default_on_enter(fake_home, tmp_path, monkeypatch):
    (fake_home / ".zshrc").write_text("X")
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "settings.json").write_text("{}")
    _no_btt(monkeypatch, fake_home)

    target = tmp_path / "i"
    answers = iter([str(target), ""])  # folder path, Enter on "Track all?"
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert "zsh" in cfg_text
    assert "claude" in cfg_text


def test_init_interactive_edit_lets_user_pick_apps(fake_home, tmp_path, monkeypatch):
    (fake_home / ".zshrc").write_text("X")
    _no_btt(monkeypatch, fake_home)

    target = tmp_path / "i"
    answers = iter([str(target), "edit", "ghostty,zsh"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert "ghostty" in cfg_text
    assert "zsh" in cfg_text


def test_init_prints_how_to_change_hint(fake_home, tmp_path, monkeypatch, capsys):
    (fake_home / ".zshrc").write_text("X")
    _no_btt(monkeypatch, fake_home)
    target = tmp_path / "h"
    rc = main(["init", "--dir", str(target), "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DOTSYNC_DIR" in out
    assert "dotsync config apps" in out


def test_init_shows_welcome_by_default(fake_home, tmp_path, monkeypatch, capsys):
    (fake_home / ".zshrc").write_text("X")
    _no_btt(monkeypatch, fake_home)
    target = tmp_path / "w"
    rc = main(["init", "--dir", str(target), "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "█" in out             # welcome ASCII logo
    assert "Quickstart" in out


def test_init_quiet_skips_welcome(fake_home, tmp_path, monkeypatch, capsys):
    (fake_home / ".zshrc").write_text("X")
    _no_btt(monkeypatch, fake_home)
    target = tmp_path / "wq"
    rc = main(["init", "--dir", str(target), "--yes", "--quiet"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "█" not in out


def test_welcome_subcommand(capsys):
    rc = main(["welcome"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "█" in out
    assert "dotsync init" in out


def test_init_yes_without_dir_uses_default_desktop_path(fake_home, monkeypatch):
    _no_btt(monkeypatch, fake_home)
    (fake_home / ".zshrc").write_text("X")

    rc = main(["init", "--yes"])
    assert rc == 0
    default_dir = fake_home / "Desktop" / "dotsync_config"
    assert default_dir.exists()
    assert (default_dir / "dotsync.toml").exists()


def test_init_interactive_default_dir_on_empty_input(fake_home, monkeypatch):
    _no_btt(monkeypatch, fake_home)
    (fake_home / ".zshrc").write_text("X")

    # user just hits Enter for the dir prompt, then Enter for "Track all?"
    answers = iter(["", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    default_dir = fake_home / "Desktop" / "dotsync_config"
    assert (default_dir / "dotsync.toml").exists()


def test_init_interactive_custom_dir_overrides_default(fake_home, monkeypatch, tmp_path):
    _no_btt(monkeypatch, fake_home)
    (fake_home / ".zshrc").write_text("X")
    custom = tmp_path / "elsewhere"

    answers = iter([str(custom), ""])  # custom path, then Enter for "Track all?"
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    assert (custom / "dotsync.toml").exists()
    # default not used
    assert not (fake_home / "Desktop" / "dotsync_config").exists()


def test_config_error_uses_ui_error_styling(fake_home, monkeypatch, tmp_path, capsys):
    """ConfigError must be rendered with the design-system error glyph,
    not raw text. (NO_COLOR keeps it deterministic in tests.)"""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.chdir(tmp_path)  # cwd has no dotsync.toml
    rc = main(["status"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "✗" in err  # ui.error glyph
    assert "dotsync init" in err  # next-action hint preserved


def test_no_args_shows_welcome(capsys):
    """`dotsync` with no subcommand should print the welcome banner
    (quickstart guidance) and exit 0, not raise argparse error."""
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "██████╗" in out  # ASCII logo present (block chars)
    assert "Quickstart" in out
