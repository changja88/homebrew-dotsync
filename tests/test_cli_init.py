import os
from pathlib import Path
from unittest.mock import patch
import pytest
from dotsync import __version__
from dotsync.cli import main
from dotsync.config import Config, save_config


def _no_btt(monkeypatch, fake_home):
    """Make BTT detection return False regardless of host machine state."""
    monkeypatch.setattr(
        "dotsync.apps.bettertouchtool.BetterTouchToolApp.APP_PATH",
        fake_home / "no-btt.app",
    )


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
    # folder + 5 picker fallback answers (sorted: bettertouchtool, claude,
    # codex, ghostty, zsh). Bare Enter keeps each row's default.
    answers = iter([str(target), "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert '"zsh"' in cfg_text
    assert '"claude"' in cfg_text
    assert '"codex"' not in cfg_text
    assert '"ghostty"' not in cfg_text
    assert '"bettertouchtool"' not in cfg_text


def test_init_interactive_picker_lets_user_change_selection(fake_home, tmp_path, monkeypatch):
    """The picker (fallback mode under pytest) lets the user toggle off
    a preselected app and toggle on an undetected one."""
    (fake_home / ".zshrc").write_text("X")
    _no_btt(monkeypatch, fake_home)

    target = tmp_path / "i"
    # folder + per-app y/n in sorted order: bettertouchtool, claude, codex, ghostty, zsh.
    # Default is N for unselected items, Y for the preselected zsh.
    # User picks ghostty (toggle on) + zsh (default), drops everything else.
    answers = iter([str(target), "n", "n", "n", "y", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    cfg_text = (target / "dotsync.toml").read_text()
    assert '"ghostty"' in cfg_text
    assert '"zsh"' in cfg_text
    assert '"codex"' not in cfg_text
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

    # bare Enter for the dir prompt + 5 picker fallback answers (Enter on each)
    answers = iter(["", "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    default_dir = fake_home / "Desktop" / "dotsync_config"
    assert (default_dir / "dotsync.toml").exists()


def test_init_interactive_custom_dir_overrides_default(fake_home, monkeypatch, tmp_path):
    _no_btt(monkeypatch, fake_home)
    (fake_home / ".zshrc").write_text("X")
    custom = tmp_path / "elsewhere"

    # custom path + 5 picker fallback answers (Enter on each)
    answers = iter([str(custom), "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    assert (custom / "dotsync.toml").exists()
    # default not used
    assert not (fake_home / "Desktop" / "dotsync_config").exists()


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
    answers = iter([str(target), "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["init"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Step 1 — Sync folder" in out
    assert "Step 2 — Pick apps to track" in out
    assert "folder ready" in out
    assert "tracked:" in out


def test_no_args_shows_welcome(capsys):
    """`dotsync` with no subcommand should print the welcome banner
    (quickstart guidance) and exit 0, not raise argparse error."""
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "██████╗" in out  # ASCII logo present (block chars)
    assert "Quickstart" in out


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
    # folder + 5 picker fallback Enters (preselected: bettertouchtool, zsh)
    answers = iter([str(target), "", "", "", "", ""])
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
    answers = iter([str(target), "", "", "", "", ""])
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
    answers = iter([str(target), "", "", "", "", ""])
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


# ---------- shell rc auto-init -------------------------------------------

def test_init_yes_auto_adds_export_to_zshrc(fake_home, tmp_path, monkeypatch):
    """--yes mode = explicit user consent → write `export DOTSYNC_DIR=...` to
    ~/.zshrc so future shells find the sync folder without manual setup."""
    monkeypatch.setenv("SHELL", "/bin/zsh")
    _no_btt(monkeypatch, fake_home)
    rc = fake_home / ".zshrc"
    rc.write_text("# pre-existing\nalias ll='ls -la'\n")

    target = tmp_path / "configs"
    code = main(["init", "--dir", str(target), "--apps", "zsh", "--yes"])
    assert code == 0
    text = rc.read_text()
    assert "alias ll='ls -la'" in text         # original preserved
    assert f'export DOTSYNC_DIR="{target}"' in text


def test_init_yes_with_no_shell_init_flag_skips(fake_home, tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    _no_btt(monkeypatch, fake_home)
    rc = fake_home / ".zshrc"
    rc.write_text("# pre-existing\n")

    target = tmp_path / "configs"
    code = main(["init", "--dir", str(target), "--apps", "zsh",
                 "--yes", "--no-shell-init"])
    assert code == 0
    assert "DOTSYNC_DIR" not in rc.read_text()


def test_init_yes_is_idempotent_on_rc(fake_home, tmp_path, monkeypatch):
    """Calling init twice with the same dir leaves a single export line."""
    monkeypatch.setenv("SHELL", "/bin/zsh")
    _no_btt(monkeypatch, fake_home)
    rc = fake_home / ".zshrc"
    rc.write_text("")

    target = tmp_path / "configs"
    main(["init", "--dir", str(target), "--apps", "zsh", "--yes"])
    main(["init", "--dir", str(target), "--apps", "zsh", "--yes"])
    text = rc.read_text()
    line = f'export DOTSYNC_DIR="{target}"'
    assert text.count(line) == 1


def test_init_yes_replaces_old_export_when_dir_changes(fake_home, tmp_path, monkeypatch):
    """User moved their sync folder → init points the export at the new path."""
    monkeypatch.setenv("SHELL", "/bin/zsh")
    _no_btt(monkeypatch, fake_home)
    rc = fake_home / ".zshrc"
    old = tmp_path / "old"
    new = tmp_path / "new"
    rc.write_text(f'export DOTSYNC_DIR="{old}"\n')

    code = main(["init", "--dir", str(new), "--apps", "zsh", "--yes"])
    assert code == 0
    text = rc.read_text()
    assert f'export DOTSYNC_DIR="{new}"' in text
    assert f'export DOTSYNC_DIR="{old}"' not in text


def test_init_yes_unsupported_shell_falls_back_to_hint(fake_home, tmp_path, monkeypatch, capsys):
    """fish/nu/etc. → don't touch any rc, but still surface the export line in
    the next-steps hints so the user can wire it manually."""
    monkeypatch.setenv("SHELL", "/usr/local/bin/fish")
    _no_btt(monkeypatch, fake_home)
    rc = fake_home / ".zshrc"
    rc.write_text("# untouched\n")

    target = tmp_path / "configs"
    code = main(["init", "--dir", str(target), "--apps", "zsh", "--yes"])
    assert code == 0
    assert rc.read_text() == "# untouched\n"
    out = capsys.readouterr().out
    assert "DOTSYNC_DIR" in out                # still in the hints block


def test_init_interactive_prompts_and_accepts_default(fake_home, tmp_path, monkeypatch):
    """Default to Y when the user just hits Enter on the rc-add prompt."""
    monkeypatch.setenv("SHELL", "/bin/zsh")
    _no_btt(monkeypatch, fake_home)
    rc = fake_home / ".zshrc"
    rc.write_text("")

    target = tmp_path / "configs"
    # dir + 5 picker fallback Enters + rc-add prompt Enter
    answers = iter([str(target), "", "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    code = main(["init"])
    assert code == 0
    assert f'export DOTSYNC_DIR="{target}"' in rc.read_text()


def test_init_interactive_decline_skips_rc(fake_home, tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    _no_btt(monkeypatch, fake_home)
    rc = fake_home / ".zshrc"
    rc.write_text("")

    target = tmp_path / "configs"
    # dir + 5 picker fallback Enters + 'n' on rc prompt
    answers = iter([str(target), "", "", "", "", "", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    code = main(["init"])
    assert code == 0
    assert "DOTSYNC_DIR" not in rc.read_text()


def test_init_yes_prints_rc_updated_confirmation(fake_home, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("NO_COLOR", "1")
    _no_btt(monkeypatch, fake_home)
    rc = fake_home / ".zshrc"
    rc.write_text("")

    target = tmp_path / "configs"
    code = main(["init", "--dir", str(target), "--apps", "zsh", "--yes"])
    assert code == 0
    out = capsys.readouterr().out
    # Some confirmation must reference the rc path so the user knows what happened
    assert ".zshrc" in out
    assert "DOTSYNC_DIR" in out
