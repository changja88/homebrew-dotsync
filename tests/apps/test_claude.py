import json
from pathlib import Path
from unittest.mock import patch
import pytest
from dotsync.apps.claude import ClaudeApp


def _plugin_entry(install_path: str, version: str = "1.0.0") -> dict:
    return {
        "scope": "user",
        "installPath": install_path,
        "version": version,
    }


def _make_local(home: Path, plugins: dict | None = None, marketplaces: dict | None = None,
                mcp: dict | None = None, settings: dict | None = None,
                plugin_configs: dict | None = None):
    cdir = home / ".claude"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "settings.json").write_text(json.dumps(settings or {"theme": "dark"}))
    pdir = cdir / "plugins"
    pdir.mkdir(exist_ok=True)
    (pdir / "installed_plugins.json").write_text(json.dumps(
        plugins if plugins is not None else {"version": 2, "plugins": {}}
    ))
    (pdir / "known_marketplaces.json").write_text(json.dumps(marketplaces or {}))
    (home / ".claude.json").write_text(json.dumps({"mcpServers": mcp or {}}))
    for name, cfg in (plugin_configs or {}).items():
        (pdir / name).mkdir(parents=True, exist_ok=True)
        (pdir / name / "config.json").write_text(json.dumps(cfg))


def test_sync_from_copies_all_files(fake_home, tmp_path):
    _make_local(
        fake_home,
        plugins={"version": 2, "plugins": {
            "superpowers@official": [_plugin_entry("/p/sp/1.0.0")]
        }},
        marketplaces={"official": {"source": {"source": "github", "repo": "anthropics/sp"}}},
        mcp={"playwright": {"command": "npx"}},
        settings={"theme": "dark"},
        plugin_configs={"superpowers": {"foo": "bar"}},
    )
    target = tmp_path / "configs"
    target.mkdir()

    ClaudeApp().sync_from(target)

    cdir = target / "claude"
    assert json.loads((cdir / "settings.json").read_text())["theme"] == "dark"
    assert json.loads((cdir / "mcp-servers.json").read_text()) == {"playwright": {"command": "npx"}}
    ip = json.loads((cdir / "plugins" / "installed_plugins.json").read_text())
    assert ip["plugins"]["superpowers@official"][0]["installPath"] == "/p/sp/1.0.0"
    km = json.loads((cdir / "plugins" / "known_marketplaces.json").read_text())
    assert km["official"]["source"]["repo"] == "anthropics/sp"
    assert json.loads((cdir / "plugins" / "superpowers" / "config.json").read_text()) == {"foo": "bar"}


def test_sync_to_replaces_mcp_servers_in_claude_json(fake_home, tmp_path):
    """sync_to overwrites .claude.json's mcpServers wholesale — pre-existing
    entries that aren't in the stored mcp-servers.json are dropped."""
    _make_local(fake_home, mcp={"existing": {"command": "old"}}, settings={"theme": "old"})
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text(json.dumps({"theme": "new"}))
    (cdir / "mcp-servers.json").write_text(json.dumps({"new-mcp": {"command": "x"}}))
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({}))
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.claude.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        ClaudeApp().sync_to(target, backup)

    assert json.loads((fake_home / ".claude" / "settings.json").read_text())["theme"] == "new"
    cj = json.loads((fake_home / ".claude.json").read_text())
    assert cj["mcpServers"] == {"new-mcp": {"command": "x"}}
    # The pre-existing "existing" key MUST be gone — current behavior is replace, not merge.
    assert "existing" not in cj["mcpServers"]
    assert json.loads((backup / "claude" / "settings.json").read_text())["theme"] == "old"


