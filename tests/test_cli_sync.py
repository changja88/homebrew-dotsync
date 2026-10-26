import pytest
from dotsync.cli import _build_parser, main
from dotsync.cli_json import change_diff_text as _change_diff_text
from dotsync.config import Config, save_config
from dotsync.plan import Change


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


# ----- sync commands hold the locks the other dotsync commands share -----


def _lock_recorders(monkeypatch):
    import contextlib
    from dotsync import cli

    held: list[str] = []
    seen: dict[str, list[str]] = {}

    @contextlib.contextmanager
    def fake_accounts_locked():
        held.append("accounts")
        try:
            yield
        finally:
            held.remove("accounts")

    @contextlib.contextmanager
    def fake_tool_lock(name, *, shared, wait=10.0):
        tag = f"{name}:{'shared' if shared else 'exclusive'}"
        held.append(tag)
        try:
            yield
        finally:
            held.remove(tag)

    monkeypatch.setattr(cli.accounts, "locked", fake_accounts_locked)
    monkeypatch.setattr(cli.locks, "tool_lock", fake_tool_lock)
    return held, seen


def _stub_app(name, held, seen):
    from dotsync.plan import AppPlan, Change

    class Stub:
        description = "stub"
        warnings: list = []

        def plan_to(self, target_dir):
            return AppPlan(app=name, direction="to", changes=[Change("f", "update")])

        def plan_from(self, target_dir):
            return AppPlan(app=name, direction="from", changes=[Change("f", "update")])

        def sync_to(self, target_dir):
            seen["sync_to"] = list(held)

        def sync_from(self, target_dir):
            seen["sync_from"] = list(held)

    return Stub()


def _sync_setup(monkeypatch, tmp_path, app):
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=[app]))
    monkeypatch.setenv("DOTSYNC_DIR", str(target))


def test_pull_claude_holds_accounts_lock(fake_home, monkeypatch, tmp_path):
    _sync_setup(monkeypatch, tmp_path, "claude")
    held, seen = _lock_recorders(monkeypatch)
    monkeypatch.setattr("dotsync.apps.build_app", lambda name, cfg: _stub_app(name, held, seen))

    assert main(["pull", "claude", "--yes", "--json"]) == 0
    assert seen["sync_to"] == ["accounts"]
    assert held == []


def test_push_codex_holds_shared_codex_lock(fake_home, monkeypatch, tmp_path):
    _sync_setup(monkeypatch, tmp_path, "codex")
    held, seen = _lock_recorders(monkeypatch)
    monkeypatch.setattr("dotsync.apps.build_app", lambda name, cfg: _stub_app(name, held, seen))

    assert main(["push", "codex", "--yes", "--json"]) == 0
    assert seen["sync_from"] == ["codex:shared"]


def test_pull_zsh_takes_no_lock(fake_home, monkeypatch, tmp_path):
    _sync_setup(monkeypatch, tmp_path, "zsh")
    held, seen = _lock_recorders(monkeypatch)
    monkeypatch.setattr("dotsync.apps.build_app", lambda name, cfg: _stub_app(name, held, seen))

    assert main(["pull", "zsh", "--yes", "--json"]) == 0
    assert seen["sync_to"] == []


def test_status_codex_holds_shared_codex_lock(fake_home, monkeypatch, tmp_path):
    from dotsync.apps.base import AppStatus

    _sync_setup(monkeypatch, tmp_path, "codex")
    held, seen = _lock_recorders(monkeypatch)

    class StatusStub:
        def status(self, target_dir):
            seen["status"] = list(held)
            return AppStatus(state="clean")

    monkeypatch.setattr("dotsync.apps.build_app", lambda name, cfg: StatusStub())

    assert main(["status", "--json"]) == 0
    assert seen["status"] == ["codex:shared"]


