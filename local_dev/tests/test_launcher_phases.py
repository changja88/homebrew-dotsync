import io
import os
import re
import shutil
import time
from unittest import mock

import pytest

from local_dev.serena_mcp_management import serena_agent_launcher as launcher
from local_dev.serena_mcp_management.node_preflight import NodeNeed
from local_dev.serena_mcp_management.serena_mcp.diagnostics import GlobalLifecycleSnapshot

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", s)


@pytest.fixture(autouse=True)
def stub_external_cli_resolution(monkeypatch):
    """이 파일의 phase 테스트는 CLI가 해석되는 머신을 기본으로 모델링한다.

    해석/설치 동작 자체를 검증하는 테스트는 개별 monkeypatch로 이 기본값을
    덮어쓴다. (autouse가 없으면 테스트 결과가 실행 머신의 serena/graphify
    설치 여부에 따라 달라진다.)
    """
    monkeypatch.setattr(launcher, "serena_server_command",
                        lambda: ["serena"], raising=False)
    monkeypatch.setattr(launcher, "graphify_command",
                        lambda: ["graphify"], raising=False)
    monkeypatch.setattr(
        launcher, "serena_install_command",
        lambda: ["/stub/uv", "tool", "install", "--from",
                 "git+https://github.com/oraios/serena", "serena-agent"],
        raising=False)
    monkeypatch.setattr(
        launcher, "graphify_install_command",
        lambda: ["/stub/uv", "tool", "install", "graphifyy"],
        raising=False)
    # Default to a machine that needs no node runtime, so the node-runtime
    # preflight step is inert unless a test opts in. (Without this, the check
    # would read the real ~/.claude / ~/.codex and fire on machines lacking
    # node, breaking unrelated phase tests.)
    monkeypatch.setattr(
        launcher, "_client_node_need",
        lambda client: NodeNeed(generic=False, homebrew=False), raising=False)


def test_main_always_dispatches_to_v2(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    called = {}

    def fake_v2(args):
        called["v2"] = args
        return 0

    monkeypatch.setattr(launcher, "_main_v2", fake_v2, raising=False)
    rc = launcher.main(["--help"])
    assert rc == 0
    assert called["v2"] == ["--help"]


def test_main_dispatches_to_v2_regardless_of_tui_env(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    called = {}

    def fake_v2(args):
        called["v2"] = args
        return 0

    monkeypatch.setattr(launcher, "_main_v2", fake_v2, raising=False)
    rc = launcher.main([])
    assert rc == 0
    assert called["v2"] == []


def _set_graphify_env(monkeypatch, *, global_="installed", graph="built",
                       integration="installed", hook="installed"):
    """Helper: set the four graphify preflight env vars in one call."""
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS", global_)
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS", graph)
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", integration)
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", hook)


def _stub_preflight_inventory(
    monkeypatch,
    *,
    client="codex",
    sessions_total=174,
    sessions_to_delete=92,
    sessions_to_keep=82,
    memory_total=3,
    memory_to_reset=3,
    memory_to_keep=0,
    criteria="sessions: same cwd + older than 3d . memory: reset all",
):
    from pathlib import Path

    from local_dev.serena_mcp_management.session_inventory import (
        AgentInventory,
        CountStats,
    )

    monkeypatch.setattr(
        launcher,
        "scan_inventory",
        lambda **kwargs: AgentInventory(
            client=client,
            sessions=CountStats(
                total=sessions_total,
                to_delete=sessions_to_delete,
                to_keep=sessions_to_keep,
            ),
            memory=CountStats(
                total=memory_total,
                to_reset=memory_to_reset,
                to_keep=memory_to_keep,
            ),
            criteria=criteria,
            sessions_dir=Path(f"/tmp/{client}/sessions"),
            memory_dir=Path(f"/tmp/{client}/memories"),
            session_delete_paths=[],
            memory_reset=memory_to_reset > 0,
        ),
        raising=False,
    )


@pytest.fixture(autouse=True)
def _stub_global_mcp_snapshot(monkeypatch):
    monkeypatch.setattr(
        launcher,
        "snapshot_global_lifecycle",
        lambda **kwargs: GlobalLifecycleSnapshot(
            ps_server_count=0,
            managed_server_count=0,
            orphan_server_count=0,
            lease_count=0,
            stale_lease_count=0,
        ),
        raising=False,
    )


def test_v2_preflight_marks_all_graphify_rows_warn_when_env_missing(monkeypatch):
    """When the shim does not export status env vars, the launcher must not
    silently render ✓. Treating "no info" as "installed" hides real issues —
    e.g. running codex outside any project showed all four rows green even
    though graphify-out/, AGENTS.md, and .codex/hooks.json did not exist.
    """
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    for var in (
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS",
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS",
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS",
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS",
    ):
        monkeypatch.delenv(var, raising=False)

    box = launcher._preflight_box()
    rows = {item.id: item for item in box.items}
    assert rows["graphify-global"].status == "warn"
    assert rows["graphify-graph"].status == "warn"
    assert rows["graphify-integration"].status == "warn"
    assert rows["graphify-hook"].status == "warn"


def test_v2_preflight_renders_box_with_sessions_and_serena(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    _stub_preflight_inventory(
        monkeypatch,
        sessions_total=103,
        sessions_to_delete=0,
        sessions_to_keep=103,
        memory_total=0,
        memory_to_reset=0,
    )

    out = io.StringIO()
    # Everything installed -> no prompts should fire from preflight.
    answers = iter([])

    # The box itself is now rendered upstream by _render_preflight_overview_v2;
    # we exercise it here to keep the integration assertions meaningful.
    launcher._render_preflight_overview_v2(stream=out)
    rc = launcher._run_preflight_v2(stream=out, input_fn=lambda: next(answers))
    text = out.getvalue()
    plain = _strip_ansi(text)
    assert "codex 103 total . 0 to delete . 103 to keep" in plain
    assert "codex 0 total . 0 to reset . 0 to keep" in plain
    assert "preflight" in text
    assert "codex" in text
    # All four graphify rows render with their distinct labels.
    assert "graphify global" in plain
    assert "graphify graph" in plain
    assert "graphify integration" in plain
    assert "graphify hook" in plain
    # The hook row reveals which git hooks are installed, not just "initialized".
    assert "post-commit" in plain
    assert "post-checkout" in plain
    assert rc == 0  # preflight no longer aborts; final 'Run codex?' moved out


def test_v2_preflight_renders_box_with_sessions_memory_and_criteria(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    _stub_preflight_inventory(monkeypatch)

    out = io.StringIO()
    launcher._render_preflight_overview_v2(stream=out)
    plain = _strip_ansi(out.getvalue())

    assert "sessions" in plain
    assert "codex 174 total . 92 to delete . 82 to keep" in plain
    assert "memory" in plain
    assert "codex 3 total . 3 to reset . 0 to keep" in plain
    assert "criteria" in plain
    assert "sessions: same cwd + older than 3d . memory: reset all" in plain
    assert "cleanup" not in plain


def test_v2_preflight_uses_real_codex_inventory_for_current_context(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    codex_home = tmp_path / "codex-home"
    home.mkdir()
    repo.mkdir()
    old = codex_home / "sessions" / "2026" / "05" / "01" / "old.jsonl"
    new = codex_home / "sessions" / "2026" / "05" / "10" / "new.jsonl"
    other = codex_home / "sessions" / "2026" / "05" / "10" / "other.jsonl"
    old.parent.mkdir(parents=True)
    new.parent.mkdir(parents=True, exist_ok=True)
    old.write_text(f'{{"type":"session_meta","payload":{{"cwd":"{repo}"}}}}\n')
    new.write_text(f'{{"type":"session_meta","payload":{{"cwd":"{repo}"}}}}\n')
    other.write_text('{"type":"session_meta","payload":{"cwd":"/other"}}\n')
    old_time = time.time() - 4 * 86400
    os.utime(old, (old_time, old_time))
    os.utime(other, (old_time, old_time))
    memory_dir = codex_home / "memories"
    memory_dir.mkdir()
    (memory_dir / "a.md").write_text("a")
    (memory_dir / "b.md").write_text("b")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(repo))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.setattr(launcher.os, "getcwd", lambda: str(repo))
    _set_graphify_env(monkeypatch)

    out = io.StringIO()
    launcher._render_preflight_overview_v2(stream=out)
    plain = _strip_ansi(out.getvalue())

    assert "codex 2 total . 1 to delete . 1 to keep" in plain
    assert "codex 2 total . 2 to reset . 0 to keep" in plain
    assert "sessions: same cwd + older than 3d . memory: reset all" in plain


def test_v2_preflight_inventory_scan_failure_renders_warning_row(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)

    def fail_scan(**kwargs):
        raise RuntimeError("inventory unavailable")

    monkeypatch.setattr(launcher, "scan_inventory", fail_scan, raising=False)

    box = launcher._preflight_box()
    rows = {item.id: item for item in box.items}

    assert rows["sessions"].status == "warn"
    assert "scan unavailable: inventory unavailable" in rows["sessions"].value
    assert "codex 0 total . 0 to reset . 0 to keep" in _strip_ansi(
        rows["memory"].value
    )
    assert _strip_ansi(rows["criteria"].value) == "scan unavailable"


def test_v2_preflight_returns_zero_on_run_confirm(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, global_="missing")

    out = io.StringIO()
    # Decline the global install prompt. Preflight no longer asks "Run codex?".
    answers = iter(["n"])
    rc = launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_global=lambda client: 0,
    )
    assert rc == 0


def test_v2_preflight_marks_graphify_hook_missing(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, hook="missing")

    out = io.StringIO()
    # Decline the hook install prompt. Preflight no longer asks "Run codex?".
    answers = iter(["n"])
    launcher._render_preflight_overview_v2(stream=out)
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        is_git_repo=lambda project_root: True,
        install_graphify_hooks=lambda project_root: 0,
    )
    text = _strip_ansi(out.getvalue())
    assert "hooks not installed" in text
    assert "graphify hook install" in text


def test_v2_preflight_runs_graphify_hook_install_when_user_confirms(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, hook="missing")

    install_calls: list = []

    def fake_install(project_root):
        install_calls.append(project_root)
        return 0

    out = io.StringIO()
    answers = iter(["y"])  # accept hook install; preflight no longer gates on run
    rc = launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        is_git_repo=lambda project_root: True,
        install_graphify_hooks=fake_install,
    )
    text = _strip_ansi(out.getvalue())
    assert install_calls, "graphify hook install should have been invoked"
    assert "Install graphify hooks" in text
    # After successful install, the hook row flips to the done variant.
    assert "post-commit + post-checkout hooks installed" in text
    assert rc == 0  # preflight always returns 0; abort moved to _run_final_confirm_v2


def test_v2_preflight_offers_git_init_when_hook_step_in_non_git_repo(monkeypatch):
    """graphify hook은 git post-commit/post-checkout 훅이라 git repo가 전제다.
    프로젝트가 git repo가 아니면 hook 단계에서 `git init`을 한 줄 동의로 제안하고,
    수락하면 init → hook install 순으로 진행해야 한다 (수동 git init 요구 없이)."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, hook="missing")

    init_calls: list = []
    hook_calls: list = []

    out = io.StringIO()
    answers = iter(["y"])  # accept git init (which also implies hook install)
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        is_git_repo=lambda project_root: False,
        init_git=lambda project_root: init_calls.append(project_root) or 0,
        install_graphify_hooks=lambda project_root: hook_calls.append(project_root) or 0,
    )
    text = _strip_ansi(out.getvalue())
    assert "need a git repo" in text
    assert init_calls, "git init should run when the project is not a git repo"
    assert hook_calls, "hook install should run after git init succeeds"
    assert "initialized empty repo" in text
    assert "post-commit + post-checkout hooks installed" in text


def test_v2_preflight_skips_hooks_when_git_init_declined(monkeypatch):
    """git repo가 아닌데 사용자가 `git init`을 거절하면 init도 hook install도
    하지 않고, 사유(git init 필요)를 한 줄로 알려야 한다."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, hook="missing")

    init_calls: list = []
    hook_calls: list = []

    out = io.StringIO()
    answers = iter(["n"])  # decline git init
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        is_git_repo=lambda project_root: False,
        init_git=lambda project_root: init_calls.append(project_root) or 0,
        install_graphify_hooks=lambda project_root: hook_calls.append(project_root) or 0,
    )
    text = _strip_ansi(out.getvalue())
    assert init_calls == [], "git init must not run when declined"
    assert hook_calls == [], "hook install must not run without a git repo"
    assert "git repo" in text
    assert "git init" in text