def test_sync_to_invokes_plugin_restore_with_scope_user(fake_home, tmp_path):
    _make_local(fake_home)
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text("{}")
    (cdir / "mcp-servers.json").write_text("{}")
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 2,
        "plugins": {"superpowers@official": [_plugin_entry("/nonexistent/path")]}
    }))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({
        "official": {"source": {"source": "github", "repo": "anthropics/sp"}}
    }))
    backup = tmp_path / "backup"
    backup.mkdir()

    with patch("dotsync.apps.claude.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        ClaudeApp().sync_to(target, backup)

    cmds = [" ".join(c.args[0]) for c in run.call_args_list]
    assert any("marketplace add --scope user anthropics/sp" in c for c in cmds)
    assert any("plugin install --scope user superpowers@official" in c for c in cmds)


def test_sync_to_skips_install_when_installpath_exists(fake_home, tmp_path):
    _make_local(fake_home)
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text("{}")
    (cdir / "mcp-servers.json").write_text("{}")
    existing = tmp_path / "cached_plugin"
    existing.mkdir()
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 2,
        "plugins": {"sp@official": [_plugin_entry(str(existing))]}
    }))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({}))
    backup = tmp_path / "backup"; backup.mkdir()

    with patch("dotsync.apps.claude.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        ClaudeApp().sync_to(target, backup)

    cmds = [" ".join(c.args[0]) for c in run.call_args_list]
    assert not any("plugin install" in c for c in cmds)


def test_sync_to_disables_plugins_marked_false(fake_home, tmp_path):
    _make_local(fake_home)
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"a@mp": True, "b@mp": False, "c@mp": False}
    }))
    (cdir / "mcp-servers.json").write_text("{}")
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({}))
    backup = tmp_path / "backup"; backup.mkdir()

    with patch("dotsync.apps.claude.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        ClaudeApp().sync_to(target, backup)

    cmds = [" ".join(c.args[0]) for c in run.call_args_list]
    disable_cmds = [c for c in cmds if "plugin disable" in c]
    assert any("b@mp" in c for c in disable_cmds)
    assert any("c@mp" in c for c in disable_cmds)
    assert not any("a@mp" in c for c in disable_cmds)


def test_sync_to_directory_marketplace_uses_path(fake_home, tmp_path):
    _make_local(fake_home)
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text("{}")
    (cdir / "mcp-servers.json").write_text("{}")
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({
        "local-mp": {"source": {"source": "directory", "path": "/Users/x/local-marketplace"}}
    }))
    backup = tmp_path / "backup"; backup.mkdir()

    with patch("dotsync.apps.claude.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        ClaudeApp().sync_to(target, backup)

    cmds = [" ".join(c.args[0]) for c in run.call_args_list]
    assert any("marketplace add --scope user /Users/x/local-marketplace" in c for c in cmds)


def test_sync_to_missing_target_raises(fake_home, tmp_path):
    target = tmp_path / "configs"
    target.mkdir()
    backup = tmp_path / "backup"
    backup.mkdir()
    with pytest.raises(FileNotFoundError, match="claude/settings.json"):
        ClaudeApp().sync_to(target, backup)


def test_status_clean(fake_home, tmp_path):
    _make_local(fake_home, settings={"x": 1}, mcp={"a": 1})
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text(json.dumps({"x": 1}))
    (cdir / "mcp-servers.json").write_text(json.dumps({"a": 1}))
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({}))

    s = ClaudeApp().status(target)
    assert s.state == "clean"


def test_status_dirty_when_settings_differ(fake_home, tmp_path):
    _make_local(fake_home, settings={"x": "OLD"})
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text(json.dumps({"x": "NEW"}))
    (cdir / "mcp-servers.json").write_text("{}")
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({}))

    s = ClaudeApp().status(target)
    assert s.state == "dirty"


def test_is_present_locally_true_when_claude_dir_exists(fake_home):
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude" / "settings.json").write_text("{}")
    assert ClaudeApp.is_present_locally() is True


def test_is_present_locally_false_when_no_claude_dir(fake_home):
    assert ClaudeApp.is_present_locally() is False


def test_sync_to_tolerates_v1_plugin_entries_dict(fake_home, tmp_path):
    """Sync folders saved before the v2 schema may have plugins values that are
    dicts (single entry) rather than lists. sync_to must not crash, and must
    treat such plugins as 'no installPath provided' so the install path runs."""
    _make_local(fake_home)
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text("{}")
    (cdir / "mcp-servers.json").write_text("{}")
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 1,
        "plugins": {"legacy@official": {"installPath": "/p/legacy/1.0.0"}},  # v1: value is dict, not list
    }))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({
        "official": {"source": {"source": "github", "repo": "anthropics/sp"}}
    }))
    backup = tmp_path / "backup"; backup.mkdir()

    with patch("dotsync.apps.claude.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        ClaudeApp().sync_to(target, backup)  # must not raise

    cmds = [" ".join(c.args[0]) for c in run.call_args_list]
    # marketplace add still happens; plugin install also runs because we
    # cannot read installPath out of a non-list value.
    assert any("marketplace add --scope user anthropics/sp" in c for c in cmds)
    assert any("plugin install --scope user legacy@official" in c for c in cmds)


def test_sync_to_corrupted_mcp_servers_json_raises_runtime_error(fake_home, tmp_path):
    """When mcp-servers.json is hand-corrupted, surface a friendly RuntimeError
    so cli.py:507's friendly handler catches it instead of a raw traceback."""
    _make_local(fake_home)
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text("{}")
    (cdir / "mcp-servers.json").write_text("{not valid json")  # corrupted
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({}))
    backup = tmp_path / "backup"; backup.mkdir()

    with pytest.raises(RuntimeError, match="mcp-servers.json"):
        ClaudeApp().sync_to(target, backup)


