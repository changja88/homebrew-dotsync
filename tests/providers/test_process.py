import json
import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from dotsync.providers.base import ProviderError
from dotsync.providers.process import (
    JsonRpcProcess,
    PtySession,
    provider_environment,
    resolve_executable,
    run_checked,
)


RPC_FIXTURE = r'''import json
import os
import sys
import time

mode = sys.argv[1]
pid_file = sys.argv[2]
with open(pid_file, "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))

if mode == "respond":
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        print(json.dumps({
            "jsonrpc": "2.0",
            "method": "fixture/progress",
            "params": {"state": "waiting"},
        }), flush=True)
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {"id": message["id"], "method": message["method"]},
        }), flush=True)
elif mode == "observe-notify":
    notification = json.loads(sys.stdin.readline())
    request = json.loads(sys.stdin.readline())
    print(json.dumps({
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {
            "notify_method": notification["method"],
            "has_notify_id": "id" in notification,
        },
    }), flush=True)
elif mode == "malformed":
    sys.stdin.readline()
    print("sentinel-malformed-secret{", flush=True)
    time.sleep(10)
elif mode == "invalid-utf8":
    sys.stdin.readline()
    sys.stdout.buffer.write(
        b'{"jsonrpc":"2.0","id":1,"result":"sentinel-invalid-secret-\xff"}\n'
    )
    sys.stdout.buffer.flush()
    time.sleep(10)
elif mode == "oversized":
    sys.stdin.readline()
    print("sentinel-oversized-secret" + ("x" * (1024 * 1024)), flush=True)
    time.sleep(10)
elif mode == "stall":
    sys.stdin.readline()
    time.sleep(10)
elif mode == "exit":
    sys.stdin.readline()
    raise SystemExit(9)
'''

PTY_FIXTURE = r'''import os
import sys
import time

mode = sys.argv[1]
pid_file = sys.argv[2]
with open(pid_file, "w", encoding="utf-8") as handle:
    handle.write(str(os.getpid()))

if mode == "ansi":
    print("\x1b[31mREADY\x1b[0m", flush=True)
elif mode == "argument":
    print("ARG=" + sys.argv[3], flush=True)
    print("INPUT=" + input(), flush=True)
elif mode == "stall":
    print("sentinel-pty-timeout-secret", flush=True)
    time.sleep(10)
elif mode == "oversized":
    os.write(1, b"sentinel-pty-output-secret" + (b"x" * (2 * 1024 * 1024)))
    time.sleep(10)
elif mode == "exit":
    print("sentinel-pty-exit-secret", flush=True)
    raise SystemExit(11)
'''


def _rpc_command(tmp_path: Path, mode: str) -> tuple[list[Path | str], Path]:
    script = tmp_path / "rpc_fixture.py"
    script.write_text(RPC_FIXTURE, encoding="utf-8")
    pid_file = tmp_path / f"{mode}.pid"
    return [Path(sys.executable).resolve(), script, mode, pid_file], pid_file


def _pty_command(
    tmp_path: Path, mode: str, *arguments: str
) -> tuple[list[Path | str], Path]:
    script = tmp_path / "pty_fixture.py"
    script.write_text(PTY_FIXTURE, encoding="utf-8")
    pid_file = tmp_path / f"pty-{mode}.pid"
    return [
        Path(sys.executable).resolve(),
        script,
        mode,
        pid_file,
        *arguments,
    ], pid_file


def _assert_process_stopped(pid_file: Path) -> None:
    pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_claude_environment_removes_api_billing_and_default_profile(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://must-not-leak.example")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/Users/x/.claude")

    env = provider_environment("claude", tmp_path / "account")

    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "account/home")
    assert env["CLAUDE_CODE_TMPDIR"] == str(tmp_path / "account/tmp")


def test_codex_environment_removes_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://must-not-leak.example")
    monkeypatch.setenv("CODEX_HOME", "/Users/x/.codex")

    env = provider_environment("codex", tmp_path / "account")

    assert "OPENAI_API_KEY" not in env
    assert "OPENAI_BASE_URL" not in env
    assert env["CODEX_HOME"] == str(tmp_path / "account/home")


