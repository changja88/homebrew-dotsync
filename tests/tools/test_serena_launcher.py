import os

import pytest

from tools.serena_agent_launcher import build_child_command, find_real_binary, infer_client_type


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

    class Proc:
        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pass

    commands = []
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setattr("tools.serena_agent_launcher.ensure_server", lambda scope, lease: Record())
    monkeypatch.setattr("tools.serena_agent_launcher.find_real_binary", lambda client: "/opt/homebrew/bin/codex")
    monkeypatch.setattr("tools.serena_agent_launcher._remove_lease_and_shutdown_if_empty", lambda scope, lease_id: None)
    monkeypatch.setattr("tools.serena_agent_launcher.subprocess.Popen", lambda cmd, cwd=None: commands.append(cmd) or Proc())

    from tools.serena_agent_launcher import main

    assert main(["--help"]) == 0
    assert commands[0][0] == "/opt/homebrew/bin/codex"
    assert commands[0][1] == "-c"
