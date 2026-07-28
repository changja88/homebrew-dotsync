import io
import re
import threading
from pathlib import Path
from unittest import mock

import pytest

from local_dev.serena_mcp_management import serena_agent_launcher as launcher
from local_dev.serena_mcp_management.codex_reset import (
    CodexResetResult,
)
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
        "_inventory_for_preflight",
        lambda selected_client, project_root: AgentInventory(
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


def test_v2_preflight_inventory_scan_failure_renders_warning_row(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)

    def fail_scan(**kwargs):
        raise RuntimeError("inventory unavailable")

    monkeypatch.setattr(
        launcher,
        "_inventory_for_preflight",
        lambda client, project_root: fail_scan(),
    )
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
    assert f"\x1b[{launcher.AMBER}m" in text
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


def test_codex_cleanup_defaults_to_keep_before_reset_scan(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")

    def unexpected_scan(**kwargs):
        raise AssertionError("keep choice must not enter the reset scan")

    monkeypatch.setattr(
        launcher,
        "scan_codex_session_catalog",
        unexpected_scan,
    )

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
    assert sessions == "keep"
    assert memory_out.getvalue() == ""
    plain = _strip_ansi(session_out.getvalue())
    assert "Reset Codex sessions and memories before launch?" in plain
    assert "Keep all sessions and memories (default)" in plain
    assert "Delete all sessions, memories, and conversation traces" in plain
    assert "Select Codex sessions to force-delete" not in plain
    assert f"\x1b[{launcher.AMBER}m" in session_out.getvalue()


def test_codex_reset_choice_confirms_full_reset_without_session_catalog(
    monkeypatch,
):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")

    def unexpected_scan(**kwargs):
        raise AssertionError("full reset must not depend on a session catalog")

    monkeypatch.setattr(
        launcher,
        "scan_codex_session_catalog",
        unexpected_scan,
    )
    out = io.StringIO()
    answers = iter(("2", "yes"))

    choice = launcher._run_session_choice_v2(
        stream=out,
        input_fn=lambda: next(answers),
    )

    assert choice == "reset_all"
    plain = _strip_ansi(out.getvalue())
    assert "Reset Codex sessions and memories before launch?" in plain
    assert "Delete all sessions, memories, and conversation traces" in plain
    assert "currently running sessions" in plain
    assert "Select Codex sessions to force-delete" not in plain


def test_codex_full_reset_confirmation_cancel_keeps_everything(
    monkeypatch,
):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    answers = iter(("2", "no"))
    out = io.StringIO()

    choice = launcher._run_session_choice_v2(
        stream=out,
        input_fn=lambda: next(answers),
    )

    assert choice == "keep"
    plain = _strip_ansi(out.getvalue())
    assert "currently running sessions" in plain
    assert "Select Codex sessions to force-delete" not in plain


def test_codex_reset_action_uses_full_reset_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    calls = []
    monkeypatch.setattr(
        launcher,
        "reset_all_codex_data",
        lambda **kwargs: calls.append(kwargs)
        or CodexResetResult(
            discovered_sessions=3,
            deleted_sessions=3,
            deleted_trace_targets=7,
            terminated_processes=2,
        ),
    )
    out = io.StringIO()

    result = launcher._run_codex_reset_v2(
        stream=out,
        child_args=(
            "--config",
            'sqlite_home="/tmp/codex-state"',
        ),
        working_directory=tmp_path,
    )

    assert result.succeeded
    assert calls == [
        {
            "home": Path.home(),
            "codex_home": tmp_path / ".codex",
            "working_directory": tmp_path,
            "cli_arguments": (
                "--config",
                'sqlite_home="/tmp/codex-state"',
            ),
        }
    ]
    plain = _strip_ansi(out.getvalue())
    assert "3/3 sessions" in plain
    assert "7 conversation-state targets deleted" in plain
    assert "2 runtimes stopped" in plain


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
    client="codex",
    memory_choice,
    session_choice,
    deletion_succeeds=True,
    deletion_error="unsafe memory store",
    deleted_stores=2,
    deleted_files=17,
    delete_exception=None,
    session_choice_exception=None,
    explicit_cleanup_result=None,
    explicit_cleanup_inventory=None,
    call_public_main=False,
    serena_state="skipped",
    captured_summary_warnings=None,
):
    from local_dev.serena_mcp_management.memory_management import MemoryDeleteResult
    from local_dev.serena_mcp_management.session_cleanup import CleanupResult

    monkeypatch.setenv("SERENA_AGENT_CLIENT", client)
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    if client == "claude":
        monkeypatch.setenv(
            "CLAUDE_CONFIG_DIR",
            str(tmp_path / ".claude"),
        )
    _set_graphify_env(monkeypatch)

    call_log: list[str] = []
    snapshot = _inventory_snapshot(
        client=client,
        total=1,
        to_delete=0,
        to_keep=1,
    )

    def fake_overview(*, stream=None, guard_item=None):
        call_log.append("overview")
        return snapshot

    def fake_preflight(**kwargs):
        call_log.append("setup")
        return 0

    def fake_serena_init(**kwargs):
        call_log.append("serena-init")
        return serena_state

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
            "scan_claude_inventory",
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
    if captured_summary_warnings is not None:
        fake_record = mock.Mock(
            mcp_url="http://127.0.0.1:9999/mcp",
            dashboard_url="",
        )

        class FakeChild:
            def poll(self):
                return 0

            def terminate(self):
                return None

            def wait(self):
                return 0

        monkeypatch.setattr(
            launcher,
            "serena_server_command",
            lambda: ["/fake/serena"],
        )
        monkeypatch.setattr(
            launcher,
            "_start_mcp_with_spinner",
            lambda **kwargs: fake_record,
        )
        monkeypatch.setattr(
            launcher,
            "make_launcher_lease",
            lambda lease_id: mock.Mock(lease_id=lease_id),
        )
        monkeypatch.setattr(launcher, "_heartbeat_loop", lambda *args: None)
        monkeypatch.setattr(
            launcher,
            "build_child_command",
            lambda **kwargs: (["/usr/bin/true"], lambda: None),
        )
        monkeypatch.setattr(launcher, "open_dashboard_if_requested", lambda url: None)
        monkeypatch.setattr(
            launcher.subprocess,
            "Popen",
            lambda *args, **kwargs: call_log.append("launch") or FakeChild(),
        )
        monkeypatch.setattr(launcher.signal, "signal", lambda *args: None)
        monkeypatch.setattr(
            launcher,
            "_stop_mcp_with_spinner",
            lambda **kwargs: mock.Mock(
                server_stopped=True,
                server_was_running=True,
                sessions_remaining=0,
            ),
        )
        monkeypatch.setattr(
            launcher,
            "_render_summary_v2",
            lambda **kwargs: captured_summary_warnings.extend(kwargs["warnings"]),
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
        client="claude",
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


def test_v2_main_codex_keep_runs_no_cleanup(monkeypatch, tmp_path):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice="delete",
        session_choice="keep",
    )

    assert rc == 0
    assert "memory-delete" not in call_log
    assert "session-retention" not in call_log
    assert "session-delete-inactive" not in call_log
    assert call_log[-1] == "launch"


def test_v2_main_codex_reset_all_uses_combined_reset(
    monkeypatch,
    tmp_path,
):
    reset_calls = []
    monkeypatch.setattr(
        launcher,
        "_run_codex_reset_v2",
        lambda **kwargs: reset_calls.append(kwargs)
        or CodexResetResult(
            discovered_sessions=4,
            deleted_sessions=4,
            deleted_trace_targets=4,
        ),
    )

    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice="delete",
        session_choice="reset_all",
    )

    assert rc == 0
    assert len(reset_calls) == 1
    assert set(reset_calls[0]) == {
        "stream",
        "child_args",
        "working_directory",
    }
    assert reset_calls[0]["child_args"] == ()
    assert reset_calls[0]["working_directory"] == tmp_path
    assert "memory-delete" not in call_log
    assert "session-retention" not in call_log
    assert "session-delete-inactive" not in call_log
    assert call_log[-1] == "launch"


