"""notification_guard 짝 테스트 — 설계 명세: local_dev/docs/notification-guard-spec.md"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_dev.serena_mcp_management.notification_guard import (
    CodexTarget,
    discover_codex_targets,
    discover_orca_data_files,
    permission_request_state_keys,
    repair_hooks_state,
    repair_notify,
    repair_tui_condition,
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