def test_v2_preflight_skips_hooks_when_git_init_fails(monkeypatch):
    """`git init` 자체가 실패하면 hook install을 시도하지 않고 실패를 surface한다."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, hook="missing")

    hook_calls: list = []

    out = io.StringIO()
    answers = iter(["y"])  # accept git init, but it fails
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        is_git_repo=lambda project_root: False,
        init_git=lambda project_root: 1,
        install_graphify_hooks=lambda project_root: hook_calls.append(project_root) or 0,
    )
    text = _strip_ansi(out.getvalue())
    assert hook_calls == [], "hook install must not run after git init fails"
    assert "git init failed" in text


def test_is_git_repo_and_git_init_roundtrip(tmp_path):
    """_is_git_repo는 init 전 False, _git_init 후 True여야 한다 (실제 git 사용)."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    assert launcher._is_git_repo(tmp_path) is False
    assert launcher._git_init(tmp_path) == 0
    assert launcher._is_git_repo(tmp_path) is True


def test_is_git_repo_false_inside_git_dir(tmp_path):
    """B2: `.git` 내부는 work tree가 아니다. `git rev-parse --is-inside-work-tree`는
    거기서 'false'를 찍으면서도 exit 0이라, exit code만 보면 오탐한다."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    launcher._git_init(tmp_path)
    assert launcher._is_git_repo(tmp_path) is True
    assert launcher._is_git_repo(tmp_path / ".git") is False


def _node_resolver(*values):
    """A resolve_node stub returning each value in turn (last value sticks).

    Used to model node being missing on the first probe (triggering the prompt)
    and present on the post-install probe (reporting success)."""
    it = iter(values)
    state = {"last": None}

    def resolve():
        try:
            state["last"] = next(it)
        except StopIteration:
            pass
        return state["last"]

    return resolve


def test_v2_preflight_offers_node_install_when_required_and_missing(monkeypatch):
    """node를 쓰는 플러그인/MCP가 있는데 node가 없으면 `brew install node`를
    동의 프롬프트로 제안하고, 수락하면 설치 후 성공을 surface해야 한다."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)  # graphify fully installed -> no graphify prompts

    install_calls: list = []

    out = io.StringIO()
    answers = iter(["y"])  # accept node install
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        node_need_check=lambda client: NodeNeed(generic=True, homebrew=False),
        resolve_node=_node_resolver(None, ["/opt/homebrew/bin/node"]),
        homebrew_node_present=lambda: True,
        install_node=lambda *, stream=None: install_calls.append(1) or 0,
    )
    text = _strip_ansi(out.getvalue())
    assert install_calls, "brew install node should run"
    assert "brew install node" in text
    assert "installed at /opt/homebrew/bin/node" in text


def test_v2_preflight_offers_node_install_for_unmet_homebrew_statusline(monkeypatch):
    """PATH에 node가 있어도(generic 충족) statusLine이 hardcode한
    /opt/homebrew/bin/node가 없으면(homebrew need 미충족) 설치를 제안해야 한다 (F2)."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)

    install_calls: list = []
    out = io.StringIO()
    answers = iter(["y"])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        node_need_check=lambda client: NodeNeed(generic=False, homebrew=True),
        resolve_node=lambda: ["/Users/x/.nvm/node"],  # PATH node exists...
        homebrew_node_present=_node_resolver(False, True),  # ...but homebrew path missing then present
        install_node=lambda *, stream=None: install_calls.append(1) or 0,
    )
    text = _strip_ansi(out.getvalue())
    assert install_calls, "should offer install when only the homebrew need is unmet"
    assert "installed at /opt/homebrew/bin/node" in text


def test_v2_preflight_skips_node_when_path_node_satisfies_generic_need(monkeypatch):
    """generic need(npx MCP)만 있고 PATH에 node가 있으면 묻지 않는다."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)

    install_calls: list = []
    out = io.StringIO()
    answers = iter([])  # no prompt expected
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        node_need_check=lambda client: NodeNeed(generic=True, homebrew=False),
        resolve_node=lambda: ["/usr/bin/node"],
        homebrew_node_present=lambda: False,  # irrelevant: no homebrew need
        install_node=lambda *, stream=None: install_calls.append(1) or 0,
    )
    assert install_calls == []
    assert "brew install node" not in out.getvalue()


def test_v2_preflight_skips_node_when_not_required(monkeypatch):
    """node를 쓰는 플러그인/MCP가 없으면 node가 없어도 묻지 않는다."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)

    install_calls: list = []
    out = io.StringIO()
    answers = iter([])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        node_need_check=lambda client: NodeNeed(generic=False, homebrew=False),
        resolve_node=lambda: None,
        homebrew_node_present=lambda: False,
        install_node=lambda *, stream=None: install_calls.append(1) or 0,
    )
    assert install_calls == []
    assert "node runtime" not in out.getvalue()


def test_v2_preflight_warns_when_node_install_declined(monkeypatch):
    """node 설치를 거절하면 설치하지 않고 사유를 한 줄로 알린다."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)

    install_calls: list = []
    out = io.StringIO()
    answers = iter(["n"])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        node_need_check=lambda client: NodeNeed(generic=True, homebrew=False),
        resolve_node=lambda: None,
        homebrew_node_present=lambda: False,
        install_node=lambda *, stream=None: install_calls.append(1) or 0,
    )
    text = _strip_ansi(out.getvalue())
    assert install_calls == []
    assert "node-based plugins/MCP will not start" in text