def test_v2_main_codex_reset_failure_aborts_before_launch(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        launcher,
        "_run_codex_reset_v2",
        lambda **kwargs: CodexResetResult(
            discovered_sessions=4,
            deleted_sessions=3,
            deleted_trace_targets=7,
            error="one desktop WAL is still open",
        ),
    )

    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice="keep",
        session_choice="reset_all",
    )

    assert rc == 1
    assert "launch" not in call_log
    assert "session-retention" not in call_log
    assert "session-delete-inactive" not in call_log


def test_v2_main_session_choice_ctrl_c_precedes_memory_delete(
    monkeypatch,
    tmp_path,
):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        client="claude",
        memory_choice="delete",
        session_choice="retention_5d",
        session_choice_exception=KeyboardInterrupt(),
        call_public_main=True,
    )

    assert rc == 130
    assert call_log[-1] == "session-choice"
    assert "memory-delete" not in call_log
    assert "launch" not in call_log


def test_v2_main_combined_cleanup_failures_warn_once_and_launch(
    monkeypatch,
    tmp_path,
):
    inventory_warnings = (
        "unsafe session root at /tmp/claude/a.jsonl",
        "malformed session metadata at /tmp/claude/b.jsonl",
        "active session scan unavailable: lsof is unavailable",
        "unsafe fourth cause must remain summarized",
    )
    session_error = (
        "cannot safely inventory every inactive Claude session: "
        f"{inventory_warnings[0]}; {inventory_warnings[1]}; "
        f"{inventory_warnings[2]}; +1 more"
    )
    summary_warnings: list[str] = []

    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        client="claude",
        memory_choice="delete",
        session_choice="delete_inactive",
        deletion_succeeds=False,
        deletion_error="4 running Claude process(es)",
        deleted_stores=0,
        deleted_files=0,
        explicit_cleanup_result=launcher.CleanupResult(
            warnings=inventory_warnings,
            error=session_error,
        ),
        serena_state="created",
        captured_summary_warnings=summary_warnings,
    )

    assert rc == 0
    assert call_log[-3:] == ["memory-delete", "session-delete-inactive", "launch"]
    assert summary_warnings == [
        "memory: 4 running Claude process(es)",
        f"sessions: {session_error}",
    ]