def test_provider_environment_copies_only_documented_ordinary_variables(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("PATH", "/controlled/bin")
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example")
    monkeypatch.setenv("SSL_CERT_FILE", "/controlled/cert.pem")
    monkeypatch.setenv("LC_CTYPE", "UTF-8")
    monkeypatch.setenv("LC_SENTINEL_SECRET", "must-not-leak")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/private/tmp/sentinel-agent.sock")
    monkeypatch.setenv("UNRELATED_SENTINEL_SECRET", "must-not-leak")

    env = provider_environment("codex", tmp_path / "account")

    assert env["PATH"] == "/controlled/bin"
    assert env["LANG"] == "ko_KR.UTF-8"
    assert env["HTTPS_PROXY"] == "http://proxy.example"
    assert env["SSL_CERT_FILE"] == "/controlled/cert.pem"
    assert env["LC_CTYPE"] == "UTF-8"
    assert "LC_SENTINEL_SECRET" not in env
    assert "SSH_AUTH_SOCK" not in env
    assert "UNRELATED_SENTINEL_SECRET" not in env
    assert env["HOME"] == str(tmp_path / "account/home")
    assert env["TMPDIR"] == str(tmp_path / "account/tmp")


def test_provider_environment_rejects_unknown_provider(tmp_path):
    with pytest.raises(ValueError, match="unsupported provider"):
        provider_environment("other", tmp_path / "account")


def test_resolve_executable_returns_verified_absolute_regular_file(tmp_path):
    executable = tmp_path / "fixture-tool"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    resolved = resolve_executable("fixture-tool", path=str(tmp_path))

    assert resolved == executable.resolve()
    assert resolved.is_absolute()


@pytest.mark.parametrize("kind", ["missing", "directory", "not-executable"])
def test_resolve_executable_rejects_invalid_targets(tmp_path, kind):
    target = tmp_path / "fixture-tool"
    if kind == "directory":
        target.mkdir()
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
    elif kind == "not-executable":
        target.write_text("fixture", encoding="utf-8")

    with pytest.raises(ProviderError) as error:
        resolve_executable("fixture-tool", path=str(tmp_path))

    assert error.value.code == "executable_unavailable"
    assert str(tmp_path) not in error.value.safe_message


def test_run_checked_preserves_arguments_and_uses_requested_cwd(tmp_path):
    argument = "value with spaces; $(must-not-run)"

    completed = run_checked(
        [
            Path(sys.executable).resolve(),
            "-c",
            "import json, os, sys; print(json.dumps([os.getcwd(), sys.argv[1]]))",
            argument,
        ],
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=3.0,
    )

    assert completed.returncode == 0
    assert completed.stdout == f'["{tmp_path}", "{argument}"]\n'


def test_run_checked_failure_does_not_expose_output_or_arguments(tmp_path):
    secret = "sentinel-secret-must-not-leak"

    with pytest.raises(ProviderError) as error:
        run_checked(
            [
                Path(sys.executable).resolve(),
                "-c",
                "import sys; print(sys.argv[1]); "
                "print(sys.argv[1], file=sys.stderr); raise SystemExit(7)",
                secret,
            ],
            env={"PATH": os.environ.get("PATH", "")},
            cwd=tmp_path,
            timeout=3.0,
        )

    assert error.value.code == "process_failed"
    assert "exit status 7" in error.value.safe_message
    assert secret not in error.value.safe_message


def test_run_checked_requires_absolute_verified_argv_zero(tmp_path):
    with pytest.raises(ProviderError) as error:
        run_checked(
            ["python3", "-c", "raise SystemExit(0)"],
            env={"PATH": os.environ.get("PATH", "")},
            cwd=tmp_path,
            timeout=3.0,
        )

    assert error.value.code == "invalid_executable"


def test_json_rpc_process_matches_sequential_responses_and_reports_notifications(
    tmp_path,
):
    command, _ = _rpc_command(tmp_path, "respond")
    notifications = []

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=2.0,
        on_notification=lambda method, params: notifications.append(
            (method, params)
        ),
    ) as rpc:
        first = rpc.request("first/read", {})
        second = rpc.request("second/read", {})

    assert first == {"id": 1, "method": "first/read"}
    assert second == {"id": 2, "method": "second/read"}
    assert notifications == [
        ("fixture/progress", {"state": "waiting"}),
        ("fixture/progress", {"state": "waiting"}),
    ]


def test_json_rpc_notify_sends_a_message_without_an_id(tmp_path):
    command, _ = _rpc_command(tmp_path, "observe-notify")

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=2.0,
    ) as rpc:
        rpc.notify("initialized", {})
        result = rpc.request("account/read", {})

    assert result == {"notify_method": "initialized", "has_notify_id": False}


@pytest.mark.parametrize(
    ("mode", "expected_code", "secret"),
    [
        ("malformed", "rpc_protocol_error", "sentinel-malformed-secret"),
        ("invalid-utf8", "rpc_protocol_error", "sentinel-invalid-secret"),
        ("oversized", "rpc_line_too_large", "sentinel-oversized-secret"),
    ],
)
def test_json_rpc_rejects_unsafe_lines_without_exposing_them(
    tmp_path, mode, expected_code, secret
):
    command, pid_file = _rpc_command(tmp_path, mode)

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=2.0,
    ) as rpc:
        with pytest.raises(ProviderError) as error:
            rpc.request("account/rateLimits/read", {})

    assert error.value.code == expected_code
    assert "account/rateLimits/read" in error.value.safe_message
    assert "exit status" in error.value.safe_message
    assert secret not in error.value.safe_message
    _assert_process_stopped(pid_file)


