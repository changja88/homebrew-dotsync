import io
import json
import os
import re
import shutil
import time
from pathlib import Path
from unittest import mock

import pytest

from local_dev.serena_mcp_management import serena_agent_launcher as launcher
from local_dev.serena_mcp_management.node_preflight import NodeNeed
from local_dev.serena_mcp_management.serena_mcp.diagnostics import GlobalLifecycleSnapshot
from local_dev.serena_mcp_management.session_inventory import (
    AgentInventory,
    CountStats,
)

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


def test_main_turns_keyboard_interrupt_into_clean_cancel(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(launcher.sys, "stdout", out)

    def interrupt(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(launcher, "_main_v2", interrupt)

    try:
        rc = launcher.main([])
    except KeyboardInterrupt:
        rc = None

    visible = (
        _strip_ansi(out.getvalue())
        .replace("\r", "")
        .replace("\x1b[J", "")
    )
    assert rc == 130
    assert visible == "  ! cancelled\n"
    assert "Traceback" not in visible


def test_main_does_not_swallow_non_interrupt_exceptions(monkeypatch):
    def fail(args):
        raise RuntimeError("boom")

    monkeypatch.setattr(launcher, "_main_v2", fail)

    with pytest.raises(RuntimeError, match="boom"):
        launcher.main([])


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
    records_total=None,
    records_to_delete=None,
    records_to_keep=None,
    criteria="sessions: all known homes + inactive longer than 5d",
):
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
            criteria=criteria,
            records=CountStats(
                total=sessions_total if records_total is None else records_total,
                to_delete=(
                    sessions_to_delete
                    if records_to_delete is None
                    else records_to_delete
                ),
                to_keep=(
                    sessions_to_keep
                    if records_to_keep is None
                    else records_to_keep
                ),
            ),
        ),
        raising=False,
    )


def _stub_memory_inventory(
    monkeypatch,
    *,
    client="codex",
    stores=2,
    files=17,
    scope=None,
    warnings=(),
):
    from local_dev.serena_mcp_management.memory_management import (
        MemoryInventory,
        MemoryStore,
    )

    memory_inventory = MemoryInventory(
        client=client,
        stores=tuple(
            MemoryStore(
                path=Path(f"/tmp/test-{client}-memory-{index}"),
                source="test",
                file_count=0,
            )
            for index in range(stores)
        ),
        file_count=files,
        scope=scope or (
            "all known Codex homes"
            if client == "codex"
            else "all Claude project memory + custom store"
        ),
        warnings=warnings,
    )
    monkeypatch.setattr(
        launcher,
        "scan_memory_inventory",
        lambda **kwargs: memory_inventory,
        raising=False,
    )
    return memory_inventory


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
    )
    _stub_memory_inventory(monkeypatch, stores=0, files=0)

    out = io.StringIO()
    # Everything installed -> no prompts should fire from preflight.
    answers = iter([])

    # The box itself is now rendered upstream by _render_preflight_overview_v2;
    # we exercise it here to keep the integration assertions meaningful.
    launcher._render_preflight_overview_v2(stream=out)
    rc = launcher._run_preflight_v2(stream=out, input_fn=lambda: next(answers))
    text = out.getvalue()
    plain = _strip_ansi(text)
    assert "├─ groups   103 total · 0 to delete · 103 to keep" in plain
    assert "├─ records  103 total · 0 to delete · 103 to keep" in plain
    assert "└─ cleanup  inactive longer than 5 days" in plain
    assert "· memory      codex" in plain
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


