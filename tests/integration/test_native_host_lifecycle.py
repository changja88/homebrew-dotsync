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
response_sent = home / "provider-response-sent.fifo"
exit_release = home / "provider-exit-release.fifo"
write_lock = threading.Lock()
exit_lock = threading.Lock()
exit_written = False
exit_descriptor = os.open(exited, os.O_WRONLY)
(home / "provider-process.json").write_text(
    json.dumps(
        {
            "argv": sys.argv,
            "environment": dict(os.environ),
        },
        sort_keys=True,
    ),
    encoding="utf-8",
)

def write_exit_once():
    global exit_written
    previous_mask = signal.pthread_sigmask(
        signal.SIG_BLOCK,
        {signal.SIGTERM, signal.SIGINT},
    )
    try:
        with exit_lock:
            if exit_written:
                return
            os.write(exit_descriptor, (str(os.getpid()) + "\n").encode("ascii"))
            exit_written = True
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

def signal_exit(signum, _frame):
    write_exit_once()
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
    with response_sent.open("w", encoding="ascii") as stream:
        stream.write(str(os.getpid()) + "\n")
        stream.flush()
    with exit_release.open("rb", buffering=0) as stream:
        if stream.read(1) != b"E":
            return
    write_exit_once()
    os._exit(0)

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
        write_exit_once()
    except BaseException:
        pass
'''


_SITECUSTOMIZE = r'''from __future__ import annotations

import json
import os
from pathlib import Path
import threading

from dotsync.jobs import JobRegistry
from dotsync.web.server import WebApplication


root = Path(os.environ["DOTSYNC_TEST_HOOK_ROOT"])
terminal_records = root / "terminal-records.jsonl"
published = root / "job-published.fifo"
publication_release = root / "job-publication-release.fifo"
completion_entered = root / "job-completion-entered.fifo"
completion_release = root / "job-completion-release.fifo"
shutdown_records = root / "shutdown-records"
block_publication = root / "block-job-publication"
block_completion = root / "block-job-completion"

original_finish = JobRegistry._finish_locked
original_shutdown = WebApplication.shutdown


def publish_after_unlock(registry, job_id):
    with registry._condition:
        record = registry._jobs[job_id]
        payload = {
            "id": record.job.id,
            "state": record.state,
            "result": record.result,
            "error_code": record.error_code,
        }
    with terminal_records.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    if block_publication.exists():
        with published.open("w", encoding="ascii") as stream:
            stream.write(job_id + "\n")
            stream.flush()
        with publication_release.open("rb", buffering=0) as stream:
            if stream.read(1) != b"J":
                raise RuntimeError("invalid publication release")


def finish_and_publish(registry, record, *, result=None, error_code=None):
    if block_completion.exists():
        with completion_entered.open("w", encoding="ascii") as stream:
            stream.write(record.job.id + "\n")
            stream.flush()
        with completion_release.open("rb", buffering=0) as stream:
            if stream.read(1) != b"K":
                raise RuntimeError("invalid completion release")
    original_finish(registry, record, result=result, error_code=error_code)
    threading.Thread(
        target=publish_after_unlock,
        args=(registry, record.job.id),
        name="dotsync-test-job-published",
        daemon=False,
    ).start()


def count_shutdown(application):
    descriptor = os.open(
        shutdown_records,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o600,
    )
    try:
        os.write(descriptor, b"S")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return original_shutdown(application)


JobRegistry._finish_locked = finish_and_publish
WebApplication.shutdown = count_shutdown
'''


_NATIVE_PARENT_RELAY = r'''from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


command = json.loads(sys.argv[1])
ready = Path(sys.argv[2])
child = subprocess.Popen(
    command,
    cwd="/",
    env=dict(os.environ),
    stdin=subprocess.PIPE,
    stdout=sys.stdout.buffer,
    stderr=sys.stderr.buffer,
    start_new_session=True,
)
with ready.open("w", encoding="ascii") as stream:
    stream.write(f"{os.getpid()} {child.pid}\n")
    stream.flush()
