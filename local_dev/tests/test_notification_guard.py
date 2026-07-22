"""notification_guard 짝 테스트 — 설계 명세: local_dev/docs/notification-guard-spec.md"""
from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path

import pytest

from local_dev.serena_mcp_management.notification_guard import (
    CodexTarget,
    RepairOutcome,
    apply_text_repair,
    check_orca_notifications,
    discover_codex_targets,
    discover_orca_data_files,
    guard_codex_target,
    permission_request_state_keys,
    repair_claude_settings,
    repair_hooks_state,
    repair_notify,
    repair_tui_condition,
    run_notification_guard,
)

HOOKS_JSON = json.dumps({
    "hooks": {
        "PermissionRequest": [
            {"hooks": [{"type": "command", "command": "/bin/true", "timeout": 10}]}
        ],
        "Stop": [
            {"hooks": [{"type": "command", "command": "/bin/true", "timeout": 10}]}
        ],
    }
})

CLEAN_TUI = '[tui]\nnotifications = ["approval-requested"]\nnotification_condition = "unfocused"\n'


def clean_managed_config(home_dir: Path) -> str:
    key = f"{home_dir}/hooks.json:permission_request:0:0"
    return (
        'approvals_reviewer = "guardian_subagent"\n'
        "notify = []\n\n"
        f"{CLEAN_TUI}\n"
        f'[hooks.state."{key}"]\n'
        'trusted_hash = "sha256:e460"\n'
        "enabled = false\n"
    )


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    # 공백 포함 경로 강제: 실경로의 "Application Support" 대응을 우회로 통과 못 하게 한다
    home = tmp_path / "fake home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(
        'notify = []\n\n' + CLEAN_TUI
    )
    orca = home / "Library" / "Application Support" / "orca"
    for rel in ("codex-accounts/abc-123/home", "codex-runtime-home/home"):
        managed = orca / rel
        managed.mkdir(parents=True)
        (managed / "hooks.json").write_text(HOOKS_JSON)
        (managed / "config.toml").write_text(clean_managed_config(managed))
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"preferredNotifChannel": "notifications_disabled"}, indent=2)
    )
    profile = orca / "profiles" / "local-default"
    profile.mkdir(parents=True)
    (profile / "orca-data.json").write_text(json.dumps({
        "settings": {"notifications": {
            "enabled": True, "agentTaskComplete": True, "terminalBell": False,
        }}
    }))
    return home


class TestDiscovery:
    def test_finds_user_and_managed_configs(self, fake_home: Path) -> None:
        targets = discover_codex_targets(fake_home)
        configs = [t.config for t in targets]
        assert fake_home / ".codex" / "config.toml" in configs
        assert len([t for t in targets if t.hooks_json is not None]) == 2

    def test_user_config_has_no_hooks_json(self, fake_home: Path) -> None:
        user = [t for t in discover_codex_targets(fake_home)
                if t.config == fake_home / ".codex" / "config.toml"]
        assert user[0].hooks_json is None

    def test_missing_files_are_skipped(self, tmp_path: Path) -> None:
        assert discover_codex_targets(tmp_path / "empty home") == []

    def test_finds_orca_profiles(self, fake_home: Path) -> None:
        files = discover_orca_data_files(fake_home)
        assert len(files) == 1
        assert files[0].name == "orca-data.json"


SKY = ("/Users/x/.codex/computer-use/Codex Computer Use.app/Contents/SharedSupport/"
       "SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient")


class TestRepairNotify:
    def test_clean_config_unchanged(self) -> None:
        text = "notify = []\n\n[tools]\nview_image = true\n"
        assert repair_notify(text) == (text, None)

    def test_sky_reinjection_with_previous_notify_arg(self) -> None:
        text = (
            "# 주석은 보존된다\n"
            f'notify = ["{SKY}", "turn-ended", "--previous-notify", "[]"]\n'
            "\n[mcp_servers.computer-use]\n"
            f'command = "{SKY}"\n'
        )
        new, removed = repair_notify(text)
        assert "notify = []\n" in new
        assert removed is not None and "turn-ended" in removed
        assert "# 주석은 보존된다" in new
        # 테이블 내부의 SkyComputerUseClient 경로 줄은 무접촉
        assert f'command = "{SKY}"' in new

    def test_unknown_program_also_emptied(self) -> None:
        text = 'notify = ["/usr/bin/say", "done"]\n\n[tools]\n'
        new, removed = repair_notify(text)
        assert "notify = []\n" in new
        assert removed is not None and "/usr/bin/say" in removed

    def test_quoted_notify_key_also_repaired(self) -> None:
        text = '"notify" = ["/usr/bin/say"]\n\n[tools]\n'
        new, removed = repair_notify(text)
        assert "notify = []\n" in new
        assert removed is not None and "/usr/bin/say" in removed

    def test_absent_notify_untouched(self) -> None:
        text = "[tools]\nview_image = true\n"
        assert repair_notify(text) == (text, None)