def test_v2_preflight_runs_node_check_even_when_graphify_unavailable(monkeypatch):
    """B1: graphify CLI를 해석 못 해 graphify 단계가 조기 종료돼도 node 체크는
    독립적으로 실행돼야 한다 (node와 graphify는 무관)."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, global_="missing")  # triggers graphify-missing branch
    # graphify entirely unresolvable -> cli install offer returns "unavailable",
    # which makes _run_preflight_v2 early-return from the graphify section.
    monkeypatch.setattr(launcher, "graphify_command", lambda: None, raising=False)
    monkeypatch.setattr(launcher, "graphify_install_command", lambda: None, raising=False)
    monkeypatch.setattr(
        launcher, "node_install_command",
        lambda: ["/stub/brew", "install", "node"], raising=False)

    install_calls: list = []
    out = io.StringIO()
    answers = iter(["y"])  # accept node install
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        node_need_check=lambda client: NodeNeed(generic=True, homebrew=False),
        resolve_node=_node_resolver(None, ["/opt/homebrew/bin/node"]),
        homebrew_node_present=lambda: True,
        install_node=lambda *, stream=None: install_calls.append(1) or 0,
    )
    text = _strip_ansi(out.getvalue())
    assert "cli unavailable" in text, "graphify section should have early-returned"
    assert install_calls, "node check must still run despite graphify early-return"
    assert "installed at /opt/homebrew/bin/node" in text


def test_v2_preflight_does_not_prompt_node_when_brew_missing(monkeypatch):
    """brew가 없으면 설치할 수 없으니 묻지 않고 수동 설치 안내만 남긴다 (F1).

    serena/graphify가 'uv not found'일 때 프롬프트를 띄우지 않는 것과 동일한 흐름."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    # brew unresolvable -> install argv is None
    monkeypatch.setattr(launcher, "node_install_command", lambda: None, raising=False)

    install_calls: list = []
    out = io.StringIO()
    answers = iter([])  # a prompt firing would raise StopIteration
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        node_need_check=lambda client: NodeNeed(generic=True, homebrew=False),
        resolve_node=lambda: None,
        homebrew_node_present=lambda: False,
        install_node=lambda *, stream=None: install_calls.append(1) or 0,
    )
    text = _strip_ansi(out.getvalue())
    assert install_calls == [], "must not install when brew is unavailable"
    assert "brew install node" not in text, "must not prompt when it can't install"
    assert "brew not found" in text


def test_v2_preflight_skips_graphify_hook_prompt_when_already_installed(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)

    install_calls: list = []

    def fake_install(project_root):
        install_calls.append(project_root)
        return 0

    out = io.StringIO()
    # Everything installed -> no prompts should fire from preflight.
    answers = iter([])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_hooks=fake_install,
    )
    assert install_calls == []
    assert "Install graphify hooks" not in out.getvalue()


def test_v2_preflight_graphify_global_missing_claude_offers_auto_install(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, global_="missing")

    install_calls: list = []

    def fake_install(client):
        install_calls.append(client)
        return 0

    out = io.StringIO()
    answers = iter(["y"])  # accept global install; preflight has no run prompt
    rc = launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_global=fake_install,
    )
    text = _strip_ansi(out.getvalue())
    assert install_calls == ["claude"]
    assert "graphify install" in text
    assert "user skill at ~/.claude/skills/graphify" in text  # post-install row
    assert rc == 0


def test_v2_preflight_graphify_global_missing_codex_uses_platform_codex(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, global_="missing")

    install_calls: list = []

    def fake_install(client):
        install_calls.append(client)
        return 0

    out = io.StringIO()
    answers = iter(["y"])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_global=fake_install,
    )
    text = _strip_ansi(out.getvalue())
    assert install_calls == ["codex"]
    assert "graphify install --platform codex" in text
    assert "user skill at ~/.codex/skills/graphify" in text


def test_v2_preflight_graphify_graph_missing_shows_hint_no_callback(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, graph="missing")

    out = io.StringIO()
    # graph row is informational (no install callback). All other rows installed.
    answers = iter([])
    launcher._render_preflight_overview_v2(stream=out)
    launcher._run_preflight_v2(stream=out, input_fn=lambda: next(answers))
    text = _strip_ansi(out.getvalue())
    assert "no graph" in text
    assert "/graphify ." in text


def test_v2_preflight_graphify_integration_missing_claude_offers_install(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, integration="missing")

    install_calls: list = []

    def fake_install(project_root, client):
        install_calls.append((project_root, client))
        return 0

    out = io.StringIO()
    answers = iter(["y"])  # accept integration install
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_integration=fake_install,
    )
    text = _strip_ansi(out.getvalue())
    assert len(install_calls) == 1
    project_root, client = install_calls[0]
    assert client == "claude"
    assert str(project_root).endswith("/repo")
    assert "graphify claude install" in text
    assert "CLAUDE.md + .claude/settings.json registered" in text


def test_v2_preflight_graphify_integration_missing_codex_uses_codex_subcommand(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, integration="missing")

    install_calls: list = []

    def fake_install(project_root, client):
        install_calls.append(client)
        return 0

    out = io.StringIO()
    answers = iter(["y"])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_integration=fake_install,
    )
    text = _strip_ansi(out.getvalue())
    assert install_calls == ["codex"]
    assert "graphify codex install" in text
    assert "AGENTS.md + .codex/hooks.json registered" in text


def test_v2_preflight_graphify_global_install_failure_marks_warn(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, global_="missing")

    out = io.StringIO()
    answers = iter(["y"])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_global=lambda client: 7,
    )
    text = _strip_ansi(out.getvalue())
    assert "global install failed (exit 7)" in text


def test_v2_preflight_does_not_redraw_box_after_install(monkeypatch):
    """install 성공/실패 직후의 결과는 inline 한 줄로만 surface해야 한다 — 박스를
    통째로 다시 그리면 (a) 화면 위쪽의 원래 overview 박스가 밀려 올라가고
    (b) ASCII art 배너가 'Run <client>?' 직전에 또 한 번 깜빡인다.
    실제 사용자 보고된 증상: 한 세션에서 박스가 두 번 그려졌다.

    이 테스트는 _run_preflight_v2가 BoxRenderer를 안 쓰고 inline 출력만 한다는 걸
    배너 art (██████)와 박스 테두리 (── 60자)가 안 나타나는 것으로 검증한다.
    """
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, integration="missing", hook="missing")

    out = io.StringIO()
    answers = iter(["y", "y"])  # accept integration, accept hooks
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        is_git_repo=lambda project_root: True,
        install_graphify_integration=lambda project_root, client: 0,
        install_graphify_hooks=lambda project_root: 0,
    )
    text = _strip_ansi(out.getvalue())
    # No banner art (claude block font) should appear — that only belongs to
    # the box rendered upstream by _render_preflight_overview_v2.
    assert "██████" not in text, (
        "preflight must surface install results inline, not by redrawing "
        "the entire box (which flashes the banner art again)"
    )
    # No long box border either.
    assert "─" * 60 not in text
    # And the success messages must still surface as inline status lines.
    assert "CLAUDE.md + .claude/settings.json registered" in text
    assert "post-commit + post-checkout hooks installed" in text


