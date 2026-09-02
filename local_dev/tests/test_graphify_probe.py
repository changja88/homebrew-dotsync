"""graphify_probe: Graphify 자신의 식별 표식만으로 설치 상태를 판정한다."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from local_dev.serena_mcp_management import graphify_probe as probe


def _hook_text(start_marker: str, *, guard: bool = True, python_pin: str | None = None) -> str:
    end_marker = start_marker.replace("-start", "-end")
    guard_lines = (
        '_GFY_GITDIR=$(cd "$(git rev-parse --git-dir 2>/dev/null)" 2>/dev/null && pwd)\n'
        '_GFY_COMMONDIR=$(cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd)\n'
        'if [ -n "$_GFY_COMMONDIR" ] && [ "$_GFY_GITDIR" != "$_GFY_COMMONDIR" ]; then\n'
        "    exit 0\n"
        "fi\n"
        if guard
        else ""
    )
    pin_line = f'GRAPHIFY_PYTHON="{python_pin}"\n' if python_pin else ""
    return (
        "#!/bin/sh\n"
        f"{start_marker}\n"
        f"{guard_lines}"
        f"{pin_line}"
        "graphify update . >/dev/null 2>&1\n"
        f"{end_marker}\n"
    )


def _write_claude_integration(project: Path, *, command: str, section: bool = True) -> None:
    body = "# CLAUDE.md\n\n"
    if section:
        body += "## graphify\n\nThis project has a knowledge graph at graphify-out/.\n"
    (project / "CLAUDE.md").write_text(body)
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({
        "hooks": {"PreToolUse": [
            {"matcher": "Bash|Grep", "hooks": [{"type": "command", "command": command}]},
        ]},
    }))


def _fake_exe(tmp_path: Path) -> Path:
    exe = tmp_path / "bin" / "graphify"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("#!/bin/sh\nexit 0\n")
    return exe


# ---------------------------------------------------------------- integration


def test_integration_claude_recognises_hook_guard_command(tmp_path):
    """0.9.4x+ 형식: `<exe> hook-guard search` — 명령 문구에 graphify-out이 없다."""
    project = tmp_path / "project"
    project.mkdir()
    exe = _fake_exe(tmp_path)
    _write_claude_integration(project, command=f"{exe} hook-guard search")

    assert probe.integration_status(project, "claude") == "installed"


def test_integration_claude_recognises_legacy_inline_shell_hook(tmp_path):
    """예전 형식: 인라인 bash가 graphify-out/graph.json을 직접 참조한다."""
    project = tmp_path / "project"
    project.mkdir()
    legacy = (
        "CMD=$(python3 -c \"import json,sys; print(json.load(sys.stdin))\"); "
        "[ -f graphify-out/graph.json ] && echo graphify || true"
    )
    _write_claude_integration(project, command=legacy)

    assert probe.integration_status(project, "claude") == "installed"


def test_integration_claude_accepts_project_scoped_bare_command(tmp_path):
    """`graphify claude install --project`는 절대경로 대신 bare `graphify`를 쓴다."""
    project = tmp_path / "project"
    project.mkdir()
    _write_claude_integration(project, command="graphify hook-guard search")

    assert probe.integration_status(project, "claude") == "installed"


def test_integration_missing_without_section_header(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    exe = _fake_exe(tmp_path)
    _write_claude_integration(project, command=f"{exe} hook-guard search", section=False)

    assert probe.integration_status(project, "claude") == "missing"


def test_integration_missing_when_graphify_only_appears_outside_pretooluse(tmp_path):
    """permissions에 graphify가 있어도 PreToolUse hook이 없으면 통합이 아니다."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("## graphify\n")
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "permissions": {"allow": ["Bash(graphify:*)"]},
        "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other"}]},
        ]},
    }))

    assert probe.integration_status(project, "claude") == "missing"


def test_integration_missing_when_hook_executable_dangles(tmp_path):
    """uv tool 재설치 후 hook에 박힌 절대경로가 사라지면 등록돼 있어도 실행 불가."""
    project = tmp_path / "project"
    project.mkdir()
    _write_claude_integration(
        project, command=f"{tmp_path}/gone/graphify hook-guard search"
    )

    assert probe.integration_status(project, "claude") == "missing"


def test_integration_missing_when_settings_json_is_invalid(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("## graphify\n")
    settings = project / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{not json")

    assert probe.integration_status(project, "claude") == "missing"


def test_integration_codex_uses_agents_md_and_codex_hooks(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    exe = _fake_exe(tmp_path)
    (project / "AGENTS.md").write_text("# AGENTS.md\n\n## graphify\n\ngraph at graphify-out/\n")
    hooks = project / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": f"{exe} hook-check"}]},
    ]}}))

    assert probe.integration_status(project, "codex") == "installed"
    assert probe.integration_status(project, "claude") == "missing"


def test_integration_fingerprint_tracks_both_files(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    exe = _fake_exe(tmp_path)
    _write_claude_integration(project, command=f"{exe} hook-guard search")
    before = probe.integration_fingerprint(project, "claude")

    (project / "CLAUDE.md").write_text("## graphify\n\nedited\n")

    assert probe.integration_fingerprint(project, "claude") != before
    assert probe.integration_fingerprint(project, "codex") != before


# ------------------------------------------------------------------ git hooks


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)


@pytest.mark.no_subprocess_block
def test_hook_status_respects_core_hooks_path(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "core.hooksPath", ".githooks")
    hooks_dir = project / ".githooks"
    hooks_dir.mkdir()
    (hooks_dir / "post-commit").write_text(_hook_text("# graphify-hook-start"))
    (hooks_dir / "post-checkout").write_text(_hook_text("# graphify-checkout-hook-start"))

    assert probe.hook_status(project) == "installed"


