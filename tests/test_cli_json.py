"""`--json` output of the sync commands — the contract dotsync.app decodes."""

import json
import re

import pytest

from dotsync.apps.base import AppStatus
from dotsync.cli import main
from dotsync.config import Config, save_config


def _folder(point_at, tmp_path, apps):
    target = tmp_path / "configs"
    target.mkdir()
    save_config(Config(dir=target, apps=apps))
    point_at(target)
    return target


def _run(capsys, argv):
    rc = main(argv)
    captured = capsys.readouterr()
    return rc, json.loads(captured.out), captured


def _app(payload, name):
    return next(a for a in payload["apps"] if a["name"] == name)


@pytest.mark.parametrize(
    "argv",
    [[], ["status"], ["push", "--all", "--yes"], ["config", "apps", "zsh"],
     ["account", "usage"], ["sessions", "list", "claude"]],
)
def test_a_command_without_json_says_dotsync_is_for_the_app(argv, capsys):
    assert main(argv) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "dotsync.app" in captured.err


def test_status_json_shape(point_at, fake_home, monkeypatch, tmp_path, capsys):
    target = _folder(point_at, tmp_path, ["zsh"])
    (fake_home / ".zshrc").write_text("NEW\n")
    (target / "zsh").mkdir()
    (target / "zsh" / ".zshrc").write_text("OLD\n")

    rc, payload, _ = _run(capsys, ["status", "--json"])

    assert rc == 0
    assert payload["dir"] == str(target)
    assert "dir_source" not in payload
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", payload["checked_at"])
    zsh = _app(payload, "zsh")
    assert zsh["tracked"] is True
    assert zsh["state"] == "dirty"
    assert zsh["direction"] in ("local-newer", "folder-newer")
    assert zsh["reason"] == ""
    assert [c["label"] for c in zsh["changes"]] == [".zshrc"]
    assert zsh["changes"][0]["kind"] == "update"
    assert set(zsh["changes"][0]) == {"label", "kind", "details", "file_changes", "diffable"}
    assert _app(payload, "ghostty") == {
        "name": "ghostty",
        "description": _app(payload, "ghostty")["description"],
        "tracked": False,
    }


def test_status_json_lists_every_supported_app_in_registry_order(point_at, fake_home, monkeypatch, tmp_path, capsys):
    from dotsync.apps import APP_CLASSES

    _folder(point_at, tmp_path, [])

    _, payload, _ = _run(capsys, ["status", "--json"])

    assert [a["name"] for a in payload["apps"]] == [cls.name for cls in APP_CLASSES]


def test_status_json_reports_btt_reason(point_at, fake_home, monkeypatch, tmp_path, capsys):
    from dotsync.apps.bettertouchtool import BetterTouchToolApp

    _folder(point_at, tmp_path, ["bettertouchtool"])
    monkeypatch.setattr(
        BetterTouchToolApp,
        "status",
        lambda self, target_dir: AppStatus("unknown", "not allowed", reason="btt_permission"),
    )

    _, payload, _ = _run(capsys, ["status", "--json"])

    btt = _app(payload, "bettertouchtool")
    assert (btt["state"], btt["reason"], btt["details"]) == ("unknown", "btt_permission", "not allowed")


def test_status_json_carries_each_tracked_apps_options(point_at, fake_home, monkeypatch, tmp_path, capsys):
    from dotsync.apps.bettertouchtool import BetterTouchToolApp

    target = tmp_path / "configs"
    save_config(
        Config(
            dir=target,
            apps=["bettertouchtool", "zsh"],
            app_options={"bettertouchtool": {"presets": ["Master_bt"]}},
        )
    )
    point_at(target)
    monkeypatch.setattr(BetterTouchToolApp, "status", lambda self, target_dir: AppStatus("clean"))

    _, payload, _ = _run(capsys, ["status", "--json"])

    # The settings table names BTT's presets.
    assert _app(payload, "bettertouchtool")["options"] == {"presets": ["Master_bt"]}
    assert _app(payload, "zsh")["options"] == {}
    assert "options" not in _app(payload, "ghostty")


