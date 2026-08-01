from __future__ import annotations

import json
import subprocess

from local_dev.serena_mcp_management import claude_reset
from local_dev.serena_mcp_management.memory_management import ClientProcess


class CommandRecorder:
    def __init__(
        self,
        responses: dict[tuple[str, ...], tuple[int, str, str]],
    ) -> None:
        self.responses = responses
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def __call__(self, command, **kwargs):
        key = tuple(command)
        self.calls.append((key, kwargs))
        code, stdout, stderr = self.responses.get(key, (0, "", ""))
        return subprocess.CompletedProcess(command, code, stdout, stderr)


def test_result_succeeds_only_without_error():
    assert claude_reset.ClaudeResetResult().succeeded is True
    assert claude_reset.ClaudeResetResult(error="failed").succeeded is False


def test_missing_official_purge_capability_fails_before_mutation(
    monkeypatch,
    tmp_path,
):
    config_dir = tmp_path / ".claude"
    config_dir.mkdir()
    settings = config_dir / "settings.json"
    settings.write_text('{"theme":"dark"}', encoding="utf-8")
    generated = config_dir / "plans/plan.md"
    generated.parent.mkdir()
    generated.write_text("keep until reset starts", encoding="utf-8")
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --dry-run",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=recorder,
    )

    assert result.succeeded is False
    assert "--yes" in (result.error or "")
    assert settings.read_text(encoding="utf-8") == '{"theme":"dark"}'
    assert generated.read_text(encoding="utf-8") == "keep until reset starts"
    assert [call[0] for call in recorder.calls] == [
        ("/real/claude", "project", "purge", "--help"),
    ]


def test_invalid_custom_global_json_fails_before_capability_probe(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    global_config = config_dir / ".claude.json"
    global_config.write_text("not-json", encoding="utf-8")
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --yes",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=recorder,
    )

    assert result.succeeded is False
    assert str(global_config) in (result.error or "")
    assert recorder.calls == []


def test_capability_probe_preserves_custom_config_environment(tmp_path):
    config_dir = tmp_path / ".claude-custom"
    config_dir.mkdir()
    (config_dir / ".claude.json").write_text(
        json.dumps({"theme": "dark"}),
        encoding="utf-8",
    )
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --yes",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=config_dir,
        real_claude_binary="/real/claude",
        run_command=recorder,
    )

    assert result.succeeded is True
    assert recorder.calls[0][1]["env"]["CLAUDE_CONFIG_DIR"] == str(config_dir)


def test_broad_claude_config_root_fails_before_capability_probe(tmp_path):
    recorder = CommandRecorder(
        {
            ("/real/claude", "project", "purge", "--help"): (
                0,
                "Options: --all --yes",
                "",
            ),
        }
    )

    result = claude_reset.reset_all_claude_data(
        home=tmp_path,
        claude_config_dir=tmp_path,
        real_claude_binary="/real/claude",
        run_command=recorder,
    )

    assert result.succeeded is False
    assert "too broad" in (result.error or "")
    assert recorder.calls == []


def test_runtime_quiescence_stops_daemon_then_identity_pinned_cli():
    events: list[str] = []
    process = ClientProcess(
        pid=4242,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )
    scans = iter(((process,), (process,), ()))

    def run_command(command, **kwargs):
        assert command == ["/real/claude", "daemon", "stop", "--any"]
        assert kwargs["env"] == {"CLAUDE_CONFIG_DIR": "/tmp/claude"}
        events.append("daemon")
        return subprocess.CompletedProcess(command, 0, "", "")

    def process_scanner(*args, **kwargs):
        assert args == ("claude",)
        events.append("scan")
        return next(scans)

    termination = claude_reset._terminate_claude_runtimes(
        real_claude_binary="/real/claude",
        environment={"CLAUDE_CONFIG_DIR": "/tmp/claude"},
        run_command=run_command,
        process_scanner=process_scanner,
        identity_reader=lambda pid: "start-1",
        process_terminator=lambda pid, **kwargs: events.append(
            f"terminate:{pid}:{kwargs['expected_identity']}"
        ),
        process_alive=lambda pid: False,
    )

    assert termination.error is None
    assert termination.terminated == 1
    assert termination.warnings == ()
    assert events[0] == "daemon"
    assert events.count("scan") == 3
    assert "terminate:4242:start-1" in events


def test_runtime_quiescence_reports_process_identity_inspection_error():
    process = ClientProcess(
        pid=4242,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )

    def identity_reader(pid):
        raise OSError("ps unavailable")

    termination = claude_reset._terminate_claude_runtimes(
        real_claude_binary="/real/claude",
        environment={},
        run_command=lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            "",
            "",
        ),
        process_scanner=lambda *args, **kwargs: (process,),
        identity_reader=identity_reader,
        process_terminator=lambda *args, **kwargs: None,
        process_alive=lambda pid: False,
    )

    assert termination.error is not None
    assert "identity" in (termination.error or "")
    assert "ps unavailable" in (termination.error or "")


def test_runtime_quiescence_reports_post_termination_liveness_error():
    process = ClientProcess(
        pid=4242,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )
    scans = iter(((process,), (process,)))

    def process_alive(pid):
        raise OSError("kill probe unavailable")

    termination = claude_reset._terminate_claude_runtimes(
        real_claude_binary="/real/claude",
        environment={},
        run_command=lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            "",
            "",
        ),
        process_scanner=lambda *args, **kwargs: next(scans),
        identity_reader=lambda pid: "start-1",
        process_terminator=lambda *args, **kwargs: None,
        process_alive=process_alive,
    )

    assert termination.error is not None
    assert "liveness" in termination.error
    assert "kill probe unavailable" in termination.error


def test_runtime_quiescence_rejects_four_consecutive_respawns():
    process = ClientProcess(
        pid=4242,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )

    termination = claude_reset._terminate_claude_runtimes(
        real_claude_binary="/real/claude",
        environment={},
        run_command=lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            "",
            "",
        ),
        process_scanner=lambda *args, **kwargs: (process,),
        identity_reader=lambda pid: "start-1",
        process_terminator=lambda *args, **kwargs: None,
        process_alive=lambda pid: False,
    )

    assert termination.terminated == 4
    assert termination.error is not None
    assert "respawning" in termination.error


def test_runtime_quiescence_keeps_daemon_failure_as_warning_when_scan_is_empty():
    termination = claude_reset._terminate_claude_runtimes(
        real_claude_binary="/real/claude",
        environment={},
        run_command=lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            1,
            "",
            "daemon unavailable",
        ),
        process_scanner=lambda *args, **kwargs: (),
        identity_reader=lambda pid: "unused",
        process_terminator=lambda *args, **kwargs: None,
        process_alive=lambda pid: False,
    )

    assert termination.error is None
    assert termination.terminated == 0
    assert termination.warnings == (
        "could not stop Claude daemon: daemon unavailable",
    )
