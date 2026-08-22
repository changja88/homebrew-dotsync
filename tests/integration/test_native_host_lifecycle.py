from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading

import pytest

from dotsync.accounts import AccountStore
from dotsync.app_paths import AppPaths


pytestmark = pytest.mark.no_subprocess_block


_FIXTURE_CODEX = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import signal
import sys
import threading

home = Path(os.environ["CODEX_HOME"])
started = home / "provider-started.fifo"
release = home / "provider-release.fifo"
exited = home / "provider-exited.fifo"
write_lock = threading.Lock()

def signal_exit(signum, _frame):
    raise SystemExit(128 + signum)

signal.signal(signal.SIGTERM, signal_exit)
signal.signal(signal.SIGINT, signal_exit)

def send(value):
    encoded = json.dumps(value, separators=(",", ":")) + "\n"
    with write_lock:
        sys.stdout.write(encoded)
        sys.stdout.flush()

def refresh(request_id):
    with started.open("w", encoding="ascii") as stream:
        stream.write(str(os.getpid()) + "\n")
        stream.flush()
    if (home / "crash-on-refresh").exists():
        with release.open("rb", buffering=0) as stream:
            if stream.read(1) != b"C":
                return
        os.kill(os.getpid(), signal.SIGTERM)
        return
    with release.open("rb", buffering=0) as stream:
        if stream.read(1) != b"R":
            return
    send({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "rateLimits": {
                "primary": {
                    "usedPercent": 42.0,
                    "windowDurationMins": 300,
                    "resetsAt": 1_800_000_000,
                }
            }
        },
    })

try:
    for source in sys.stdin:
        message = json.loads(source)
        request_id = message.get("id")
        if request_id is None:
            continue
        method = message.get("method")
        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "userAgent": "codex_cli_rs/0.42.0 (lifecycle fixture)",
                    "codexHome": str(home),
                    "platformFamily": "unix",
                    "platformOs": "macos",
                },
            })
        elif method == "account/rateLimits/read":
            threading.Thread(
                target=refresh,
                args=(request_id,),
                daemon=True,
            ).start()
        else:
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})
finally:
    try:
        with exited.open("w", encoding="ascii") as stream:
            stream.write(str(os.getpid()) + "\n")
            stream.flush()
    except BaseException:
        pass