def test_status_json_keeps_going_when_one_app_raises(point_at, fake_home, monkeypatch, tmp_path, capsys):
    from dotsync.apps.ghostty import GhosttyApp

    target = _folder(point_at, tmp_path, ["ghostty", "zsh"])
    (fake_home / ".zshrc").write_text("X\n")
    (target / "zsh").mkdir()
    (target / "zsh" / ".zshrc").write_text("X\n")

    def broken(self, target_dir):
        raise PermissionError("settings unreadable")

    monkeypatch.setattr(GhosttyApp, "status", broken)

    rc, payload, _ = _run(capsys, ["status", "--json"])

    assert rc == 0
    ghostty = _app(payload, "ghostty")
    assert (ghostty["state"], ghostty["reason"]) == ("unknown", "error")
    assert "settings unreadable" in ghostty["details"]
    assert _app(payload, "zsh")["state"] == "clean"


def test_status_json_does_not_plan_a_clean_app(point_at, fake_home, monkeypatch, tmp_path, capsys):
    # BetterTouchTool's plan_from runs osascript: a clean app must not pay it.
    from dotsync.apps.zsh import ZshApp

    target = _folder(point_at, tmp_path, ["zsh"])
    _zsh_pair(fake_home, target, local="SAME\n", stored="SAME\n")

    def must_not_be_called(self, target_dir):
        raise AssertionError("must not be called")

    monkeypatch.setattr(ZshApp, "plan_from", must_not_be_called)

    rc, payload, _ = _run(capsys, ["status", "--json"])

    assert rc == 0
    assert _app(payload, "zsh")["state"] == "clean"


def test_status_json_keeps_a_dirty_app_when_its_plan_fails(point_at, fake_home, monkeypatch, tmp_path, capsys):
    from dotsync.apps.zsh import ZshApp

    target = _folder(point_at, tmp_path, ["zsh"])
    _zsh_pair(fake_home, target, local="LOCAL\n", stored="STORED\n")

    def boom(self, target_dir):
        raise RuntimeError("plan exploded")

    monkeypatch.setattr(ZshApp, "plan_from", boom)

    rc, payload, _ = _run(capsys, ["status", "--json"])

    assert rc == 0
    assert (_app(payload, "zsh")["state"], _app(payload, "zsh")["changes"]) == ("dirty", [])


def test_status_json_error_without_folder(fake_home, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)

    rc, payload, _ = _run(capsys, ["status", "--json"])

    assert rc == 1
    assert payload["error"]["code"] == "config"


def test_status_json_stdout_is_one_object(point_at, fake_home, monkeypatch, tmp_path, capsys):
    from dotsync.apps.zsh import ZshApp

    _folder(point_at, tmp_path, ["zsh"])

    def chatty(self, target_dir):
        print("raw print")
        return AppStatus("clean")

    monkeypatch.setattr(ZshApp, "status", chatty)

    rc, payload, captured = _run(capsys, ["status", "--json"])

    assert rc == 0
    assert _app(payload, "zsh")["state"] == "clean"
    assert "raw print" in captured.err


def test_status_json_busy_when_codex_lock_is_held(point_at, fake_home, monkeypatch, tmp_path, capsys):
    from dotsync import locks

    _folder(point_at, tmp_path, ["codex"])
    real = locks.tool_lock

    def quick(name, *, shared, wait=10.0):
        return real(name, shared=shared, wait=0.2)

    monkeypatch.setattr(locks, "tool_lock", quick)

    with real("codex", shared=False):
        rc, payload, _ = _run(capsys, ["status", "--json"])

    assert rc == 1
    assert payload["error"]["code"] == "busy"


# ----- push / pull --json ---------------------------------------------------


def _zsh_pair(fake_home, target, local, stored):
    (fake_home / ".zshrc").write_text(local)
    (target / "zsh").mkdir(exist_ok=True)
    (target / "zsh" / ".zshrc").write_text(stored)


def _ghostty_pair(fake_home, target, text):
    local = fake_home / "Library" / "Application Support" / "com.mitchellh.ghostty" / "config.ghostty"
    local.parent.mkdir(parents=True)
    local.write_text(text)
    (target / "ghostty").mkdir()
    (target / "ghostty" / "config.ghostty").write_text(text)


