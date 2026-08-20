import json
import os
import pty
import selectors
import signal
import stat
import sys
import threading
import time
import traceback
from pathlib import Path

import pytest

import dotsync.providers.process as process_module
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
elif mode == "future-response":
    request = json.loads(sys.stdin.readline())
    print(json.dumps({
        "jsonrpc": "2.0",
        "id": request["id"] + 1,
        "result": "sentinel-future-response",
    }), flush=True)
    time.sleep(0.5)
elif mode == "duplicate-response":
    first = json.loads(sys.stdin.readline())
    with open(pid_file + ".first-read", "w", encoding="utf-8") as handle:
        handle.write("ready")
    json.loads(sys.stdin.readline())
    response = json.dumps({
        "jsonrpc": "2.0",
        "id": first["id"],
        "result": "sentinel-duplicate-response",
    })
    sys.stdout.write(response + "\n" + response + "\n")
    sys.stdout.flush()
    time.sleep(0.5)
elif mode == "allocated-unsent-response":
    first = json.loads(sys.stdin.readline())
    with open(pid_file + ".control", "r", encoding="utf-8") as handle:
        handle.readline()
    print(json.dumps({
        "jsonrpc": "2.0",
        "id": first["id"] + 1,
        "result": "sentinel-allocated-unsent-response",
    }), flush=True)
    with open(pid_file + ".ack", "w", encoding="utf-8") as handle:
        handle.write("emitted")
    sys.stdin.readline()
    time.sleep(0.5)
elif mode == "post-send-failure-response":
    with open(pid_file + ".control", "r", encoding="utf-8") as handle:
        handle.readline()
    print(json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": "sentinel-post-send-failure-response",
    }), flush=True)
    with open(pid_file + ".ack", "w", encoding="utf-8") as handle:
        handle.write("emitted")
    sys.stdin.readline()
    time.sleep(0.5)
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
elif mode == "blocked-stdin":
    time.sleep(0.5)
elif mode == "descendant-stall":
    import subprocess
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with open(pid_file + ".child", "w", encoding="utf-8") as handle:
        handle.write(str(child.pid))
    sys.stdin.readline()
    time.sleep(30)
elif mode == "exit":
    sys.stdin.readline()
    raise SystemExit(9)
elif mode == "remote-error":
    request = json.loads(sys.stdin.readline())
    code = json.loads(sys.argv[3])
    print(json.dumps({
        "jsonrpc": "2.0",
        "id": request["id"],
        "error": {
            "code": code,
            "message": "sentinel-remote-message",
            "data": {"token": "sentinel-remote-data"},
        },
    }), flush=True)
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
elif mode == "descendant-stall":
    import subprocess
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with open(pid_file + ".child", "w", encoding="utf-8") as handle:
        handle.write(str(child.pid))
    print("CHILD-READY", flush=True)
    time.sleep(30)
elif mode == "oversized":
    os.write(1, b"sentinel-pty-output-secret" + (b"x" * (2 * 1024 * 1024)))
    time.sleep(10)
elif mode == "exit":
    print("sentinel-pty-exit-secret", flush=True)
    raise SystemExit(11)
