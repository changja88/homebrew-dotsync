import os
import subprocess

import pytest

from local_dev.serena_mcp_management.serena_agent_launcher import (
    build_child_command,
    find_real_binary,
    format_shutdown_status,
    format_shutdown_progress_status,
    format_launch_status,
    infer_client_type,
)
from local_dev.serena_mcp_management.serena_mcp.watchdog import ShutdownStats


def test_infer_client_type_from_program_name():
    assert infer_client_type("codex") == "codex"
    assert infer_client_type("/tmp/claude") == "claude"


def test_build_codex_command_injects_runtime_mcp_url():
    cmd, cleanup = build_child_command(
        client_type="codex",
        real_binary="/opt/homebrew/bin/codex",
        mcp_url="http://127.0.0.1:9000/mcp",
        child_args=["--help"],
    )

    assert cmd == [
        "/opt/homebrew/bin/codex",
        "-c",
        'mcp_servers.serena.url="http://127.0.0.1:9000/mcp"',
        "--help",
    ]
    cleanup()


def test_build_claude_command_uses_temp_mcp_config():
    cmd, cleanup = build_child_command(
        client_type="claude",
        real_binary="/opt/homebrew/bin/claude",
        mcp_url="http://127.0.0.1:9000/mcp",
        child_args=["--help"],
    )

    assert cmd[0] == "/opt/homebrew/bin/claude"
    assert cmd[1].startswith("--mcp-config=")
    config_path = cmd[1].split("=", 1)[1]
    assert os.path.exists(config_path)
    assert cmd[2:] == ["--help"]
    cleanup()
    assert not os.path.exists(config_path)


def test_build_claude_command_does_not_swallow_positional_args():
    cmd, cleanup = build_child_command(
        client_type="claude",
        real_binary="/opt/homebrew/bin/claude",
        mcp_url="http://127.0.0.1:9000/mcp",
        child_args=["mcp", "list"],
    )

    assert cmd[1].startswith("--mcp-config=")
    assert cmd[2:] == ["mcp", "list"]
    cleanup()


def test_format_launch_status_shows_client_project_and_mcp_url():
    assert format_launch_status(
        client_type="claude",
        project_root="/repo",
        mcp_url="http://127.0.0.1:9122/mcp",
    ) == "serena launcher: claude project=/repo mcp=http://127.0.0.1:9122/mcp"


def test_format_shutdown_status_matches_agent_event_style():
    assert format_shutdown_status(
        ShutdownStats(
            sessions_before=3,
            sessions_closed=1,
            sessions_remaining=2,
            server_was_running=True,
            server_stopped=False,
        )
    ) == "  * serena     done      . sessions_before=3 closed=1 remaining=2 server=kept"


def test_format_shutdown_status_reports_stopped_server():
    assert format_shutdown_status(
        ShutdownStats(
            sessions_before=1,
            sessions_closed=1,
            sessions_remaining=0,
            server_was_running=True,
            server_stopped=True,
        )
    ) == "  * serena     done      . sessions_before=1 closed=1 remaining=0 server=stopped"


def test_format_shutdown_progress_status_matches_agent_event_style():
    assert (
        format_shutdown_progress_status("stopping scoped MCP server")
        == "  * serena     shutdown  . stopping scoped MCP server"
    )


def test_format_mcp_progress_status_matches_agent_event_style():
    import local_dev.serena_mcp_management.serena_agent_launcher as launcher

    assert (
        launcher.format_mcp_progress_status("pending", "preparing scoped server")
        == "  * serena     mcp       . preparing scoped server"
    )
    assert (
        launcher.format_mcp_progress_status("ready", "http://127.0.0.1:9000/mcp")
        == "  * serena     ready     . http://127.0.0.1:9000/mcp"
    )