'''


class NativeBackend:
    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "fixture-home"
        self.home.mkdir(mode=0o700)
        _seed_default_profiles(self.home)
        self.default_profiles_before = _snapshot_default_profiles(self.home)
        self.paths = AppPaths.for_home(self.home)
        self.account = AccountStore(self.paths).create("codex", "Lifecycle")
        self.account_home = self.paths.account_home("codex", self.account.id)
        self.started_path = self.account_home / "provider-started.fifo"
        self.release_path = self.account_home / "provider-release.fifo"
        self.exited_path = self.account_home / "provider-exited.fifo"
        for path in (self.started_path, self.release_path, self.exited_path):
            os.mkfifo(path, mode=0o600)
        self.started_fd = os.open(self.started_path, os.O_RDWR)
        self.release_fd = os.open(self.release_path, os.O_RDWR)
        self.exited_fd = os.open(self.exited_path, os.O_RDWR)

        fixture_bin = tmp_path / "fixture-bin"
        fixture_bin.mkdir()
        executable = fixture_bin / "codex"
        executable.write_text(_FIXTURE_CODEX, encoding="utf-8")
        executable.chmod(0o700)
        repository = Path(__file__).resolve().parents[2]
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "DOTSYNC_DIR",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN",
            }
        }
        environment.update(
            {
                "HOME": str(self.home),
                "PATH": os.pathsep.join((str(fixture_bin), os.defpath)),
                "PYTHONPATH": str(repository / "lib"),
            }
        )
        self.process = subprocess.Popen(
            [sys.executable, "-m", "dotsync", "ui", "--native-host"],
            cwd=repository,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        line = _readline_bounded(self.process.stdout, timeout=5.0)
        handshake = json.loads(line)
        self.origin = handshake["origin"]
        self.token = handshake["token"]
        self.provider_pid: int | None = None
        self.provider_exit: ProcessExitBarrier | None = None

    def start_refresh(self) -> str:
        response = self.request(
            "POST",
            f"/api/accounts/{self.account.id}/refresh",
            {"provider": "codex"},
        )
        assert response[0] == 202, response
        return json.loads(response[1])["job_id"]

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
    ) -> tuple[int, bytes]:
        port = int(self.origin.rsplit(":", 1)[1])
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
        headers = {"X-DotSync-Token": self.token}
        encoded = None
        if body is not None:
            encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def wait_provider_started(self) -> int:
        self.provider_pid = int(_read_fd_bounded(self.started_fd, timeout=5.0))
        os.kill(self.provider_pid, 0)
        self.provider_exit = ProcessExitBarrier(self.provider_pid)
        return self.provider_pid

    def release_provider(self) -> None:
        assert os.write(self.release_fd, b"R") == 1

    def crash_provider(self) -> None:
        assert os.write(self.release_fd, b"C") == 1

    def wait_provider_exit(self) -> None:
        assert self.provider_pid is not None
        assert int(_read_fd_bounded(self.exited_fd, timeout=5.0)) == self.provider_pid

    def close_control(self) -> None:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()

    def wait_backend(self, expected: set[int]) -> None:
        returncode = self.process.wait(timeout=5.0)
        assert returncode in expected, self.process.stderr.read().decode(
            "utf-8", errors="replace"
        )

    def assert_provider_gone(self) -> None:
        assert self.provider_pid is not None
        assert self.provider_exit is not None
        self.provider_exit.wait(timeout=5.0)
        with pytest.raises(ProcessLookupError):
            os.kill(self.provider_pid, 0)

    def cleanup(self) -> None:
        self.close_control()
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5.0)
        for descriptor in (self.started_fd, self.release_fd, self.exited_fd):
            os.close(descriptor)
        if self.provider_exit is not None:
            self.provider_exit.close()
        assert _snapshot_default_profiles(self.home) == self.default_profiles_before


class ProcessExitBarrier:
    """One-shot kernel process-exit notification without timing loops."""

    def __init__(self, pid: int) -> None:
        self._queue = select.kqueue()
        event = select.kevent(
            pid,
            filter=select.KQ_FILTER_PROC,
            flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
            fflags=select.KQ_NOTE_EXIT,
        )
        self._queue.control([event], 0, 0)

    def wait(self, *, timeout: float) -> None:
        events = self._queue.control(None, 1, timeout)
        assert len(events) == 1, "provider process did not exit"

    def close(self) -> None:
        self._queue.close()


def _seed_default_profiles(home: Path) -> None:
    claude = home / ".claude"
    claude.mkdir(mode=0o700)
    (claude / "settings.json").write_text('{"fixture":true}\n', encoding="utf-8")
    (home / ".claude.json").write_text('{"fixture":true}\n', encoding="utf-8")
    codex = home / ".codex"
    codex.mkdir(mode=0o700)
    (codex / "auth.json").write_text('{"fixture":true}\n', encoding="utf-8")


def _snapshot_default_profiles(home: Path) -> tuple[tuple[str, int, int, bytes], ...]:
    entries: list[tuple[str, int, int, bytes]] = []
    for relative in (Path(".claude"), Path(".claude.json"), Path(".codex")):
        target = home / relative
        descendants = tuple(sorted(target.rglob("*"))) if target.is_dir() else ()
        for path in (target, *descendants):
            stat_result = path.lstat()
            entries.append(
                (
                    str(path.relative_to(home)),
                    stat_result.st_mode,
                    stat_result.st_mtime_ns,
                    path.read_bytes() if path.is_file() else b"",
                )
            )
    return tuple(entries)


def _readline_bounded(stream, *, timeout: float) -> bytes:
    ready, _, _ = select.select([stream.fileno()], [], [], timeout)
    assert ready, "native handshake timed out"
    line = stream.readline(4097)
    assert line.endswith(b"\n")
    assert len(line) <= 4096
    return line


def _read_fd_bounded(descriptor: int, *, timeout: float) -> str:
    ready, _, _ = select.select([descriptor], [], [], timeout)
    assert ready, "fixture pipe timed out"
    value = os.read(descriptor, 64)
    assert value.endswith(b"\n")
    return value.decode("ascii").strip()


def _read_terminal_job(backend: NativeBackend, job_id: str) -> dict[str, object]:
    # Job status is an explicitly polling public API. The bounded requests do
    # not gate provider/process timing; all lifecycle edges use pipes/kqueue.
    for _ in range(8):
        status, body = backend.request("GET", f"/api/jobs/{job_id}")
        assert status == 200
        job = json.loads(body)["job"]
        if job["state"] in {"succeeded", "failed"}:
            return job
    pytest.fail("provider crash job did not reach a terminal API state")


@pytest.fixture
def native_backend(tmp_path: Path):
    backend = NativeBackend(tmp_path)
    try:
        yield backend
    finally:
        backend.cleanup()


def test_native_control_eof_closes_jobs_then_provider(native_backend: NativeBackend):
    native_backend.start_refresh()
    native_backend.wait_provider_started()

    native_backend.close_control()

    native_backend.wait_provider_exit()
    native_backend.wait_backend({0})
    native_backend.assert_provider_gone()


def test_backend_sigterm_bounds_provider_cleanup(native_backend: NativeBackend):
    native_backend.start_refresh()
    native_backend.wait_provider_started()

    native_backend.process.send_signal(signal.SIGTERM)

    native_backend.wait_provider_exit()
    native_backend.wait_backend({-signal.SIGTERM})
    native_backend.assert_provider_gone()


def test_provider_crash_is_safe_job_failure_and_backend_remains(
    native_backend: NativeBackend,
):
    (native_backend.account_home / "crash-on-refresh").write_bytes(b"")
    job_id = native_backend.start_refresh()
    native_backend.wait_provider_started()
    native_backend.crash_provider()
    native_backend.wait_provider_exit()
    native_backend.assert_provider_gone()

    job = _read_terminal_job(native_backend, job_id)
    assert job["state"] == "succeeded"
    assert job["result"]["usage"] is None
    assert job["result"]["error_code"] == "provider_unavailable"
    assert native_backend.process.poll() is None
    native_backend.close_control()
    native_backend.wait_backend({0})


def test_native_parent_crash_pipe_eof_reaps_backend_and_provider(
    native_backend: NativeBackend,
):
    native_backend.start_refresh()
    native_backend.wait_provider_started()

    # A crashing native parent closes this exact inherited control-pipe writer.
    native_backend.close_control()

    native_backend.wait_provider_exit()
    native_backend.wait_backend({0})
    native_backend.assert_provider_gone()


def test_handshake_reader_disappearance_closes_without_provider_orphan(
    tmp_path: Path,
):
    home = tmp_path / "fixture-home"
    home.mkdir(mode=0o700)
    _seed_default_profiles(home)
    before = _snapshot_default_profiles(home)
    repository = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment.update({"HOME": str(home), "PYTHONPATH": str(repository / "lib")})
    process = subprocess.Popen(
        [sys.executable, "-m", "dotsync", "ui", "--native-host"],
        cwd=repository,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None
    process.stdout.close()

    assert process.wait(timeout=5.0) != 0
    assert not list(AppPaths.for_home(home).accounts.glob("codex/*/home/provider-*.fifo"))
    assert _snapshot_default_profiles(home) == before


def test_concurrent_quit_and_provider_completion_has_one_bounded_shutdown(
    native_backend: NativeBackend,
):
    native_backend.start_refresh()
    native_backend.wait_provider_started()
    barrier = threading.Barrier(3)

    quit_thread = threading.Thread(
        target=lambda: (barrier.wait(), native_backend.close_control())
    )
    completion_thread = threading.Thread(
        target=lambda: (barrier.wait(), native_backend.release_provider())
    )
    quit_thread.start()
    completion_thread.start()
    barrier.wait()
    quit_thread.join(timeout=2.0)
    completion_thread.join(timeout=2.0)

    native_backend.wait_backend({0})
    native_backend.assert_provider_gone()
    assert not quit_thread.is_alive()
    assert not completion_thread.is_alive()