def test_sync_to_treats_string_false_as_truthy_in_enabled_plugins(fake_home, tmp_path):
    """Today's contract: enabledPlugins values are interpreted as Python truthy.
    A literal string "false" (hand-edited) counts as enabled and is NOT disabled.
    Pin this so any future fix (proper bool check) is a deliberate change."""
    _make_local(fake_home)
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"truthy-str@mp": "false", "real-bool@mp": False}
    }))
    (cdir / "mcp-servers.json").write_text("{}")
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({}))
    backup = tmp_path / "backup"; backup.mkdir()

    with patch("dotsync.apps.claude.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        ClaudeApp().sync_to(target, backup)

    cmds = [" ".join(c.args[0]) for c in run.call_args_list]
    disable_cmds = [c for c in cmds if "plugin disable" in c]
    # Real bool False → disabled. String "false" (truthy) → not disabled.
    assert any("real-bool@mp" in c for c in disable_cmds)
    assert not any("truthy-str@mp" in c for c in disable_cmds)


def test_sync_to_warns_instead_of_raising_when_claude_cli_missing(fake_home, tmp_path):
    """When `claude` is not in PATH, plugin restoration is skipped with a
    warning — settings.json copy must still complete."""
    _make_local(fake_home)
    target = tmp_path / "configs"
    cdir = target / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text(json.dumps({"theme": "x"}))
    (cdir / "mcp-servers.json").write_text("{}")
    (cdir / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 2, "plugins": {"sp@official": [_plugin_entry("/nope")]}
    }))
    (cdir / "plugins" / "known_marketplaces.json").write_text(json.dumps({
        "official": {"source": {"source": "github", "repo": "anthropics/sp"}}
    }))
    backup = tmp_path / "backup"; backup.mkdir()

    with patch("dotsync.apps.claude.subprocess.run", side_effect=FileNotFoundError("claude")):
        app = ClaudeApp()
        app.sync_to(target, backup)  # must not raise

    assert (fake_home / ".claude" / "settings.json").read_text()  # got copied
    assert any("claude" in w.lower() for w in app.warnings)


def test_diff_tree_both_absent_returns_empty(tmp_path):
    app = ClaudeApp()
    added, removed, modified = app._diff_tree(tmp_path / "a", tmp_path / "b")
    assert added == set() and removed == set() and modified == set()


def test_diff_tree_only_stored_returns_all_added(tmp_path):
    stored = tmp_path / "stored"
    stored.mkdir()
    (stored / "foo.md").write_text("x")
    (stored / "sub").mkdir()
    (stored / "sub" / "bar.md").write_text("y")
    app = ClaudeApp()
    added, removed, modified = app._diff_tree(tmp_path / "local", stored)
    assert added == {Path("foo.md"), Path("sub/bar.md")}
    assert removed == set() and modified == set()


def test_diff_tree_only_local_returns_all_removed(tmp_path):
    local = tmp_path / "local"
    local.mkdir()
    (local / "foo.md").write_text("x")
    app = ClaudeApp()
    added, removed, modified = app._diff_tree(local, tmp_path / "stored")
    assert added == set() and removed == {Path("foo.md")} and modified == set()


def test_diff_tree_classifies_added_removed_modified(tmp_path):
    local = tmp_path / "local"; local.mkdir()
    stored = tmp_path / "stored"; stored.mkdir()
    (local / "same.md").write_text("same")
    (stored / "same.md").write_text("same")
    (local / "different.md").write_text("v1")
    (stored / "different.md").write_text("v2")
    (local / "only-local.md").write_text("local")
    (stored / "only-stored.md").write_text("stored")
    app = ClaudeApp()
    added, removed, modified = app._diff_tree(local, stored)
    assert added == {Path("only-stored.md")}
    assert removed == {Path("only-local.md")}
    assert modified == {Path("different.md")}


def test_mirror_tree_creates_dst_when_absent(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "foo.md").write_text("hello")
    dst = tmp_path / "dst"
    ClaudeApp()._mirror_tree(src, dst)
    assert (dst / "foo.md").read_text() == "hello"


def test_mirror_tree_overwrites_existing_files(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "foo.md").write_text("new")
    dst = tmp_path / "dst"; dst.mkdir()
    (dst / "foo.md").write_text("old")
    ClaudeApp()._mirror_tree(src, dst)
    assert (dst / "foo.md").read_text() == "new"


def test_mirror_tree_deletes_dst_only_files(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "keep.md").write_text("keep")
    dst = tmp_path / "dst"; dst.mkdir()
    (dst / "keep.md").write_text("keep")
    (dst / "extra.md").write_text("extra")
    ClaudeApp()._mirror_tree(src, dst)
    assert (dst / "keep.md").exists()
    assert not (dst / "extra.md").exists()


def test_mirror_tree_handles_nested_subdirectories(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "a").mkdir()
    (src / "a" / "b").mkdir()
    (src / "a" / "b" / "deep.md").write_text("deep")
    dst = tmp_path / "dst"
    ClaudeApp()._mirror_tree(src, dst)
    assert (dst / "a" / "b" / "deep.md").read_text() == "deep"


def test_mirror_tree_cleans_up_empty_subdirs(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "keep.md").write_text("keep")
    dst = tmp_path / "dst"; dst.mkdir()
    (dst / "keep.md").write_text("keep")
    (dst / "old_subdir").mkdir()
    (dst / "old_subdir" / "ghost.md").write_text("ghost")
    ClaudeApp()._mirror_tree(src, dst)
    assert not (dst / "old_subdir" / "ghost.md").exists()
    assert not (dst / "old_subdir").exists()
