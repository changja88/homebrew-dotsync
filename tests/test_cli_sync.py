from pathlib import Path
from unittest.mock import patch
import pytest
from dotsync.cli import main
from dotsync.config import Config, save_config
from dotsync.plan import AppPlan


def test_from_single_app_calls_sync_from(fake_home, monkeypatch, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("X")

    rc = main(["from", "zsh", "--yes"])
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

    rc = main(["from", "--all", "--yes"])
    out = capsys.readouterr().out
    # zsh succeeded (file copied)
    assert (target / "zsh" / ".zshrc").read_text() == "Z"
    # summary line shows 1 ok and 1 error
    assert "1 ok" in out
    assert "1 error" in out
    # exit code reflects partial failure
    assert rc != 0


def test_from_dry_run_shows_preview_without_changing_folder(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("LOCAL")

    rc = main(["from", "zsh", "--dry-run"])

    assert rc == 0
    assert not (target / "zsh" / ".zshrc").exists()
    out = capsys.readouterr().out
    assert "preview" in out
    assert "create" in out
    assert ".zshrc" in out
    assert "dry-run" in out.lower()


def test_from_prompts_confirmation_by_default_and_decline_keeps_folder(fake_home, monkeypatch, tmp_path):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("LOCAL")
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    rc = main(["from", "zsh"])

    assert rc == 0
    assert not (target / "zsh" / ".zshrc").exists()


def test_from_bare_enter_aborts(fake_home, monkeypatch, tmp_path):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("LOCAL")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    rc = main(["from", "zsh"])

    assert rc == 0
    assert not (target / "zsh" / ".zshrc").exists()


def test_from_yes_skips_prompt_and_applies(fake_home, monkeypatch, tmp_path):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("LOCAL")

    rc = main(["from", "zsh", "--yes"])

    assert rc == 0
    assert (target / "zsh" / ".zshrc").read_text() == "LOCAL"


def test_to_preview_uses_concrete_plan_actions(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("FROM_FOLDER")
    (fake_home / ".zshrc").write_text("LOCAL_ORIG")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    rc = main(["to", "zsh", "--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "preview" in out
    assert "update" in out
    assert ".zshrc" in out


def test_from_unknown_empty_plan_still_applies_after_yes(monkeypatch, tmp_path):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["claude"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    calls = {"sync_from": 0}

    class CustomApp:
        description = "Custom app"
        warnings = []

        def plan_from(self, target_dir):
            return AppPlan(app="claude", direction="from", changes=[])

        def sync_from(self, target_dir):
            calls["sync_from"] += 1

        def _finish_ok(self):
            pass

        def _finish_unchanged(self):
            pass

    monkeypatch.setattr("dotsync.cli.build_app", lambda name, cfg: CustomApp())

    rc = main(["from", "claude", "--yes"])

    assert rc == 0
    assert calls["sync_from"] == 1


def test_to_unknown_empty_plan_still_applies_after_yes(monkeypatch, tmp_path):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["claude"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    calls = {"sync_to": 0}

    class CustomApp:
        description = "Custom app"
        warnings = []

        def plan_to(self, target_dir):
            return AppPlan(app="claude", direction="to", changes=[])

        def sync_to(self, target_dir, session):
            calls["sync_to"] += 1

        def _finish_ok(self):
            pass

        def _finish_unchanged(self):
            pass

    monkeypatch.setattr("dotsync.cli.build_app", lambda name, cfg: CustomApp())

    rc = main(["to", "claude", "--yes"])

    assert rc == 0
    assert calls["sync_to"] == 1


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
