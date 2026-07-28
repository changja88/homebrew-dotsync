"""notification_guard 짝 테스트 — 설계 명세: local_dev/docs/notification-guard-spec.md"""
from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path

import pytest

from local_dev.serena_mcp_management.notification_guard import (
    CodexTarget,
    apply_text_repair,
    check_orca_notifications,
    discover_codex_targets,
    discover_orca_data_files,
    guard_codex_target,
    hook_state_keys,
    permission_request_state_keys,
    repair_claude_settings,
    repair_hooks_state,
    repair_notify,
    run_notification_guard,
)

_HOOK = {"type": "command", "command": "/bin/true", "timeout": 10}
# 실환경(orca 07-23 설치본) 근사: Subagent 이벤트 포함
HOOKS_JSON = json.dumps({
    "hooks": {
        "PermissionRequest": [{"hooks": [_HOOK]}],
        "SubagentStart": [{"hooks": [_HOOK]}],
        "SubagentStop": [{"hooks": [_HOOK]}],
        "Stop": [{"hooks": [_HOOK]}],
    }
})

CLEAN_TUI = '[tui]\nnotifications = ["approval-requested"]\nnotification_condition = "unfocused"\n'


def clean_managed_config(home_dir: Path) -> str:
    blocks = "".join(
        f'[hooks.state."{home_dir}/hooks.json:{event}:0:0"]\n'
        f'trusted_hash = "sha256:e460"\n'
        "enabled = false\n\n"
        for event in ("permission_request", "subagent_start", "subagent_stop")
    )
    return (
        'approvals_reviewer = "guardian_subagent"\n'
        "notify = []\n\n"
        f"{CLEAN_TUI}\n"
        f"{blocks}"
    )


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    # 공백 포함 경로 강제: 실경로의 "Application Support" 대응을 우회로 통과 못 하게 한다
    home = tmp_path / "fake home"
    user = home / ".codex"
    user.mkdir(parents=True)
    # v6: orca가 user 홈에도 hooks.json을 설치한다 (실행 홈이 user 홈)
    (user / "hooks.json").write_text(HOOKS_JSON)
    (user / "config.toml").write_text(clean_managed_config(user))
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
            # terminalBell은 사용자 관리 영역 — 켜져 있어도 clean이어야 한다
            "enabled": True, "agentTaskComplete": True,
            "suppressWhenFocused": False, "terminalBell": True,
        }}
    }))
    return home


class TestDiscovery:
    def test_finds_user_and_managed_configs(self, fake_home: Path) -> None:
        targets = discover_codex_targets(fake_home)
        configs = [t.config for t in targets]
        assert fake_home / ".codex" / "config.toml" in configs
        assert len([t for t in targets if t.hooks_json is not None]) == 3

    def test_user_config_targets_user_hooks_json(self, fake_home: Path) -> None:
        # v6: orca가 user 홈에 hooks.json을 설치하고 codex가 user 홈으로 실행되므로
        # user config에도 #3/#6을 적용한다 (부재 시 공허 충족은 guard 쪽에서)
        user = [t for t in discover_codex_targets(fake_home)
                if t.config == fake_home / ".codex" / "config.toml"]
        assert user[0].hooks_json == fake_home / ".codex" / "hooks.json"

    def test_missing_files_are_skipped(self, tmp_path: Path) -> None:
        assert discover_codex_targets(tmp_path / "empty home") == []

    def test_finds_orca_profiles(self, fake_home: Path) -> None:
        second = (fake_home / "Library" / "Application Support" / "orca"
                  / "profiles" / "work" / "orca-data.json")
        second.parent.mkdir(parents=True)
        second.write_text("{}")
        files = discover_orca_data_files(fake_home)
        assert len(files) == 2
        assert all(f.name == "orca-data.json" for f in files)


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


    def test_absent_notify_untouched(self) -> None:
        text = "[tools]\nview_image = true\n"
        assert repair_notify(text) == (text, None)


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