class TestRepairTuiCondition:
    def test_always_becomes_unfocused(self) -> None:
        text = '[tui]\nnotification_condition = "always"\ntheme = "x"\n'
        new, repaired = repair_tui_condition(text)
        assert repaired is True
        assert 'notification_condition = "unfocused"' in new
        assert 'theme = "x"' in new

    def test_unfocused_unchanged(self) -> None:
        text = '[tui]\nnotification_condition = "unfocused"\n'
        assert repair_tui_condition(text) == (text, False)

    def test_same_key_outside_tui_untouched(self) -> None:
        # 수리가 실제로 일어나는 경로에서 [tui] 밖 동명 키가 보호되는지 검증
        text = (
            '[other]\nnotification_condition = "always"\n\n'
            '[tui]\nnotification_condition = "always"\n'
        )
        new, repaired = repair_tui_condition(text)
        assert repaired is True
        assert '[other]\nnotification_condition = "always"' in new
        assert '[tui]\nnotification_condition = "unfocused"' in new


class TestPermissionRequestKeys:
    def test_derives_index_from_hooks_json(self, tmp_path: Path) -> None:
        hooks = tmp_path / "fake home" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(json.dumps({"hooks": {"PermissionRequest": [
            {"hooks": [{"type": "command", "command": "/bin/true"},
                       {"type": "command", "command": "/bin/echo"}]},
        ]}}))
        keys = permission_request_state_keys(hooks)
        assert keys == [
            f"{hooks}:permission_request:0:0",
            f"{hooks}:permission_request:0:1",
        ]

    def test_no_permission_request_event(self, tmp_path: Path) -> None:
        hooks = tmp_path / "hooks.json"
        hooks.write_text(json.dumps({"hooks": {"Stop": []}}))
        assert permission_request_state_keys(hooks) == []


class TestRepairHooksState:
    KEY = "/fake home/hooks.json:permission_request:0:0"

    def test_enabled_false_already_present(self) -> None:
        text = f'[hooks.state."{self.KEY}"]\ntrusted_hash = "sha256:x"\nenabled = false\n'
        assert repair_hooks_state(text, [self.KEY]) == (text, [])

    def test_reinserts_removed_enabled_line(self) -> None:
        text = (
            f'[hooks.state."{self.KEY}"]\n'
            'trusted_hash = "sha256:x"\n\n'
            "[tools]\nview_image = true\n"
        )
        new, repaired = repair_hooks_state(text, [self.KEY])
        assert repaired == [self.KEY]
        cfg = __import__("tomllib").loads(new)
        assert cfg["hooks"]["state"][self.KEY]["enabled"] is False
        assert cfg["hooks"]["state"][self.KEY]["trusted_hash"] == "sha256:x"
        assert cfg["tools"]["view_image"] is True

    def test_creates_missing_block_at_eof(self) -> None:
        text = "[tools]\nview_image = true\n"
        new, repaired = repair_hooks_state(text, [self.KEY])
        assert repaired == [self.KEY]
        cfg = __import__("tomllib").loads(new)
        assert cfg["hooks"]["state"][self.KEY]["enabled"] is False

    def test_enabled_true_replaced_in_place(self) -> None:
        text = (
            f'[hooks.state."{self.KEY}"]\n'
            'trusted_hash = "sha256:x"\n'
            "enabled = true\n\n"
            "[tools]\nview_image = true\n"
        )
        new, repaired = repair_hooks_state(text, [self.KEY])
        assert repaired == [self.KEY]
        cfg = tomllib.loads(new)  # 중복 키 없이 유효 TOML이어야 한다
        assert cfg["hooks"]["state"][self.KEY]["enabled"] is False
        assert cfg["hooks"]["state"][self.KEY]["trusted_hash"] == "sha256:x"

    def test_multiple_keys_and_adjacent_headers(self) -> None:
        # 블록이 빈 줄 없이 다음 [hooks.state...] 헤더와 붙어 있는 변형 + 다중 키 한 번에 수리
        k2 = "/fake home/hooks.json:permission_request:0:1"
        text = (
            f'[hooks.state."{self.KEY}"]\ntrusted_hash = "sha256:x"\n'
            f'[hooks.state."{k2}"]\ntrusted_hash = "sha256:y"\n'
        )
        new, repaired = repair_hooks_state(text, [self.KEY, k2])
        assert repaired == [self.KEY, k2]
        cfg = tomllib.loads(new)
        assert cfg["hooks"]["state"][self.KEY]["enabled"] is False
        assert cfg["hooks"]["state"][k2]["enabled"] is False
        assert cfg["hooks"]["state"][self.KEY]["trusted_hash"] == "sha256:x"


