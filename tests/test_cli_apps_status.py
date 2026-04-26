import os
from pathlib import Path
import pytest
from dotsync.cli import main
from dotsync.config import Config, save_config


def test_status_reports_diff(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NO_COLOR", "1")
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
    assert "⚠" in out  # design-system glyph for dirty


def test_status_shows_direction_hint(fake_home, monkeypatch, tmp_path, capsys):
    """When local is newer than stored, status surfaces 'local-newer' so the
    user knows to run `from`."""
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    stored = target / "zsh" / ".zshrc"
    local = fake_home / ".zshrc"
    stored.write_text("OLD")
    local.write_text("NEW")
    os.utime(stored, (1000, 1000))
    os.utime(local, (2000, 2000))
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    rc = main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "local-newer" in out


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


def test_apps_lets_user_change_tracked_set_via_picker(fake_home, monkeypatch, tmp_path):
    """`dotsync apps` runs the same picker as init Step 2. Under pytest
    (non-TTY) the picker falls back to per-app y/n in sorted order:
    bettertouchtool, claude, ghostty, zsh."""
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.APP_PATH",
        fake_home / "no-btt.app",
    )

    # btt=n, claude=y (toggle on), ghostty=n, zsh=Enter (preselected → keeps Y)
    answers = iter(["n", "y", "n", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["apps"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert '"claude"' in cfg_text
    assert '"zsh"' in cfg_text
    assert '"ghostty"' not in cfg_text
    assert '"bettertouchtool"' not in cfg_text


def test_apps_no_change_when_user_keeps_current_set(fake_home, monkeypatch, tmp_path):
    """If the picker returns the same set as the saved config, nothing
    is rewritten and a 'no change' line is shown."""
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["claude", "zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    mtime_before = (target / "dotsync.toml").stat().st_mtime_ns

    monkeypatch.setattr(
        "dotsync.ui_picker.pick_apps",
        lambda items, preselected, detected, **kw: ["claude", "zsh"],
    )

    rc = main(["apps"])
    assert rc == 0
    mtime_after = (target / "dotsync.toml").stat().st_mtime_ns
    assert mtime_before == mtime_after


def test_apps_cancel_keeps_current_config(fake_home, monkeypatch, tmp_path, capsys):
    """Cancelling the picker leaves the saved config untouched and prints
    a quiet 'cancelled' marker."""
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh", "claude"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    monkeypatch.setattr(
        "dotsync.ui_picker.pick_apps",
        lambda items, preselected, detected, **kw: None,
    )

    rc = main(["apps"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert '"zsh"' in cfg_text
    assert '"claude"' in cfg_text
    out = capsys.readouterr().out
    assert "cancelled" in out


def test_apps_toggle_on_btt_auto_discovers_presets(fake_home, monkeypatch, tmp_path):
    """Adding BTT to the tracked set re-runs preset discovery so the saved
    list reflects the current BTT state — the user doesn't have to remember
    a separate `dotsync config btt-presets` step."""
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"], bettertouchtool_presets=["Old"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.discover_preset_names",
        classmethod(lambda cls: ["Master_bt", "Travel"]),
    )
    monkeypatch.setattr(
        "dotsync.ui_picker.pick_apps",
        lambda items, preselected, detected, **kw: ["bettertouchtool", "zsh"],
    )

    rc = main(["apps"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    # New format: presets stored under [options.bettertouchtool] sub-table.
    assert 'presets = ["Master_bt", "Travel"]' in cfg_text
    assert '"bettertouchtool"' in cfg_text


def test_apps_requires_initialized_config(fake_home, monkeypatch, tmp_path, capsys):
    """`dotsync apps` edits an existing tracked set, so it requires init
    to have run first. Without a config it fails the same way other
    config-needing commands do (the user is pointed back at `dotsync init`)."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.chdir(tmp_path)  # no DOTSYNC_DIR, no dotsync.toml in cwd
    rc = main(["apps"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "dotsync init" in err or "DOTSYNC_DIR" in err