def test_v2_preflight_renders_box_with_session_records_and_cleanup(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    _stub_preflight_inventory(
        monkeypatch,
        sessions_total=58,
        sessions_to_delete=35,
        sessions_to_keep=23,
        records_total=855,
        records_to_delete=358,
        records_to_keep=497,
    )
    _stub_memory_inventory(monkeypatch, stores=0, files=0)

    out = io.StringIO()
    launcher._render_preflight_overview_v2(stream=out)
    plain = _strip_ansi(out.getvalue())

    assert "sessions" in plain
    assert "├─ groups   58 total · 35 to delete · 23 to keep" in plain
    assert "├─ records  855 total · 358 to delete · 497 to keep" in plain
    assert "└─ cleanup  inactive longer than 5 days" in plain
    assert "· memory      codex" in plain
    assert "criteria" not in plain
    assert "retention" not in plain
    assert "cleanup" in plain
    box = launcher._preflight_box()
    assert "cleanup" not in {item.id for item in box.items}


def test_v2_preflight_labels_claude_candidates_as_native_cleanup(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    _stub_preflight_inventory(
        monkeypatch,
        client="claude",
        sessions_total=108,
        sessions_to_delete=74,
        sessions_to_keep=34,
        criteria="sessions: all projects + native retention 5d",
    )
    _stub_memory_inventory(monkeypatch, client="claude", stores=0, files=0)

    out = io.StringIO()
    launcher._render_preflight_overview_v2(stream=out)
    plain = _strip_ansi(out.getvalue())

    assert "├─ records  108 total · 74 to delete · 34 to keep" in plain
    assert (
        "└─ cleanup  inactive longer than 5 days · native Claude cleanup"
    ) in plain
    assert "criteria" not in plain
    assert "retention" not in plain
    assert "· memory      claude" in plain


def test_v2_preflight_uses_real_global_codex_logical_inventory(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    codex_home = tmp_path / "codex-home"
    home.mkdir()
    repo.mkdir()
    root_id = "00000000-0000-4000-8000-000000000001"
    child_id = "00000000-0000-4000-8000-000000000002"
    fresh_id = "00000000-0000-4000-8000-000000000003"
    root = codex_home / "sessions" / "2026" / "05" / "01" / "root.jsonl"
    child = codex_home / "sessions" / "2026" / "05" / "01" / "child.jsonl"
    fresh = codex_home / "sessions" / "2026" / "05" / "10" / "fresh.jsonl"
    root.parent.mkdir(parents=True)
    fresh.parent.mkdir(parents=True, exist_ok=True)
    root.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": root_id}}) + "\n"
    )
    child.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": child_id,
                    "source": {"parent_thread_id": root_id},
                },
            }
        )
        + "\n"
    )
    fresh.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": fresh_id}}) + "\n"
    )
    old_time = time.time() - 6 * 86400
    os.utime(root, (old_time, old_time))
    os.utime(child, (old_time, old_time))
    memory_dir = codex_home / "memories"
    memory_dir.mkdir()
    (memory_dir / "a.md").write_text("a")
    (memory_dir / "b.md").write_text("b")

    from local_dev.serena_mcp_management import session_inventory

    monkeypatch.setattr(
        session_inventory,
        "snapshot_open_rollouts",
        lambda session_dirs: frozenset(),
    )

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

    assert "├─ groups   2 total · 1 to delete · 1 to keep" in plain
    assert "├─ records  3 total · 2 to delete · 1 to keep" in plain
    assert "└─ cleanup  inactive longer than 5 days" in plain
    assert "· memory      codex" in plain
    assert "├─ stores   1 found" in plain
    assert "├─ files    2" in plain
    assert "criteria" not in plain
    assert (memory_dir / "a.md").exists()


def test_v2_preflight_inventory_scan_failure_renders_warning_row(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)

    def fail_scan(**kwargs):
        raise RuntimeError("inventory unavailable")

    monkeypatch.setattr(launcher, "scan_inventory", fail_scan, raising=False)
    memory_inventory = _stub_memory_inventory(monkeypatch, stores=0, files=0)

    box = launcher._preflight_box()
    rows = {item.id: item for item in box.items}

    assert rows["sessions"].status == "warn"
    assert "scan unavailable: inventory unavailable" in rows["sessions"].value
    assert rows["memory"].status == "info"
    assert rows["memory"].value == launcher.style_memory_tree(
        client=memory_inventory.client,
        stores=0,
        files=0,
        scope=memory_inventory.scope,
    )
    assert "cleanup" not in rows


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
    assert rc == 0  # preflight always returns 0; abort lives in the memory choice


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