raise SystemExit(child.wait())
'''


class NativeBackend:
    def __init__(self, tmp_path: Path, *, relay_parent: bool = False) -> None:
        self.tmp_path = tmp_path
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
        self.response_sent_path = self.account_home / "provider-response-sent.fifo"
        self.provider_exit_release_path = (
            self.account_home / "provider-exit-release.fifo"
        )
        for path in (
            self.started_path,
            self.release_path,
            self.exited_path,
            self.response_sent_path,
            self.provider_exit_release_path,
        ):
            os.mkfifo(path, mode=0o600)
        self.started_fd = os.open(self.started_path, os.O_RDWR)
        self.release_fd = os.open(self.release_path, os.O_RDWR)
        self.exited_fd = os.open(self.exited_path, os.O_RDWR)
        self.response_sent_fd = os.open(self.response_sent_path, os.O_RDWR)
        self.provider_exit_release_fd = os.open(
            self.provider_exit_release_path,
            os.O_RDWR,
        )

        self.hook_root = tmp_path / "native-hooks"
        self.hook_root.mkdir(mode=0o700)
        self.job_published_path = self.hook_root / "job-published.fifo"
        self.job_publication_release_path = (
            self.hook_root / "job-publication-release.fifo"
        )
        self.job_completion_entered_path = (
            self.hook_root / "job-completion-entered.fifo"
        )
        self.job_completion_release_path = (
            self.hook_root / "job-completion-release.fifo"
        )
        self.block_job_publication_path = self.hook_root / "block-job-publication"
        self.block_job_completion_path = self.hook_root / "block-job-completion"
        self.terminal_records_path = self.hook_root / "terminal-records.jsonl"
        self.shutdown_records_path = self.hook_root / "shutdown-records"
        for path in (
            self.job_published_path,
            self.job_publication_release_path,
            self.job_completion_entered_path,
            self.job_completion_release_path,
        ):
            os.mkfifo(path, mode=0o600)
        self.job_published_fd = os.open(self.job_published_path, os.O_RDWR)
        self.job_publication_release_fd = os.open(
            self.job_publication_release_path,
            os.O_RDWR,
        )
        self.job_completion_entered_fd = os.open(
            self.job_completion_entered_path,
            os.O_RDWR,
        )
        self.job_completion_release_fd = os.open(
            self.job_completion_release_path,
            os.O_RDWR,
        )
        hook_path = tmp_path / "python-hooks"
        hook_path.mkdir(mode=0o700)
        (hook_path / "sitecustomize.py").write_text(
            _SITECUSTOMIZE,
            encoding="utf-8",
        )

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
                "PYTHONPATH": os.pathsep.join(
                    (str(hook_path), str(repository / "lib"))
                ),
                "DOTSYNC_TEST_HOOK_ROOT": str(self.hook_root),
            }
        )
        backend_command = [
            sys.executable,
            "-m",
            "dotsync",
            "ui",
            "--native-host",
        ]
        self.relay_parent = relay_parent
        self.relay_pid: int | None = None
        self.relay_exit: ProcessExitBarrier | None = None
        self.relay_ready_fd: int | None = None
        self.process_channel_capture = json.dumps(
            {
                "backend_argv": backend_command,
                "backend_environment": environment,
            },
            sort_keys=True,
        ).encode("utf-8")
        if relay_parent:
            relay_ready_path = tmp_path / "relay-ready.fifo"
            os.mkfifo(relay_ready_path, mode=0o600)
            self.relay_ready_fd = os.open(relay_ready_path, os.O_RDWR)
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    _NATIVE_PARENT_RELAY,
                    json.dumps(backend_command),
                    str(relay_ready_path),
                ],
                cwd=repository,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            relay_metadata = _read_fd_bounded(self.relay_ready_fd, timeout=5.0)
            relay_pid, backend_pid = (int(value) for value in relay_metadata.split())
            assert relay_pid == self.process.pid
            self.relay_pid = relay_pid
            self.relay_exit = ProcessExitBarrier(relay_pid)
            self.backend_pid = backend_pid
        else:
            self.process = subprocess.Popen(
                backend_command,
                cwd=repository,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            self.backend_pid = self.process.pid
        self.backend_exit = ProcessExitBarrier(self.backend_pid)
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        if not relay_parent:
            assert self.process.stdin is not None
        self.handshake_frame = _readline_bounded(self.process.stdout, timeout=5.0)
        handshake = json.loads(self.handshake_frame)
        self.origin = handshake["origin"]
        self.token = handshake["token"]
        self.provider_pid: int | None = None
        self.provider_exit: ProcessExitBarrier | None = None
        self.stdout_tail: bytes | None = None
        self.stderr_capture: bytes | None = None

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
        self.process_channel_capture += (
            self.account_home / "provider-process.json"
        ).read_bytes()
        self._assert_secret_absent(
            self.process_channel_capture,
            "process argv/environment",
        )
        return self.provider_pid

    def release_provider(self) -> None:
        assert os.write(self.release_fd, b"R") == 1

    def wait_provider_response_sent(self) -> None:
        assert self.provider_pid is not None
        assert int(_read_fd_bounded(self.response_sent_fd, timeout=5.0)) == (
            self.provider_pid
        )

    def release_provider_exit(self) -> None:
        assert os.write(self.provider_exit_release_fd, b"E") == 1

    def crash_provider(self) -> None:
        assert os.write(self.release_fd, b"C") == 1

    def wait_provider_exit(self) -> None:
        assert self.provider_pid is not None
        assert int(_read_fd_bounded(self.exited_fd, timeout=5.0)) == self.provider_pid

    def assert_single_provider_exit_record(self) -> None:
        os.set_blocking(self.exited_fd, False)
        try:
            extra = os.read(self.exited_fd, 64)
        except BlockingIOError:
            extra = b""
        assert extra == b"", "fixture provider emitted duplicate exit records"

    def enable_job_publication_barrier(self) -> None:
        self.block_job_publication_path.touch(mode=0o600)

    def enable_job_completion_barrier(self) -> None:
        self.block_job_completion_path.touch(mode=0o600)

    def wait_job_completion_entered(self, job_id: str) -> None:
        entered = _read_fd_bounded(self.job_completion_entered_fd, timeout=5.0)
        assert entered == job_id

    def release_job_completion(self) -> None:
        assert os.write(self.job_completion_release_fd, b"K") == 1

    def wait_job_published(self, job_id: str) -> None:
        published = _read_fd_bounded(self.job_published_fd, timeout=5.0)
        assert published == job_id

    def release_job_publication(self) -> None:
        assert os.write(self.job_publication_release_fd, b"J") == 1

    def terminal_records(self, job_id: str) -> list[dict[str, object]]:
        records = [
            json.loads(line)
            for line in self.terminal_records_path.read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        return [record for record in records if record["id"] == job_id]

    @property
    def shutdown_count(self) -> int:
        return len(self.shutdown_records_path.read_bytes())

    def close_control(self) -> None:
        assert not self.relay_parent
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()

    def crash_relay_parent(self) -> None:
        assert self.relay_parent
        assert self.relay_pid == self.process.pid
        self.process.send_signal(signal.SIGKILL)

    def wait_relay_crash(self) -> None:
        assert self.relay_parent
        assert self.process.wait(timeout=5.0) == -signal.SIGKILL
        assert self.relay_exit is not None
        self.relay_exit.wait(timeout=5.0)
        with pytest.raises(ProcessLookupError):
            os.kill(self.process.pid, 0)

    def wait_backend(self, expected: set[int]) -> None:
        assert not self.relay_parent
        returncode = self.process.wait(timeout=5.0)
        self._capture_remaining_output()
        assert returncode in expected, "native backend returned an unexpected status"

    def assert_backend_gone(self) -> None:
        self.backend_exit.wait(timeout=5.0)
        with pytest.raises(ProcessLookupError):
            os.kill(self.backend_pid, 0)

    def assert_provider_gone(self) -> None:
        assert self.provider_pid is not None
        assert self.provider_exit is not None
        self.provider_exit.wait(timeout=5.0)
        with pytest.raises(ProcessLookupError):
            os.kill(self.provider_pid, 0)

    def assert_capability_contained(self, test_output: bytes = b"") -> None:
        encoded = self.token.encode("ascii")
        if self.handshake_frame.count(encoded) != 1:
            pytest.fail("native capability handshake occurrence count changed")
        self._capture_remaining_output()
        self._assert_secret_absent(self.stdout_tail or b"", "trailing stdout")
        self._assert_secret_absent(self.stderr_capture or b"", "stderr/diagnostics")
        self._assert_secret_absent(self.process_channel_capture, "process channels")
        self._assert_secret_absent(test_output, "test output")
        for path in self.tmp_path.rglob("*"):
            if path.is_file():
                self._assert_secret_absent(path.read_bytes(), "isolated fixture storage")

    def _capture_remaining_output(self) -> None:
        if self.stdout_tail is not None:
            return
        assert self.process.poll() is not None
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self.stdout_tail = self.process.stdout.read()
        self.stderr_capture = self.process.stderr.read()

    def _assert_secret_absent(self, value: bytes, channel: str) -> None:
        if self.token.encode("ascii") in value:
            pytest.fail(f"native capability escaped through {channel}")

    def cleanup(self) -> None:
        if self.block_job_publication_path.exists():
            try:
                os.write(self.job_publication_release_fd, b"J")
            except OSError:
                pass
        if self.block_job_completion_path.exists():
            try:
                os.write(self.job_completion_release_fd, b"K")
            except OSError:
                pass
        try:
            os.write(self.provider_exit_release_fd, b"E")
        except OSError:
            pass
        if not self.relay_parent:
            self.close_control()
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5.0)
        if _process_exists(self.backend_pid):
            os.kill(self.backend_pid, signal.SIGKILL)
            self.backend_exit.wait(timeout=5.0)
        self._capture_remaining_output()
        for descriptor in (
            self.started_fd,
            self.release_fd,
            self.exited_fd,
            self.response_sent_fd,
            self.provider_exit_release_fd,
            self.job_published_fd,
            self.job_publication_release_fd,
            self.job_completion_entered_fd,
            self.job_completion_release_fd,
        ):
            os.close(descriptor)
        if self.relay_ready_fd is not None:
            os.close(self.relay_ready_fd)
        if self.provider_exit is not None:
            self.provider_exit.close()
        self.backend_exit.close()
        if self.relay_exit is not None:
            self.relay_exit.close()
        self.assert_capability_contained()
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


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def native_backend(tmp_path: Path):
    backend = NativeBackend(tmp_path)
    try:
        yield backend
    finally:
        backend.cleanup()


def test_native_control_eof_closes_jobs_then_provider(
    native_backend: NativeBackend,
    capfd: pytest.CaptureFixture[str],
):
    native_backend.start_refresh()
    native_backend.wait_provider_started()

    native_backend.close_control()

    native_backend.wait_backend({0})
    native_backend.wait_provider_exit()
    native_backend.assert_provider_gone()
    captured = capfd.readouterr()
    native_backend.assert_capability_contained(
        (captured.out + captured.err).encode("utf-8")
    )


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
    native_backend.enable_job_publication_barrier()
    job_id = native_backend.start_refresh()
    native_backend.wait_provider_started()
    native_backend.crash_provider()
    native_backend.wait_provider_exit()
    native_backend.wait_job_published(job_id)
    native_backend.assert_provider_gone()

    status, body = native_backend.request("GET", f"/api/jobs/{job_id}")
    assert status == 200
    job = json.loads(body)["job"]
    assert job["state"] == "succeeded"
    assert job["result"]["usage"] is None
    assert job["result"]["error_code"] == "provider_unavailable"
    native_backend.release_job_publication()
    assert native_backend.process.poll() is None
    native_backend.close_control()
    native_backend.wait_backend({0})


def test_native_parent_crash_pipe_eof_reaps_backend_and_provider(
    tmp_path: Path,
):
    native_backend = NativeBackend(tmp_path, relay_parent=True)
    try:
        native_backend.start_refresh()
        provider_pid = native_backend.wait_provider_started()
        relay_pid = native_backend.relay_pid
        backend_pid = native_backend.backend_pid
        assert relay_pid is not None
        assert len({relay_pid, backend_pid, provider_pid}) == 3

        native_backend.crash_relay_parent()

        native_backend.wait_relay_crash()
        native_backend.wait_provider_exit()
        native_backend.assert_backend_gone()
        native_backend.assert_provider_gone()
        native_backend.assert_single_provider_exit_record()
    finally:
        native_backend.cleanup()


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
    native_backend.enable_job_completion_barrier()
    job_id = native_backend.start_refresh()
    native_backend.wait_provider_started()
    native_backend.release_provider()
    native_backend.wait_provider_response_sent()
    native_backend.release_provider_exit()
    native_backend.wait_provider_exit()
    native_backend.wait_job_completion_entered(job_id)
    barrier = threading.Barrier(3)

    quit_thread = threading.Thread(
        target=lambda: (barrier.wait(), native_backend.close_control())
    )
    completion_thread = threading.Thread(
        target=lambda: (
            barrier.wait(),
            native_backend.release_job_completion(),
        )
    )
    quit_thread.start()
    completion_thread.start()
    barrier.wait()
    quit_thread.join(timeout=2.0)
    completion_thread.join(timeout=2.0)

    native_backend.wait_backend({0})
    native_backend.assert_provider_gone()
    native_backend.assert_single_provider_exit_record()
    assert not quit_thread.is_alive()
    assert not completion_thread.is_alive()
    assert native_backend.shutdown_count == 1
    records = native_backend.terminal_records(job_id)
    assert len(records) == 1
    terminal = records[0]
    assert terminal["id"] == job_id
    assert terminal["state"] == "succeeded"
    assert terminal["error_code"] is None
    assert terminal["result"]["stale"] is False
    assert terminal["result"]["error_code"] is None
    assert terminal["result"]["usage"]["account_id"] == native_backend.account.id
    assert terminal["result"]["usage"]["windows"][0]["used_percent"] == 42.0
