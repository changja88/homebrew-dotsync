from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from local_dev.serena_mcp_management import user_scope_guidance as guidance


REPO_ROOT = Path(__file__).resolve().parents[2]

LEGACY_PRE_TOOL_USE_COMMAND = r'''r="$PWD"; while [ "$r" != "/" ] && [ ! -e "$r/.git" ] && [ ! -f "$r/.serena/project.yml" ]; do r=$(dirname "$r"); done; [ -f "$r/.serena/project.yml" ] && printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"This repository explicitly opts into Serena via .serena/project.yml. For exact symbol definitions and who-calls-what, prefer Serena find_symbol / find_referencing_symbols over text grep. If Serena tools are deferred, load them with ToolSearch first. No active-project check is needed because the launcher pins Serena to this repo. If Serena tools are unavailable, continue with built-in tools."}}' || true'''
LEGACY_SESSION_START_COMMAND = r'''root="$PWD"; while [ "$root" != "/" ] && [ ! -e "$root/.git" ]; do root=$(dirname "$root"); done; [ "$root" = "/" ] && root="$PWD"; serena=disabled; [ -f "$root/.serena/project.yml" ] && serena=enabled; graphify=disabled; [ -f "$root/graphify-out/graph.json" ] && graphify=enabled; printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Project tool opt-in: Serena=%s (.serena/project.yml), graphify=%s (graphify-out/graph.json). Only use a tool when enabled or when the user explicitly requests it. When disabled, do not load, call, or initialize it; do not create or rebuild graphify-out or install graphify integration or hooks. Use built-in tools instead. If Serena is enabled, the dotsync launcher pins it to this repo; load symbolic tools before symbol-level code work and never call activate_project. If graphify is enabled, query the existing graph but never rebuild it without an explicit request."}}' "$serena" "$graphify"'''