def test_v2_preflight_node_success_reports_actual_resolved_path(monkeypatch):
    """②: 성공 행은 하드코딩된 /opt/homebrew/bin/node가 아니라 실제로 해석된
    node 경로를 보고해야 한다 (설치됐다고 거짓 보고하던 문제의 정직화)."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)

    out = io.StringIO()
    answers = iter(["y"])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        node_need_check=lambda client: NodeNeed(generic=True, homebrew=False),
        resolve_node=_node_resolver(None, ["/usr/local/bin/node"]),
        homebrew_node_present=lambda: True,
        install_node=lambda *, stream=None: 0,
    )
    text = _strip_ansi(out.getvalue())
    assert "installed at /usr/local/bin/node" in text
    assert "/opt/homebrew/bin/node" not in text  # must not hardcode


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
        return 0, ""

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
                        lambda project_root: (1, ""), raising=False)
    out = io.StringIO()
    answers = iter(["y"])
    result = launcher._run_serena_init_v2(stream=out, input_fn=lambda: next(answers))
    assert result == "failed"


def _inventory_snapshot(
    *, client="codex", total=10, to_delete=3, to_keep=7, error=None
):
    from local_dev.serena_mcp_management.session_inventory import (
        AgentInventory,
        CountStats,
    )

    if error is not None:
        return launcher.InventorySnapshot(inventory=None, error=error)
    criteria = (
        "sessions: all projects + native retention 5d"
        if client == "claude"
        else "sessions: all known homes + inactive longer than 5d"
    )
    return launcher.InventorySnapshot(
        inventory=AgentInventory(
            client=client,
            sessions=CountStats(total=total, to_delete=to_delete, to_keep=to_keep),
            criteria=criteria,
        )
    )


def _stub_isolated_main_preflight(monkeypatch, tmp_path):
    """Keep interactive main tests away from inherited user stores."""

    temp_home = tmp_path / "home"
    temp_home.mkdir()
    monkeypatch.setenv("HOME", str(temp_home))
    monkeypatch.setenv("CODEX_HOME", str(temp_home / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(temp_home / ".claude"))

    def fail_real_inventory(*args, **kwargs):
        pytest.fail("real preflight inventory must not run")

    monkeypatch.setattr(launcher, "_inventory_for_preflight", fail_real_inventory)
    monkeypatch.setattr(
        launcher,
        "_memory_inventory_for_preflight",
        fail_real_inventory,
    )
    snapshot = launcher.InventorySnapshot(
        inventory=None,
        error="synthetic session inventory",
        memory_inventory=None,
        memory_error="synthetic memory inventory",
    )
    monkeypatch.setattr(
        launcher,
        "_render_preflight_overview_v2",
        lambda **kwargs: snapshot,
    )
    return snapshot


def test_v2_launch_prep_claude_arms_native_cleanup_without_deleting_memory(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    memory = tmp_path / ".claude/projects/-repo/memory/MEMORY.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("keep")
    snapshot = _inventory_snapshot(
        client="claude", total=8, to_delete=5, to_keep=3
    )
    monkeypatch.setattr(
        launcher,
        "scan_inventory",
        lambda **kwargs: pytest.fail("default cleanup must reuse the snapshot"),
    )
    out = io.StringIO()

    summary = launcher._run_launch_prep_v2(
        snapshot=snapshot,
        real_binary="/fake/claude",
        stream=out,
    )

    assert summary.cleanup_deleted == 0
    assert summary.native_eligible == 5
    assert "native retention 5d . 5 eligible" in _strip_ansi(out.getvalue())
    assert "sessions" in _strip_ansi(out.getvalue())
    assert "cleanup" not in _strip_ansi(out.getvalue())
    assert f"\x1b[{launcher.YELLOW}m" in out.getvalue()
    assert memory.read_text() == "keep"


def test_v2_launch_prep_codex_uses_snapshot_and_official_cleanup(monkeypatch):
    from local_dev.serena_mcp_management.session_cleanup import CleanupResult

    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    snapshot = _inventory_snapshot()
    seen = []
    monkeypatch.setattr(
        launcher,
        "scan_inventory",
        lambda **kwargs: pytest.fail("default cleanup must reuse the snapshot"),
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_codex_inventory",
        lambda inventory, **kwargs: seen.append((inventory, kwargs["codex_binary"]))
        or CleanupResult(deleted=3, warnings=("one warning",)),
    )
    out = io.StringIO()

    summary = launcher._run_launch_prep_v2(
        snapshot=snapshot,
        real_binary="/fake/codex",
        stream=out,
    )

    assert seen == [(snapshot.inventory, "/fake/codex")]
    assert summary.cleanup_deleted == 3
    assert summary.warnings == ("one warning",)
    assert "3 sessions deleted" in _strip_ansi(out.getvalue())
    assert "sessions" in _strip_ansi(out.getvalue())
    assert "cleanup" not in _strip_ansi(out.getvalue())
    assert "memory" not in _strip_ansi(out.getvalue())
    assert f"\x1b[{launcher.YELLOW}m" in out.getvalue()


def test_v2_launch_prep_scan_failure_skips_cleanup(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setattr(
        launcher,
        "cleanup_codex_inventory",
        lambda *args, **kwargs: pytest.fail("cleanup must not run"),
    )

    summary = launcher._run_launch_prep_v2(
        snapshot=_inventory_snapshot(error="inventory unavailable"),
        real_binary="/fake/codex",
        stream=io.StringIO(),
    )

    assert summary.cleanup_deleted == 0
    assert summary.warnings == ("inventory unavailable",)


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


def test_v2_render_summary_box_includes_duration_and_full_session_cleanup():
    out = io.StringIO()
    summary = launcher._render_summary_v2(
        stream=out,
        client="codex",
        duration_seconds=125.0,
        cleanup_deleted=2,
        native_eligible=0,
        running_preserved=1,
        full_cleanup=True,
        mcp_lifecycle="stopped",
        warnings=[],
    )
    assert summary is None  # writes to stream, no return
    text = out.getvalue()
    assert "summary" in text
    assert "2m 5s" in _strip_ansi(text) or "125" in _strip_ansi(text)
    assert "2 sessions deleted · 1 running preserved" in _strip_ansi(text)
    assert "sessions" in _strip_ansi(text)
    assert f"\x1b[{launcher.YELLOW}m" in text
    assert "memory" not in _strip_ansi(text)
    assert "stopped" in text


def test_v2_render_summary_includes_warnings():
    out = io.StringIO()
    launcher._render_summary_v2(
        stream=out,
        client="claude",
        duration_seconds=10.0,
        cleanup_deleted=0,
        native_eligible=4,
        running_preserved=0,
        full_cleanup=False,
        mcp_lifecycle="kept",
        warnings=["serena project create skipped"],
    )
    assert "serena project create skipped" in out.getvalue()
    assert "native retention 5d . 4 eligible" in _strip_ansi(out.getvalue())


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
    """preflight 단계에서는 더 이상 'Run codex?'를 묻지 않는다 — 실행 게이트는
    setup 질문들 + serena init이 모두 끝난 뒤 memory choice로 옮겨졌다.
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


