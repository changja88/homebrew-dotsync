import io
import os
import re
import time
from unittest import mock

import pytest

from local_dev.serena_mcp_management import serena_agent_launcher as launcher

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


def test_v2_preflight_renders_box_with_cleanup_and_serena(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 103 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "installed")

    out = io.StringIO()
    answers = iter(["n"])  # Abort

    rc = launcher._run_preflight_v2(stream=out, input_fn=lambda: next(answers))
    text = out.getvalue()
    assert "0 to delete . 103 to keep" in _strip_ansi(text)
    assert "0 files to reset" in _strip_ansi(text)
    assert "preflight" in text
    assert "codex" in text
    assert rc == 130  # abort -> non-zero


def test_v2_preflight_returns_zero_on_run_confirm(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "missing")

    out = io.StringIO()
    answers = iter(["y"])
    rc = launcher._run_preflight_v2(stream=out, input_fn=lambda: next(answers))
    assert rc == 0
    assert "not installed" in out.getvalue()  # graphify warn surfaced


def test_v2_preflight_marks_graphify_hook_missing(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "hook-missing")

    out = io.StringIO()
    # Decline the hook install prompt, then decline the run prompt to avoid
    # exercising downstream phases.
    answers = iter(["n", "n"])
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_hooks=lambda project_root: 0,
    )
    text = _strip_ansi(out.getvalue())
    assert "hooks not installed" in text
    assert "graphify hook install" in text
    assert "not installed" not in text.split("hooks not installed")[0]


def test_v2_preflight_runs_graphify_hook_install_when_user_confirms(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "hook-missing")

    install_calls: list = []

    def fake_install(project_root):
        install_calls.append(project_root)
        return 0

    out = io.StringIO()
    answers = iter(["y", "n"])  # accept hook install, decline running the agent
    rc = launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_hooks=fake_install,
    )
    text = _strip_ansi(out.getvalue())
    assert install_calls, "graphify hook install should have been invoked"
    assert "Install graphify hooks" in text
    assert "initialized" in text
    assert rc == 130  # declined run -> abort


def test_v2_preflight_skips_graphify_hook_prompt_when_already_installed(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "installed")

    install_calls: list = []

    def fake_install(project_root):
        install_calls.append(project_root)
        return 0

    out = io.StringIO()
    answers = iter(["n"])  # only one prompt expected: the run confirmation
    launcher._run_preflight_v2(
        stream=out,
        input_fn=lambda: next(answers),
        install_graphify_hooks=fake_install,
    )
    assert install_calls == []
    assert "Install graphify hooks" not in out.getvalue()


def test_v2_preflight_marks_serena_warn_when_missing(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "missing")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "installed")

    out = io.StringIO()
    answers = iter(["y"])
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
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "installed")

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
