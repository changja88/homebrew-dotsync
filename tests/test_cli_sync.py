from unittest.mock import patch
import pytest
from dotsync.cli import _build_parser, _change_diff_text, main
from dotsync.config import Config, save_config
from dotsync.plan import AppPlan, Change


def test_push_single_app_calls_sync_from(fake_home, monkeypatch, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("X")

    rc = main(["push", "zsh", "--yes"])
    assert rc == 0
    assert (target / "zsh" / ".zshrc").read_text() == "X"


def test_pull_all_iterates_registered_apps(fake_home, monkeypatch, tmp_path):
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("Z")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    rc = main(["pull", "--all", "--yes"])
    assert rc == 0
    assert (fake_home / ".zshrc").read_text() == "Z"


@pytest.mark.parametrize("old", ["backup", "apply", "from", "to"])
def test_old_command_names_are_rejected(old):
    with pytest.raises(SystemExit) as exc:
        main([old, "zsh", "--yes"])
    assert exc.value.code == 2


def test_help_lists_push_and_pull_only():
    help_text = _build_parser().format_help()

    assert "push" in help_text
    assert "pull" in help_text
    assert "backup" not in help_text
    assert "apply" not in help_text


def test_no_config_shows_init_hint(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)  # cwd has no dotsync.toml
    rc = main(["push", "--all"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "dotsync init" in err or "DOTSYNC_DIR" in err


def test_from_continues_after_one_app_fails(fake_home, monkeypatch, tmp_path, capsys):
    """If one app raises during `backup --all`, others should still run
    and the summary should report 1 ok / 1 error."""
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh", "ghostty"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("Z")
    # ghostty source missing → its sync_from raises FileNotFoundError

    rc = main(["push", "--all", "--yes"])
    out = capsys.readouterr().out
    # zsh succeeded (file copied)
    assert (target / "zsh" / ".zshrc").read_text() == "Z"
    # summary line shows 1 ok and 1 error
    assert "1 ok" in out
    assert "1 error" in out
    # exit code reflects partial failure
    assert rc != 0


def test_from_dry_run_shows_preview_without_changing_folder(
    fake_home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("LOCAL")

    rc = main(["push", "zsh", "--dry-run"])

    assert rc == 0
    assert not (target / "zsh" / ".zshrc").exists()
    out = capsys.readouterr().out
    assert "preview" in out
    assert "create" in out
    assert ".zshrc" in out
    assert "dry-run" in out.lower()


def test_from_prompts_confirmation_by_default_and_decline_keeps_folder(
    fake_home, monkeypatch, tmp_path
):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("LOCAL")
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    rc = main(["push", "zsh"])

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

    rc = main(["push", "zsh"])

    assert rc == 0
    assert not (target / "zsh" / ".zshrc").exists()


def test_from_yes_skips_prompt_and_applies(fake_home, monkeypatch, tmp_path):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("LOCAL")

    rc = main(["push", "zsh", "--yes"])

    assert rc == 0
    assert (target / "zsh" / ".zshrc").read_text() == "LOCAL"


def test_to_preview_uses_concrete_plan_actions(
    fake_home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("FROM_FOLDER")
    (fake_home / ".zshrc").write_text("LOCAL_ORIG")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    rc = main(["pull", "zsh", "--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "preview" in out
    assert "update" in out
    assert ".zshrc" in out


def test_to_unknown_app_returns_cli_error(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    rc = main(["pull", "nonsense", "--dry-run"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown app" in err


def test_from_unknown_app_returns_cli_error(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    rc = main(["push", "nonsense", "--dry-run"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown app" in err


def test_backup_d_key_shows_diff_then_reprompts(
    fake_home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("OLD\n")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("NEW\n")

    prompts: list[str] = []
    answers = iter(["d", "n"])

    def fake_input(prompt=""):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)

    rc = main(["push", "zsh"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "-OLD" in out  # 폴더의 기존 내용이 빠지고
    assert "+NEW" in out  # 로컬의 새 내용이 들어간다
    assert "zsh/.zshrc" in out  # 구분선 라벨
    assert len(prompts) == 2  # d 후 재질문
    assert "y/N/d" in prompts[0]
    # n으로 중단했으므로 폴더는 그대로
    assert (target / "zsh" / ".zshrc").read_text() == "OLD\n"


def test_backup_yes_flag_skips_prompt_entirely(fake_home, monkeypatch, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))
    (fake_home / ".zshrc").write_text("X")

    def boom(prompt=""):
        raise AssertionError("prompt must not be shown with --yes")

    monkeypatch.setattr("builtins.input", boom)
    assert main(["push", "zsh", "--yes"]) == 0


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

    rc = main(["push", "claude", "--yes"])

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

        def sync_to(self, target_dir):
            calls["sync_to"] += 1

        def _finish_ok(self):
            pass

        def _finish_unchanged(self):
            pass

    monkeypatch.setattr("dotsync.cli.build_app", lambda name, cfg: CustomApp())

    rc = main(["pull", "claude", "--yes"])

    assert rc == 0
    assert calls["sync_to"] == 1


def test_to_dry_run_does_not_change_local_or_create_backup(
    fake_home, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("FROM_FOLDER")
    (fake_home / ".zshrc").write_text("LOCAL_ORIG")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    rc = main(["pull", "--all", "--dry-run"])
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
    """Without --yes or --dry-run, `apply` must ask before overwriting."""
    monkeypatch.setenv("NO_COLOR", "1")
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("FROM_FOLDER")
    (fake_home / ".zshrc").write_text("LOCAL_ORIG")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    answers = iter(["n"])  # decline
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    rc = main(["pull", "--all"])
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

    rc = main(["pull", "--all", "--yes"])
    assert rc == 0
    assert (fake_home / ".zshrc").read_text() == "FROM_FOLDER"
    assert not (target / ".backups").exists()


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

    rc = main(["pull", "--all"])
    assert rc == 0
    assert (fake_home / ".zshrc").read_text() == "LOCAL_ORIG"


def test_runtime_error_caught_with_friendly_exit(
    fake_home, monkeypatch, tmp_path, capsys
):
    target = tmp_path / "configs"
    (target / "zsh").mkdir(parents=True)
    (target / "zsh" / ".zshrc").write_text("Z")
    save_config(Config(dir=target, apps=["zsh"]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))

    with patch("dotsync.apps.base.shutil.copy2", side_effect=RuntimeError("disk full")):
        rc = main(["pull", "zsh", "--yes"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "disk full" in err


def test_cmd_to_surfaces_app_warnings_in_summary(
    fake_home, monkeypatch, capsys, tmp_path
):
    """Warnings collected on the App during sync show up after the summary
    so partial failures aren't silenced."""
    monkeypatch.setenv("NO_COLOR", "1")
    folder = tmp_path / "sync"
    folder.mkdir()
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

    rc = main(["pull", "--all", "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "simulated network blip" in out


def test_change_diff_text_update_with_none_source_is_guarded(tmp_path):
    """BetterTouchTool's plan_from builds update Changes with source=None
    (live preset export, nothing on disk to diff against). d-key preview
    must not crash with AttributeError on None.read_bytes()."""
    dest = tmp_path / "dest.bttpreset"
    dest.write_text("STORED")
    change = Change("presets/x.bttpreset", "update", source=None, dest=dest)

    text = _change_diff_text(change)

    assert text == "(diff unavailable: no on-disk copy to compare)"


def test_change_diff_text_create_dumps_full_source(tmp_path):
    source = tmp_path / "new.txt"
    source.write_text("LINE1\nLINE2")
    change = Change("f", "create", source=source, dest=None)

    text = _change_diff_text(change)

    assert text == "+LINE1\n+LINE2"


def test_change_diff_text_remove_dumps_full_dest(tmp_path):
    dest = tmp_path / "old.txt"
    dest.write_text("BYE")
    change = Change("f", "remove", source=None, dest=dest)

    text = _change_diff_text(change)

    assert text == "-BYE"


def test_change_diff_text_tree_lists_per_file_blocks(tmp_path):
    source_dir = tmp_path / "src"
    dest_dir = tmp_path / "dst"
    source_dir.mkdir()
    dest_dir.mkdir()
    (source_dir / "a.md").write_text("A")
    (dest_dir / "b.md").write_text("B_OLD")
    (source_dir / "b.md").write_text("B_NEW")
    (dest_dir / "c.md").write_text("C")
    change = Change(
        "tree",
        "update",
        source=source_dir,
        dest=dest_dir,
        file_changes=("+ a.md", "~ b.md", "− c.md"),
    )

    text = _change_diff_text(change)

    assert "◦ a.md" in text
    assert "◦ b.md" in text
    assert "◦ c.md" in text


def test_change_diff_text_non_diffable_change_skips_file_diff(tmp_path):
    """A semantic change (e.g. claude's mcp-servers.json extraction, where
    source and dest are structurally different files) must not dump raw
    file contents just because it happens to have real paths."""
    source = tmp_path / "claude.json"
    source.write_text('{"mcpServers": {"secret-token": "abc"}}')
    dest = tmp_path / "mcp-servers.json"
    dest.write_text("{}")
    change = Change("mcp-servers.json", "update", source=source, dest=dest, diffable=False)

    text = _change_diff_text(change)

    assert text == "(semantic change — no file diff)"
    assert "secret-token" not in text