def test_launcher_prints_mcp_progress_and_clears_before_child(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()

    class Record:
        mcp_url = "http://127.0.0.1:9000/mcp"
        dashboard_url = "http://127.0.0.1:9001"

    class Proc:
        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pass

    import io as _io
    events = []

    def fake_ensure_server(scope, lease):
        events.append(("ensure",))
        return Record()

    def fake_popen(cmd, cwd=None):
        events.append(("popen",))
        return Proc()

    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_QUIET", "1")
    monkeypatch.setenv("SERENA_AGENT_CLEAR_BEFORE_CHILD", "1")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "installed")
    # Mock preflight to avoid stdin interaction
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._run_preflight_v2",
                        lambda **kw: 0, raising=False)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._run_serena_init_v2",
                        lambda **kw: "managed", raising=False)
    from local_dev.serena_mcp_management.serena_agent_launcher import LaunchPrepSummary
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._run_launch_prep_v2",
                        lambda **kw: LaunchPrepSummary(cleanup_deleted=0, cleanup_memory_files_reset=0),
                        raising=False)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.ensure_server", fake_ensure_server)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.find_real_binary", lambda client: "/opt/homebrew/bin/codex")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._remove_lease_and_shutdown_if_empty", lambda scope, lease_id: None)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.subprocess.Popen", fake_popen)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.subprocess.run", lambda cmd, **kwargs: None)

    from local_dev.serena_mcp_management.serena_agent_launcher import main

    assert main([]) == 0
    # v2 uses _start_mcp_with_spinner: ensure_server is called, then popen
    assert any(e[0] == "ensure" for e in events)
    assert any(e[0] == "popen" for e in events)
    # v2 clears terminal before child when SERENA_AGENT_CLEAR_BEFORE_CHILD=1
    output = capsys.readouterr().out
    assert "\x1b[3J\x1b[H\x1b[2J" in output


def test_launcher_status_can_be_suppressed_by_zsh_adapter(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()

    class Record:
        mcp_url = "http://127.0.0.1:9000/mcp"
        dashboard_url = "http://127.0.0.1:9001"

    class Proc:
        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pass

    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_QUIET", "1")
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.ensure_server", lambda scope, lease: Record())
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.find_real_binary", lambda client: "/opt/homebrew/bin/codex")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._remove_lease_and_shutdown_if_empty", lambda scope, lease_id: None)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.subprocess.Popen", lambda cmd, cwd=None: Proc())

    from local_dev.serena_mcp_management.serena_agent_launcher import main

    assert main([]) == 0
    assert "serena launcher:" not in capsys.readouterr().err


def test_launcher_opens_dashboard_for_interactive_agent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()

    class Record:
        mcp_url = "http://127.0.0.1:9000/mcp"
        dashboard_url = "http://127.0.0.1:9001"

    class Proc:
        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pass

    calls = []
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "installed")
    # Mock preflight/init/launch-prep to avoid stdin interaction
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._run_preflight_v2",
                        lambda **kw: 0, raising=False)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._run_serena_init_v2",
                        lambda **kw: "managed", raising=False)
    from local_dev.serena_mcp_management.serena_agent_launcher import LaunchPrepSummary
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._run_launch_prep_v2",
                        lambda **kw: LaunchPrepSummary(cleanup_deleted=0, cleanup_memory_files_reset=0),
                        raising=False)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.ensure_server", lambda scope, lease: Record())
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.find_real_binary", lambda client: "/opt/homebrew/bin/codex")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._remove_lease_and_shutdown_if_empty", lambda scope, lease_id: None)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.subprocess.Popen", lambda cmd, cwd=None: Proc())
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.subprocess.run", lambda cmd, **kwargs: calls.append((cmd, kwargs)))

    from local_dev.serena_mcp_management.serena_agent_launcher import main

    assert main([]) == 0
    assert any(
        cmd == ["open", "http://127.0.0.1:9001"]
        for cmd, _ in calls
    )


def test_launcher_prints_shutdown_stats_for_interactive_agent(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()

    class Record:
        mcp_url = "http://127.0.0.1:9000/mcp"
        dashboard_url = "http://127.0.0.1:9001"

    class Proc:
        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pass

    stats = ShutdownStats(
        sessions_before=2,
        sessions_closed=1,
        sessions_remaining=1,
        server_was_running=True,
        server_stopped=False,
    )
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    monkeypatch.setenv("SERENA_AGENT_QUIET", "1")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "installed")
    # Mock preflight/init/launch-prep to avoid stdin interaction
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._run_preflight_v2",
                        lambda **kw: 0, raising=False)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._run_serena_init_v2",
                        lambda **kw: "managed", raising=False)
    from local_dev.serena_mcp_management.serena_agent_launcher import LaunchPrepSummary
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._run_launch_prep_v2",
                        lambda **kw: LaunchPrepSummary(cleanup_deleted=0, cleanup_memory_files_reset=0),
                        raising=False)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.ensure_server", lambda scope, lease: Record())
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.find_real_binary", lambda client: "/opt/homebrew/bin/codex")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._remove_lease_and_shutdown_if_empty", lambda scope, lease_id: stats)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.subprocess.Popen", lambda cmd, cwd=None: Proc())
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.subprocess.run", lambda cmd, **kwargs: None)

    from local_dev.serena_mcp_management.serena_agent_launcher import main

    assert main([]) == 0
    output = capsys.readouterr().out
    # v2 renders a summary box on exit, not the v1 event-style lines
    assert "summary" in output
    assert "codex" in output