class TestApplyTextRepair:
    def test_unchanged_when_transform_is_identity(self, tmp_path: Path) -> None:
        p = tmp_path / "a.toml"
        p.write_text("x = 1\n")
        outcome = apply_text_repair(p, lambda t: (t, None), tomllib.loads)
        assert outcome.status == "unchanged"

    def test_repaired_and_meta_passthrough(self, tmp_path: Path) -> None:
        p = tmp_path / "a.toml"
        p.write_text("x = 1\n")
        outcome = apply_text_repair(p, lambda t: ("x = 2\n", "meta!"), tomllib.loads)
        assert outcome.status == "repaired"
        assert outcome.meta == "meta!"
        assert p.read_text() == "x = 2\n"

    def test_invalid_result_leaves_original_untouched(self, tmp_path: Path) -> None:
        p = tmp_path / "a.toml"
        p.write_text("x = 1\n")
        outcome = apply_text_repair(p, lambda t: ("[broken", None), tomllib.loads)
        assert outcome.status == "invalid"
        assert p.read_text() == "x = 1\n"
        assert list(tmp_path.iterdir()) == [p]  # 임시 파일 잔류 없음

    def test_concurrent_write_detected_then_retried(self, tmp_path: Path) -> None:
        p = tmp_path / "a.toml"
        p.write_text("x = 1\n")
        calls = {"n": 0}

        def transform(text: str) -> tuple[str, None]:
            calls["n"] += 1
            if calls["n"] == 1:
                # 첫 시도 도중 다른 writer가 파일을 바꿔치기
                p.write_text("x = 99\n")
            return text.replace("x = 99", "x = 2").replace("x = 1", "x = 2"), None

        outcome = apply_text_repair(p, transform, tomllib.loads)
        assert outcome.status == "repaired"
        assert calls["n"] == 2          # 재시도 1회
        assert p.read_text() == "x = 2\n"

    def test_replace_failure_cleans_tmp_and_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = tmp_path / "a.toml"
        p.write_text("x = 1\n")

        def boom(src: object, dst: object) -> None:
            raise OSError("replace denied")

        monkeypatch.setattr(
            "local_dev.serena_mcp_management.notification_guard.os.replace", boom
        )
        with pytest.raises(OSError):
            apply_text_repair(p, lambda t: ("x = 2\n", None), tomllib.loads)
        assert p.read_text() == "x = 1\n"
        assert list(tmp_path.iterdir()) == [p]  # 임시 파일 잔류 없음

    def test_repeated_concurrent_writes_conflicted(self, tmp_path: Path) -> None:
        p = tmp_path / "a.toml"
        p.write_text("x = 1\n")
        counter = {"n": 0}

        def transform(text: str) -> tuple[str, None]:
            counter["n"] += 1
            # 매 시도마다 다른 writer가 개입 (크기가 매번 달라지도록 해 mtime 해상도 플레이크 회피)
            p.write_text(f"x = {'9' * (counter['n'] + 1)}\n")
            return "x = 2\n", None

        outcome = apply_text_repair(p, transform, tomllib.loads)
        assert outcome.status == "conflicted"
        assert counter["n"] == 2  # 정확히 1회 재시도 후 포기
        assert list(tmp_path.iterdir()) == [p]  # 임시 파일 잔류 없음