def test_cleanup_choices_are_product_scoped_and_default_to_keep(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")

    memory_out = io.StringIO()
    session_out = io.StringIO()
    memory = launcher._run_memory_choice_v2(
        stream=memory_out,
        input_fn=lambda: "",
    )
    sessions = launcher._run_session_choice_v2(
        stream=session_out,
        input_fn=lambda: "",
    )

    assert memory == "keep"
    assert sessions == "retention_5d"
    assert "Keep all memory (default)" in _strip_ansi(memory_out.getvalue())
    assert "Delete all Codex auto-memory" in _strip_ansi(memory_out.getvalue())
    assert "automatic cleanup after 5 days (default)" in _strip_ansi(
        session_out.getvalue()
    )
    assert "running sessions are preserved" in _strip_ansi(
        session_out.getvalue()
    )
    assert f"\x1b[{launcher.PURPLE}m" in memory_out.getvalue()
    assert f"\x1b[{launcher.YELLOW}m" in session_out.getvalue()


def test_cleanup_choices_use_claude_product_scope(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    memory_out = io.StringIO()
    session_out = io.StringIO()

    assert launcher._run_memory_choice_v2(
        stream=memory_out,
        input_fn=lambda: "2",
    ) == "delete"
    assert launcher._run_session_choice_v2(
        stream=session_out,
        input_fn=lambda: "2",
    ) == "delete_inactive"
    assert "Claude auto-memory" in _strip_ansi(memory_out.getvalue())
    assert "Claude sessions" in _strip_ansi(session_out.getvalue())
    assert "Codex" not in _strip_ansi(memory_out.getvalue())
    assert "Codex" not in _strip_ansi(session_out.getvalue())


def test_cleanup_choices_bypass_prompts_when_non_interactive(monkeypatch):
    monkeypatch.delenv("SERENA_AGENT_INTERACTIVE", raising=False)
    memory_out = io.StringIO()
    session_out = io.StringIO()

    assert launcher._run_memory_choice_v2(stream=memory_out) == "keep"
    assert launcher._run_session_choice_v2(
        stream=session_out
    ) == "retention_5d"
    assert memory_out.getvalue() == ""
    assert session_out.getvalue() == ""


def test_memory_delete_action_uses_purple_rows(monkeypatch, tmp_path):
    from local_dev.serena_mcp_management.memory_management import MemoryDeleteResult

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr(
        launcher,
        "delete_all_memory",
        lambda **kwargs: MemoryDeleteResult(
            deleted_stores=2,
            deleted_files=17,
        ),
    )
    out = io.StringIO()

    result = launcher._run_memory_action_v2(
        choice="delete",
        client="codex",
        stream=out,
    )

    assert result.succeeded
    assert "2 stores · 17 files deleted" in _strip_ansi(out.getvalue())
    assert out.getvalue().count(f"\x1b[{launcher.PURPLE}m") >= 2


def test_memory_keep_action_is_silent_and_does_not_access_stores(monkeypatch):
    monkeypatch.setattr(
        launcher,
        "delete_all_memory",
        lambda **kwargs: pytest.fail("keep must not access memory stores"),
    )
    out = io.StringIO()

    result = launcher._run_memory_action_v2(
        choice="keep",
        client="codex",
        stream=out,
    )

    assert result.succeeded
    assert out.getvalue() == ""


def _run_main_for_cleanup_choices(
    monkeypatch,
    tmp_path,
    *,
    memory_choice,
    session_choice,
    deletion_succeeds=True,
    deletion_error="unsafe memory store",
    deleted_stores=2,
    deleted_files=17,
    delete_exception=None,
    codex_home=None,
    session_choice_exception=None,
    explicit_cleanup_result=None,
    explicit_cleanup_inventory=None,
    call_public_main=False,
):
    from local_dev.serena_mcp_management.memory_management import MemoryDeleteResult
    from local_dev.serena_mcp_management.session_cleanup import CleanupResult

    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    configured_codex_home = codex_home or tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(configured_codex_home))
    _set_graphify_env(monkeypatch)

    call_log: list[str] = []
    snapshot = _inventory_snapshot(total=1, to_delete=0, to_keep=1)

    def fake_overview(*, stream=None):
        call_log.append("overview")
        return snapshot

    def fake_preflight(**kwargs):
        call_log.append("setup")
        return 0

    def fake_serena_init(**kwargs):
        call_log.append("serena-init")
        return "skipped"

    def fake_memory_choice(**kwargs):
        call_log.append("memory-choice")
        return memory_choice

    def fake_session_choice(**kwargs):
        call_log.append("session-choice")
        if session_choice_exception is not None:
            raise session_choice_exception
        return session_choice

    def fake_delete_all_memory(**kwargs):
        call_log.append("memory-delete")
        if delete_exception is not None:
            raise delete_exception
        if deletion_succeeds:
            return MemoryDeleteResult(
                deleted_stores=deleted_stores,
                deleted_files=deleted_files,
            )
        return MemoryDeleteResult(
            deleted_stores=deleted_stores,
            deleted_files=deleted_files,
            error=deletion_error,
        )

    monkeypatch.setattr(launcher, "_render_preflight_overview_v2", fake_overview)
    monkeypatch.setattr(launcher, "_run_serena_cli_install_v2", lambda **kwargs: None)
    monkeypatch.setattr(launcher, "_run_preflight_v2", fake_preflight, raising=False)
    monkeypatch.setattr(launcher, "_run_serena_init_v2", fake_serena_init, raising=False)
    monkeypatch.setattr(launcher, "_run_memory_choice_v2", fake_memory_choice,
                        raising=False)
    monkeypatch.setattr(launcher, "_run_session_choice_v2", fake_session_choice,
                        raising=False)
    monkeypatch.setattr(launcher, "delete_all_memory", fake_delete_all_memory,
                        raising=False)
    monkeypatch.setattr(
        launcher,
        "find_real_binary",
        lambda client: "/usr/bin/true",
    )
    monkeypatch.setattr(
        launcher,
        "_run_launch_prep_v2",
        lambda **kwargs: call_log.append("session-retention")
        or launcher.LaunchPrepSummary(),
    )
    if explicit_cleanup_inventory is None:
        monkeypatch.setattr(
            launcher,
            "_run_explicit_session_cleanup_v2",
            lambda **kwargs: call_log.append("session-delete-inactive")
            or (
                explicit_cleanup_result
                if explicit_cleanup_result is not None
                else CleanupResult()
            ),
            raising=False,
        )
    else:
        run_explicit_cleanup = launcher._run_explicit_session_cleanup_v2
        monkeypatch.setattr(
            launcher,
            "scan_inventory",
            lambda **kwargs: explicit_cleanup_inventory,
        )
        monkeypatch.setattr(
            launcher,
            "_run_explicit_session_cleanup_v2",
            lambda **kwargs: call_log.append("session-delete-inactive")
            or run_explicit_cleanup(**kwargs),
            raising=False,
        )
    monkeypatch.setattr(
        launcher,
        "_launch_bare_child",
        lambda *args, **kwargs: call_log.append("launch") or 0,
    )

    entrypoint = launcher.main if call_public_main else launcher._main_v2
    return entrypoint([]), call_log


@pytest.mark.parametrize(
    ("memory_choice", "session_choice", "expected_actions"),
    [
        ("keep", "retention_5d", ["session-retention"]),
        (
            "delete",
            "retention_5d",
            ["memory-delete", "session-retention"],
        ),
        ("keep", "delete_inactive", ["session-delete-inactive"]),
        (
            "delete",
            "delete_inactive",
            ["memory-delete", "session-delete-inactive"],
        ),
    ],
)
def test_v2_main_collects_both_choices_before_actions(
    monkeypatch,
    tmp_path,
    memory_choice,
    session_choice,
    expected_actions,
):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice=memory_choice,
        session_choice=session_choice,
    )

    assert rc == 0
    assert call_log[:5] == [
        "overview",
        "serena-init",
        "setup",
        "memory-choice",
        "session-choice",
    ]
    assert [
        entry for entry in call_log if entry in expected_actions
    ] == expected_actions
    assert call_log[-1] == "launch"