@pytest.mark.no_subprocess_block
def test_hook_status_maps_husky_wrapper_dir_to_parent(tmp_path):
    """Husky 9는 core.hooksPath=.husky/_ 이고 사용자 훅은 .husky/ 에 산다 (graphify #987)."""
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "core.hooksPath", ".husky/_")
    (project / ".husky" / "_").mkdir(parents=True)
    (project / ".husky" / "post-commit").write_text(_hook_text("# graphify-hook-start"))
    (project / ".husky" / "post-checkout").write_text(
        _hook_text("# graphify-checkout-hook-start")
    )

    assert probe.hook_status(project) == "installed"


@pytest.mark.no_subprocess_block
def test_hook_status_rejects_hooks_without_worktree_guard(tmp_path):
    """0.9.14 이전 훅은 linked worktree 안에서도 그래프를 재빌드하므로 outdated."""
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    hooks_dir = project / ".git" / "hooks"
    (hooks_dir / "post-commit").write_text(_hook_text("# graphify-hook-start", guard=False))
    (hooks_dir / "post-checkout").write_text(
        _hook_text("# graphify-checkout-hook-start", guard=False)
    )

    assert probe.hook_status(project) == "missing"


@pytest.mark.no_subprocess_block
def test_hook_status_ignores_guard_outside_graphify_block(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    hooks_dir = project / ".git" / "hooks"
    outside = "git rev-parse --git-common-dir >/dev/null\n"
    (hooks_dir / "post-commit").write_text(
        "#!/bin/sh\n" + outside + _hook_text("# graphify-hook-start", guard=False)[len("#!/bin/sh\n"):]
    )
    (hooks_dir / "post-checkout").write_text(
        "#!/bin/sh\n" + outside
        + _hook_text("# graphify-checkout-hook-start", guard=False)[len("#!/bin/sh\n"):]
    )

    assert probe.hook_status(project) == "missing"


@pytest.mark.no_subprocess_block
def test_hook_status_resolves_linked_worktree_common_dir(tmp_path):
    repository = tmp_path / "repository"
    worktree = tmp_path / "worktree"
    repository.mkdir()
    _git(repository, "init")
    _git(
        repository, "-c", "user.name=t", "-c", "user.email=t@example.com",
        "commit", "--allow-empty", "-m", "initial",
    )
    _git(repository, "worktree", "add", "--detach", str(worktree))
    hooks_dir = repository / ".git" / "hooks"
    (hooks_dir / "post-commit").write_text(_hook_text("# graphify-hook-start"))
    (hooks_dir / "post-checkout").write_text(_hook_text("# graphify-checkout-hook-start"))

    assert probe.hook_status(worktree) == "installed"


@pytest.mark.no_subprocess_block
def test_hook_status_missing_when_interpreter_pin_dangles(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    hooks_dir = project / ".git" / "hooks"
    gone = f"{tmp_path}/gone/graphifyy/bin/python3"
    (hooks_dir / "post-commit").write_text(_hook_text("# graphify-hook-start", python_pin=gone))
    (hooks_dir / "post-checkout").write_text(
        _hook_text("# graphify-checkout-hook-start", python_pin=gone)
    )

    assert probe.hook_status(project) == "missing"


@pytest.mark.no_subprocess_block
def test_hook_status_missing_outside_git_repository(tmp_path):
    project = tmp_path / "plain"
    project.mkdir()

    assert probe.hook_status(project) == "missing"


@pytest.mark.no_subprocess_block
def test_hook_fingerprint_changes_with_hook_content(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    hooks_dir = project / ".git" / "hooks"
    (hooks_dir / "post-commit").write_text(_hook_text("# graphify-hook-start"))
    before = probe.hook_fingerprint(project)

    (hooks_dir / "post-commit").write_text(_hook_text("# graphify-hook-start", guard=False))

    assert probe.hook_fingerprint(project) != before


# ------------------------------------------------------------ other probes


def test_global_skill_status_honours_claude_config_dir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".codex" / "skills" / "graphify").mkdir(parents=True)
    config_dir = tmp_path / "claude-config"
    (config_dir / "skills" / "graphify").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    assert probe.global_skill_status("claude", home=home) == "installed"
    assert probe.global_skill_status("codex", home=home) == "installed"

    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert probe.global_skill_status("claude", home=home) == "missing"


def test_graph_status_requires_graph_json(tmp_path):
    assert probe.graph_status(tmp_path) == "missing"
    (tmp_path / "graphify-out").mkdir()
    (tmp_path / "graphify-out" / "graph.json").write_text("{}")
    assert probe.graph_status(tmp_path) == "built"


def test_cli_status_follows_resolved_command():
    assert probe.cli_status(command=lambda: ["/usr/local/bin/graphify"]) == "installed"
    assert probe.cli_status(command=lambda: None) == "missing"


@pytest.mark.no_subprocess_block
def test_populate_environ_fills_only_missing_keys(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    environ = {"SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS": "built"}
    statuses = probe.probe(project, "claude", home=tmp_path, command=lambda: None)

    probe.populate_environ(statuses, environ)

    assert environ["SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS"] == "built"
    assert environ["SERENA_AGENT_PREFLIGHT_GRAPHIFY_CLI_STATUS"] == "missing"
    assert environ["SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS"] == "missing"
    assert environ["SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS"] == "missing"
    assert environ["SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS"] == "missing"