def test_v2_main_inventory_failure_renders_bounded_causes_and_launches(
    monkeypatch,
    tmp_path,
    capsys,
):
    warnings = (
        "unsafe session root at /tmp/claude/a.jsonl",
        "malformed session metadata at /tmp/claude/b.jsonl",
        "active session scan unavailable: lsof is unavailable",
        "unsafe fourth cause must be summarized",
    )
    inventory = AgentInventory(
        client="claude",
        policy="all_inactive",
        sessions=CountStats(total=0, to_delete=0, to_keep=0),
        criteria="all inactive",
        warnings=warnings,
    )

    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        client="claude",
        memory_choice="keep",
        session_choice="delete_inactive",
        explicit_cleanup_inventory=inventory,
    )

    text = _strip_ansi(capsys.readouterr().out)
    assert rc == 0
    assert "session-delete-inactive" in call_log
    assert call_log[-1] == "launch"
    assert warnings[0] in text
    assert warnings[1] in text
    assert warnings[2] in text
    assert "+1 more" in text
    assert warnings[3] not in text


def test_explicit_session_cleanup_animates_until_result(monkeypatch):
    out = io.StringIO()

    class FakeSpinnerTicker:
        def __init__(self, *, on_tick, interval):
            assert interval == 0.1
            self._on_tick = on_tick

        def start(self):
            self._on_tick(1)
            self._on_tick(2)

        def stop(self):
            out.write("<ticker-stopped>")

    monkeypatch.setattr(launcher, "SpinnerTicker", FakeSpinnerTicker)
    monkeypatch.setattr(
        launcher,
        "scan_claude_inventory",
        lambda **kwargs: _inventory_snapshot(
            client="claude",
            total=1,
            to_delete=1,
            to_keep=0,
        ).inventory,
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_claude_inventory",
        lambda inventory: launcher.CleanupResult(deleted=1),
    )

    result = launcher._run_explicit_session_cleanup_v2(
        client="claude",
        stream=out,
    )

    text = _strip_ansi(out.getvalue())
    assert result.succeeded
    assert "\r  ⠙ sessions" in text
    assert "\r  ⠹ sessions" in text
    assert text.index("<ticker-stopped>") < text.index("✓ sessions")


