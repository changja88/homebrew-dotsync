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

    with patch("dotsync.apps.zsh.shutil.copy2", side_effect=RuntimeError("disk full")):
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
    """edit branch: in non-TTY (pytest) the picker falls back to per-app
    y/n. The user keeps ghostty and zsh, drops claude and bettertouchtool."""
    (fake_home / ".zshrc").write_text("X")
    _no_btt(monkeypatch, fake_home)

    target = tmp_path / "i"
    # Folder, then "edit", then four per-app fallback answers (sorted order:
    # bettertouchtool, claude, ghostty, zsh).
    answers = iter([str(target), "edit", "n", "n", "y", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert "ghostty" in cfg_text
    assert "zsh" in cfg_text
    # The apps array shouldn't contain claude/bettertouchtool. We check for
    # the quoted form so we don't collide with `bettertouchtool_preset` in
    # the [options] section, which is always written.
    assert '"claude"' not in cfg_text
    assert '"bettertouchtool"' not in cfg_text


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


def test_init_interactive_explains_edit_option(fake_home, tmp_path, monkeypatch, capsys):
    """The 'Track all of these?' prompt must include a dim hint that names
    what each of y/n/edit does, so first-time users don't have to guess."""
    monkeypatch.setenv("NO_COLOR", "1")
    (fake_home / ".zshrc").write_text("X")
    _no_btt(monkeypatch, fake_home)

    target = tmp_path / "i"
    answers = iter([str(target), "y"])  # folder, then accept default
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    out = capsys.readouterr().out
    # The hint must explicitly describe the 'edit' choice.
    assert "edit" in out and "pick" in out.lower()


def test_apps_shows_tracked_and_installed_status(fake_home, monkeypatch, tmp_path, capsys):
    """`dotsync apps` reports each supported app's (tracked, installed) state
    so users can see what they manage and what their machine has."""
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh", "claude"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    # Locally: zsh installed, claude not, ghostty/btt not
    (fake_home / ".zshrc").write_text("X")
    _no_btt(monkeypatch, fake_home)

    rc = main(["apps"])
    assert rc == 0
    out = capsys.readouterr().out
    # zsh is both tracked and installed (the canonical "all good" row)
    assert "zsh" in out
    # the words tracked AND installed both appear somewhere
    assert "tracked" in out
    assert "installed" in out
    # claude is tracked but not present locally — must be flagged
    assert "claude" in out
    assert "not installed" in out


def test_apps_works_without_config(fake_home, monkeypatch, tmp_path, capsys):
    """`dotsync apps` should still work when dotsync isn't initialized —
    just shows the catalog with installed status and tracked = none."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.chdir(tmp_path)  # cwd has no dotsync.toml, no DOTSYNC_DIR
    (fake_home / ".zshrc").write_text("X")
    _no_btt(monkeypatch, fake_home)

    rc = main(["apps"])
    assert rc == 0
    out = capsys.readouterr().out
    # Doesn't crash on missing config; still lists apps and zsh's install status
    assert "zsh" in out
    assert "installed" in out


def test_apps_edit_updates_config_via_picker(fake_home, monkeypatch, tmp_path):
    """Picker (in fallback mode under pytest) lets the user track a new
    set of apps. Sorted order: bettertouchtool, claude, ghostty, zsh."""
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    # btt=n, claude=y, ghostty=n, zsh=y (default Y because preselected)
    answers = iter(["n", "y", "n", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["apps", "edit"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert "claude" in cfg_text
    assert "zsh" in cfg_text
    # Negative checks use quoted form: `bettertouchtool_preset` always
    # appears in [options], so the bare substring would falsely match.
    assert '"ghostty"' not in cfg_text
    assert '"bettertouchtool"' not in cfg_text


def test_apps_edit_keeps_defaults_when_user_just_hits_enter(fake_home, monkeypatch, tmp_path):
    """Bare Enter on every fallback prompt accepts the current set."""
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh", "claude"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    answers = iter(["", "", "", ""])  # accept default for all 4
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["apps", "edit"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert "zsh" in cfg_text
    assert "claude" in cfg_text


def test_apps_edit_cancel_keeps_current(fake_home, monkeypatch, tmp_path, capsys):
    """When the picker returns None (cancel), config is unchanged and
    a 'cancelled' notice is shown."""
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh", "claude"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    # Force pick_apps to return None to simulate cancel (fallback can't
    # cancel — it only ever returns a list — so we patch directly).
    monkeypatch.setattr(
        "dotsync.ui_picker.pick_apps",
        lambda items, preselected, title="Pick apps to track": None,
    )

    rc = main(["apps", "edit"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    # Untouched
    assert "zsh" in cfg_text
    assert "claude" in cfg_text


def test_apps_edit_no_change_is_silent_noop(fake_home, monkeypatch, tmp_path, capsys):
    """If picker returns the same list as already saved, config file is
    not rewritten."""
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["claude", "zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    mtime_before = (target / "dotsync.toml").stat().st_mtime_ns

    monkeypatch.setattr(
        "dotsync.ui_picker.pick_apps",
        lambda items, preselected, title="Pick apps to track": ["claude", "zsh"],
    )

    rc = main(["apps", "edit"])
    assert rc == 0
    mtime_after = (target / "dotsync.toml").stat().st_mtime_ns
    assert mtime_before == mtime_after  # file untouched