class TestClaudeSettings:
    def test_drifted_channel_repaired_with_korean_preserved(self, tmp_path: Path) -> None:
        p = tmp_path / "settings.json"
        p.write_text(json.dumps(
            {"preferredNotifChannel": "terminal_bell", "language": "한국어"},
            indent=2, ensure_ascii=False,
        ))
        outcome = repair_claude_settings(p)
        assert outcome.status == "repaired"
        data = json.loads(p.read_text())
        assert data["preferredNotifChannel"] == "notifications_disabled"
        assert "한국어" in p.read_text()  # ensure_ascii=False 왕복

    def test_clean_settings_unchanged(self, tmp_path: Path) -> None:
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"preferredNotifChannel": "notifications_disabled"}))
        assert repair_claude_settings(p).status == "unchanged"


class TestOrcaToggles:
    def _write(self, tmp_path: Path, **notif: object) -> Path:
        p = tmp_path / "orca-data.json"
        base = {"enabled": True, "agentTaskComplete": True, "terminalBell": False}
        base.update(notif)
        p.write_text(json.dumps({"settings": {"notifications": base}}))
        return p

    def test_clean_toggles_no_actions(self, tmp_path: Path) -> None:
        assert check_orca_notifications(self._write(tmp_path)) == []

    def test_master_enabled_off_warns(self, tmp_path: Path) -> None:
        actions = check_orca_notifications(self._write(tmp_path, enabled=False))
        assert len(actions) == 1 and actions[0].kind == "warn"

    def test_bell_on_warns_without_writing(self, tmp_path: Path) -> None:
        p = self._write(tmp_path, terminalBell=True)
        before = p.read_text()
        actions = check_orca_notifications(p)
        assert actions[0].kind == "warn"
        assert p.read_text() == before  # 절대 수정하지 않는다


class TestGuardCodexTarget:
    def test_reviewer_user_skips_hooks_repair_but_warns_on_leftover(
        self, fake_home: Path
    ) -> None:
        managed = (fake_home / "Library" / "Application Support" / "orca"
                   / "codex-accounts" / "abc-123" / "home")
        config = managed / "config.toml"
        config.write_text(
            clean_managed_config(managed).replace(
                '"guardian_subagent"', '"user"'
            )
        )
        target = CodexTarget(config=config, hooks_json=managed / "hooks.json")
        actions = guard_codex_target(target)
        # enabled=false가 남아 있으므로 경고 1건, 수리 0건
        assert [a.kind for a in actions] == ["warn"]

    def test_missing_hooks_json_warns_and_skips(self, fake_home: Path) -> None:
        managed = (fake_home / "Library" / "Application Support" / "orca"
                   / "codex-runtime-home" / "home")
        (managed / "hooks.json").unlink()
        target = CodexTarget(config=managed / "config.toml",
                             hooks_json=managed / "hooks.json")
        actions = guard_codex_target(target)
        assert any(a.kind == "warn" for a in actions)


class TestRunNotificationGuard:
    def test_clean_home_silent(self, fake_home: Path) -> None:
        out = io.StringIO()
        actions = run_notification_guard(home=fake_home, stream=out)
        assert actions == []
        assert out.getvalue() == ""

    def test_drift_repaired_and_reported(self, fake_home: Path) -> None:
        user = fake_home / ".codex" / "config.toml"
        user.write_text(f'notify = ["{SKY}", "turn-ended"]\n\n' + CLEAN_TUI)
        out = io.StringIO()
        actions = run_notification_guard(home=fake_home, stream=out)
        assert any(a.kind == "repair" for a in actions)
        assert "notif guard" in out.getvalue()
        assert "notify = []" in user.read_text().splitlines()[0]

    def test_internal_error_becomes_warn_not_raise(self, fake_home: Path) -> None:
        # 파손된 TOML → 개별 대상 오류가 warn으로 강등되고 전체는 계속
        (fake_home / ".codex" / "config.toml").write_text("[broken")
        out = io.StringIO()
        actions = run_notification_guard(home=fake_home, stream=out)
        assert any(a.kind == "warn" for a in actions)
