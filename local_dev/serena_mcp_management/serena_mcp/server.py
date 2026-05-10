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
from pathlib import Path

from local_dev.serena_mcp_management.serena_mcp.health import (
    dashboard_matches_project,
    http_endpoint_alive,
    normalize_dashboard_url,
    pid_is_alive,
    process_identity,
)
from local_dev.serena_mcp_management.serena_mcp.paths import Scope, serena_context_for, state_dir_for
from local_dev.serena_mcp_management.serena_mcp.processes import (
    SerenaMcpProcess,
    list_serena_mcp_processes,
    process_matches_scope,
)
from local_dev.serena_mcp_management.serena_mcp.registry import (
    Lease,
    ServerRecord,
    locked_registry,
    record_belongs_to_scope,
    touch_lease,
)
from local_dev.serena_mcp_management.serena_mcp.termination import terminate_pid
from local_dev.serena_mcp_management.serena_mcp.watchdog import ensure_watchdog

REPO_ROOT = Path(__file__).resolve().parents[3]


def ensure_server(scope: Scope, initial_lease: Lease) -> ServerRecord:
    """Return a healthy server for a scope and atomically register a lease."""

    with locked_registry(scope) as registry:
        fresh_lease = _fresh_lease(initial_lease)
        if registry.record and not record_belongs_to_scope(registry.record, scope):
            registry.record = None
        if registry.record and server_is_healthy(registry.record, scope):
            touch_lease(registry, fresh_lease)
            record = registry.record
            _cleanup_same_scope_orphans(scope, preserve_server_pid=record.server_pid)
        else:
            if registry.record:
                _terminate_record(registry.record)
                registry.record = None
            _cleanup_same_scope_orphans(scope, preserve_server_pid=None)
            record = _start_healthy_server(scope, fresh_lease)
            registry.record = record
    ensure_watchdog(scope)
    return record


def server_is_healthy(record: ServerRecord, scope: Scope) -> bool:
    """Return true if a registry server record is usable for this scope."""

    if record.project_root != str(scope.project_root):
        return False
    if record.client_type != scope.client_type:
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


def _cleanup_same_scope_orphans(scope: Scope, *, preserve_server_pid: int | None) -> None:
    for process in list_serena_mcp_processes():
        if not process_matches_scope(process, scope):
            continue
        if preserve_server_pid is not None and process.pid == preserve_server_pid:
            continue
        if process.identity is not None:
            _terminate_pid(process.pid, expected_identity=process.identity)


def _start_healthy_server(scope: Scope, initial_lease: Lease) -> ServerRecord:
    last_error: Exception | None = None
    for _attempt in range(3):
        upstream_port = _find_free_port_with_host_lock()
        proc = _start_serena_process(scope, upstream_port)
        server_identity = process_identity(proc.pid)
        proxy_proc = None
        proxy_identity = None
        try:
            upstream_mcp_url = f"http://127.0.0.1:{upstream_port}/mcp"
            dashboard_url = _discover_dashboard_url(proc)
            proxy_port = _find_free_port_with_host_lock()
            proxy_proc = _start_proxy_process(scope, proxy_port, upstream_mcp_url)
            proxy_identity = process_identity(proxy_proc.pid)
            record = ServerRecord(
                server_pid=proc.pid,
                mcp_url=f"http://127.0.0.1:{proxy_port}/mcp",
                dashboard_url=dashboard_url,
                project_root=str(scope.project_root),
                client_type=scope.client_type,
                started_at=time.time(),
                leases={initial_lease.lease_id: initial_lease},
                upstream_mcp_url=upstream_mcp_url,
                proxy_pid=proxy_proc.pid,
                server_identity=server_identity,
                proxy_identity=proxy_identity,
            )
            _wait_until_healthy(record, scope)
            return record
        except Exception as exc:
            last_error = exc
            if proxy_proc is not None:
                _terminate_pid(proxy_proc.pid, expected_identity=proxy_identity)
            _terminate_pid(proc.pid, expected_identity=server_identity)
    raise RuntimeError(f"failed to start healthy Serena MCP server: {last_error}")


def _find_free_port_with_host_lock() -> int:
    lock_path = Path("/tmp/dotsync-serena-mcp-ports.lock")
    with lock_path.open("a+") as handle:
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
    log_path = _serena_process_log_path(scope)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as log:
        proc = subprocess.Popen(
            [
                "serena",
                "start-mcp-server",
                "--project",
                str(scope.project_root),
                "--context",
                serena_context_for(scope.client_type),
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
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as log:
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


def _terminate_record(record: ServerRecord) -> None:
    if record.proxy_pid is not None and record.proxy_identity is not None:
        _terminate_pid(record.proxy_pid, expected_identity=record.proxy_identity)
    if record.server_identity is not None:
        _terminate_pid(record.server_pid, expected_identity=record.server_identity)


def _terminate_pid(pid: int, *, expected_identity: str | None = None) -> None:
    terminate_pid(pid, expected_identity=expected_identity)


def _pid_identity_matches(pid: int, expected_identity: str | None) -> bool:
    if expected_identity is None:
        return False
    return process_identity(pid) == expected_identity