def test_v2_preflight_marks_serena_warn_when_missing(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    _set_graphify_env(monkeypatch)

    out = io.StringIO()
    # Serena row is rendered by the overview; preflight only handles graphify
    # prompts and stays silent when graphify env is clean.
    answers = iter([])
    launcher._render_preflight_overview_v2(stream=out)
    launcher._run_preflight_v2(stream=out, input_fn=lambda: next(answers))
    assert "project config missing" in out.getvalue()


def test_v2_serena_init_skip_returns_skipped_status(monkeypatch, tmp_path):
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    out = io.StringIO()
    answers = iter(["n"])  # Skip
    result = launcher._run_serena_init_v2(stream=out, input_fn=lambda: next(answers))
    assert result == "skipped"
    assert "Initialize" in out.getvalue()


def test_v2_serena_init_no_op_when_serena_present(monkeypatch, tmp_path):
    (tmp_path / ".serena").mkdir()
    (tmp_path / ".serena" / "project.yml").write_text("project: test\n")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    out = io.StringIO()
    result = launcher._run_serena_init_v2(stream=out, input_fn=lambda: pytest.fail("no input"))
    assert result == "managed"
    assert out.getvalue() == ""


def test_v2_serena_init_create_calls_serena_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")

    captured = {}

    def fake_create(project_root):
        captured["root"] = project_root
        # simulate Serena writing project.yml
        (project_root / ".serena").mkdir(exist_ok=True)
        (project_root / ".serena" / "project.yml").write_text("ok\n")
        return 0

    monkeypatch.setattr(launcher, "_serena_project_create", fake_create, raising=False)
    out = io.StringIO()
    answers = iter(["y"])
    result = launcher._run_serena_init_v2(stream=out, input_fn=lambda: next(answers))
    assert result == "created"
    assert captured["root"] == tmp_path


def test_v2_serena_init_create_failure_returns_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    monkeypatch.setattr(launcher, "_serena_project_create",
                        lambda project_root: 1, raising=False)
    out = io.StringIO()
    answers = iter(["y"])
    result = launcher._run_serena_init_v2(stream=out, input_fn=lambda: next(answers))
    assert result == "failed"


def _make_old_file(path):
    """Write a file and set its mtime to 4 days ago."""
    if not path.exists():
        path.write_text("x")
    old = time.time() - 4 * 86400
    os.utime(path, (old, old))


def test_v2_run_cleanup_claude_deletes_old_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setattr(launcher.os, "getcwd", lambda: "/repo")
    proj_dir = tmp_path / ".claude" / "projects" / "-repo"
    proj_dir.mkdir(parents=True)
    old = proj_dir / "abc.jsonl"
    _make_old_file(old)
    fresh = proj_dir / "fresh.jsonl"
    fresh.write_text("x")
    mem = proj_dir / "memory"
    mem.mkdir()
    (mem / "m1.txt").write_text("x")

    result = launcher._run_cleanup_claude()
    assert result.deleted == 1
    assert result.memory_files_reset == 1
    assert not old.exists()
    assert fresh.exists()
    assert not mem.exists()


def test_v2_run_cleanup_codex_does_not_require_jq(tmp_path):
    codex_home = tmp_path / ".codex"
    old = codex_home / "sessions" / "2026" / "05" / "01" / "rollout-old.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text('{"type":"session_meta","payload":{"cwd":"/repo"}}\n')
    _make_old_file(old)
    mem = codex_home / "memories"
    mem.mkdir()
    (mem / "m.txt").write_text("x")

    result = launcher._run_cleanup_codex(codex_home, "/repo")
    assert result.deleted == 1
    assert result.memory_files_reset == 1
    assert not old.exists()
    assert not mem.exists()


def test_v2_run_cleanup_codex_uses_default_home_when_codex_home_empty(tmp_path, monkeypatch):
    # 이 스위트는 shim launcher가 띄운 agent 세션 안에서도 돈다 — 거기서는
    # SERENA_AGENT_CLIENT=claude가 누출되므로 codex 분기를 명시적으로 고정한다.
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", "")
    monkeypatch.setattr(launcher.os, "getcwd", lambda: "/repo")
    default_home = tmp_path / ".codex"
    old = default_home / "sessions" / "2026" / "05" / "01" / "rollout-old.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text('{"type":"session_meta","payload":{"cwd":"/repo"}}\n')
    _make_old_file(old)
    mem = default_home / "memories"
    mem.mkdir()
    (mem / "m.txt").write_text("x")

    out = io.StringIO()
    summary = launcher._run_launch_prep_v2(stream=out)

    assert summary.cleanup_deleted == 1
    assert summary.cleanup_memory_files_reset == 1
    assert not old.exists()
    assert not mem.exists()


def test_v2_run_cleanup_codex_expands_tilde_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", "~/.codex")
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setattr(launcher.os, "getcwd", lambda: "/repo")
    codex_home = tmp_path / ".codex"
    old = codex_home / "sessions" / "2026" / "05" / "01" / "rollout-old.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text('{"type":"session_meta","payload":{"cwd":"/repo"}}\n')
    _make_old_file(old)
    mem = codex_home / "memories"
    mem.mkdir()
    (mem / "m.txt").write_text("x")

    summary = launcher._run_launch_prep_v2(stream=io.StringIO())

    assert summary.cleanup_deleted == 1
    assert summary.cleanup_memory_files_reset == 1
    assert not old.exists()
    assert not mem.exists()


def test_v2_launch_prep_claude_ignores_relative_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", "relative-codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setattr(launcher.os, "getcwd", lambda: "/repo")
    session_dir = tmp_path / ".claude" / "projects" / "-repo"
    session_dir.mkdir(parents=True)
    old = session_dir / "abc.jsonl"
    _make_old_file(old)
    mem = session_dir / "memory"
    mem.mkdir()
    (mem / "m.txt").write_text("x")

    summary = launcher._run_launch_prep_v2(stream=io.StringIO())

    assert summary.cleanup_deleted == 1
    assert summary.cleanup_memory_files_reset == 1
    assert not old.exists()
    assert not mem.exists()


def test_v2_launch_prep_rejects_relative_codex_home_before_cleanup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("CODEX_HOME", "relative-codex")
    mem = tmp_path / "relative-codex" / "memories"
    mem.mkdir(parents=True)
    (mem / "m.txt").write_text("x")

    with pytest.raises(ValueError, match="codex_home must be absolute"):
        launcher._run_launch_prep_v2(stream=io.StringIO())

    assert mem.exists()


def test_v2_launch_prep_rejects_unknown_client_before_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "bad-client")
    monkeypatch.setenv("HOME", str(tmp_path))
    codex_home = tmp_path / ".codex"
    mem = codex_home / "memories"
    mem.mkdir(parents=True)
    (mem / "m.txt").write_text("x")

    with pytest.raises(RuntimeError, match="unsupported launcher name"):
        launcher._run_launch_prep_v2(stream=io.StringIO())

    assert mem.exists()


def test_v2_launch_prep_runs_cleanup_and_renders_done_row(tmp_path, monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setattr(launcher.os, "getcwd", lambda: "/x")
    proj_dir = tmp_path / ".claude" / "projects" / "-x"
    proj_dir.mkdir(parents=True)

    out = io.StringIO()
    summary = launcher._run_launch_prep_v2(stream=out)
    text = out.getvalue()
    assert "cleanup" in text
    assert "0 sessions deleted . 0 memory files reset" in text
    assert summary.cleanup_deleted == 0
    assert summary.cleanup_memory_files_reset == 0


def test_v2_start_mcp_with_spinner_returns_record_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))

    fake_record = mock.Mock()
    fake_record.mcp_url = "http://127.0.0.1:9999/mcp"
    fake_record.dashboard_url = "http://127.0.0.1:9999/"
    monkeypatch.setattr(launcher, "ensure_server",
                        lambda scope, lease: fake_record, raising=False)

    out = io.StringIO()
    record = launcher._start_mcp_with_spinner(
        scope=mock.Mock(),
        lease=mock.Mock(),
        stream=out,
    )
    assert record is fake_record
    text = out.getvalue()
    assert "http://127.0.0.1:9999/mcp" in text


def test_v2_start_mcp_with_spinner_raises_on_failure(monkeypatch):
    def boom(scope, lease):
        raise RuntimeError("server unhealthy")
    monkeypatch.setattr(launcher, "ensure_server", boom, raising=False)

    out = io.StringIO()
    with pytest.raises(RuntimeError, match="server unhealthy"):
        launcher._start_mcp_with_spinner(scope=mock.Mock(), lease=mock.Mock(),
                                         stream=out)
    text = out.getvalue()
    assert "server unhealthy" in text or "preparing" in text


def test_v2_render_summary_box_includes_duration_and_cleanup():
    out = io.StringIO()
    summary = launcher._render_summary_v2(
        stream=out,
        client="codex",
        duration_seconds=125.0,
        cleanup_deleted=2,
        cleanup_memory_files_reset=10,
        mcp_lifecycle="stopped",
        warnings=[],
    )
    assert summary is None  # writes to stream, no return
    text = out.getvalue()
    assert "summary" in text
    assert "2m 5s" in _strip_ansi(text) or "125" in _strip_ansi(text)
    assert "2 sessions deleted" in _strip_ansi(text)
    assert "10 memory files reset" in _strip_ansi(text)
    assert "stopped" in text


def test_v2_render_summary_includes_warnings():
    out = io.StringIO()
    launcher._render_summary_v2(
        stream=out,
        client="claude",
        duration_seconds=10.0,
        cleanup_deleted=0,
        cleanup_memory_files_reset=0,
        mcp_lifecycle="kept",
        warnings=["serena project create skipped"],
    )
    assert "serena project create skipped" in out.getvalue()


def test_v2_shutdown_with_spinner_outputs_progress_then_done(monkeypatch):
    monkeypatch.delenv("SERENA_AGENT_INTERACTIVE", raising=False)

    fake_stats = mock.Mock(
        sessions_before=1, sessions_closed=1, sessions_remaining=0,
        server_was_running=True, server_stopped=True,
    )

    out = io.StringIO()
    stats = launcher._stop_mcp_with_spinner(
        scope=mock.Mock(),
        lease_id="lease-1",
        stream=out,
        shutdown_fn=lambda scope, lease_id: fake_stats,
    )
    assert stats is fake_stats
    text = out.getvalue()
    # spinner emitted "stopping" line, replaced with done line on completion
    assert "stopping" in text
    assert "stopped" in text or "done" in text


def test_v2_shutdown_with_spinner_propagates_exception(monkeypatch):
    monkeypatch.delenv("SERENA_AGENT_INTERACTIVE", raising=False)

    def boom(scope, lease_id):
        raise RuntimeError("shutdown failed")

    out = io.StringIO()
    with pytest.raises(RuntimeError, match="shutdown failed"):
        launcher._stop_mcp_with_spinner(
            scope=mock.Mock(),
            lease_id="lease-1",
            stream=out,
            shutdown_fn=boom,
        )
    text = out.getvalue()
    assert "stopping" in text or "shutdown failed" in text


def test_v2_main_returns_child_exit_code(monkeypatch, tmp_path):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "0")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)

    fake_record = mock.Mock()
    fake_record.mcp_url = "http://127.0.0.1:0/mcp"
    fake_record.dashboard_url = ""
    monkeypatch.setattr(launcher, "ensure_server",
                        lambda scope, lease: fake_record, raising=False)
    monkeypatch.setattr(launcher, "find_real_binary",
                        lambda client: "/usr/bin/true", raising=False)
    monkeypatch.setattr(launcher, "_remove_lease_and_shutdown_if_empty",
                        lambda scope, lease_id: mock.Mock(
                            sessions_before=1, sessions_closed=1, sessions_remaining=0,
                            server_was_running=True, server_stopped=True),
                        raising=False)

    rc = launcher._main_v2([])
    assert rc == 0


def test_v2_preflight_skips_hook_prompt_when_integration_declined(monkeypatch):
    """integration_status가 missing이고 사용자가 그 install을 거절하면
    hook 질문은 나오지 않아야 한다. hooks는 graphify가 프로젝트에 wire-up된
    뒤에야 의미가 있으므로, integration 없이 hooks만 묻는 건 잘못된 흐름이다.
    """
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, integration="missing", hook="missing")

    integration_calls: list = []
    hook_calls: list = []

    def fake_integration(project_root, client):
        integration_calls.append(client)
        return 0

    def fake_hook(project_root):
        hook_calls.append(project_root)
        return 0

    out = io.StringIO()
    # Only one prompt is expected: the integration install (declined).
    # If a hook prompt fires, this iter exhausts and StopIteration surfaces.
    answers = iter(["n"])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_integration=fake_integration,
        install_graphify_hooks=fake_hook,
    )
    text = _strip_ansi(out.getvalue())
    assert integration_calls == []
    assert hook_calls == []
    assert "Install graphify hooks" not in text