def test_v2_main_session_choice_ctrl_c_precedes_memory_delete(
    monkeypatch,
    tmp_path,
):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice="delete",
        session_choice="retention_5d",
        session_choice_exception=KeyboardInterrupt(),
        call_public_main=True,
    )

    assert rc == 130
    assert call_log[-1] == "session-choice"
    assert "memory-delete" not in call_log
    assert "launch" not in call_log


def test_v2_main_explicit_cleanup_failure_stops_launch(monkeypatch, tmp_path):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice="keep",
        session_choice="delete_inactive",
        explicit_cleanup_result=launcher.CleanupResult(
            error="unsafe session inventory"
        ),
    )

    assert rc == 1
    assert "session-delete-inactive" in call_log
    assert "launch" not in call_log


def test_v2_main_inventory_failure_renders_bounded_causes_before_exit(
    monkeypatch,
    tmp_path,
    capsys,
):
    warnings = (
        "parent cycle at /tmp/codex/a.jsonl",
        "malformed session metadata at /tmp/codex/b.jsonl",
        "active session scan unavailable: lsof is unavailable",
        "unsafe fourth cause must be summarized",
    )
    inventory = AgentInventory(
        client="codex",
        policy="all_inactive",
        sessions=CountStats(total=0, to_delete=0, to_keep=0),
        criteria="all inactive",
        warnings=warnings,
    )

    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice="keep",
        session_choice="delete_inactive",
        explicit_cleanup_inventory=inventory,
    )

    text = _strip_ansi(capsys.readouterr().out)
    assert rc == 1
    assert call_log[-1] == "session-delete-inactive"
    assert "launch" not in call_log
    assert warnings[0] in text
    assert warnings[1] in text
    assert warnings[2] in text
    assert "+1 more" in text
    assert warnings[3] not in text


def test_explicit_session_cleanup_reports_newly_running_session(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        launcher,
        "scan_inventory",
        lambda **kwargs: _inventory_snapshot(
            total=1,
            to_delete=1,
            to_keep=0,
        ).inventory,
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_codex_inventory",
        lambda inventory, codex_binary: launcher.CleanupResult(
            deleted=0,
            preserved_running=1,
        ),
    )
    out = io.StringIO()

    result = launcher._run_explicit_session_cleanup_v2(
        client="codex",
        real_binary="/fake/codex",
        stream=out,
    )

    assert result.succeeded
    assert result.preserved_running == 1
    assert "1 running preserved" in _strip_ansi(out.getvalue())


