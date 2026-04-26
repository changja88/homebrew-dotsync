from pathlib import Path
from unittest.mock import patch
import pytest
from dotsync import __version__
from dotsync.cli import main
from dotsync.config import Config, save_config


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out


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


def test_init_with_btt_presets_flag(fake_home, tmp_path):
    target = tmp_path / "myconfigs"
    rc = main([
        "init", "--dir", str(target),
        "--apps", "bettertouchtool",
        "--btt-presets", "MyPreset,Other",
        "--yes",
    ])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    # New format: presets stored under [options.bettertouchtool] sub-table.
    assert 'presets = ["MyPreset", "Other"]' in cfg_text


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

    rc = main(["to", "--all", "--yes"])
    assert rc == 0
    assert (fake_home / ".zshrc").read_text() == "Z"


def test_no_config_shows_init_hint(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)  # cwd has no dotsync.toml
    rc = main(["from", "--all"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "dotsync init" in err or "DOTSYNC_DIR" in err


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
    import os
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


def test_runtime_error_caught_with_friendly_exit(fake_home, monkeypatch, tmp_path, capsys):
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("Z")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    with patch("dotsync.apps.base.shutil.copy2", side_effect=RuntimeError("disk full")):
        rc = main(["to", "zsh", "--yes"])
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
        "--btt-presets", "New",
        "--yes",
    ])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert "zsh" in cfg_text and "ghostty" in cfg_text
    assert "claude" not in cfg_text  # overridden
    # BTT is not in apps, so --btt-presets is silently ignored (no BTT section written)
    assert "[options.bettertouchtool]" not in cfg_text