def test_pull_dry_run_json_has_changes_and_diff(point_at, fake_home, monkeypatch, tmp_path, capsys):
    target = _folder(point_at, tmp_path, ["zsh"])
    _zsh_pair(fake_home, target, local="OLD\n", stored="NEW\n")

    rc, payload, _ = _run(capsys, ["pull", "zsh", "--dry-run", "--json"])

    assert rc == 0
    assert (payload["dir"], payload["direction"]) == (str(target), "to")
    assert "dir_source" not in payload
    change = payload["plans"][0]["changes"][0]
    assert payload["plans"][0]["app"] == "zsh"
    assert change["kind"] == "update"
    assert "-OLD" in change["diff"] and "+NEW" in change["diff"]
    assert "plugin_actions" not in payload["plans"][0]
    assert (fake_home / ".zshrc").read_text() == "OLD\n"


def test_pull_yes_json_reports_outcomes(point_at, fake_home, monkeypatch, tmp_path, capsys):
    target = _folder(point_at, tmp_path, ["zsh", "ghostty"])
    _zsh_pair(fake_home, target, local="OLD\n", stored="NEW\n")
    _ghostty_pair(fake_home, target, "font-size = 13\n")

    rc, payload, _ = _run(capsys, ["pull", "--all", "--yes", "--json"])

    assert rc == 0
    outcomes = {r["app"]: r["outcome"] for r in payload["results"]}
    assert outcomes == {"zsh": "changed", "ghostty": "unchanged"}
    assert all(r["warnings"] == [] for r in payload["results"])
    assert (fake_home / ".zshrc").read_text() == "NEW\n"


def test_pull_yes_json_reports_a_failed_app(point_at, fake_home, monkeypatch, tmp_path, capsys):
    target = _folder(point_at, tmp_path, ["zsh"])
    (target / "zsh").mkdir()
    (fake_home / ".zshrc").write_text("X\n")

    rc, payload, _ = _run(capsys, ["pull", "zsh", "--yes", "--json"])

    assert rc == 0
    result = payload["results"][0]
    assert result["outcome"] == "failed"
    assert "zsh/.zshrc" in result["error"]


def test_push_yes_json_goes_on_after_a_failed_app(point_at, fake_home, monkeypatch, tmp_path, capsys):
    target = _folder(point_at, tmp_path, ["ghostty", "zsh"])
    (fake_home / ".zshrc").write_text("Z")  # ghostty has no local config to push

    rc, payload, _ = _run(capsys, ["push", "--all", "--yes", "--json"])

    assert rc == 0
    assert [(r["app"], r["outcome"]) for r in payload["results"]] == [
        ("ghostty", "failed"), ("zsh", "changed")
    ]
    assert (target / "zsh" / ".zshrc").read_text() == "Z"


def test_push_yes_json_applies_an_app_whose_plan_lists_nothing(point_at, fake_home, monkeypatch, tmp_path, capsys):
    # An empty plan says nothing about what will change, so it is applied.
    from dotsync.plan import AppPlan

    _folder(point_at, tmp_path, ["claude"])
    calls = []

    class Custom:
        warnings: list = []

        def plan_from(self, target_dir):
            return AppPlan(app="claude", direction="from", changes=[])

        def sync_from(self, target_dir):
            calls.append(target_dir)

    monkeypatch.setattr("dotsync.apps.build_app", lambda name, cfg: Custom())

    rc, payload, _ = _run(capsys, ["push", "claude", "--yes", "--json"])

    assert rc == 0
    assert payload["results"][0]["outcome"] == "changed"
    assert len(calls) == 1


def test_pull_yes_json_carries_an_apps_warnings(point_at, fake_home, monkeypatch, tmp_path, capsys):
    from dotsync.apps import build_app as real_build

    target = _folder(point_at, tmp_path, ["zsh"])
    (target / "zsh").mkdir()
    (target / "zsh" / ".zshrc").write_text("X")

    def build(name, cfg):
        app = real_build(name, cfg)
        app.warnings.append("zsh: simulated network blip")
        return app

    monkeypatch.setattr("dotsync.apps.build_app", build)

    rc, payload, _ = _run(capsys, ["pull", "--all", "--yes", "--json"])

    assert rc == 0
    assert payload["results"][0]["warnings"] == ["zsh: simulated network blip"]


def test_push_json_needs_yes_or_dry_run(point_at, fake_home, monkeypatch, tmp_path, capsys):
    _folder(point_at, tmp_path, ["zsh"])

    rc, payload, _ = _run(capsys, ["push", "zsh", "--json"])

    assert rc == 1
    assert payload["error"]["code"] == "needs_yes_or_dry_run"