def test_explicit_session_cleanup_reports_bounded_partial_mutation(
    monkeypatch,
):
    inventory = AgentInventory(
        client="codex",
        policy="all_inactive",
        sessions=CountStats(total=1, to_delete=1, to_keep=0),
        criteria="all inactive",
    )
    monkeypatch.setattr(
        launcher,
        "scan_inventory",
        lambda **kwargs: inventory,
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_codex_inventory",
        lambda inventory, codex_binary: launcher.CleanupResult(
            deleted=0,
            partial_mutations=4,
            partial_mutation_details=(
                "Codex member child-a in /codex-a",
                "Codex member child-b in /codex-b",
                "Codex member child-c in /codex-c",
            ),
            error="injected parent failure",
        ),
    )
    out = io.StringIO()

    result = launcher._run_explicit_session_cleanup_v2(
        client="codex",
        real_binary="/fake/codex",
        stream=out,
    )

    text = _strip_ansi(out.getvalue())
    assert not result.succeeded
    assert "0 sessions fully deleted" in text
    assert "partial mutation: 4 operations completed" in text
    assert "Codex member child-a in /codex-a" in text
    assert "Codex member child-b in /codex-b" in text
    assert "Codex member child-c in /codex-c" in text
    assert "+1 more" in text
    assert "injected parent failure" in text


def test_explicit_session_cleanup_codex_uses_fresh_all_inactive_scan(
    monkeypatch,
):
    inventory = AgentInventory(
        client="codex",
        policy="all_inactive",
        sessions=CountStats(total=0),
        criteria="all inactive",
    )
    scan_calls = []
    cleanup_calls = []
    monkeypatch.setattr(
        launcher,
        "scan_inventory",
        lambda **kwargs: scan_calls.append(kwargs) or inventory,
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_codex_inventory",
        lambda value, codex_binary: cleanup_calls.append(
            (value, codex_binary)
        )
        or launcher.CleanupResult(),
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_claude_inventory",
        lambda value: pytest.fail("Claude cleanup must not run"),
        raising=False,
    )

    result = launcher._run_explicit_session_cleanup_v2(
        client="codex",
        real_binary="/fake/codex",
        stream=io.StringIO(),
    )

    assert result.succeeded
    assert scan_calls[0]["policy"] == "all_inactive"
    assert cleanup_calls == [(inventory, "/fake/codex")]


def test_explicit_session_cleanup_claude_uses_only_claude_cleanup(
    monkeypatch,
):
    inventory = AgentInventory(
        client="claude",
        policy="all_inactive",
        sessions=CountStats(total=0),
        criteria="all inactive",
    )
    monkeypatch.setattr(
        launcher,
        "scan_inventory",
        lambda **kwargs: inventory,
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_codex_inventory",
        lambda *args, **kwargs: pytest.fail("Codex cleanup must not run"),
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_claude_inventory",
        lambda value: launcher.CleanupResult(),
        raising=False,
    )

    result = launcher._run_explicit_session_cleanup_v2(
        client="claude",
        real_binary="/fake/claude",
        stream=io.StringIO(),
    )

    assert result.succeeded


def test_v2_main_zero_store_delete_then_cleans_sessions_and_launches(
    monkeypatch, tmp_path, capsys
):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice="delete",
        session_choice="retention_5d",
        deleted_stores=0,
        deleted_files=0,
    )

    assert rc == 0
    assert call_log[-3:] == ["memory-delete", "session-retention", "launch"]
    assert (
        "memory      0 stores · 0 files deleted"
        in _strip_ansi(capsys.readouterr().out)
    )


def test_v2_main_partial_delete_failure_reports_counts_and_stops(
    monkeypatch, tmp_path, capsys
):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice="delete",
        session_choice="retention_5d",
        deletion_succeeds=False,
        deletion_error="disk busy",
        deleted_stores=1,
        deleted_files=4,
    )

    assert rc == 1
    assert call_log[-1] == "memory-delete"
    assert "session-retention" not in call_log
    assert "launch" not in call_log
    assert (
        "memory      delete failed · 1 stores · 4 files deleted · disk busy"
        in _strip_ansi(capsys.readouterr().out)
    )


def test_v2_main_invalid_memory_scan_config_returns_one_before_launch(
    monkeypatch, tmp_path, capsys
):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice="delete",
        session_choice="retention_5d",
        codex_home=Path("relative-codex-home"),
    )

    assert rc == 1
    assert call_log[-1] == "session-choice"
    assert "memory-delete" not in call_log
    assert "launch" not in call_log
    assert (
        "memory      delete failed · 0 stores · 0 files deleted · "
        "codex_home must be absolute"
        in _strip_ansi(capsys.readouterr().out)
    )


def test_v2_main_authoritative_memory_rescan_failure_returns_one_before_launch(
    monkeypatch, tmp_path, capsys
):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice="delete",
        session_choice="retention_5d",
        delete_exception=OSError("rescan unavailable"),
    )

    assert rc == 1
    assert call_log[-1] == "memory-delete"
    assert "session-retention" not in call_log
    assert "launch" not in call_log
    assert (
        "memory      delete failed · 0 stores · 0 files deleted · "
        "rescan unavailable"
        in _strip_ansi(capsys.readouterr().out)
    )


