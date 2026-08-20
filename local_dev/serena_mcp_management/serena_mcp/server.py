"""Start or reuse a healthy scoped Serena MCP server."""
from __future__ import annotations

import fcntl
import os
import re
import select
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from local_dev.serena_mcp_management.external_cli import serena_server_command
from local_dev.serena_mcp_management.serena_mcp.health import (
    dashboard_matches_project,
    http_endpoint_alive,
    normalize_dashboard_url,
    pid_is_alive,
    process_identity,
)
from local_dev.serena_mcp_management.serena_mcp.paths import (
    Scope,
    open_private_runtime_file,
    runtime_root_path,
    shared_context_path,
    state_dir_for,
)
from local_dev.serena_mcp_management.serena_mcp.registry import (
    Lease,
    ServerRecord,
    locked_registry,
    record_belongs_to_scope,
    touch_lease,
)
from local_dev.serena_mcp_management.serena_mcp.termination import terminate_pid
from local_dev.serena_mcp_management.serena_mcp.watchdog import (
    ensure_watchdog,
    release_lease_and_shutdown_if_empty,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
IDENTITY_CAPTURE_TIMEOUT_SECONDS = 2.0


@dataclass(slots=True)
class _StartedServer:
    """A new server generation whose direct children remain caller-owned."""

    record: ServerRecord
    server_process: subprocess.Popen
    proxy_process: subprocess.Popen

    def __getattr__(self, name: str):
        return getattr(self.record, name)


def ensure_server(scope: Scope, initial_lease: Lease) -> ServerRecord:
    """Return a healthy server for a scope and atomically register a lease."""

    started: _StartedServer | None = None
    fresh_lease = _fresh_lease(initial_lease)
    try:
        with locked_registry(scope) as registry:
            if registry.record and not record_belongs_to_scope(registry.record, scope):
                registry.record = None
            if registry.record and server_is_healthy(registry.record, scope):
                touch_lease(registry, fresh_lease)
                record = registry.record
            else:
                if registry.record:
                    _terminate_record(registry.record)
                    registry.record = None
                candidate = _start_healthy_server(scope, fresh_lease)
                if isinstance(candidate, _StartedServer):
                    started = candidate
                    record = candidate.record
                else:
                    record = candidate
                registry.record = record
    except BaseException as primary_error:
        if started is not None:
            try:
                _stop_and_reap_started_server(started)
            except BaseException as cleanup_error:
                primary_error.add_note(
                    f"owned server cleanup failed: {cleanup_error}"
                )
        raise
    try:
        ensure_watchdog(scope)
    except BaseException as primary_error:
        try:
            release_lease_and_shutdown_if_empty(
                scope,
                fresh_lease.lease_id,
                record.server_instance_id,
            )
        except BaseException as cleanup_error:
            primary_error.add_note(f"lease rollback failed: {cleanup_error}")
        if started is not None:
            try:
                _stop_and_reap_started_server(started)
            except BaseException as cleanup_error:
                primary_error.add_note(
                    f"owned server rollback cleanup failed: {cleanup_error}"
                )
        raise
    return record


def server_is_healthy(record: ServerRecord, scope: Scope) -> bool:
    """Return true if a registry server record is usable for this scope."""

    if record.project_root != str(scope.project_root):
        return False
    if record.context_profile != scope.context_profile:
        return False
    if record.proxy_pid is None or not record.upstream_mcp_url:
        return False
    if record.server_identity is None or record.proxy_identity is None:
        return False
    return (
        pid_is_alive(record.server_pid)
        and pid_is_alive(record.proxy_pid)
        and _pid_identity_matches(record.server_pid, record.server_identity)
        and _pid_identity_matches(record.proxy_pid, record.proxy_identity)
        and http_endpoint_alive(record.mcp_url)
        and dashboard_matches_project(record.dashboard_url, scope.project_root)
    )


def _start_healthy_server(scope: Scope, initial_lease: Lease) -> _StartedServer:
    last_error: Exception | None = None
    for _attempt in range(3):
        upstream_port = _find_free_port_with_host_lock()
        proc = _start_serena_process(scope, upstream_port)
        server_identity = None
        proxy_proc = None
        proxy_identity = None
        try:
            server_identity = _capture_owned_process_identity(proc)
            upstream_mcp_url = f"http://127.0.0.1:{upstream_port}/mcp"
            dashboard_url = _discover_dashboard_url(proc)
            proxy_port = _find_free_port_with_host_lock()
            proxy_proc = _start_proxy_process(scope, proxy_port, upstream_mcp_url)
            proxy_identity = _capture_owned_process_identity(proxy_proc)
            record = ServerRecord(
                server_instance_id=str(uuid.uuid4()),
                server_pid=proc.pid,
                mcp_url=f"http://127.0.0.1:{proxy_port}/mcp",
                dashboard_url=dashboard_url,
                project_root=str(scope.project_root),
                context_profile=scope.context_profile,
                started_at=time.time(),
                leases={initial_lease.lease_id: initial_lease},
                upstream_mcp_url=upstream_mcp_url,
                proxy_pid=proxy_proc.pid,
                server_identity=server_identity,
                proxy_identity=proxy_identity,
            )
            _wait_until_healthy(record, scope)
            return _StartedServer(record, proc, proxy_proc)
        except BaseException as exc:
            if proxy_proc is not None:
                _stop_and_reap_owned_process(
                    proxy_proc,
                    expected_identity=proxy_identity,
                )
            _stop_and_reap_owned_process(proc, expected_identity=server_identity)
            if not isinstance(exc, Exception):
                raise
            last_error = exc
    raise RuntimeError(f"failed to start healthy Serena MCP server: {last_error}")


def _find_free_port_with_host_lock() -> int:
    lock_path = runtime_root_path() / "host-ports.lock"
    with open_private_runtime_file(lock_path, append=True) as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            return _find_free_port()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_serena_process(scope: Scope, port: int) -> subprocess.Popen:
    serena = serena_server_command()
    if serena is None:
        raise RuntimeError(
            "serena CLI not found (expected on PATH or ~/.local/bin; "
            "install it with uv tool)"
        )
    log_path = _serena_process_log_path(scope)
    with open_private_runtime_file(log_path) as log:
        proc = subprocess.Popen(
            [
                *serena,
                "start-mcp-server",
                "--project",
                str(scope.project_root),
                "--context",
                str(shared_context_path()),
                "--mode",
                "editing",
                "--mode",
                "interactive",
                "--transport",
                "streamable-http",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--enable-web-dashboard",
                "true",
                "--open-web-dashboard",
                "false",
            ],
            cwd=str(scope.project_root),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    proc.dotsync_log_path = log_path
    return proc


def _start_proxy_process(scope: Scope, port: int, upstream_url: str) -> subprocess.Popen:
    log_path = state_dir_for(scope) / "serena-proxy.log"
    with open_private_runtime_file(log_path) as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "local_dev.serena_mcp_management.serena_mcp.proxy",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--upstream-url",
                upstream_url,
                "--log-path",
                str(log_path),
            ],
            cwd=str(REPO_ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    proc.dotsync_log_path = log_path
    return proc


def _serena_process_log_path(scope: Scope) -> Path:
    return state_dir_for(scope) / "serena-server.log"


_DASHBOARD_RE = re.compile(r"https?://127\.0\.0\.1:\d+(?:/[^\s]*)?")


def _discover_dashboard_url(proc: subprocess.Popen, *, timeout: float = 20.0) -> str:
    log_path = getattr(proc, "dotsync_log_path", None)
    if log_path is not None:
        return _discover_dashboard_url_from_log(proc, Path(log_path), timeout=timeout)
    if proc.stdout is None:
        raise RuntimeError("Serena stdout is unavailable")
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], 0.1)
        if not ready:
            if proc.poll() is not None:
                raise RuntimeError("Serena exited before dashboard URL was discovered")
            continue
        line = proc.stdout.readline()
        if not line:
            continue
        match = _DASHBOARD_RE.search(line)
        if match and _looks_like_dashboard_line(line):
            return normalize_dashboard_url(match.group(0))
    raise RuntimeError("timed out waiting for Serena dashboard URL")