@pytest.mark.parametrize("failure_site", ("scan", "cleanup"))
def test_explicit_session_cleanup_stops_spinner_before_error_result(
    monkeypatch,
    failure_site,
):
    out = io.StringIO()

    class FakeSpinnerTicker:
        def __init__(self, *, on_tick, interval):
            self._on_tick = on_tick

        def start(self):
            self._on_tick(1)

        def stop(self):
            out.write("<ticker-stopped>")

    def raise_scan_error(**kwargs):
        raise RuntimeError("injected scan failure")

    def raise_cleanup_error(inventory):
        raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(launcher, "SpinnerTicker", FakeSpinnerTicker)
    if failure_site == "scan":
        monkeypatch.setattr(
            launcher,
            "scan_claude_inventory",
            raise_scan_error,
        )
        expected_error = "injected scan failure"
    else:
        monkeypatch.setattr(
            launcher,
            "scan_claude_inventory",
            lambda **kwargs: _inventory_snapshot(
                client="claude",
                total=1,
                to_delete=1,
                to_keep=0,
            ).inventory,
        )
        monkeypatch.setattr(
            launcher,
            "cleanup_claude_inventory",
            raise_cleanup_error,
        )
        expected_error = "injected cleanup failure"

    result = launcher._run_explicit_session_cleanup_v2(
        client="claude",
        stream=out,
    )

    text = _strip_ansi(out.getvalue())
    assert not result.succeeded
    assert result.error == expected_error
    assert text.index("<ticker-stopped>") < text.index("! sessions")


def test_explicit_session_cleanup_waits_for_inflight_tick_before_final_row(
    monkeypatch,
):
    tick_write_started = threading.Event()
    release_tick_write = threading.Event()
    final_write_during_tick = threading.Event()
    ticker_threads = []

    class BlockingStream:
        def __init__(self):
            self.ticker_thread = None

        def write(self, value):
            if threading.current_thread() is self.ticker_thread:
                tick_write_started.set()
                assert release_tick_write.wait(timeout=1)
            elif tick_write_started.is_set() and not release_tick_write.is_set():
                final_write_during_tick.set()

        def flush(self):
            pass

    class FakeSpinnerTicker:
        def __init__(self, *, on_tick, interval):
            self._on_tick = on_tick

        def start(self):
            thread = threading.Thread(target=self._on_tick, args=(1,))
            ticker_threads.append(thread)
            stream.ticker_thread = thread
            thread.start()

        def stop(self):
            pass

    stream = BlockingStream()
    monkeypatch.setattr(launcher, "SpinnerTicker", FakeSpinnerTicker)
    monkeypatch.setattr(
        launcher,
        "scan_claude_inventory",
        lambda **kwargs: _inventory_snapshot(
            client="claude",
            total=1,
            to_delete=1,
            to_keep=0,
        ).inventory,
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_claude_inventory",
        lambda inventory: launcher.CleanupResult(deleted=1),
    )

    worker = threading.Thread(
        target=launcher._run_explicit_session_cleanup_v2,
        kwargs={
            "client": "claude",
            "stream": stream,
        },
    )
    worker.start()
    try:
        assert tick_write_started.wait(timeout=1)
        assert not final_write_during_tick.wait(timeout=0.1)
    finally:
        release_tick_write.set()
        if ticker_threads:
            ticker_threads[0].join(timeout=1)
        worker.join(timeout=1)

    assert not ticker_threads[0].is_alive()
    assert not worker.is_alive()


def test_explicit_session_cleanup_reports_bounded_partial_mutation(
    monkeypatch,
):
    inventory = AgentInventory(
        client="claude",
        policy="all_inactive",
        sessions=CountStats(total=1, to_delete=1, to_keep=0),
        criteria="all inactive",
    )
    monkeypatch.setattr(
        launcher,
        "scan_claude_inventory",
        lambda **kwargs: inventory,
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_claude_inventory",
        lambda inventory: launcher.CleanupResult(
            deleted=0,
            partial_mutations=4,
            partial_mutation_details=(
                "Claude root child-a in /claude-a",
                "Claude root child-b in /claude-b",
                "Claude root child-c in /claude-c",
            ),
            error="injected parent failure",
        ),
    )
    out = io.StringIO()

    result = launcher._run_explicit_session_cleanup_v2(
        client="claude",
        stream=out,
    )

    text = _strip_ansi(out.getvalue())
    assert not result.succeeded
    assert "0 sessions fully deleted" in text
    assert "partial mutation: 4 operations completed" in text
    assert "Claude root child-a in /claude-a" in text
    assert "Claude root child-b in /claude-b" in text
    assert "Claude root child-c in /claude-c" in text
    assert "+1 more" in text
    assert "injected parent failure" in text


def test_explicit_session_cleanup_uses_claude_cleanup(
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
        "scan_claude_inventory",
        lambda **kwargs: inventory,
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_claude_inventory",
        lambda value: launcher.CleanupResult(),
        raising=False,
    )

    result = launcher._run_explicit_session_cleanup_v2(
        client="claude",
        stream=io.StringIO(),
    )

    assert result.succeeded