def test_v2_preflight_skips_hook_prompt_when_integration_install_fails(monkeypatch):
    """integration install이 실패하면 hook 질문을 묻지 않는다 — integration이
    실제로 설치되지 않은 상태에서 hook을 깔아도 의미가 없다.
    """
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, integration="missing", hook="missing")

    hook_calls: list = []

    out = io.StringIO()
    # Accept integration install (fails with rc=7), then no hook prompt should fire.
    answers = iter(["y"])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_integration=lambda project_root, client: 7,
        install_graphify_hooks=lambda project_root: hook_calls.append(project_root) or 0,
    )
    text = _strip_ansi(out.getvalue())
    assert hook_calls == []
    assert "integration install failed" in text
    assert "Install graphify hooks" not in text


def test_v2_preflight_asks_hook_after_successful_integration_install(monkeypatch):
    """integration install이 성공하면 hook 질문은 정상적으로 이어서 묻는다."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, integration="missing", hook="missing")

    hook_calls: list = []

    out = io.StringIO()
    # Accept integration (succeeds), accept hooks too.
    answers = iter(["y", "y"])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        is_git_repo=lambda project_root: True,
        install_graphify_integration=lambda project_root, client: 0,
        install_graphify_hooks=lambda project_root: hook_calls.append(project_root) or 0,
    )
    text = _strip_ansi(out.getvalue())
    assert len(hook_calls) == 1
    assert "Install graphify hooks" in text


def test_v2_preflight_no_longer_asks_run_codex(monkeypatch):
    """preflight 단계에서는 더 이상 'Run codex?'를 묻지 않는다 — 그 게이트는
    setup 질문들 + serena init이 모두 끝난 뒤 final-confirm으로 옮겨졌다.
    """
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)  # everything installed -> no install prompts

    out = io.StringIO()
    # If preflight tries to ask anything, this iter exhausts.
    answers = iter([])
    rc = launcher._run_preflight_v2(stream=out, input_fn=lambda: next(answers))
    assert rc == 0
    assert "Run codex?" not in out.getvalue()
    assert "Run claude?" not in out.getvalue()


def test_v2_final_confirm_yes_returns_true(monkeypatch):
    """_run_final_confirm_v2는 'Run <client>?' 한 줄만 묻고 True/False를 반환한다."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")

    out = io.StringIO()
    answers = iter(["y"])
    result = launcher._run_final_confirm_v2(stream=out, input_fn=lambda: next(answers))
    assert result is True
    assert "Run codex?" in out.getvalue()


def test_v2_final_confirm_no_returns_false(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")

    out = io.StringIO()
    answers = iter(["n"])
    result = launcher._run_final_confirm_v2(stream=out, input_fn=lambda: next(answers))
    assert result is False
    assert "Run claude?" in out.getvalue()


def test_v2_final_confirm_skips_when_non_interactive(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "0")

    out = io.StringIO()
    # No prompt should be issued when interactive mode is off.
    result = launcher._run_final_confirm_v2(
        stream=out, input_fn=lambda: pytest.fail("no input should be requested")
    )
    assert result is True
    assert out.getvalue() == ""


def test_v2_main_orders_overview_then_serena_then_setup_then_final_confirm(
    monkeypatch, tmp_path
):
    """전체 흐름의 순서를 검증한다 — 박스 overview가 가장 먼저:
        0) preflight 박스 렌더 (status overview)
        1) serena init 질문 (가장 중요한 질문)
        2) preflight (graphify 질문들)
        3) final 'Run codex?' 게이트
    final 게이트에서 No하면 130을 반환하고 child agent는 실행되지 않는다.
    """
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    _set_graphify_env(monkeypatch)  # graphify clean -> no graphify prompts

    call_log: list = []

    def fake_overview(*, stream=None):
        call_log.append("render_overview")

    def fake_preflight(*, stream=None, input_fn=None,
                       serena_state="managed",
                       install_graphify_global=None,
                       install_graphify_integration=None,
                       install_graphify_hooks=None):
        call_log.append("preflight")
        return 0

    def fake_serena_init(*, stream=None, input_fn=None):
        call_log.append("serena_init")
        return "skipped"

    def fake_final_confirm(*, stream=None, input_fn=None):
        call_log.append("final_confirm")
        return False  # decline -> abort

    def boom(*args, **kwargs):
        pytest.fail("agent must not launch when final confirm is declined")

    import subprocess as _subprocess
    monkeypatch.setattr(launcher, "_render_preflight_overview_v2", fake_overview,
                        raising=False)
    monkeypatch.setattr(launcher, "_run_preflight_v2", fake_preflight, raising=False)
    monkeypatch.setattr(launcher, "_run_serena_init_v2", fake_serena_init, raising=False)
    monkeypatch.setattr(launcher, "_run_final_confirm_v2", fake_final_confirm,
                        raising=False)
    monkeypatch.setattr(launcher, "find_real_binary", boom, raising=False)
    monkeypatch.setattr(launcher, "ensure_server", boom, raising=False)
    monkeypatch.setattr(_subprocess, "run",
                        lambda *a, **k: pytest.fail("subprocess.run should not run"))

    rc = launcher._main_v2([])
    assert rc == 130
    assert call_log == ["render_overview", "serena_init", "preflight", "final_confirm"]


def test_v2_render_preflight_overview_draws_box_with_all_rows(monkeypatch):
    """preflight overview는 box 렌더만 담당한다 — 어떤 prompt도 띄우지 않고
    sessions/memory/criteria/serena/graphify/context 행을 모두 한 번 그린다.
    """
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    _stub_preflight_inventory(
        monkeypatch,
        sessions_total=103,
        sessions_to_delete=0,
        sessions_to_keep=103,
        memory_total=0,
        memory_to_reset=0,
    )

    out = io.StringIO()
    launcher._render_preflight_overview_v2(stream=out)
    text = out.getvalue()
    plain = _strip_ansi(text)
    assert "codex 103 total . 0 to delete . 103 to keep" in plain
    assert "codex 0 total . 0 to reset . 0 to keep" in plain
    assert "sessions: same cwd + older than 3d . memory: reset all" in plain
    assert "preflight" in text
    assert "codex" in text
    assert "graphify global" in plain
    assert "graphify graph" in plain
    assert "graphify integration" in plain
    assert "graphify hook" in plain


def test_preflight_box_includes_global_serena_mcp_inventory(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    monkeypatch.setattr(
        launcher,
        "snapshot_global_lifecycle",
        lambda **kwargs: GlobalLifecycleSnapshot(
            ps_server_count=3,
            managed_server_count=2,
            orphan_server_count=1,
            lease_count=3,
            stale_lease_count=1,
        ),
        raising=False,
    )

    box = launcher._preflight_box()

    ids = [item.id for item in box.items]
    assert ids[ids.index("serena") + 1] == "serena-mcp"
    item = box.items[ids.index("serena-mcp")]
    assert item.label == "serena mcp"
    assert item.status == "warn"
    assert _strip_ansi(item.value) == (
        "ps[3 servers] -> managed[2 servers] . "
        "orphan[1] . leases[3] . stale[1]"
    )


def test_preflight_box_marks_global_serena_mcp_idle_as_info(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    monkeypatch.setattr(
        launcher,
        "snapshot_global_lifecycle",
        lambda **kwargs: GlobalLifecycleSnapshot(
            ps_server_count=0,
            managed_server_count=0,
            orphan_server_count=0,
            lease_count=0,
            stale_lease_count=0,
        ),
        raising=False,
    )

    item = next(item for item in launcher._preflight_box().items if item.id == "serena-mcp")

    assert item.status == "info"
    assert _strip_ansi(item.value) == (
        "ps[0 servers] -> managed[0 servers] . "
        "orphan[0] . leases[0] . stale[0]"
    )


def test_preflight_box_marks_global_serena_mcp_scan_failure_as_warn(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    monkeypatch.setattr(
        launcher,
        "snapshot_global_lifecycle",
        lambda **kwargs: GlobalLifecycleSnapshot(
            ps_server_count=0,
            managed_server_count=0,
            orphan_server_count=0,
            lease_count=0,
            stale_lease_count=0,
            scan_failed=True,
        ),
        raising=False,
    )

    item = next(item for item in launcher._preflight_box().items if item.id == "serena-mcp")

    assert item.status == "warn"
    assert item.value == "scan unavailable"


def test_preflight_box_marks_global_serena_mcp_clean_running_as_done(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    monkeypatch.setattr(
        launcher,
        "snapshot_global_lifecycle",
        lambda **kwargs: GlobalLifecycleSnapshot(
            ps_server_count=2,
            managed_server_count=2,
            orphan_server_count=0,
            lease_count=3,
            stale_lease_count=0,
        ),
        raising=False,
    )

    item = next(item for item in launcher._preflight_box().items if item.id == "serena-mcp")

    assert item.status == "done"


def test_v2_render_preflight_overview_skips_when_non_interactive(monkeypatch):
    """interactive=0이면 overview는 아무 것도 안 그린다."""
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "0")

    out = io.StringIO()
    launcher._render_preflight_overview_v2(stream=out)
    assert out.getvalue() == ""


def test_v2_run_preflight_v2_does_not_render_box_initially(monkeypatch):
    """초기 박스 렌더는 _render_preflight_overview_v2가 책임지므로,
    _run_preflight_v2는 prompt가 없을 때(이미 모두 installed) 어떤 출력도 만들지 않는다.
    """
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)

    out = io.StringIO()
    rc = launcher._run_preflight_v2(stream=out, input_fn=lambda: pytest.fail("no prompt"))
    assert rc == 0
    # Without an install change there's nothing for preflight to draw.
    assert out.getvalue() == ""


def test_v2_serena_init_create_promotes_env_status_to_managed(monkeypatch, tmp_path):
    """serena_init이 새로 .serena/project.yml을 만들면 (created), 다음 단계인
    preflight 박스가 fresh 상태를 찍을 수 있도록 env 상태를 'managed'로 승격한다.
    """
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")

    def fake_create(project_root):
        (project_root / ".serena").mkdir(exist_ok=True)
        (project_root / ".serena" / "project.yml").write_text("ok\n")
        return 0

    monkeypatch.setattr(launcher, "_serena_project_create", fake_create, raising=False)
    out = io.StringIO()
    answers = iter(["y"])
    result = launcher._run_serena_init_v2(stream=out, input_fn=lambda: next(answers))
    assert result == "created"
    assert os.environ.get("SERENA_AGENT_PREFLIGHT_SERENA_STATUS") == "managed"


def test_v2_main_clears_terminal_before_child_when_serena_skipped(monkeypatch, tmp_path):
    """serena init이 skipped/failed라 early-return으로 child를 띄울 때도
    SERENA_AGENT_CLEAR_BEFORE_CHILD=1이면 화면을 청소해야 한다.
    그렇지 않으면 codex가 preflight 출력 아래에 그대로 이어붙어 가독성을 망친다.
    """
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_CLEAR_BEFORE_CHILD", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    _set_graphify_env(monkeypatch)

    monkeypatch.setattr(launcher, "_run_preflight_v2",
                        lambda **kw: 0, raising=False)
    monkeypatch.setattr(launcher, "_run_serena_init_v2",
                        lambda **kw: "skipped", raising=False)
    monkeypatch.setattr(launcher, "_run_final_confirm_v2",
                        lambda **kw: True, raising=False)
    monkeypatch.setattr(launcher, "find_real_binary",
                        lambda client: "/usr/bin/true", raising=False)

    cleared: list = []
    monkeypatch.setattr(launcher, "clear_terminal_before_child",
                        lambda: cleared.append(True), raising=False)

    run_calls: list = []

    class _Result:
        returncode = 0

    def fake_run(cmd, *a, **k):
        run_calls.append(("run", tuple(cleared)))
        return _Result()

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)

    rc = launcher._main_v2([])
    assert rc == 0
    # The clear must fire BEFORE subprocess.run is invoked.
    assert run_calls == [("run", (True,))]


# --- Dynamic prompt-default rules -------------------------------------------
#
# Rule (per user request):
#   1. Serena init prompt          -> default=No  (only shown when missing)
#   2. graphify global install     -> default=No  (only shown when missing)
#   3. graphify integration install-> default=Yes only if Serena is initialized
#                                     AND graphify global is installed (either
#                                     was already, or just got installed in
#                                     this session); otherwise default=No
#   4. graphify hook install       -> default=Yes (always)
#   5. final "Run <client>?"       -> default=Yes (always)
#
# Defaults are observable two ways: (a) the [Y/n]/[y/N] suffix written by
# confirm() and (b) what happens when the user submits an empty line.


def test_v2_serena_init_prompt_defaults_to_no(monkeypatch, tmp_path):
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    out = io.StringIO()
    answers = iter([""])  # bare Enter
    result = launcher._run_serena_init_v2(stream=out, input_fn=lambda: next(answers))
    assert result == "skipped"
    assert "[y/N]" in out.getvalue()


def test_v2_preflight_graphify_global_prompt_defaults_to_no(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, global_="missing")

    install_calls: list = []
    out = io.StringIO()
    answers = iter([""])  # bare Enter
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_global=lambda client: install_calls.append(client) or 0,
    )
    assert install_calls == []
    assert "[y/N]" in out.getvalue()


def test_v2_preflight_graphify_integration_default_no_when_serena_skipped(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    _set_graphify_env(monkeypatch, integration="missing")  # global=installed

    integration_calls: list = []
    out = io.StringIO()
    answers = iter([""])  # bare Enter on integration prompt
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        serena_state="skipped",
        install_graphify_integration=lambda root, client:
            integration_calls.append(client) or 0,
    )
    assert integration_calls == []
    assert "[y/N]" in out.getvalue()


def test_v2_preflight_graphify_integration_default_no_when_global_declined(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, global_="missing", integration="missing")

    integration_calls: list = []
    out = io.StringIO()
    # Decline global ("n"), then bare Enter on integration prompt.
    answers = iter(["n", ""])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        serena_state="managed",
        install_graphify_global=lambda client: 0,
        install_graphify_integration=lambda root, client:
            integration_calls.append(client) or 0,
    )
    assert integration_calls == []


