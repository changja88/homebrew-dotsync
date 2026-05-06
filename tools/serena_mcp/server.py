"""Start or reuse a healthy scoped Serena MCP server."""
from __future__ import annotations

import fcntl
import os
import re
import select
import signal
import socket
import subprocess
import time
from pathlib import Path

from tools.serena_mcp.health import (
    dashboard_matches_project,
    http_endpoint_alive,
    normalize_dashboard_url,
    pid_is_alive,
)
from tools.serena_mcp.paths import Scope
from tools.serena_mcp.registry import Lease, ServerRecord, locked_registry, touch_lease
from tools.serena_mcp.watchdog import ensure_watchdog


def ensure_server(scope: Scope, initial_lease: Lease) -> ServerRecord:
    """Return a healthy server for a scope and atomically register a lease."""

    with locked_registry(scope) as registry:
        if registry.record and server_is_healthy(registry.record, scope):
            touch_lease(registry, initial_lease)
            record = registry.record
        else:
            if registry.record:
                _terminate_pid(registry.record.server_pid)
                registry.record = None
            record = _start_healthy_server(scope, initial_lease)
            registry.record = record
    ensure_watchdog(scope)
    return record


def server_is_healthy(record: ServerRecord, scope: Scope) -> bool:
    """Return true if a registry server record is usable for this scope."""

    if record.project_root != str(scope.project_root):
        return False
    if record.client_type != scope.client_type:
        return False
    return (
        pid_is_alive(record.server_pid)
        and http_endpoint_alive(record.mcp_url)
        and dashboard_matches_project(record.dashboard_url, scope.project_root)
    )


def serena_context_for(client_type: str) -> str:
    """Map a launcher client type to the Serena context name."""

    if client_type == "codex":
        return "codex"
    if client_type == "claude":
        return "claude-code"
    raise ValueError(f"unsupported client type: {client_type}")


def _start_healthy_server(scope: Scope, initial_lease: Lease) -> ServerRecord:
    last_error: Exception | None = None
    for _attempt in range(3):
        port = _find_free_port_with_host_lock()
        proc = _start_serena_process(scope, port)
        try:
            dashboard_url = _discover_dashboard_url(proc)
            record = ServerRecord(
                server_pid=proc.pid,
                mcp_url=f"http://127.0.0.1:{port}/mcp",
                dashboard_url=dashboard_url,
                project_root=str(scope.project_root),
                client_type=scope.client_type,
                started_at=time.time(),
                leases={initial_lease.lease_id: initial_lease},
            )
            _wait_until_healthy(record, scope)
            return record
        except RuntimeError as exc:
            last_error = exc
            _terminate_pid(proc.pid)
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
    return subprocess.Popen(
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
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )


_DASHBOARD_RE = re.compile(r"https?://127\.0\.0\.1:\d+(?:/[^\s]*)?")


def _discover_dashboard_url(proc: subprocess.Popen, *, timeout: float = 20.0) -> str:
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
        if match:
            return normalize_dashboard_url(match.group(0))
    raise RuntimeError("timed out waiting for Serena dashboard URL")


def _wait_until_healthy(record: ServerRecord, scope: Scope, *, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server_is_healthy(record, scope):
            return
        time.sleep(0.25)
    raise RuntimeError("Serena MCP server did not become healthy")


def _terminate_pid(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