@pytest.mark.parametrize(
    ("session_choice", "expected_session_action"),
    (
        ("retention_5d", "session-retention"),
        ("delete_inactive", "session-delete-inactive"),
    ),
)
def test_v2_main_partial_delete_failure_reports_counts_and_launches(
    monkeypatch,
    tmp_path,
    capsys,
    session_choice,
    expected_session_action,
):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        client="claude",
        memory_choice="delete",
        session_choice=session_choice,
        deletion_succeeds=False,
        deletion_error="disk busy",
        deleted_stores=1,
        deleted_files=4,
    )

    assert rc == 0
    assert call_log[-3:] == ["memory-delete", expected_session_action, "launch"]
    assert (
        "memory      delete failed · 1 stores · 4 files deleted · disk busy"
        in _strip_ansi(capsys.readouterr().out)
    )


def test_v2_main_memory_delete_exception_warns_and_launches(
    monkeypatch, tmp_path, capsys
):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        client="claude",
        memory_choice="delete",
        session_choice="retention_5d",
        delete_exception=OSError("memory inventory unavailable"),
    )

    assert rc == 0
    assert "memory-delete" in call_log
    assert call_log[-2:] == ["session-retention", "launch"]
    assert (
        "memory      delete failed · 0 stores · 0 files deleted · "
        "memory inventory unavailable"
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
        "_inventory_for_preflight",
        lambda client, project_root: (_ for _ in ()).throw(
            RuntimeError("sessions unavailable")
        ),
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


def test_v2_render_preflight_overview_skips_when_non_interactive(monkeypatch):
    """interactive=0이면 overview는 아무 것도 안 그린다."""
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "0")

    out = io.StringIO()
    launcher._render_preflight_overview_v2(stream=out)
    assert out.getvalue() == ""


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
                        lambda **kw: "keep", raising=False)
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
#   3. graphify integration install-> default=No (always). 사용자 선호
#                                     (2026-07-23): Serena/graphify-global
#                                     상태와 무관하게 항상 No — 실수로 Enter를
#                                     쳐도 프로젝트에 graphify가 심기지 않도록.
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


def test_v2_preflight_graphify_integration_default_no_even_when_serena_and_global_done(monkeypatch):
    """사용자 선호(2026-07-23): Serena/global이 모두 done이어도 통합 프롬프트는
    여전히 No가 기본값 — 실수로 Enter를 쳐도 install이 실행되지 않는다."""
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, integration="missing")  # global=installed

    integration_calls: list = []
    out = io.StringIO()
    answers = iter([""])  # bare Enter -> should decline (No default)
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        serena_state="managed",
        install_graphify_integration=lambda root, client:
            integration_calls.append(client) or 0,
    )
    assert integration_calls == []
    assert "[y/N]" in out.getvalue()


def test_v2_preflight_graphify_hook_prompt_defaults_to_yes(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
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
    assert out.getvalue() == ""


# --- External CLI resolution for prompt actions ------------------------------
#
# serena/graphify는 PATH에 없을 수 있다 (serena는 uvx로만 돌고, graphify는
# uv tool bin인 ~/.local/bin에 산다 — 둘 다 interactive PATH 밖). 프롬프트의
# Yes 액션은 bare `which` 대신 external_cli resolver가 돌려준 argv를 그대로
# 실행해야 한다. 그렇지 않으면 Yes가 조용히 exit 2로 끝난다.


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

    snapshot = _inventory_snapshot(
        client="claude",
        total=1,
        to_delete=0,
        to_keep=1,
    )
    monkeypatch.setattr(launcher, "_render_preflight_overview_v2",
                        lambda *, stream=None, guard_item=None: snapshot, raising=False)
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
    assert prep_calls == [{"snapshot": snapshot}]
    assert run_calls == [["/usr/bin/true"]]
    out = _strip_ansi(capsys.readouterr().out)
    assert "serena CLI" in out


# --- CLI self-install prompts -------------------------------------------------
#
# serena/graphify CLI가 머신에 없으면 preflight 질문 단계에서 설치 여부를 묻고,
# Yes면 uv tool로 설치한다. 이미 해석되는 머신에서는 질문 자체가 나타나지 않아
# 기존 동작이 바뀌지 않는다 (no side effects).


def test_serena_cli_install_phase_declines_without_installing(monkeypatch):
    monkeypatch.setattr(launcher, "serena_server_command",
                        lambda: None, raising=False)
    out = io.StringIO()
    answers = iter(["n"])
    state = launcher._run_serena_cli_install_v2(
        stream=out, input_fn=lambda: next(answers),
        install_fn=lambda: pytest.fail("must not install on decline"))
    assert state == "declined"


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


# --- install prompt wording -----------------------------------------------------
#
# 설치 제안 프롬프트는 "상태(없음) → 질문 → (명령어)" 순서로 읽힌다.
# 명령어가 문장 주어처럼 먼저 나오지 않는다.


def test_v2_main_runs_serena_cli_phase_before_init(monkeypatch, tmp_path):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    monkeypatch.delenv("SERENA_AGENT_CLEAR_BEFORE_CHILD", raising=False)
    _set_graphify_env(monkeypatch)

    order = []
    monkeypatch.setattr(launcher, "_render_preflight_overview_v2",
                        lambda *, stream=None, guard_item=None: None, raising=False)
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
                        lambda **kw: "keep", raising=False)
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