def _discover_dashboard_url_from_log(proc: subprocess.Popen, log_path: Path, *, timeout: float) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log_path.exists():
            text = log_path.read_text(errors="replace")
            for line in text.splitlines():
                match = _DASHBOARD_RE.search(line)
                if match and _looks_like_dashboard_line(line):
                    return normalize_dashboard_url(match.group(0))
        if proc.poll() is not None:
            raise RuntimeError("Serena exited before dashboard URL was discovered")
        time.sleep(0.1)
    raise RuntimeError("timed out waiting for Serena dashboard URL")


def _looks_like_dashboard_line(line: str) -> bool:
    lowered = line.lower()
    return "dashboard" in lowered


def _fresh_lease(lease: Lease) -> Lease:
    return Lease(
        lease_id=lease.lease_id,
        client_type=lease.client_type,
        launcher_pid=lease.launcher_pid,
        heartbeat_at=time.time(),
        launcher_identity=lease.launcher_identity,
    )


def _wait_until_healthy(record: ServerRecord, scope: Scope, *, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server_is_healthy(record, scope):
            return
        time.sleep(0.25)
    raise RuntimeError("Serena MCP server did not become healthy")


def _capture_owned_process_identity(
    process: subprocess.Popen,
    *,
    timeout: float | None = None,
) -> str:
    timeout = IDENTITY_CAPTURE_TIMEOUT_SECONDS if timeout is None else timeout
    deadline = time.monotonic() + timeout
    while True:
        returncode = process.poll()
        if returncode is not None:
            process.wait()
            raise RuntimeError(
                f"owned process {process.pid} exited before identity capture "
                f"with status {returncode}"
            )
        identity = process_identity(process.pid)
        if identity is not None:
            return identity
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"timed out capturing identity for owned process {process.pid}"
            )
        time.sleep(0.02)


def _terminate_record(record: ServerRecord) -> None:
    if record.proxy_pid is not None and record.proxy_identity is not None:
        _terminate_pid(record.proxy_pid, expected_identity=record.proxy_identity)
    if record.server_identity is not None:
        _terminate_pid(record.server_pid, expected_identity=record.server_identity)


def _stop_and_reap_started_server(started: _StartedServer) -> None:
    _stop_and_reap_owned_process(
        started.proxy_process,
        expected_identity=started.record.proxy_identity,
    )
    _stop_and_reap_owned_process(
        started.server_process,
        expected_identity=started.record.server_identity,
    )


def _stop_and_reap_owned_process(
    process: subprocess.Popen,
    *,
    expected_identity: str | None,
    timeout: float = 2.0,
) -> None:
    if process.poll() is None:
        if expected_identity is not None:
            terminate_pid(
                process.pid,
                expected_identity=expected_identity,
                timeout=min(timeout, 1.0),
            )
        elif process.poll() is None:
            process.terminate()
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=timeout)


def _terminate_pid(pid: int, *, expected_identity: str | None = None) -> None:
    if expected_identity is None:
        return
    terminate_pid(pid, expected_identity=expected_identity)


def _pid_identity_matches(pid: int, expected_identity: str | None) -> bool:
    if expected_identity is None:
        return False
    return process_identity(pid) == expected_identity