class TestHookStateKeys:
    def test_subagent_events_derive_snake_case_keys(self, tmp_path: Path) -> None:
        hooks = tmp_path / "fake home" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text(HOOKS_JSON)
        assert hook_state_keys(hooks, "SubagentStart") == [
            f"{hooks}:subagent_start:0:0"
        ]
        assert hook_state_keys(hooks, "SubagentStop") == [
            f"{hooks}:subagent_stop:0:0"
        ]

    def test_handler_index_follows_json(self, tmp_path: Path) -> None:
        hooks = tmp_path / "hooks.json"
        hooks.write_text(json.dumps({"hooks": {"SubagentStop": [
            {"hooks": [_HOOK, _HOOK]},
            {"hooks": [_HOOK]},
        ]}}))
        assert hook_state_keys(hooks, "SubagentStop") == [
            f"{hooks}:subagent_stop:0:0",
            f"{hooks}:subagent_stop:0:1",
            f"{hooks}:subagent_stop:1:0",
        ]


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

    def test_preserves_file_mode(self, tmp_path: Path) -> None:
        p = tmp_path / "a.toml"
        p.write_text("x = 1\n")
        p.chmod(0o600)
        outcome = apply_text_repair(p, lambda t: ("x = 2\n", None), tomllib.loads)
        assert outcome.status == "repaired"
        assert (p.stat().st_mode & 0o777) == 0o600


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
        base = {"enabled": True, "agentTaskComplete": True, "suppressWhenFocused": False}
        base.update(notif)
        p.write_text(json.dumps({"settings": {"notifications": base}}))
        return p

    def test_clean_toggles_no_actions(self, tmp_path: Path) -> None:
        assert check_orca_notifications(self._write(tmp_path)) == []

    def test_master_enabled_off_warns(self, tmp_path: Path) -> None:
        actions = check_orca_notifications(self._write(tmp_path, enabled=False))
        assert len(actions) == 1 and actions[0].kind == "warn"

    def test_agent_task_complete_off_warns(self, tmp_path: Path) -> None:
        actions = check_orca_notifications(self._write(tmp_path, agentTaskComplete=False))
        assert len(actions) == 1 and actions[0].kind == "warn"
        assert "완료" in actions[0].message

    def test_suppress_when_focused_on_warns_without_writing(self, tmp_path: Path) -> None:
        # 요구사항 "포커스 무관 항상 알림"의 핵심 스위치 — 켜져 있으면 경고, 수리는 안 함
        p = self._write(tmp_path, suppressWhenFocused=True)
        before = p.read_text()
        actions = check_orca_notifications(p)
        assert len(actions) == 1 and actions[0].kind == "warn"
        assert "포커스" in actions[0].message
        assert p.read_text() == before  # 절대 수정하지 않는다

    def test_terminal_bell_is_user_managed_not_checked(self, tmp_path: Path) -> None:
        # 벨은 사용자 관리 영역 — 어떤 값이든 가드가 관여하지 않는다
        assert check_orca_notifications(self._write(tmp_path, terminalBell=True)) == []
        assert check_orca_notifications(self._write(tmp_path, terminalBell=False)) == []