def test_main_v2_runs_notification_guard_before_launch(monkeypatch, tmp_path):
    """가드는 interactive 여부와 무관하게 _main_v2 진입 즉시 1회 호출된다."""
    from local_dev.serena_mcp_management import serena_agent_launcher as launcher

    calls: list[bool] = []
    monkeypatch.setattr(
        launcher, "run_notification_guard",
        lambda *, stream=None: calls.append(True) or [],
    )
    # 가드 직후 단계에서 의도적으로 중단시켜 나머지 flow를 실행하지 않는다
    monkeypatch.setattr(
        launcher, "infer_client_type",
        lambda *a, **k: (_ for _ in ()).throw(SystemExit(99)),
    )
    monkeypatch.delenv("SERENA_AGENT_INTERACTIVE", raising=False)
    with pytest.raises(SystemExit):
        launcher._main_v2([])
    assert calls == [True]


class TestNotificationGuardBoxItem:
    def _actions(self, *kinds: str):
        from local_dev.serena_mcp_management.notification_guard import GuardAction
        return [GuardAction(kind, f"msg-{i}") for i, kind in enumerate(kinds)]

    def test_interactive_clean_returns_done_item(self, monkeypatch):
        monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
        monkeypatch.setattr(launcher, "run_notification_guard", lambda *, stream=None: [])
        item, detail = launcher._run_notification_guard_capture(interactive=True)
        assert item is not None
        assert (item.id, item.label, item.value, item.status) == (
            "notif-guard", "notif guard", "clean", "done",
        )
        assert detail == ""

    def test_interactive_actions_summarized_with_detail_returned(self, monkeypatch):
        def fake_guard(*, stream=None):
            stream.write("DETAIL-LINE\n")
            return self._actions("repair", "warn")

        monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
        monkeypatch.setattr(launcher, "run_notification_guard", fake_guard)
        item, detail = launcher._run_notification_guard_capture(interactive=True)
        assert item.value == "1 repaired · 1 warning"
        assert item.status == "warn"          # 경고가 하나라도 있으면 warn
        assert detail == "DETAIL-LINE\n"      # 상세는 박스 아래 출력용으로 반환


    def test_interactive_guard_crash_is_warn_item(self, monkeypatch):
        def boom(*, stream=None):
            raise RuntimeError("boom")

        monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
        monkeypatch.setattr(launcher, "run_notification_guard", boom)
        item, detail = launcher._run_notification_guard_capture(interactive=True)
        assert item.status == "warn"
        assert "failed" in item.value
        assert detail == ""

    def test_non_interactive_delegates_silently(self, monkeypatch):
        calls = []
        monkeypatch.delenv("SERENA_AGENT_INTERACTIVE", raising=False)
        monkeypatch.setattr(
            launcher, "run_notification_guard",
            lambda *, stream=None: calls.append(stream) or [],
        )
        item, detail = launcher._run_notification_guard_capture(interactive=False)
        assert (item, detail) == (None, "")
        assert len(calls) == 1          # 가드는 여전히 조용히 1회 실행됨

    def test_preflight_box_places_guard_item_first(self, monkeypatch):
        from local_dev.serena_mcp_management.ui import Item
        monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
        monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
        monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
        _set_graphify_env(monkeypatch)
        guard_item = Item(id="notif-guard", label="notif guard", value="clean", status="done")
        box = launcher._preflight_box(guard_item=guard_item)
        assert box.items[0].id == "notif-guard"
        # 주입 안 하면 박스에 없다
        assert all(i.id != "notif-guard" for i in launcher._preflight_box().items)
