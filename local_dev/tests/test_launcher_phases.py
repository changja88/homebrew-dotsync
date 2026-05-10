import io
import os
import re
import time
from unittest import mock

import pytest

from local_dev.serena_mcp_management import serena_agent_launcher as launcher
from local_dev.serena_mcp_management.serena_mcp.diagnostics import GlobalLifecycleSnapshot

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", s)


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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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


def test_v2_preflight_renders_box_with_cleanup_and_serena(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 103 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)

    out = io.StringIO()
    # Everything installed -> no prompts should fire from preflight.
    answers = iter([])

    # The box itself is now rendered upstream by _render_preflight_overview_v2;
    # we exercise it here to keep the integration assertions meaningful.
    launcher._render_preflight_overview_v2(stream=out)
    rc = launcher._run_preflight_v2(stream=out, input_fn=lambda: next(answers))
    text = out.getvalue()
    plain = _strip_ansi(text)
    assert "0 to delete . 103 to keep" in plain
    assert "0 files to reset" in plain
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


def test_v2_preflight_returns_zero_on_run_confirm(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, hook="missing")

    out = io.StringIO()
    # Decline the hook install prompt. Preflight no longer asks "Run codex?".
    answers = iter(["n"])
    launcher._render_preflight_overview_v2(stream=out)
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_hooks=lambda project_root: 0,
    )
    text = _strip_ansi(out.getvalue())
    assert "hooks not installed" in text
    assert "graphify hook install" in text


def test_v2_preflight_runs_graphify_hook_install_when_user_confirms(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
        install_graphify_hooks=fake_install,
    )
    text = _strip_ansi(out.getvalue())
    assert install_calls, "graphify hook install should have been invoked"
    assert "Install graphify hooks" in text
    # After successful install, the hook row flips to the done variant.
    assert "post-commit + post-checkout hooks installed" in text
    assert rc == 0  # preflight always returns 0; abort moved to _run_final_confirm_v2


def test_v2_preflight_skips_graphify_hook_prompt_when_already_installed(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    assert "user skill at ~/.agents/skills/graphify" in text


def test_v2_preflight_graphify_graph_missing_shows_hint_no_callback(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, integration="missing", hook="missing")

    out = io.StringIO()
    answers = iter(["y", "y"])  # accept integration, accept hooks
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    path.write_text("x")
    old = time.time() - 4 * 86400
    os.utime(path, (old, old))


def test_v2_run_cleanup_claude_deletes_old_jsonl(tmp_path, monkeypatch):
    proj_dir = tmp_path / ".claude" / "projects" / "-repo"
    proj_dir.mkdir(parents=True)
    old = proj_dir / "abc.jsonl"
    _make_old_file(old)
    fresh = proj_dir / "fresh.jsonl"
    fresh.write_text("x")
    mem = proj_dir / "memory"
    mem.mkdir()
    (mem / "m1.txt").write_text("x")

    result = launcher._run_cleanup_claude(proj_dir)
    assert result.deleted == 1
    assert result.memory_files_reset == 1
    assert not old.exists()
    assert fresh.exists()
    assert not mem.exists()


def test_v2_run_cleanup_codex_skips_when_jq_missing(tmp_path, monkeypatch):
    codex_home = tmp_path / ".codex"
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "a.jsonl").write_text("{}\n")
    mem = codex_home / "memories"
    mem.mkdir()
    (mem / "m.txt").write_text("x")

    monkeypatch.setattr(launcher, "_jq_available", lambda: False, raising=False)
    result = launcher._run_cleanup_codex(codex_home, "/repo")
    assert result.deleted == 0
    assert result.memory_files_reset == 1
    assert not mem.exists()


def test_v2_launch_prep_runs_cleanup_and_renders_done_row(tmp_path, monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    proj_dir = tmp_path / ".claude" / "projects" / "-x"
    proj_dir.mkdir(parents=True)
    monkeypatch.setattr(launcher, "_claude_project_dir",
                        lambda: proj_dir, raising=False)

    out = io.StringIO()
    summary = launcher._run_launch_prep_v2(stream=out)
    text = out.getvalue()
    assert "cleanup" in text
    assert "0 deleted . 0 memory files reset" in text
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
    assert "2 deleted" in _strip_ansi(text)
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch, integration="missing", hook="missing")

    hook_calls: list = []

    out = io.StringIO()
    # Accept integration (succeeds), accept hooks too.
    answers = iter(["y", "y"])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    cleanup/memory/serena/graphify/context 행을 모두 한 번 그린다.
    """
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 103 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)

    out = io.StringIO()
    launcher._render_preflight_overview_v2(stream=out)
    text = out.getvalue()
    plain = _strip_ansi(text)
    assert "0 to delete . 103 to keep" in plain
    assert "0 files to reset" in plain
    assert "preflight" in text
    assert "codex" in text
    assert "graphify global" in plain
    assert "graphify graph" in plain
    assert "graphify integration" in plain
    assert "graphify hook" in plain


def test_preflight_box_includes_global_serena_mcp_inventory(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
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