def test_v2_preflight_graphify_integration_default_yes_when_serena_and_global_done(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, integration="missing")  # global=installed

    integration_calls: list = []
    out = io.StringIO()
    answers = iter([""])  # bare Enter -> should accept (Yes default)
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        serena_state="managed",
        install_graphify_integration=lambda root, client:
            integration_calls.append(client) or 0,
    )
    assert integration_calls == ["claude"]
    assert "[Y/n]" in out.getvalue()


def test_v2_preflight_graphify_integration_default_yes_after_just_installing_global(monkeypatch):
    """If user accepts global install in the same flow, integration treats
    global as 'done' and defaults to Yes."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, global_="missing", integration="missing")

    integration_calls: list = []
    out = io.StringIO()
    # Accept global ("y"), bare Enter on integration -> should accept (Yes default)
    answers = iter(["y", ""])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        serena_state="managed",
        install_graphify_global=lambda client: 0,
        install_graphify_integration=lambda root, client:
            integration_calls.append(client) or 0,
    )
    assert integration_calls == ["claude"]


def test_v2_preflight_graphify_hook_prompt_defaults_to_yes(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, hook="missing")  # integration=installed

    hook_calls: list = []
    out = io.StringIO()
    answers = iter([""])  # bare Enter
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        is_git_repo=lambda project_root: True,
        install_graphify_hooks=lambda root: hook_calls.append(root) or 0,
    )
    assert len(hook_calls) == 1
    assert "[Y/n]" in out.getvalue()


def test_v2_final_confirm_defaults_to_yes(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    out = io.StringIO()
    answers = iter([""])  # bare Enter
    result = launcher._run_final_confirm_v2(stream=out, input_fn=lambda: next(answers))
    assert result is True
    assert "[Y/n]" in out.getvalue()


def test_v2_main_passes_serena_state_to_preflight(monkeypatch, tmp_path):
    """_main_v2 must forward the result of _run_serena_init_v2 to
    _run_preflight_v2 so the integration prompt's default reflects whether
    Serena is initialized."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    _set_graphify_env(monkeypatch)

    captured: dict = {}

    def fake_preflight(**kwargs):
        captured["serena_state"] = kwargs.get("serena_state", "<missing>")
        return 0

    monkeypatch.setattr(launcher, "_render_preflight_overview_v2",
                        lambda *, stream=None: None, raising=False)
    monkeypatch.setattr(launcher, "_run_preflight_v2", fake_preflight, raising=False)
    monkeypatch.setattr(launcher, "_run_serena_init_v2",
                        lambda *, stream=None, input_fn=None: "created", raising=False)
    monkeypatch.setattr(launcher, "_run_final_confirm_v2",
                        lambda *, stream=None, input_fn=None: False, raising=False)

    rc = launcher._main_v2([])
    assert rc == 130  # final confirm declined
    assert captured["serena_state"] == "created"


# --- External CLI resolution for prompt actions ------------------------------
#
# serena/graphify는 PATH에 없을 수 있다 (serena는 uvx로만 돌고, graphify는
# uv tool bin인 ~/.local/bin에 산다 — 둘 다 interactive PATH 밖). 프롬프트의
# Yes 액션은 bare `which` 대신 external_cli resolver가 돌려준 argv를 그대로
# 실행해야 한다. 그렇지 않으면 Yes가 조용히 exit 2로 끝난다.


def test_serena_project_create_runs_resolved_command(monkeypatch, tmp_path):
    monkeypatch.setattr(
        launcher, "serena_oneshot_command",
        lambda: ["/opt/homebrew/bin/uvx", "--from", "spec", "serena"],
        raising=False,
    )

    class FakeYesProc:
        stdout = None

        def terminate(self):
            pass

        def wait(self):
            pass

    monkeypatch.setattr(launcher.subprocess, "Popen",
                        lambda cmd, stdout=None: FakeYesProc())

    run_calls = []

    class _Result:
        returncode = 0

    def fake_run(cmd, stdin=None, check=False):
        run_calls.append(cmd)
        return _Result()

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)

    rc = launcher._serena_project_create(tmp_path)
    assert rc == 0
    assert run_calls == [
        ["/opt/homebrew/bin/uvx", "--from", "spec", "serena",
         "project", "create", str(tmp_path)]
    ]


def test_serena_project_create_returns_2_when_cli_unresolvable(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "serena_oneshot_command",
                        lambda: None, raising=False)
    monkeypatch.setattr(
        launcher.subprocess, "run",
        lambda *a, **k: pytest.fail("must not spawn anything"),
    )
    assert launcher._serena_project_create(tmp_path) == 2