'''


def _rpc_command(
    tmp_path: Path, mode: str, *arguments: str
) -> tuple[list[Path | str], Path]:
    script = tmp_path / "rpc_fixture.py"
    script.write_text(RPC_FIXTURE, encoding="utf-8")
    pid_file = tmp_path / f"{mode}.pid"
    if mode in {"allocated-unsent-response", "post-send-failure-response"}:
        os.mkfifo(str(pid_file) + ".control")
        os.mkfifo(str(pid_file) + ".ack")
    return [
        Path(sys.executable).resolve(),
        script,
        mode,
        pid_file,
        *arguments,
    ], pid_file


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
    deadline = time.monotonic() + 1.0
    while _process_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _process_exists(pid)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _cleanup_process(pid_file: Path) -> None:
    if not pid_file.exists():
        return
    pid = int(pid_file.read_text(encoding="utf-8"))
    if _process_exists(pid):
        os.kill(pid, signal.SIGKILL)


def _wait_for_file(path: Path) -> None:
    deadline = time.monotonic() + 1.0
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists()


def _fd_is_open(file_descriptor: int) -> bool:
    try:
        os.fstat(file_descriptor)
    except OSError:
        return False
    return True


def _close_fd_if_open(file_descriptor: int) -> None:
    if _fd_is_open(file_descriptor):
        os.close(file_descriptor)


def _release_fifo_fixture(pid_file: Path) -> None:
    with open(str(pid_file) + ".control", "w", encoding="utf-8") as handle:
        handle.write("go\n")
    with open(str(pid_file) + ".ack", "r", encoding="utf-8") as handle:
        assert handle.read() == "emitted"


class _ControlledWriteLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._acquisitions = 0
        self.first_send_finished = threading.Event()
        self.second_send_blocked = threading.Event()
        self.allow_second_send = threading.Event()

    def __enter__(self):
        self._lock.acquire()
        self._acquisitions += 1
        if self._acquisitions == 2:
            self.second_send_blocked.set()
            assert self.allow_second_send.wait(timeout=1.0)
        return self

    def __exit__(self, *exc_info) -> None:
        if self._acquisitions == 1:
            self.first_send_finished.set()
        self._lock.release()


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


def test_run_checked_timeout_discards_unsafe_exception_state(tmp_path):
    secret = "sentinel-timeout-argument"

    with pytest.raises(ProviderError) as error:
        run_checked(
            [
                Path(sys.executable).resolve(),
                "-c",
                "import time; time.sleep(10)",
                secret,
            ],
            env={"PATH": os.environ.get("PATH", "")},
            cwd=tmp_path,
            timeout=0.05,
        )

    rendered = "".join(traceback.format_exception(error.value))
    assert error.value.code == "process_timeout"
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert secret not in rendered


def test_run_checked_rejects_invalid_utf8_without_retaining_child_output(tmp_path):
    secret = "sentinel-invalid-process-output"
    code = (
        "import os; "
        f"os.write(1, {secret.encode()!r} + bytes([255])); "
        "raise SystemExit(0)"
    )

    with pytest.raises(ProviderError) as error:
        run_checked(
            [Path(sys.executable).resolve(), "-c", code],
            env={"PATH": os.environ.get("PATH", "")},
            cwd=tmp_path,
            timeout=2.0,
        )

    rendered = "".join(traceback.format_exception(error.value))
    assert error.value.code == "process_output_invalid"
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert secret not in rendered


def test_run_checked_timeout_terminates_descendants(tmp_path):
    child_pid_file = tmp_path / "checked-child.pid"
    code = (
        "import pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGTERM, "
        "signal.SIG_IGN); time.sleep(30)'], stdin=subprocess.DEVNULL, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(30)"
    )

    try:
        with pytest.raises(ProviderError):
            run_checked(
                [Path(sys.executable).resolve(), "-c", code],
                env={"PATH": os.environ.get("PATH", "")},
                cwd=tmp_path,
                timeout=0.1,
            )

        _assert_process_stopped(child_pid_file)
    finally:
        _cleanup_process(child_pid_file)


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


@pytest.mark.parametrize(
    ("remote_code", "expected_code"),
    [
        (-32601, "rpc_method_not_found"),
        (-32600, "rpc_authentication_error"),
        (-32602, "rpc_invalid_params"),
        (-32001, "rpc_server_overloaded"),
        (-32603, "rpc_remote_error"),
    ],
)
def test_json_rpc_classifies_only_numeric_remote_error_code_safely(
    tmp_path, remote_code, expected_code
):
    command, pid_file = _rpc_command(
        tmp_path,
        "remote-error",
        json.dumps(remote_code),
    )

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=2.0,
    ) as rpc:
        with pytest.raises(ProviderError) as captured:
            rpc.request("account/rateLimits/read", {})

    rendered = "".join(traceback.format_exception(captured.value))
    assert captured.value.code == expected_code
    assert "sentinel-remote-message" not in rendered
    assert "sentinel-remote-data" not in rendered
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    _assert_process_stopped(pid_file)


def test_json_rpc_keeps_invalid_request_for_non_auth_method_unsupported(
    tmp_path,
):
    command, pid_file = _rpc_command(
        tmp_path,
        "remote-error",
        json.dumps(-32600),
    )

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=2.0,
    ) as rpc:
        with pytest.raises(ProviderError) as captured:
            rpc.request("initialize", {})

    assert captured.value.code == "rpc_invalid_request"
    _assert_process_stopped(pid_file)


@pytest.mark.parametrize("remote_code", [True, "-32601", None])
def test_json_rpc_rejects_malformed_remote_error_code_without_exposing_response(
    tmp_path, remote_code
):
    command, pid_file = _rpc_command(
        tmp_path,
        "remote-error",
        json.dumps(remote_code),
    )

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=2.0,
    ) as rpc:
        with pytest.raises(ProviderError) as captured:
            rpc.request("account/rateLimits/read", {})

    rendered = "".join(traceback.format_exception(captured.value))
    assert captured.value.code == "rpc_protocol_error"
    assert "sentinel-remote-message" not in rendered
    assert "sentinel-remote-data" not in rendered
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


def test_json_rpc_request_deadline_includes_blocked_transmission(tmp_path):
    command, pid_file = _rpc_command(tmp_path, "blocked-stdin")
    secret = "sentinel-blocked-request"
    started = time.monotonic()

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=0.05,
    ) as rpc:
        with pytest.raises(ProviderError) as error:
            rpc.request("account/read", {"value": ("x" * (2 * 1024 * 1024)) + secret})

    rendered = "".join(traceback.format_exception(error.value))
    assert time.monotonic() - started < 0.3
    assert error.value.code == "rpc_timeout"
    assert secret not in rendered
    _assert_process_stopped(pid_file)


def test_json_rpc_request_cancellation_includes_blocked_transmission(tmp_path):
    command, pid_file = _rpc_command(tmp_path, "blocked-stdin")
    cancel_event = threading.Event()
    timer = threading.Timer(0.05, cancel_event.set)
    started = time.monotonic()
    timer.start()
    try:
        with JsonRpcProcess(
            command,
            env={"PATH": os.environ.get("PATH", "")},
            cwd=tmp_path,
            timeout=2.0,
        ) as rpc:
            with pytest.raises(ProviderError) as error:
                rpc.request(
                    "account/read",
                    {"value": "x" * (2 * 1024 * 1024)},
                    cancel_event=cancel_event,
                )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 0.3
    assert error.value.code == "rpc_cancelled"
    _assert_process_stopped(pid_file)


def test_json_rpc_notification_uses_default_send_deadline(tmp_path):
    command, pid_file = _rpc_command(tmp_path, "blocked-stdin")
    started = time.monotonic()

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=0.05,
    ) as rpc:
        with pytest.raises(ProviderError) as error:
            rpc.notify("fixture/large", {"value": "x" * (2 * 1024 * 1024)})

    assert time.monotonic() - started < 0.3
    assert error.value.code == "rpc_timeout"
    _assert_process_stopped(pid_file)


def test_json_rpc_send_selector_failure_is_safe_and_closes_selector(
    monkeypatch, tmp_path
):
    command, _ = _rpc_command(tmp_path, "stall")

    class FailingSelector:
        def __init__(self) -> None:
            self.closed = False

        def register(self, *args, **kwargs):
            raise RuntimeError("sentinel-rpc-selector")

        def close(self) -> None:
            self.closed = True

    failing_selector = FailingSelector()

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=0.2,
    ) as rpc:
        monkeypatch.setattr(
            process_module.selectors,
            "DefaultSelector",
            lambda: failing_selector,
        )
        with pytest.raises(ProviderError) as error:
            rpc.request("account/read", {})

    rendered = "".join(traceback.format_exception(error.value))
    assert error.value.code == "rpc_send_failed"
    assert "sentinel-rpc-selector" not in rendered
    assert failing_selector.closed


def test_json_rpc_setup_base_exception_closes_selector_once_and_cleans_state(
    monkeypatch, tmp_path
):
    command, pid_file = _rpc_command(tmp_path, "post-send-failure-response")
    default_selector = process_module.selectors.DefaultSelector

    class SetupInterrupt(BaseException):
        pass

    class SelectorCloseInterrupt(BaseException):
        pass

    setup_interrupt = SetupInterrupt("fixture setup interruption")
    close_interrupt = SelectorCloseInterrupt("fixture close interruption")

    class SetupInterruptingSelector:
        def __init__(self) -> None:
            self.close_calls = 0

        def register(self, *args, **kwargs) -> None:
            raise setup_interrupt

        def close(self) -> None:
            self.close_calls += 1
            raise close_interrupt

    interrupting_selector = SetupInterruptingSelector()
    secret = "sentinel-setup-exception-params"

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=0.3,
    ) as rpc:
        monkeypatch.setattr(
            process_module.selectors,
            "DefaultSelector",
            lambda: interrupting_selector,
        )
        with pytest.raises(SetupInterrupt) as interrupted:
            rpc.request("first/read", {"value": secret})

        monkeypatch.setattr(
            process_module.selectors,
            "DefaultSelector",
            default_selector,
        )
        _release_fifo_fixture(pid_file)
        with rpc._condition:
            assert rpc._condition.wait_for(
                lambda: rpc._failure is not None,
                timeout=1.0,
            )
        with pytest.raises(ProviderError) as protocol_error:
            rpc.request("second/read", {})

    protocol_rendered = "".join(
        traceback.format_exception(protocol_error.value)
    )
    assert interrupted.value is setup_interrupt
    assert interrupting_selector.close_calls == 1
    assert protocol_error.value.code == "rpc_protocol_error"
    assert secret not in protocol_rendered
    assert "sentinel-post-send-failure-response" not in protocol_rendered
    _assert_process_stopped(pid_file)


def test_json_rpc_active_send_failure_is_safe_and_cleans_request_state(
    monkeypatch, tmp_path
):
    command, pid_file = _rpc_command(tmp_path, "post-send-failure-response")
    default_selector = process_module.selectors.DefaultSelector

    class ActiveFailingSelector:
        def __init__(self) -> None:
            self.closed = False

        def register(self, *args, **kwargs) -> None:
            return None

        def select(self, timeout=None):
            raise RuntimeError("sentinel-active-selector-select")

        def close(self) -> None:
            self.closed = True
            raise RuntimeError("sentinel-active-selector-close")

    failing_selector = ActiveFailingSelector()
    secret = "sentinel-active-send-params"

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=0.3,
    ) as rpc:
        monkeypatch.setattr(
            process_module.selectors,
            "DefaultSelector",
            lambda: failing_selector,
        )
        with pytest.raises(ProviderError) as send_error:
            rpc.request("first/read", {"value": secret})

        monkeypatch.setattr(
            process_module.selectors,
            "DefaultSelector",
            default_selector,
        )
        _release_fifo_fixture(pid_file)
        with rpc._condition:
            assert rpc._condition.wait_for(
                lambda: rpc._failure is not None,
                timeout=1.0,
            )
        with pytest.raises(ProviderError) as protocol_error:
            rpc.request("second/read", {})

    send_rendered = "".join(
        traceback.format_exception(send_error.value)
    )
    protocol_rendered = "".join(
        traceback.format_exception(protocol_error.value)
    )
    assert send_error.value.code == "rpc_send_failed"
    assert send_error.value.__cause__ is None
    assert send_error.value.__context__ is None
    assert failing_selector.closed
    assert "sentinel-active-selector-select" not in send_rendered
    assert "sentinel-active-selector-close" not in send_rendered
    assert secret not in send_rendered
    assert protocol_error.value.code == "rpc_protocol_error"
    assert "sentinel-post-send-failure-response" not in protocol_rendered
    _assert_process_stopped(pid_file)


def test_json_rpc_base_exception_during_send_always_closes_selector_and_cleans_state(
    monkeypatch, tmp_path
):
    command, pid_file = _rpc_command(tmp_path, "post-send-failure-response")
    default_selector = process_module.selectors.DefaultSelector

    class ActiveSendInterrupt(BaseException):
        pass

    class SelectorCloseInterrupt(BaseException):
        pass

    active_interrupt = ActiveSendInterrupt("fixture active interruption")
    close_interrupt = SelectorCloseInterrupt("fixture close interruption")

    class InterruptingSelector:
        def __init__(self) -> None:
            self.closed = False

        def register(self, *args, **kwargs) -> None:
            return None

        def select(self, timeout=None):
            raise active_interrupt

        def close(self) -> None:
            self.closed = True
            raise close_interrupt

    interrupting_selector = InterruptingSelector()
    secret = "sentinel-base-exception-send-params"

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=0.3,
    ) as rpc:
        monkeypatch.setattr(
            process_module.selectors,
            "DefaultSelector",
            lambda: interrupting_selector,
        )
        with pytest.raises(ActiveSendInterrupt) as interrupted:
            rpc.request("first/read", {"value": secret})

        monkeypatch.setattr(
            process_module.selectors,
            "DefaultSelector",
            default_selector,
        )
        _release_fifo_fixture(pid_file)
        with rpc._condition:
            assert rpc._condition.wait_for(
                lambda: rpc._failure is not None,
                timeout=1.0,
            )
        with pytest.raises(ProviderError) as protocol_error:
            rpc.request("second/read", {})

    protocol_rendered = "".join(
        traceback.format_exception(protocol_error.value)
    )
    assert interrupted.value is active_interrupt
    assert interrupting_selector.closed
    assert protocol_error.value.code == "rpc_protocol_error"
    assert secret not in protocol_rendered
    assert "sentinel-post-send-failure-response" not in protocol_rendered
    _assert_process_stopped(pid_file)


def test_json_rpc_base_exception_from_selector_close_cleans_request_state(
    monkeypatch, tmp_path
):
    command, pid_file = _rpc_command(tmp_path, "respond")
    default_selector = process_module.selectors.DefaultSelector
    real_selector = default_selector()

    class SelectorCloseInterrupt(BaseException):
        pass

    close_interrupt = SelectorCloseInterrupt("fixture close interruption")

    class CloseInterruptingSelector:
        def __init__(self) -> None:
            self.closed = False

        def register(self, *args, **kwargs):
            return real_selector.register(*args, **kwargs)

        def select(self, timeout=None):
            return real_selector.select(timeout)

        def close(self) -> None:
            self.closed = True
            real_selector.close()
            raise close_interrupt

    interrupting_selector = CloseInterruptingSelector()
    secret = "sentinel-close-exception-params"

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=0.3,
    ) as rpc:
        monkeypatch.setattr(
            process_module.selectors,
            "DefaultSelector",
            lambda: interrupting_selector,
        )
        with pytest.raises(SelectorCloseInterrupt) as interrupted:
            rpc.request("first/read", {"value": secret})

        monkeypatch.setattr(
            process_module.selectors,
            "DefaultSelector",
            default_selector,
        )
        with rpc._condition:
            assert rpc._condition.wait_for(
                lambda: rpc._failure is not None,
                timeout=1.0,
            )
        with pytest.raises(ProviderError) as protocol_error:
            rpc.request("second/read", {})

    protocol_rendered = "".join(
        traceback.format_exception(protocol_error.value)
    )
    assert interrupted.value is close_interrupt
    assert interrupting_selector.closed
    assert protocol_error.value.code == "rpc_protocol_error"
    assert secret not in protocol_rendered
    _assert_process_stopped(pid_file)


def test_json_rpc_rejects_future_response_id_without_retaining_frame(tmp_path):
    command, pid_file = _rpc_command(tmp_path, "future-response")

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=0.2,
    ) as rpc:
        with pytest.raises(ProviderError) as error:
            rpc.request("account/read", {})

    rendered = "".join(traceback.format_exception(error.value))
    assert error.value.code == "rpc_protocol_error"
    assert "sentinel-future-response" not in rendered
    _assert_process_stopped(pid_file)


def test_json_rpc_rejects_duplicate_response_id_with_pending_requests(tmp_path):
    command, pid_file = _rpc_command(tmp_path, "duplicate-response")
    first_read = Path(str(pid_file) + ".first-read")
    first_outcome = []

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=0.3,
    ) as rpc:
        def request_first() -> None:
            try:
                first_outcome.append(rpc.request("first/read", {}))
            except ProviderError as error:
                first_outcome.append(error)

        thread = threading.Thread(target=request_first)
        thread.start()
        _wait_for_file(first_read)
        with pytest.raises(ProviderError) as error:
            rpc.request("second/read", {})
        thread.join(timeout=1.0)

    rendered = "".join(traceback.format_exception(error.value))
    assert not thread.is_alive()
    assert first_outcome
    assert error.value.code == "rpc_protocol_error"
    assert "sentinel-duplicate-response" not in rendered
    _assert_process_stopped(pid_file)


def test_json_rpc_rejects_response_for_allocated_but_unsent_request(tmp_path):
    command, pid_file = _rpc_command(tmp_path, "allocated-unsent-response")
    controlled_lock = _ControlledWriteLock()
    first_outcome = []
    second_outcome = []

    with JsonRpcProcess(
        command,
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
        timeout=0.5,
    ) as rpc:
        rpc._write_lock = controlled_lock

        def request_first() -> None:
            try:
                first_outcome.append(rpc.request("first/read", {}))
            except ProviderError as error:
                first_outcome.append(error)

        def request_second() -> None:
            try:
                second_outcome.append(rpc.request("second/read", {}))
            except ProviderError as error:
                second_outcome.append(error)

        first_thread = threading.Thread(target=request_first)
        first_thread.start()
        assert controlled_lock.first_send_finished.wait(timeout=1.0)
        second_thread = threading.Thread(target=request_second)
        second_thread.start()
        assert controlled_lock.second_send_blocked.wait(timeout=1.0)
        _release_fifo_fixture(pid_file)
        with rpc._condition:
            assert rpc._condition.wait_for(
                lambda: rpc._failure is not None or bool(rpc._responses),
                timeout=1.0,
            )
        controlled_lock.allow_second_send.set()
        second_thread.join(timeout=1.0)
        first_thread.join(timeout=1.0)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert first_outcome
    assert second_outcome
    assert isinstance(second_outcome[0], ProviderError)
    assert second_outcome[0].code == "rpc_protocol_error"
    rendered = "".join(traceback.format_exception(second_outcome[0]))
    assert "sentinel-allocated-unsent-response" not in rendered
    _assert_process_stopped(pid_file)


def test_json_rpc_timeout_terminates_descendants(tmp_path):
    command, pid_file = _rpc_command(tmp_path, "descendant-stall")
    child_pid_file = Path(str(pid_file) + ".child")

    try:
        with JsonRpcProcess(
            command,
            env={"PATH": os.environ.get("PATH", "")},
            cwd=tmp_path,
            timeout=0.1,
        ) as rpc:
            with pytest.raises(ProviderError):
                rpc.request("account/read", {})

        _assert_process_stopped(child_pid_file)
    finally:
        _cleanup_process(child_pid_file)


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


def test_pty_session_timeout_terminates_descendants(tmp_path):
    command, pid_file = _pty_command(tmp_path, "descendant-stall")
    child_pid_file = Path(str(pid_file) + ".child")

    try:
        with PtySession(
            command,
            env={"PATH": os.environ.get("PATH", "")},
            cwd=tmp_path,
        ) as session:
            session.read_until(lambda output: "CHILD-READY" in output, 2.0)
            with pytest.raises(ProviderError):
                session.read_until(lambda output: "never" in output, 0.1)

        _assert_process_stopped(child_pid_file)
    finally:
        _cleanup_process(child_pid_file)


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


def test_pty_startup_value_error_closes_openpty_descriptors(
    monkeypatch, tmp_path
):
    master_fd, slave_fd = pty.openpty()
    monkeypatch.setattr(
        process_module.pty,
        "openpty",
        lambda: (master_fd, slave_fd),
    )
    session = PtySession(
        [Path(sys.executable).resolve(), "sentinel-argument\0invalid"],
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
    )

    try:
        with pytest.raises(ProviderError) as error:
            session.__enter__()

        rendered = "".join(traceback.format_exception(error.value))
        assert error.value.code == "pty_start_failed"
        assert "sentinel-argument" not in rendered
        assert not _fd_is_open(master_fd)
        assert not _fd_is_open(slave_fd)
    finally:
        _close_fd_if_open(master_fd)
        _close_fd_if_open(slave_fd)


def test_pty_selector_registration_failure_closes_all_owned_resources(
    monkeypatch, tmp_path
):
    master_fd, slave_fd = pty.openpty()
    real_selector = selectors.DefaultSelector()

    class FailingSelector:
        def __init__(self) -> None:
            self.closed = False

        def register(self, *args, **kwargs):
            raise RuntimeError("sentinel-selector-registration")

        def close(self) -> None:
            self.closed = True
            real_selector.close()

    failing_selector = FailingSelector()
    monkeypatch.setattr(
        process_module.pty,
        "openpty",
        lambda: (master_fd, slave_fd),
    )
    monkeypatch.setattr(
        process_module.selectors,
        "DefaultSelector",
        lambda: failing_selector,
    )
    session = PtySession(
        [Path(sys.executable).resolve(), "-c", "import time; time.sleep(10)"],
        env={"PATH": os.environ.get("PATH", "")},
        cwd=tmp_path,
    )

    try:
        with pytest.raises(ProviderError) as error:
            session.__enter__()

        rendered = "".join(traceback.format_exception(error.value))
        assert error.value.code == "pty_start_failed"
        assert "sentinel-selector-registration" not in rendered
        assert failing_selector.closed
        assert not _fd_is_open(master_fd)
        assert not _fd_is_open(slave_fd)
    finally:
        failing_selector.close()
        _close_fd_if_open(master_fd)
        _close_fd_if_open(slave_fd)