def test_v2_preflight_groups_memory_inventory_in_one_row(monkeypatch):
    """preflight overview는 box 렌더만 담당한다 — 어떤 prompt도 띄우지 않고
    memory/sessions/serena/graphify/context 행을 모두 한 번 그린다.
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
    )
    _stub_memory_inventory(monkeypatch, client="codex", stores=2, files=17)

    out = io.StringIO()
    snapshot = launcher._render_preflight_overview_v2(stream=out)
    text = out.getvalue()
    plain = _strip_ansi(text)
    assert plain.count("· memory      codex") == 1
    assert "├─ stores   2 found" in plain
    assert "├─ files    17" in plain
    assert "└─ scope    all known Codex homes" in plain
    assert "├─ groups   103 total · 0 to delete · 103 to keep" in plain
    assert "├─ records  103 total · 0 to delete · 103 to keep" in plain
    assert "└─ cleanup  inactive longer than 5 days" in plain
    assert "criteria" not in plain
    assert "preflight" in text
    assert "codex" in text
    assert "graphify global" in plain
    assert "graphify graph" in plain
    assert "graphify integration" in plain
    assert "graphify hook" in plain
    ids = [item.id for item in launcher._preflight_box(snapshot).items]
    assert ids.count("memory") == 1
    assert ids.index("memory") + 1 == ids.index("sessions")


def test_v2_preflight_memory_scan_failure_keeps_session_row(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    _stub_preflight_inventory(
        monkeypatch,
        sessions_total=7,
        sessions_to_delete=2,
        sessions_to_keep=5,
    )
    monkeypatch.setattr(
        launcher,
        "scan_memory_inventory",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("memory unavailable")),
        raising=False,
    )

    out = io.StringIO()
    snapshot = launcher._render_preflight_overview_v2(stream=out)
    plain = _strip_ansi(out.getvalue())

    assert "! memory      scan unavailable: memory unavailable" in plain
    assert "sessions" in plain
    assert "├─ groups   7 total · 2 to delete · 5 to keep" in plain
    assert snapshot is not None
    assert snapshot.inventory is not None
    assert snapshot.error is None
    assert snapshot.memory_inventory is None
    assert snapshot.memory_error == "memory unavailable"


def test_v2_preflight_session_scan_failure_keeps_memory_row(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    memory_inventory = _stub_memory_inventory(
        monkeypatch,
        client="codex",
        stores=1,
        files=3,
    )
    monkeypatch.setattr(
        launcher,
        "scan_inventory",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("sessions unavailable")),
    )

    out = io.StringIO()
    snapshot = launcher._render_preflight_overview_v2(stream=out)
    plain = _strip_ansi(out.getvalue())

    assert "· memory      codex" in plain
    assert "sessions" in plain
    assert "scan unavailable: sessions unavailable" in plain
    assert snapshot is not None
    assert snapshot.inventory is None
    assert snapshot.error == "sessions unavailable"
    assert snapshot.memory_inventory is memory_inventory
    assert snapshot.memory_error is None


def test_v2_render_preflight_overview_scans_inventory_once(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    inventory = _inventory_snapshot().inventory
    memory_inventory = _stub_memory_inventory(monkeypatch)
    session_scan = mock.Mock(return_value=inventory)
    memory_scan = mock.Mock(return_value=memory_inventory)
    monkeypatch.setattr(launcher, "_inventory_for_preflight", session_scan)
    monkeypatch.setattr(launcher, "_memory_inventory_for_preflight", memory_scan,
                        raising=False)

    snapshot = launcher._render_preflight_overview_v2(stream=io.StringIO())

    assert snapshot is not None
    assert snapshot.inventory is inventory
    assert snapshot.error is None
    assert snapshot.memory_inventory is memory_inventory
    assert snapshot.memory_error is None
    session_scan.assert_called_once_with("codex", "/repo")
    memory_scan.assert_called_once_with("codex")


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
        "server processes[3] → managed servers[2] · "
        "orphaned servers[1] · leases[3] · stale leases[1]"
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
        "server processes[0] → managed servers[0] · "
        "orphaned servers[0] · leases[0] · stale leases[0]"
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
        return 0, ""

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
    _stub_isolated_main_preflight(monkeypatch, tmp_path)

    monkeypatch.setattr(launcher, "_run_preflight_v2",
                        lambda **kw: 0, raising=False)
    monkeypatch.setattr(launcher, "_run_serena_init_v2",
                        lambda **kw: "skipped", raising=False)
    monkeypatch.setattr(launcher, "_run_memory_choice_v2",
                        lambda **kw: "keep", raising=False)
    monkeypatch.setattr(launcher, "_run_session_choice_v2",
                        lambda **kw: "retention_5d", raising=False)
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


def test_v2_memory_choice_defaults_to_keep(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    out = io.StringIO()
    answers = iter([""])  # bare Enter
    result = launcher._run_memory_choice_v2(
        stream=out,
        input_fn=lambda: next(answers),
    )
    assert result == "keep"
    assert "default 1" in out.getvalue()


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
    monkeypatch.setattr(
        launcher,
        "_run_memory_choice_v2",
        lambda *, stream=None, input_fn=None: (_ for _ in ()).throw(
            KeyboardInterrupt()
        ),
        raising=False,
    )
    monkeypatch.setattr(launcher, "_run_session_choice_v2",
                        lambda **kwargs: "retention_5d", raising=False)

    rc = launcher.main([])
    assert rc == 130
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
    run_kwargs = {}

    class _Result:
        returncode = 0
        stdout = "noisy serena output\n"

    def fake_run(cmd, **kwargs):
        run_calls.append(cmd)
        run_kwargs.update(kwargs)
        return _Result()

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)

    rc, output = launcher._serena_project_create(tmp_path)
    assert rc == 0
    assert output == "noisy serena output\n"
    assert run_calls == [
        ["/opt/homebrew/bin/uvx", "--from", "spec", "serena",
         "project", "create", str(tmp_path)]
    ]
    # Output is captured (not leaked to the terminal) and the pydantic-on-3.14
    # warning is silenced in the child.
    assert run_kwargs["stdout"] is launcher.subprocess.PIPE
    assert run_kwargs["env"]["PYTHONWARNINGS"] == "ignore"


def test_serena_project_create_returns_2_when_cli_unresolvable(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "serena_oneshot_command",
                        lambda: None, raising=False)
    monkeypatch.setattr(
        launcher.subprocess, "run",
        lambda *a, **k: pytest.fail("must not spawn anything"),
    )
    assert launcher._serena_project_create(tmp_path) == (2, "")


def test_v2_serena_init_success_shows_clean_row_not_raw_output(monkeypatch, tmp_path):
    """③: 생성 성공 시 serena의 날 출력(언어 프롬프트/pydantic 경고)을 흘리지 않고
    깔끔한 한 줄 상태 행만 보여준다."""
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")

    def fake_create(project_root):
        (project_root / ".serena").mkdir(exist_ok=True)
        (project_root / ".serena" / "project.yml").write_text("ok\n")
        return 0, (
            "UserWarning: Core Pydantic V1 ...\n"
            "Enable ruby (0.97%)? [y/N] Enable bash? [y/N]\n"
            "Generated project with languages {python} ...\n"
        )

    monkeypatch.setattr(launcher, "_serena_project_create", fake_create, raising=False)
    out = io.StringIO()
    answers = iter(["y"])
    result = launcher._run_serena_init_v2(stream=out, input_fn=lambda: next(answers))
    text = _strip_ansi(out.getvalue())
    assert result == "created"
    assert "project created" in text
    assert "Enable ruby" not in text  # raw serena noise suppressed on success
    assert "Pydantic" not in text


def test_v2_serena_init_failure_dumps_captured_output(monkeypatch, tmp_path):
    """③: 실패 시에는 캡처한 serena 출력을 들여쓰기 덤프해 원인 추적을 돕는다."""
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    monkeypatch.setattr(
        launcher, "_serena_project_create",
        lambda project_root: (1, "serena exploded: real traceback line\n"),
        raising=False,
    )
    out = io.StringIO()
    answers = iter(["y"])
    result = launcher._run_serena_init_v2(stream=out, input_fn=lambda: next(answers))
    text = _strip_ansi(out.getvalue())
    assert result == "failed"
    assert "serena exploded: real traceback line" in text  # dumped for diagnosis


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


def test_v2_main_runs_cleanup_before_bare_launch_when_serena_cli_missing(
    monkeypatch, tmp_path, capsys
):
    """project.yml이 있어도(managed) serena CLI 자체를 못 찾으면 scoped server를
    띄울 수 없다 — traceback 대신 경고 한 줄을 남기고 bare child로 강등한다.
    session cleanup은 Serena와 독립이므로 bare launch 전에도 실행한다."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.delenv("SERENA_AGENT_CLEAR_BEFORE_CHILD", raising=False)
    _set_graphify_env(monkeypatch)

    snapshot = _inventory_snapshot(total=1, to_delete=0, to_keep=1)
    monkeypatch.setattr(launcher, "_render_preflight_overview_v2",
                        lambda *, stream=None: snapshot, raising=False)
    monkeypatch.setattr(launcher, "_run_serena_cli_install_v2",
                        lambda **kw: "declined", raising=False)
    monkeypatch.setattr(launcher, "_run_preflight_v2",
                        lambda **kw: 0, raising=False)
    monkeypatch.setattr(launcher, "_run_memory_choice_v2",
                        lambda **kw: "keep", raising=False)
    monkeypatch.setattr(launcher, "_run_session_choice_v2",
                        lambda **kw: "retention_5d", raising=False)
    monkeypatch.setattr(launcher, "serena_server_command",
                        lambda: None, raising=False)
    monkeypatch.setattr(launcher, "find_real_binary",
                        lambda client: "/usr/bin/true", raising=False)
    prep_calls = []
    monkeypatch.setattr(
        launcher,
        "_run_launch_prep_v2",
        lambda **kwargs: prep_calls.append(kwargs) or launcher.LaunchPrepSummary(),
        raising=False,
    )
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
    assert prep_calls == [
        {"snapshot": snapshot, "real_binary": "/usr/bin/true"}
    ]
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
    monkeypatch.setattr(launcher, "_run_memory_choice_v2",
                        lambda **kw: "keep", raising=False)
    monkeypatch.setattr(launcher, "_run_session_choice_v2",
                        lambda **kw: "retention_5d", raising=False)
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