def test_graphify_install_actions_run_resolved_command(monkeypatch, tmp_path):
    monkeypatch.setattr(
        launcher, "graphify_command",
        lambda: ["/u/.local/bin/graphify"], raising=False,
    )
    run_calls = []

    class _Result:
        returncode = 0

    def fake_run(cmd, cwd=None, check=False):
        run_calls.append((cmd, cwd))
        return _Result()

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)

    assert launcher._graphify_global_install("codex") == 0
    assert launcher._graphify_integration_install(tmp_path, "codex") == 0
    assert launcher._graphify_hook_install(tmp_path) == 0
    assert run_calls == [
        (["/u/.local/bin/graphify", "install", "--platform", "codex"], None),
        (["/u/.local/bin/graphify", "codex", "install"], str(tmp_path)),
        (["/u/.local/bin/graphify", "hook", "install"], str(tmp_path)),
    ]


def test_graphify_install_actions_return_2_when_cli_unresolvable(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "graphify_command", lambda: None, raising=False)
    monkeypatch.setattr(
        launcher.subprocess, "run",
        lambda *a, **k: pytest.fail("must not spawn anything"),
    )
    assert launcher._graphify_global_install("codex") == 2
    assert launcher._graphify_integration_install(tmp_path, "codex") == 2
    assert launcher._graphify_hook_install(tmp_path) == 2