def test_json_rpc_reports_child_exit_status_without_child_output(tmp_path):
    command, pid_file = _rpc_command(tmp_path, "exit")

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=2.0,
    ) as rpc:
        with pytest.raises(ProviderError) as error:
            rpc.request("account/read", {"secret": "sentinel-request-secret"})

    assert error.value.code == "rpc_exited"
    assert "account/read" in error.value.safe_message
    assert "exit status 9" in error.value.safe_message
    assert "sentinel-request-secret" not in error.value.safe_message
    _assert_process_stopped(pid_file)


def test_json_rpc_timeout_terminates_and_reaps_child(tmp_path):
    command, pid_file = _rpc_command(tmp_path, "stall")

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=0.1,
    ) as rpc:
        with pytest.raises(ProviderError) as error:
            rpc.request("account/read", {})

    assert error.value.code == "rpc_timeout"
    assert "account/read" in error.value.safe_message
    assert "exit status" in error.value.safe_message
    _assert_process_stopped(pid_file)


def test_json_rpc_wait_observes_cancellation_and_reaps_child(tmp_path):
    command, pid_file = _rpc_command(tmp_path, "stall")
    cancel_event = threading.Event()
    timer = threading.Timer(0.1, cancel_event.set)
    started = time.monotonic()
    timer.start()
    try:
        with JsonRpcProcess(
            command,
            env={"PATH": os.environ.get("PATH", "")},
            cwd=tmp_path,
            timeout=5.0,
        ) as rpc:
            with pytest.raises(ProviderError) as error:
                rpc.request("account/read", {}, cancel_event=cancel_event)
    finally:
        timer.cancel()

    assert time.monotonic() - started < 2.0
    assert error.value.code == "rpc_cancelled"
    assert "account/read" in error.value.safe_message
    _assert_process_stopped(pid_file)


def test_pty_session_captures_ansi_output_until_predicate_matches(tmp_path):
    command, pid_file = _pty_command(tmp_path, "ansi")

    with PtySession(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
    ) as session:
        output = session.read_until(lambda value: "READY" in value, 2.0)

    assert "\x1b[31mREADY\x1b[0m" in output
    _assert_process_stopped(pid_file)


def test_pty_session_preserves_literal_arguments_and_accepts_one_line(tmp_path):
    marker = tmp_path / "must-not-exist"
    argument = f"value with spaces; $(touch {marker})"
    command, pid_file = _pty_command(tmp_path, "argument", argument)

    with PtySession(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
    ) as session:
        first = session.read_until(lambda value: "ARG=" in value, 2.0)
        session.write_line("literal input; $(false)")
        output = session.read_until(lambda value: "INPUT=" in value, 2.0)

    assert argument in first
    assert "INPUT=literal input; $(false)" in output
    assert not marker.exists()
    _assert_process_stopped(pid_file)


def test_pty_session_timeout_terminates_and_reaps_child_without_output_leak(
    tmp_path,
):
    command, pid_file = _pty_command(tmp_path, "stall")

    with PtySession(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
    ) as session:
        with pytest.raises(ProviderError) as error:
            session.read_until(lambda value: "never" in value, 0.1)

    assert error.value.code == "pty_timeout"
    assert "sentinel-pty-timeout-secret" not in error.value.safe_message
    _assert_process_stopped(pid_file)


def test_pty_session_wait_observes_cancellation(tmp_path):
    command, pid_file = _pty_command(tmp_path, "stall")
    cancel_event = threading.Event()
    timer = threading.Timer(0.1, cancel_event.set)
    started = time.monotonic()
    timer.start()
    try:
        with PtySession(
            command,
            env={"PATH": os.environ.get("PATH", "")},
            cwd=tmp_path,
        ) as session:
            with pytest.raises(ProviderError) as error:
                session.read_until(
                    lambda value: "never" in value,
                    5.0,
                    cancel_event=cancel_event,
                )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 2.0
    assert error.value.code == "pty_cancelled"
    _assert_process_stopped(pid_file)


def test_pty_session_enforces_total_output_ceiling_without_output_leak(tmp_path):
    command, pid_file = _pty_command(tmp_path, "oversized")

    with PtySession(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
    ) as session:
        with pytest.raises(ProviderError) as error:
            session.read_until(lambda value: False, 2.0)

    assert error.value.code == "pty_output_limit"
    assert "sentinel-pty-output-secret" not in error.value.safe_message
    _assert_process_stopped(pid_file)


def test_pty_session_reports_exit_status_without_output_leak(tmp_path):
    command, pid_file = _pty_command(tmp_path, "exit")

    with PtySession(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
    ) as session:
        with pytest.raises(ProviderError) as error:
            session.read_until(lambda value: "never" in value, 2.0)

    assert error.value.code == "pty_exited"
    assert "exit status 11" in error.value.safe_message
    assert "sentinel-pty-exit-secret" not in error.value.safe_message
    _assert_process_stopped(pid_file)