def test_push_json_bad_app(point_at, fake_home, monkeypatch, tmp_path, capsys):
    _folder(point_at, tmp_path, ["zsh"])

    rc, payload, _ = _run(capsys, ["push", "nope", "--yes", "--json"])

    assert rc == 1
    assert payload["error"]["code"] == "bad_app"


def test_apply_json_stdout_is_one_object_when_an_app_prints(point_at, fake_home, monkeypatch, tmp_path, capsys):
    from dotsync.apps.zsh import ZshApp

    target = _folder(point_at, tmp_path, ["zsh"])
    _zsh_pair(fake_home, target, local="OLD\n", stored="NEW\n")
    real_sync_to = ZshApp.sync_to

    def chatty(self, target_dir):
        print("raw print")
        real_sync_to(self, target_dir)

    monkeypatch.setattr(ZshApp, "sync_to", chatty)

    rc, payload, captured = _run(capsys, ["pull", "zsh", "--yes", "--json"])

    assert rc == 0
    assert payload["results"][0]["outcome"] == "changed"
    assert "raw print" in captured.err


def test_pull_json_busy_when_accounts_locked(point_at, fake_home, monkeypatch, tmp_path, capsys):
    from dotsync import accounts

    _folder(point_at, tmp_path, ["claude"])
    monkeypatch.setattr(accounts, "LOCK_WAIT_SECONDS", 0.2)

    with accounts.locked():
        rc, payload, _ = _run(capsys, ["pull", "claude", "--yes", "--json"])

    assert rc == 1
    assert payload["error"]["code"] == "busy"


def test_pull_dry_run_json_does_not_wait_for_accounts_lock(point_at, fake_home, monkeypatch, tmp_path, capsys):
    # A preview only reads: it must not wait behind `account usage`.
    from dotsync import accounts
    from dotsync.apps.claude import ClaudeApp
    from dotsync.plan import AppPlan

    _folder(point_at, tmp_path, ["claude"])
    monkeypatch.setattr(accounts, "LOCK_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(ClaudeApp, "plan_to", lambda self, d: AppPlan(app="claude", direction="to", changes=[]))
    monkeypatch.setattr(ClaudeApp, "plugin_actions_to", lambda self, d: None)

    with accounts.locked():
        rc, payload, _ = _run(capsys, ["pull", "claude", "--dry-run", "--json"])

    assert rc == 0
    assert payload["plans"][0]["app"] == "claude"


def test_pull_dry_run_json_carries_claude_plugin_actions(point_at, fake_home, monkeypatch, tmp_path, capsys):
    from dotsync.apps.claude import ClaudeApp
    from dotsync.plan import AppPlan

    _folder(point_at, tmp_path, ["claude"])
    monkeypatch.setattr(ClaudeApp, "plan_to", lambda self, d: AppPlan(app="claude", direction="to", changes=[]))
    monkeypatch.setattr(
        ClaudeApp, "plugin_actions_to", lambda self, d: {"install": ["a@m"], "disable": ["b@m"]}
    )

    _, payload, _ = _run(capsys, ["pull", "claude", "--dry-run", "--json"])

    assert payload["plans"][0]["plugin_actions"] == {"install": ["a@m"], "disable": ["b@m"]}


def test_apply_json_stops_between_apps_on_sigterm(point_at, fake_home, monkeypatch, tmp_path, capsys):
    import os
    import signal

    import dotsync.apps

    _folder(point_at, tmp_path, ["zsh", "ghostty"])
    synced = []

    class Plan:
        changes = [object()]
        has_changes = True

    class StubApp:
        warnings: list = []

        def __init__(self, name):
            self.name = name

        def plan_to(self, _dir):
            return Plan()

        def sync_to(self, _dir):
            synced.append(self.name)
            if self.name == "zsh":
                os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(dotsync.apps, "build_app", lambda name, cfg: StubApp(name))

    rc, payload, _ = _run(capsys, ["pull", "--all", "--yes", "--json"])

    assert rc == 0
    assert synced == ["zsh"]
    first, second = payload["results"]
    assert first["outcome"] == "changed"
    assert (second["outcome"], second["reason"]) == ("skipped", "stopped")