def test_launcher_uses_project_root_from_zsh_adapter(monkeypatch, tmp_path):
    displayed_root = tmp_path / "project"
    cwd = displayed_root / "nested"
    cwd.mkdir(parents=True)
    monkeypatch.chdir(cwd)

    class Record:
        mcp_url = "http://127.0.0.1:9000/mcp"
        dashboard_url = "http://127.0.0.1:9001"

    class Proc:
        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pass

    scopes = []
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", str(displayed_root))
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.ensure_server", lambda scope, lease: scopes.append(scope) or Record())
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.find_real_binary", lambda client: "/opt/homebrew/bin/codex")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._remove_lease_and_shutdown_if_empty", lambda scope, lease_id: None)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.subprocess.Popen", lambda cmd, cwd=None: Proc())

    from local_dev.serena_mcp_management.serena_agent_launcher import main

    assert main([]) == 0
    assert scopes[0].project_root == displayed_root.resolve()


def test_find_real_binary_uses_env_override(monkeypatch, tmp_path):
    real = tmp_path / "codex-real"
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    monkeypatch.setenv("SERENA_REAL_CODEX", str(real))

    assert find_real_binary("codex") == str(real)


def test_find_real_binary_rejects_missing_env_override(monkeypatch):
    monkeypatch.setenv("SERENA_REAL_CODEX", "/missing/codex")

    with pytest.raises(RuntimeError, match="SERENA_REAL_CODEX"):
        find_real_binary("codex")


def test_launcher_registers_and_removes_codex_lease(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()

    class Record:
        mcp_url = "http://127.0.0.1:9000/mcp"
        dashboard_url = "http://127.0.0.1:9001"

    class Proc:
        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pass

    commands = []
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.ensure_server", lambda scope, lease: Record())
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.find_real_binary", lambda client: "/opt/homebrew/bin/codex")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._remove_lease_and_shutdown_if_empty", lambda scope, lease_id: None)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.subprocess.Popen", lambda cmd, cwd=None: commands.append(cmd) or Proc())

    from local_dev.serena_mcp_management.serena_agent_launcher import main

    assert main(["--help"]) == 0
    assert commands[0][0] == "/opt/homebrew/bin/codex"
    assert commands[0][1] == "-c"


def test_signal_handler_defers_registry_cleanup_to_finally(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()

    class Record:
        mcp_url = "http://127.0.0.1:9000/mcp"
        dashboard_url = "http://127.0.0.1:9001"

    handlers = {}
    events = []

    class Proc:
        def poll(self):
            return None

        def wait(self):
            events.append("wait-start")
            handlers[1](1, None)
            events.append("wait-end")
            return 0

        def terminate(self):
            events.append("terminate")

    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.ensure_server", lambda scope, lease: Record())
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.find_real_binary", lambda client: "/opt/homebrew/bin/codex")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.subprocess.Popen", lambda cmd, cwd=None: Proc())
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher.signal.signal", lambda signum, handler: handlers.setdefault(signum, handler))
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_agent_launcher._remove_lease_and_shutdown_if_empty", lambda scope, lease_id: events.append("remove"))

    from local_dev.serena_mcp_management.serena_agent_launcher import main

    assert main([]) == 0
    assert events == ["wait-start", "terminate", "wait-end", "remove"]


def test_launcher_does_not_own_agent_cleanup():
    import local_dev.serena_mcp_management.serena_agent_launcher as launcher

    assert not hasattr(launcher, "cleanup_before_launch")
    assert not hasattr(launcher, "format_cleanup_status")