def test_init_interactive_picker_keeps_detected_on_bare_enter(fake_home, tmp_path, monkeypatch):
    """In non-TTY (pytest) the picker falls back to per-app y/n with each
    detected app pre-defaulted to Y. Bare Enter on every row therefore
    keeps the detected set."""
    (fake_home / ".zshrc").write_text("X")
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "settings.json").write_text("{}")
    _no_btt(monkeypatch, fake_home)

    target = tmp_path / "i"
    # folder + 4 picker fallback answers (sorted: bettertouchtool, claude,
    # ghostty, zsh). Bare Enter keeps each row's default.
    answers = iter([str(target), "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert '"zsh"' in cfg_text
    assert '"claude"' in cfg_text
    assert '"ghostty"' not in cfg_text
    assert '"bettertouchtool"' not in cfg_text


def test_init_interactive_picker_lets_user_change_selection(fake_home, tmp_path, monkeypatch):
    """The picker (fallback mode under pytest) lets the user toggle off
    a preselected app and toggle on an undetected one."""
    (fake_home / ".zshrc").write_text("X")
    _no_btt(monkeypatch, fake_home)

    target = tmp_path / "i"
    # folder + per-app y/n in sorted order: bettertouchtool, claude, ghostty, zsh.
    # Default is N for unselected items, Y for the preselected zsh.
    # User picks ghostty (toggle on) + zsh (default), drops everything else.
    answers = iter([str(target), "n", "n", "y", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert '"ghostty"' in cfg_text
    assert '"zsh"' in cfg_text
    assert '"claude"' not in cfg_text
    assert '"bettertouchtool"' not in cfg_text


def test_init_prints_next_steps_with_apps_command_hint(fake_home, tmp_path, monkeypatch, capsys):
    """The next-steps block points users at `dotsync apps` for ongoing
    changes (the previous `dotsync config apps` was removed)."""
    (fake_home / ".zshrc").write_text("X")
    _no_btt(monkeypatch, fake_home)
    target = tmp_path / "h"
    rc = main(["init", "--dir", str(target), "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DOTSYNC_DIR" in out
    assert "dotsync apps" in out
    assert "dotsync from --all" in out
    assert "dotsync to --all" in out


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

    # bare Enter for the dir prompt + 4 picker fallback answers (Enter on each)
    answers = iter(["", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    default_dir = fake_home / "Desktop" / "dotsync_config"
    assert (default_dir / "dotsync.toml").exists()


def test_init_interactive_custom_dir_overrides_default(fake_home, monkeypatch, tmp_path):
    _no_btt(monkeypatch, fake_home)
    (fake_home / ".zshrc").write_text("X")
    custom = tmp_path / "elsewhere"

    # custom path + 4 picker fallback answers (Enter on each)
    answers = iter([str(custom), "", "", "", ""])
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


def test_from_continues_after_one_app_fails(fake_home, monkeypatch, tmp_path, capsys):
    """If one app raises during `from --all`, others should still run
    and the summary should report 1 ok / 1 error."""
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh", "ghostty"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("Z")
    # ghostty source missing → its sync_from raises FileNotFoundError

    rc = main(["from", "--all"])
    out = capsys.readouterr().out
    # zsh succeeded (file copied)
    assert (target / "zsh" / ".zshrc").read_text() == "Z"
    # summary line shows 1 ok and 1 error
    assert "1 ok" in out
    assert "1 error" in out
    # exit code reflects partial failure
    assert rc != 0


def test_to_dry_run_does_not_change_local_or_create_backup(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("FROM_FOLDER")
    (fake_home / ".zshrc").write_text("LOCAL_ORIG")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    rc = main(["to", "--all", "--dry-run"])
    assert rc == 0
    # local untouched
    assert (fake_home / ".zshrc").read_text() == "LOCAL_ORIG"
    # no backup directory created (other than maybe the parent .backups root)
    backups_root = target / ".backups"
    assert not backups_root.exists() or not any(backups_root.iterdir())
    out = capsys.readouterr().out
    assert "dry-run" in out.lower()
    # preview should still show what would change
    assert "zsh" in out


def test_to_prompts_confirmation_by_default(fake_home, monkeypatch, tmp_path):
    """Without --yes or --dry-run, `to` must ask before overwriting."""
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("FROM_FOLDER")
    (fake_home / ".zshrc").write_text("LOCAL_ORIG")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    answers = iter(["n"])  # decline
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["to", "--all"])
    assert rc == 0
    # decline → local untouched
    assert (fake_home / ".zshrc").read_text() == "LOCAL_ORIG"


def test_to_with_yes_skips_prompt_and_applies(fake_home, monkeypatch, tmp_path):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("FROM_FOLDER")
    (fake_home / ".zshrc").write_text("LOCAL_ORIG")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    rc = main(["to", "--all", "--yes"])
    assert rc == 0
    assert (fake_home / ".zshrc").read_text() == "FROM_FOLDER"


def test_to_bare_enter_aborts(fake_home, monkeypatch, tmp_path):
    """Bare Enter (empty input) must abort, since the prompt is destructive
    and `default="y/N"` is only a display hint, not a return default."""
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("FROM_FOLDER")
    (fake_home / ".zshrc").write_text("LOCAL_ORIG")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    rc = main(["to", "--all"])
    assert rc == 0
    assert (fake_home / ".zshrc").read_text() == "LOCAL_ORIG"


def test_init_no_hints_skips_next_steps_block(fake_home, tmp_path, capsys):
    """--no-hints suppresses the 'next steps' guidance block (used by demo
    RAW mode to keep the install screen minimal)."""
    target = tmp_path / "configs"
    rc = main(["init", "--dir", str(target), "--apps", "zsh", "--yes",
               "--quiet", "--no-hints"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "next steps" not in out
    assert "config saved" in out  # the saved line is still shown


def test_init_no_hints_also_suppresses_adopt_branch_hints(fake_home, tmp_path, capsys):
    target = tmp_path / "existing"
    target.mkdir()
    (target / "dotsync.toml").write_text(
        'apps = ["zsh"]\n\n[options]\nbackup_keep = 10\n'
        'bettertouchtool_preset = "Master_bt"\n'
    )
    rc = main(["init", "--dir", str(target), "--yes", "--quiet", "--no-hints"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "next steps" not in out
    assert "adopted existing config" in out


def test_init_step_headers_visible(fake_home, tmp_path, monkeypatch, capsys):
    """The init flow surfaces explicit step headers so the user knows where
    they are in the wizard."""
    monkeypatch.setenv("NO_COLOR", "1")
    (fake_home / ".zshrc").write_text("X")
    _no_btt(monkeypatch, fake_home)

    target = tmp_path / "i"
    answers = iter([str(target), "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Step 1 — Sync folder" in out
    assert "Step 2 — Pick apps to track" in out
    assert "folder ready" in out
    assert "tracked:" in out


def test_apps_lets_user_change_tracked_set_via_picker(fake_home, monkeypatch, tmp_path):
    """`dotsync apps` runs the same picker as init Step 2. Under pytest
    (non-TTY) the picker falls back to per-app y/n in sorted order:
    bettertouchtool, claude, ghostty, zsh."""
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    _no_btt(monkeypatch, fake_home)

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


def test_init_btt_auto_uses_single_discovered_preset(fake_home, tmp_path, monkeypatch, capsys):
    """When BTT discovery returns exactly 1 preset, init uses it without
    prompting — the user sees a confirmation."""
    monkeypatch.setenv("NO_COLOR", "1")
    (fake_home / ".zshrc").write_text("X")
    bttapp = fake_home / "Applications" / "BetterTouchTool.app"
    bttapp.mkdir(parents=True)
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.APP_PATH", bttapp
    )
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.discover_preset_names",
        classmethod(lambda cls: ["Master_bt"]),
    )

    target = tmp_path / "i"
    # folder + 4 picker fallback Enters (preselected: bettertouchtool, zsh)
    answers = iter([str(target), "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    out = capsys.readouterr().out
    cfg_text = (target / "dotsync.toml").read_text()
    # New format: presets stored under [options.bettertouchtool] sub-table.
    assert 'presets = ["Master_bt"]' in cfg_text
    assert "Master_bt" in out
    # The legacy single-preset prompt must not appear under the new flow
    assert "BetterTouchTool preset name" not in out
    assert "which preset to track" not in out


def test_init_btt_auto_tracks_every_discovered_preset(fake_home, tmp_path, monkeypatch, capsys):
    """Multiple presets → all of them tracked automatically. No prompt."""
    monkeypatch.setenv("NO_COLOR", "1")
    (fake_home / ".zshrc").write_text("X")
    bttapp = fake_home / "Applications" / "BetterTouchTool.app"
    bttapp.mkdir(parents=True)
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.APP_PATH", bttapp
    )
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.discover_preset_names",
        classmethod(lambda cls: ["Master_bt", "Travel", "Work"]),
    )

    target = tmp_path / "i"
    answers = iter([str(target), "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    out = capsys.readouterr().out
    cfg_text = (target / "dotsync.toml").read_text()
    # New format: presets stored under [options.bettertouchtool] sub-table.
    assert 'presets = ["Master_bt", "Travel", "Work"]' in cfg_text
    assert "Master_bt" in out and "Travel" in out and "Work" in out
    assert "which preset to track" not in out


def test_init_btt_falls_back_to_default_when_discovery_empty(fake_home, tmp_path, monkeypatch):
    """No presets discovered (BTT not running, schema drift, etc.) →
    DEFAULT_BTT_PRESETS used silently. No prompt under the new flow."""
    (fake_home / ".zshrc").write_text("X")
    bttapp = fake_home / "Applications" / "BetterTouchTool.app"
    bttapp.mkdir(parents=True)
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.APP_PATH", bttapp
    )
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.discover_preset_names",
        classmethod(lambda cls: []),
    )

    target = tmp_path / "i"
    answers = iter([str(target), "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert 'bettertouchtool_presets = ["Master_bt"]' in cfg_text


def test_init_btt_presets_flag_skips_discovery(fake_home, tmp_path, monkeypatch):
    """--btt-presets X,Y always wins, even when discovery would return
    different names. No discovery call is needed."""
    (fake_home / ".zshrc").write_text("X")
    bttapp = fake_home / "Applications" / "BetterTouchTool.app"
    bttapp.mkdir(parents=True)
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.APP_PATH", bttapp
    )

    def boom(cls):
        raise AssertionError("discover_preset_names must not be called when --btt-presets is set")
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.discover_preset_names",
        classmethod(boom),
    )

    target = tmp_path / "i"
    rc = main(["init", "--dir", str(target), "--yes", "--btt-presets", "Forced,Other"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    # New format: presets stored under [options.bettertouchtool] sub-table.
    assert 'presets = ["Forced", "Other"]' in cfg_text


def test_init_btt_yes_without_flag_uses_default_skips_discovery(fake_home, tmp_path, monkeypatch):
    """--yes without --btt-presets must not consult discovery; DEFAULT_BTT_PRESETS wins.
    --yes mode is deterministic regardless of what BTT happens to have on the machine."""
    (fake_home / ".zshrc").write_text("X")
    bttapp = fake_home / "Applications" / "BetterTouchTool.app"
    bttapp.mkdir(parents=True)
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.APP_PATH", bttapp
    )
    def boom(cls):
        raise AssertionError("discovery must not run under --yes")
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.discover_preset_names",
        classmethod(boom),
    )
    target = tmp_path / "i"
    rc = main(["init", "--dir", str(target), "--yes"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert 'bettertouchtool_presets = ["Master_bt"]' in cfg_text


def test_cmd_to_surfaces_app_warnings_in_summary(fake_home, monkeypatch, capsys, tmp_path):
    """Warnings collected on the App during sync show up after the summary
    so partial failures aren't silenced."""
    monkeypatch.setenv("NO_COLOR", "1")
    folder = tmp_path / "sync"; folder.mkdir()
    (folder / "dotsync.toml").write_text('apps = ["zsh"]\n')
    (folder / "zsh").mkdir()
    (folder / "zsh" / ".zshrc").write_text("X")
    monkeypatch.setenv("DOTSYNC_DIR", str(folder))

    # Inject a warning into the ZshApp instance build_app returns.
    from dotsync.apps import build_app as real_build
    def stub_build(name, cfg):
        app = real_build(name, cfg)
        app.warnings.append("zsh: simulated network blip")
        return app
    monkeypatch.setattr("dotsync.cli.build_app", stub_build)

    from dotsync.cli import main
    rc = main(["to", "--all", "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "simulated network blip" in out