class TestGuardCodexTarget:
    def test_auto_review_disables_permission_request_hook(
        self, fake_home: Path
    ) -> None:
        managed = (
            fake_home
            / "Library"
            / "Application Support"
            / "orca"
            / "codex-runtime-home"
            / "home"
        )
        hooks = managed / "hooks.json"
        key = f"{hooks}:permission_request:0:0"
        config = managed / "config.toml"
        text = clean_managed_config(managed).replace(
            '"guardian_subagent"', '"auto_review"'
        )
        text = text.replace(
            f'[hooks.state."{key}"]\n'
            'trusted_hash = "sha256:e460"\n'
            "enabled = false\n",
            f'[hooks.state."{key}"]\n'
            'trusted_hash = "sha256:e460"\n',
        )
        config.write_text(text)

        actions = guard_codex_target(
            CodexTarget(config=config, hooks_json=hooks)
        )

        state = tomllib.loads(config.read_text())["hooks"]["state"]
        assert state[key].get("enabled") is False
        assert any(
            action.kind == "repair" and "permission_request" in action.message
            for action in actions
        )

    def test_missing_hooks_json_silently_skipped(self, fake_home: Path) -> None:
        # 훅이 없다 = 가짜 "needs input" 알림의 원인이 없다 — 공허 충족.
        # 로그인 잔재 홈(config.toml만 있는 codex-accounts/*/home)이 매 launch마다
        # 고칠 수 없는 경고를 반복하지 않아야 한다.
        managed = (fake_home / "Library" / "Application Support" / "orca"
                   / "codex-runtime-home" / "home")
        (managed / "hooks.json").unlink()
        target = CodexTarget(config=managed / "config.toml",
                             hooks_json=managed / "hooks.json")
        assert guard_codex_target(target) == []

    def test_tui_notification_condition_left_untouched(self, fake_home: Path) -> None:
        # 벨 채널 설정(codex TUI)은 사용자 관리 — "always"여도 되돌리지 않는다
        managed = (fake_home / "Library" / "Application Support" / "orca"
                   / "codex-runtime-home" / "home")
        config = managed / "config.toml"
        config.write_text(clean_managed_config(managed).replace(
            'notification_condition = "unfocused"',
            'notification_condition = "always"',
        ))
        before = config.read_text()
        target = CodexTarget(config=config, hooks_json=managed / "hooks.json")
        assert guard_codex_target(target) == []
        assert config.read_text() == before

    def test_subagent_hooks_disabled_even_when_reviewer_is_user(
        self, fake_home: Path
    ) -> None:
        # #6은 무조건 적용 — 요구 3("어떤 경우에도 알림 금지")은 reviewer와 무관
        managed = (fake_home / "Library" / "Application Support" / "orca"
                   / "codex-runtime-home" / "home")
        config = managed / "config.toml"
        text = clean_managed_config(managed).replace('"guardian_subagent"', '"user"')
        # subagent 두 블록의 enabled 줄이 제거된 드리프트 (codex 신뢰 재기록 형태)
        text = text.replace(
            f'[hooks.state."{managed}/hooks.json:subagent_start:0:0"]\n'
            'trusted_hash = "sha256:e460"\nenabled = false\n',
            f'[hooks.state."{managed}/hooks.json:subagent_start:0:0"]\n'
            'trusted_hash = "sha256:e460"\n',
        ).replace(
            f'[hooks.state."{managed}/hooks.json:subagent_stop:0:0"]\n'
            'trusted_hash = "sha256:e460"\nenabled = false\n',
            f'[hooks.state."{managed}/hooks.json:subagent_stop:0:0"]\n'
            'trusted_hash = "sha256:e460"\n',
        )
        config.write_text(text)
        target = CodexTarget(config=config, hooks_json=managed / "hooks.json")
        actions = guard_codex_target(target)
        assert any(a.kind == "repair" and "subagent" in a.message for a in actions)
        state = tomllib.loads(config.read_text())["hooks"]["state"]
        assert state[f"{managed}/hooks.json:subagent_start:0:0"]["enabled"] is False
        assert state[f"{managed}/hooks.json:subagent_stop:0:0"]["enabled"] is False
        # reviewer="user"이므로 permission_request는 수리하지 않는다 (잔존 경고만)
        assert any(a.kind == "warn" for a in actions)

    def test_tool_use_hooks_disabled_as_roster_revive_vectors(
        self, fake_home: Path
    ) -> None:
        # Orca는 agent_id가 붙은 이벤트면 무엇이든 서브에이전트 명부를 되살리고, 그러면
        # 완료 상태가 working으로 되돌아가 다음 완료에서 알림이 한 번 더 나간다
        # (orca 1.4.152 main/index.js:10535-10547 + :8266, 2026-07-25 코드 확인).
        # 서브에이전트의 도구 호출마다 PreToolUse/PostToolUse가 agent_id를 달고 오므로
        # subagent 훅만 끄는 것으로는 요구 3을 보장하지 못한다.
        managed = (fake_home / "Library" / "Application Support" / "orca"
                   / "codex-runtime-home" / "home")
        hooks = managed / "hooks.json"
        hooks.write_text(json.dumps({"hooks": {
            "SessionStart": [{"hooks": [_HOOK]}],
            "UserPromptSubmit": [{"hooks": [_HOOK]}],
            "PreToolUse": [{"hooks": [_HOOK]}],
            "PostToolUse": [{"hooks": [_HOOK]}],
            "PermissionRequest": [{"hooks": [_HOOK]}],
            "SubagentStart": [{"hooks": [_HOOK]}],
            "SubagentStop": [{"hooks": [_HOOK]}],
            "Stop": [{"hooks": [_HOOK]}],
        }}))
        config = managed / "config.toml"
        config.write_text('approvals_reviewer = "user"\nnotify = []\n\n' + CLEAN_TUI)
        target = CodexTarget(config=config, hooks_json=hooks)
        guard_codex_target(target)
        state = tomllib.loads(config.read_text())["hooks"]["state"]

        for event in ("post_tool_use", "subagent_start", "subagent_stop"):
            assert state[f"{hooks}:{event}:0:0"]["enabled"] is False, event

        # Orca가 알림을 만들려면 필요한 신호는 살아 있어야 한다:
        # user_prompt_submit→working, permission_request→needs input, stop→done,
        # session_start→프로세스당 명부 리셋(pin 상태 방지),
        # pre_tool_use→codex request_user_input이 waiting으로 전달되는 유일한 경로(요구 1)
        for event in ("session_start", "user_prompt_submit", "permission_request",
                      "stop", "pre_tool_use"):
            entry = state.get(f"{hooks}:{event}:0:0", {})
            assert entry.get("enabled") is not False, event

    def test_subagent_blocks_created_when_missing(self, fake_home: Path) -> None:
        # 엔트리 자체가 없는 홈 (orca 관리 홈 재생성 직후 형태) → 블록 생성
        managed = (fake_home / "Library" / "Application Support" / "orca"
                   / "codex-accounts" / "abc-123" / "home")
        config = managed / "config.toml"
        key = f"{managed}/hooks.json:permission_request:0:0"
        config.write_text(
            'approvals_reviewer = "guardian_subagent"\n'
            "notify = []\n\n"
            f'[hooks.state."{key}"]\n'
            "enabled = false\n"
        )
        target = CodexTarget(config=config, hooks_json=managed / "hooks.json")
        actions = guard_codex_target(target)
        assert any(a.kind == "repair" and "subagent" in a.message for a in actions)
        state = tomllib.loads(config.read_text())["hooks"]["state"]
        assert state[f"{managed}/hooks.json:subagent_start:0:0"]["enabled"] is False
        assert state[f"{managed}/hooks.json:subagent_stop:0:0"]["enabled"] is False


    def test_user_home_hooks_json_gets_full_repair(self, fake_home: Path) -> None:
        # v6 핵심 시나리오: user 홈이 실행 홈 — trusted_hash만 있고 enabled 없음
        user = fake_home / ".codex"
        config = user / "config.toml"
        blocks = "".join(
            f'[hooks.state."{user}/hooks.json:{event}:0:0"]\n'
            f'trusted_hash = "sha256:e460"\n\n'
            for event in ("permission_request", "subagent_start", "subagent_stop")
        )
        config.write_text(
            'approvals_reviewer = "guardian_subagent"\n'
            "notify = []\n\n" + blocks
        )
        out = io.StringIO()
        actions = run_notification_guard(home=fake_home, stream=out)
        repairs = [a for a in actions if a.kind == "repair" and a.path == config]
        assert repairs, "user config가 수리 대상이어야 한다"
        state = tomllib.loads(config.read_text())["hooks"]["state"]
        for event in ("permission_request", "subagent_start", "subagent_stop"):
            assert state[f"{user}/hooks.json:{event}:0:0"]["enabled"] is False, event
            assert state[f"{user}/hooks.json:{event}:0:0"]["trusted_hash"] == "sha256:e460"

    def test_corrupt_hooks_json_warns_but_keeps_other_repairs(self, fake_home: Path) -> None:
        managed = (fake_home / "Library" / "Application Support" / "orca"
                   / "codex-accounts" / "abc-123" / "home")
        (managed / "hooks.json").write_text("{broken json")
        config = managed / "config.toml"
        config.write_text(clean_managed_config(managed).replace(
            "notify = []", f'notify = ["{SKY}", "turn-ended"]'
        ))
        target = CodexTarget(config=config, hooks_json=managed / "hooks.json")
        actions = guard_codex_target(target)
        # notify 수리는 수행·보고되고, hooks.json 문제는 warn으로 남는다
        assert any(a.kind == "repair" for a in actions)
        assert any(a.kind == "warn" and "hooks.json" in a.message for a in actions)
        assert 'notify = []' in config.read_text()


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