def test_v2_main_degrades_to_bare_launch_when_serena_cli_missing(
    monkeypatch, tmp_path, capsys
):
    """project.yml이 있어도(managed) serena CLI 자체를 못 찾으면 scoped server를
    띄울 수 없다 — traceback 대신 경고 한 줄을 남기고 bare child로 강등한다.
    skipped/failed 경로와 동일하게 cleanup(launch prep)도 건너뛴다."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.delenv("SERENA_AGENT_CLEAR_BEFORE_CHILD", raising=False)
    _set_graphify_env(monkeypatch)

    monkeypatch.setattr(launcher, "_render_preflight_overview_v2",
                        lambda *, stream=None: None, raising=False)
    monkeypatch.setattr(launcher, "_run_serena_cli_install_v2",
                        lambda **kw: "declined", raising=False)
    monkeypatch.setattr(launcher, "_run_preflight_v2",
                        lambda **kw: 0, raising=False)
    monkeypatch.setattr(launcher, "_run_final_confirm_v2",
                        lambda **kw: True, raising=False)
    monkeypatch.setattr(launcher, "serena_server_command",
                        lambda: None, raising=False)
    monkeypatch.setattr(launcher, "find_real_binary",
                        lambda client: "/usr/bin/true", raising=False)
    monkeypatch.setattr(launcher, "_run_launch_prep_v2",
                        lambda **kw: pytest.fail("cleanup must not run when degrading"),
                        raising=False)
    monkeypatch.setattr(launcher, "_start_mcp_with_spinner",
                        lambda **kw: pytest.fail("scoped server must not start"),
                        raising=False)
    monkeypatch.setattr(launcher, "ensure_server",
                        lambda *a, **k: pytest.fail("scoped server must not start"),
                        raising=False)

    run_calls = []

    class _Result:
        returncode = 0

    def fake_run(cmd, *a, **k):
        run_calls.append(cmd)
        return _Result()

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)

    rc = launcher._main_v2([])
    assert rc == 0
    assert run_calls == [["/usr/bin/true"]]
    out = _strip_ansi(capsys.readouterr().out)
    assert "serena CLI" in out


# --- CLI self-install prompts -------------------------------------------------
#
# serena/graphify CLI가 머신에 없으면 preflight 질문 단계에서 설치 여부를 묻고,
# Yes면 uv tool로 설치한다. 이미 해석되는 머신에서는 질문 자체가 나타나지 않아
# 기존 동작이 바뀌지 않는다 (no side effects).


def test_serena_cli_install_phase_silent_when_resolvable(monkeypatch):
    monkeypatch.setattr(launcher, "serena_server_command",
                        lambda: ["serena"], raising=False)
    out = io.StringIO()
    state = launcher._run_serena_cli_install_v2(
        stream=out, input_fn=lambda: pytest.fail("no prompt expected"))
    assert state == "present"
    assert out.getvalue() == ""


def test_serena_cli_install_phase_installs_on_yes(monkeypatch):
    resolution = [None]
    monkeypatch.setattr(launcher, "serena_server_command",
                        lambda: resolution[0], raising=False)

    def fake_install():
        resolution[0] = ["serena"]
        return 0

    out = io.StringIO()
    answers = iter(["y"])
    state = launcher._run_serena_cli_install_v2(
        stream=out, input_fn=lambda: next(answers), install_fn=fake_install)
    assert state == "installed"
    text = _strip_ansi(out.getvalue())
    # 프롬프트가 실행할 실제 명령을 보여주고, 성공을 인라인 행으로 알린다.
    assert "uv tool install --from git+https://github.com/oraios/serena serena-agent" in text
    assert "serena cli" in text


def test_serena_cli_install_phase_declines_without_installing(monkeypatch):
    monkeypatch.setattr(launcher, "serena_server_command",
                        lambda: None, raising=False)
    out = io.StringIO()
    answers = iter(["n"])
    state = launcher._run_serena_cli_install_v2(
        stream=out, input_fn=lambda: next(answers),
        install_fn=lambda: pytest.fail("must not install on decline"))
    assert state == "declined"


def test_serena_cli_install_phase_reports_failure(monkeypatch):
    monkeypatch.setattr(launcher, "serena_server_command",
                        lambda: None, raising=False)
    out = io.StringIO()
    answers = iter(["y"])
    state = launcher._run_serena_cli_install_v2(
        stream=out, input_fn=lambda: next(answers), install_fn=lambda: 1)
    assert state == "failed"
    assert "install failed" in _strip_ansi(out.getvalue())


def test_serena_cli_install_phase_fails_when_binary_still_missing(monkeypatch):
    # uv가 0을 돌려줘도 binary가 해석되지 않으면 성공으로 치지 않는다.
    monkeypatch.setattr(launcher, "serena_server_command",
                        lambda: None, raising=False)
    out = io.StringIO()
    answers = iter(["y"])
    state = launcher._run_serena_cli_install_v2(
        stream=out, input_fn=lambda: next(answers), install_fn=lambda: 0)
    assert state == "failed"


def test_serena_cli_install_phase_warns_without_uv(monkeypatch):
    monkeypatch.setattr(launcher, "serena_server_command",
                        lambda: None, raising=False)
    monkeypatch.setattr(launcher, "serena_install_command",
                        lambda: None, raising=False)
    out = io.StringIO()
    state = launcher._run_serena_cli_install_v2(
        stream=out, input_fn=lambda: pytest.fail("no prompt without uv"))
    assert state == "unavailable"
    assert "uv" in _strip_ansi(out.getvalue())


# --- CLI install output streaming ----------------------------------------------
#
# uv tool install의 패키지 벽 출력은 숨긴다: 설치 중에는 spinner 행 하나에
# 마지막 의미 있는 줄(패키지 1개)만 갱신해 보여주고, 실패했을 때만 캡처한
# 전체 출력을 들여쓰기 dump로 풀어 원인을 남긴다.


class _FakeInstallProc:
    def __init__(self, lines, returncode):
        self.stdout = iter(lines)
        self._returncode = returncode

    def wait(self):
        return self._returncode


def test_install_progress_value_maps_lines():
    assert launcher._install_progress_value(" + cffi==2.0.0\n") == "cffi==2.0.0"
    assert (launcher._install_progress_value("Resolved 74 packages in 20.57s\n")
            == "Resolved 74 packages in 20.57s")
    assert launcher._install_progress_value("   \n") is None


def test_install_progress_value_truncates_long_lines():
    value = launcher._install_progress_value("x" * 200)
    assert len(value) <= 58
    assert value.endswith("…")


def test_tool_install_streaming_hides_uv_output_on_success():
    lines = [
        "Resolved 74 packages in 20.57s\n",
        " + cffi==2.0.0\n",
        " + httpcore==1.0.9\n",
        "Installed 3 executables: serena, serena-agent, serena-hooks\n",
    ]
    out = io.StringIO()
    rc = launcher._run_tool_install_streaming(
        ["/stub/uv", "tool", "install", "serena-agent"],
        label="serena cli",
        stream=out,
        popen_fn=lambda cmd, **kw: _FakeInstallProc(lines, 0),
        tick_interval=999.0,
    )
    assert rc == 0
    text = _strip_ansi(out.getvalue())
    # 성공하면 uv 출력은 dump되지 않는다 (들여쓰기 dump 라인이 없어야 한다).
    assert "    + cffi==2.0.0" not in text
    # 진행 행에는 줄이 도착할 때마다 마지막 값 하나가 spinner 옆에 갱신된다.
    assert "cffi==2.0.0" in text


def test_tool_install_streaming_dumps_output_on_failure():
    lines = [
        "Resolved 2 packages in 1.00s\n",
        "error: Request failed after 3 retries\n",
    ]
    out = io.StringIO()
    rc = launcher._run_tool_install_streaming(
        ["/stub/uv", "tool", "install", "serena-agent"],
        label="serena cli",
        stream=out,
        popen_fn=lambda cmd, **kw: _FakeInstallProc(lines, 2),
        tick_interval=999.0,
    )
    assert rc == 2
    text = _strip_ansi(out.getvalue())
    # 실패 시 캡처한 uv 출력이 들여쓰기 dump로 그대로 보존된다.
    assert "    error: Request failed after 3 retries" in text
    assert "    Resolved 2 packages in 1.00s" in text


def test_serena_cli_install_streams_with_label(monkeypatch):
    calls = {}

    def fake_streaming(cmd, *, label, stream=None, **kw):
        calls.update(cmd=cmd, label=label, stream=stream)
        return 7

    monkeypatch.setattr(launcher, "_run_tool_install_streaming", fake_streaming)
    out = io.StringIO()
    rc = launcher._serena_cli_install(stream=out)
    assert rc == 7
    assert calls["label"] == "serena cli"
    assert calls["stream"] is out
    assert calls["cmd"][-1] == "serena-agent"


def test_graphify_cli_install_streams_with_label(monkeypatch):
    calls = {}

    def fake_streaming(cmd, *, label, stream=None, **kw):
        calls.update(cmd=cmd, label=label, stream=stream)
        return 7

    monkeypatch.setattr(launcher, "_run_tool_install_streaming", fake_streaming)
    out = io.StringIO()
    rc = launcher._graphify_cli_install(stream=out)
    assert rc == 7
    assert calls["label"] == "graphify cli"
    assert calls["stream"] is out
    assert calls["cmd"][-1] == "graphifyy"


def test_serena_cli_install_phase_passes_stream_to_default_installer(monkeypatch):
    resolution = [None]
    monkeypatch.setattr(launcher, "serena_server_command",
                        lambda: resolution[0], raising=False)
    seen = {}

    def fake_install(*, stream=None):
        seen["stream"] = stream
        resolution[0] = ["serena"]
        return 0

    monkeypatch.setattr(launcher, "_serena_cli_install", fake_install)
    out = io.StringIO()
    answers = iter(["y"])
    state = launcher._run_serena_cli_install_v2(
        stream=out, input_fn=lambda: next(answers))
    assert state == "installed"
    assert seen["stream"] is out


def test_graphify_cli_install_phase_passes_stream_to_default_installer(monkeypatch):
    resolution = [None]
    monkeypatch.setattr(launcher, "graphify_command",
                        lambda: resolution[0], raising=False)
    seen = {}

    def fake_install(*, stream=None):
        seen["stream"] = stream
        resolution[0] = ["graphify"]
        return 0

    monkeypatch.setattr(launcher, "_graphify_cli_install", fake_install)
    out = io.StringIO()
    answers = iter(["y"])
    state = launcher._run_graphify_cli_install_v2(
        stream=out, input_fn=lambda: next(answers))
    assert state == "installed"
    assert seen["stream"] is out


# --- install prompt wording -----------------------------------------------------
#
# 설치 제안 프롬프트는 "상태(없음) → 질문 → (명령어)" 순서로 읽힌다.
# 명령어가 문장 주어처럼 먼저 나오지 않는다.


def test_serena_cli_install_prompt_states_missing_before_command(monkeypatch):
    monkeypatch.setattr(launcher, "serena_server_command",
                        lambda: None, raising=False)
    out = io.StringIO()
    answers = iter(["n"])
    launcher._run_serena_cli_install_v2(stream=out, input_fn=lambda: next(answers))
    text = _strip_ansi(out.getvalue())
    assert "serena CLI is not installed" in text
    assert (text.index("serena CLI is not installed")
            < text.index("uv tool install --from"))


def test_graphify_cli_install_prompt_states_missing_before_command(monkeypatch):
    monkeypatch.setattr(launcher, "graphify_command",
                        lambda: None, raising=False)
    out = io.StringIO()
    answers = iter(["n"])
    launcher._run_graphify_cli_install_v2(stream=out, input_fn=lambda: next(answers))
    text = _strip_ansi(out.getvalue())
    assert "graphify CLI is not installed" in text
    assert (text.index("graphify CLI is not installed")
            < text.index("uv tool install graphifyy"))


def test_graphify_global_install_prompt_states_missing_before_command(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS", "missing")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS", "built")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "installed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "installed")
    out = io.StringIO()
    answers = iter(["n"])
    launcher._run_preflight_v2(stream=out, input_fn=lambda: next(answers))
    text = _strip_ansi(out.getvalue())
    assert "graphify global skill is not installed" in text
    assert (text.index("graphify global skill is not installed")
            < text.index("graphify install --platform codex"))


def test_graphify_integration_prompt_states_missing_before_command(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS", "installed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS", "built")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "missing")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "installed")
    out = io.StringIO()
    answers = iter(["n"])
    launcher._run_preflight_v2(stream=out, input_fn=lambda: next(answers))
    text = _strip_ansi(out.getvalue())
    assert "graphify is not wired into this project" in text
    assert (text.index("graphify is not wired into this project")
            < text.index("graphify codex install"))


def test_v2_main_runs_serena_cli_phase_before_init(monkeypatch, tmp_path):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    monkeypatch.delenv("SERENA_AGENT_CLEAR_BEFORE_CHILD", raising=False)
    _set_graphify_env(monkeypatch)

    order = []
    monkeypatch.setattr(launcher, "_render_preflight_overview_v2",
                        lambda *, stream=None: None, raising=False)
    monkeypatch.setattr(launcher, "_run_serena_cli_install_v2",
                        lambda **kw: order.append("cli") or "present",
                        raising=False)
    monkeypatch.setattr(launcher, "_run_serena_init_v2",
                        lambda **kw: order.append("init") or "skipped",
                        raising=False)
    monkeypatch.setattr(launcher, "_run_preflight_v2",
                        lambda **kw: 0, raising=False)
    monkeypatch.setattr(launcher, "_run_final_confirm_v2",
                        lambda **kw: True, raising=False)
    monkeypatch.setattr(launcher, "find_real_binary",
                        lambda client: "/usr/bin/true", raising=False)

    class _Result:
        returncode = 0

    monkeypatch.setattr(launcher.subprocess, "run", lambda cmd, *a, **k: _Result())

    rc = launcher._main_v2([])
    assert rc == 0
    assert order == ["cli", "init"]


def test_v2_preflight_offers_graphify_cli_install_before_actions(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS", "missing")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS", "built")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "installed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "installed")

    resolution = [None]
    monkeypatch.setattr(launcher, "graphify_command",
                        lambda: resolution[0], raising=False)

    def fake_cli_install():
        resolution[0] = ["graphify"]
        return 0

    global_calls = []
    out = io.StringIO()
    answers = iter(["y", "y"])  # CLI 설치 → global skill 설치
    rc = launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_cli=fake_cli_install,
        install_graphify_global=lambda client: global_calls.append(client) or 0,
    )
    assert rc == 0
    assert global_calls == ["codex"]
    text = _strip_ansi(out.getvalue())
    assert "uv tool install graphifyy" in text


def test_v2_preflight_skips_graphify_actions_when_cli_declined(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS", "missing")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS", "missing")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "missing")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "missing")
    monkeypatch.setattr(launcher, "graphify_command",
                        lambda: None, raising=False)

    out = io.StringIO()
    answers = iter(["n"])  # CLI 설치 거절 — 이후 어떤 graphify 질문도 없어야 한다
    rc = launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_global=lambda client: pytest.fail("must not run"),
        install_graphify_integration=lambda root, client: pytest.fail("must not run"),
        install_graphify_hooks=lambda root: pytest.fail("must not run"),
    )
    assert rc == 0
    assert "skipping graphify setup" in _strip_ansi(out.getvalue())


def test_v2_preflight_no_graphify_cli_prompt_when_nothing_missing(monkeypatch):
    # 모든 graphify 항목이 이미 갖춰져 있으면 CLI가 없어도 묻지 않는다 —
    # 제안할 설치 액션이 없는데 질문만 늘리는 것을 피한다.
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS", "installed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS", "built")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "installed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "installed")
    monkeypatch.setattr(launcher, "graphify_command",
                        lambda: None, raising=False)

    out = io.StringIO()
    rc = launcher._run_preflight_v2(
        stream=out, input_fn=lambda: pytest.fail("no prompts expected"))
    assert rc == 0
    assert "graphifyy" not in _strip_ansi(out.getvalue())


def test_cli_install_runners_use_resolved_uv_commands(monkeypatch):
    monkeypatch.setattr(
        launcher, "serena_install_command",
        lambda: ["/u/uv", "tool", "install", "--from", "spec", "serena-agent"],
        raising=False)
    monkeypatch.setattr(
        launcher, "graphify_install_command",
        lambda: ["/u/uv", "tool", "install", "graphifyy"],
        raising=False)
    popen_calls = []

    def fake_popen(cmd, **kwargs):
        popen_calls.append(cmd)
        return _FakeInstallProc([], 0)

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    assert launcher._serena_cli_install() == 0
    assert launcher._graphify_cli_install() == 0
    assert popen_calls == [
        ["/u/uv", "tool", "install", "--from", "spec", "serena-agent"],
        ["/u/uv", "tool", "install", "graphifyy"],
    ]


def test_cli_install_runners_return_2_without_uv(monkeypatch):
    monkeypatch.setattr(launcher, "serena_install_command",
                        lambda: None, raising=False)
    monkeypatch.setattr(launcher, "graphify_install_command",
                        lambda: None, raising=False)
    monkeypatch.setattr(launcher.subprocess, "run",
                        lambda *a, **k: pytest.fail("must not spawn anything"))
    assert launcher._serena_cli_install() == 2
    assert launcher._graphify_cli_install() == 2