def _write_user_scope_fixture(root: Path, *, live: bool) -> None:
    codex_dir = root / (".codex" if live else "codex")
    claude_dir = root / (".claude" if live else "claude")
    codex_dir.mkdir(parents=True)
    claude_dir.mkdir(parents=True)
    codex_dir.joinpath("AGENTS.md").write_text(
        "# Global\n\n"
        "## 도구 활용 원칙\n\n"
        "### Serena MCP\n\n"
        "old codex context: --context codex, get_current_config, search_for_pattern\n\n"
        "### graphify · Serena · 기본 도구 라우팅\n\n"
        "old graphify guidance\n\n"
        "## 코딩 설계 원칙\n\n"
        "keep-codex-tail\n",
        encoding="utf-8",
    )
    claude_dir.joinpath("CLAUDE.md").write_text(
        "# Global\n\n"
        "## 도구 활용 원칙\n\n"
        "### Serena MCP\n\n"
        "old claude context: --context claude-code, replace_content\n\n"
        "### graphify · Serena · 기본 도구 라우팅\n\n"
        "old graphify guidance\n\n"
        "## 코딩 설계 원칙\n\n"
        "keep-claude-tail\n",
        encoding="utf-8",
    )
    claude_dir.joinpath("settings.json").write_text(
        json.dumps(
            {
                "permissions": {"allow": ["keep-permission"]},
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Grep",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": LEGACY_PRE_TOOL_USE_COMMAND,
                                },
                                {
                                    "type": "command",
                                    "command": "personal check for .serena/project.yml",
                                }
                            ],
                        },
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": "keep-bash-hook"}
                            ],
                        },
                    ],
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": LEGACY_SESSION_START_COMMAND,
                                },
                                {
                                    "type": "command",
                                    "command": "personal Project tool opt-in audit",
                                }
                            ]
                        }
                    ],
                },
                "unrelated": {"keep": True},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _install_shim(tmp_path: Path) -> tuple[Path, Path]:
    sync_root = tmp_path / "dotsync_config"
    live_home = tmp_path / "home"
    _write_user_scope_fixture(sync_root, live=False)
    _write_user_scope_fixture(live_home, live=True)
    zshrc = live_home / ".zshrc"
    zshrc.write_text("# existing\n", encoding="utf-8")

    subprocess.run(
        [
            "make",
            "-C",
            str(REPO_ROOT / "local_dev"),
            "install-shim",
            f"STABLE_DIR={sync_root / 'agent_launcher'}",
            f"DOTSYNC_CONFIG_DIR={sync_root}",
            f"LIVE_HOME={live_home}",
            f"ZSHRC={zshrc}",
            f"PYTHON={sys.executable}",
            f"PYTHON_EXECUTABLE={sys.executable}",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    return sync_root, live_home


def test_install_guidance_rolls_back_all_targets_when_a_commit_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """한 target의 교체 실패가 sync/live 지침을 서로 다른 상태로 남기지 않는다."""
    sync_root = tmp_path / "dotsync_config"
    live_home = tmp_path / "home"
    _write_user_scope_fixture(sync_root, live=False)
    _write_user_scope_fixture(live_home, live=True)
    targets = [
        path
        for path, _kind in guidance._target_paths(sync_root, live_home)
    ]
    original = {path: path.read_bytes() for path in targets}
    real_atomic_write = guidance._atomic_write
    calls = 0

    def fail_fourth_write(path: Path, text: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("injected live-scope write failure")
        real_atomic_write(path, text)

    monkeypatch.setattr(guidance, "_atomic_write", fail_fourth_write)

    with pytest.raises(guidance.GuidanceUpdateError, match="rolled back"):
        guidance.install_guidance(sync_root, live_home)

    assert {path: path.read_bytes() for path in targets} == original


def test_install_guidance_rejects_dangling_partial_sync_target(
    tmp_path: Path,
) -> None:
    """깨진 symlink 하나를 전체 sync 설정 부재로 오인해 조용히 skip하지 않는다."""
    sync_root = tmp_path / "dotsync_config"
    (sync_root / "codex").mkdir(parents=True)
    (sync_root / "codex" / "AGENTS.md").symlink_to(sync_root / "missing")

    with pytest.raises(guidance.GuidanceUpdateError, match="partial"):
        guidance.install_guidance(sync_root, tmp_path / "home")


@pytest.mark.no_subprocess_block
def test_install_shim_updates_guidance_without_replacing_unrelated_settings(
    tmp_path: Path,
) -> None:
    """빠진 updater 호출은 지침을 stale 상태로 남기고 unrelated 보존도 증명하지 못한다."""
    sync_root, live_home = _install_shim(tmp_path)

    for root, live in ((sync_root, False), (live_home, True)):
        codex_dir = root / (".codex" if live else "codex")
        claude_dir = root / (".claude" if live else "claude")
        codex = (codex_dir / "AGENTS.md").read_text(encoding="utf-8")
        claude = (claude_dir / "CLAUDE.md").read_text(encoding="utf-8")
        settings = json.loads(
            (claude_dir / "settings.json").read_text(encoding="utf-8")
        )

        assert "같은 워크트리에서 실행한 Codex와 Claude는 동일한 Serena 서버" in codex
        assert "같은 워크트리에서 실행한 Codex와 Claude는 동일한 Serena 서버" in claude
        assert "linked worktree에서는 기존 그래프를 조회만" in codex
        assert "linked worktree에서는 기존 그래프를 조회만" in claude
        assert "primary checkout의 공식 Git 훅" in codex
        assert "primary checkout의 공식 Git 훅" in claude
        assert "Graphify를 MCP 서버로 등록하지 않는다" in codex
        assert "Graphify를 MCP 서버로 등록하지 않는다" in claude
        assert "--context codex" not in codex
        assert "--context claude-code" not in claude
        assert "keep-codex-tail" in codex
        assert "keep-claude-tail" in claude
        assert settings["permissions"] == {"allow": ["keep-permission"]}
        assert settings["unrelated"] == {"keep": True}
        assert settings["hooks"]["PreToolUse"][1]["hooks"][0]["command"] == (
            "keep-bash-hook"
        )
        assert settings["hooks"]["PreToolUse"][0]["hooks"][1]["command"] == (
            "personal check for .serena/project.yml"
        )
        assert settings["hooks"]["SessionStart"][0]["hooks"][1]["command"] == (
            "personal Project tool opt-in audit"
        )

    assert (sync_root / "codex" / "AGENTS.md").read_bytes() == (
        live_home / ".codex" / "AGENTS.md"
    ).read_bytes()
    assert (sync_root / "claude" / "CLAUDE.md").read_bytes() == (
        live_home / ".claude" / "CLAUDE.md"
    ).read_bytes()
    assert (sync_root / "claude" / "settings.json").read_bytes() == (
        live_home / ".claude" / "settings.json"
    ).read_bytes()


def _session_context(settings_path: Path, cwd: Path) -> str:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    proc = subprocess.run(
        ["sh", "-c", command],
        cwd=cwd,
        env={**os.environ, "HOME": str(settings_path.parents[1])},
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(proc.stdout)
    return payload["hookSpecificOutput"]["additionalContext"]


@pytest.mark.no_subprocess_block
def test_installed_session_hook_distinguishes_primary_and_linked_worktrees(
    tmp_path: Path,
) -> None:
    """git-dir/common-dir 판별이 빠지면 linked checkout에 update 안내가 노출된다."""
    sync_root, _live_home = _install_shim(tmp_path)
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    primary.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=primary, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=primary,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=primary, check=True
    )
    (primary / "tracked.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=primary, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=primary, check=True)
    subprocess.run(
        ["git", "worktree", "add", "-q", str(linked)], cwd=primary, check=True
    )
    for root in (primary, linked):
        (root / ".serena").mkdir()
        (root / ".serena" / "project.yml").write_text("project_name: test\n")
        (root / "graphify-out").mkdir()
        (root / "graphify-out" / "graph.json").write_text("{}\n")

    settings_path = sync_root / "claude" / "settings.json"
    primary_context = _session_context(settings_path, primary)
    linked_context = _session_context(settings_path, linked)

    assert "checkout=primary" in primary_context
    assert "canonical graph" in primary_context
    assert "checkout=linked" in linked_context
    assert "query-only" in linked_context
    assert "do not run graphify update" in linked_context
