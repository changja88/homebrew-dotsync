"""Watchdog for stale Serena MCP session leases."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from local_dev.serena_mcp_management.serena_mcp.health import pid_is_alive, process_identity
from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.registry import (
    Lease,
    ServerRecord,
    locked_registry,
    record_belongs_to_scope,
)
from local_dev.serena_mcp_management.serena_mcp.termination import terminate_pid

HEARTBEAT_INTERVAL_SECONDS = 5.0
LEASE_TIMEOUT_SECONDS = 30.0
_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True, slots=True)
class ShutdownStats:
    """Visible summary for an agent lease release."""

    sessions_before: int
    sessions_closed: int
    sessions_remaining: int
    server_was_running: bool
    server_stopped: bool


def cleanup_once(scope: Scope, *, now: float, lease_timeout_seconds: float) -> bool:
    """Prune dead stale leases and stop the server when none remain.

    A stale heartbeat alone is not enough to remove an identity-matched live
    launcher. macOS sleep/wake can pause both heartbeat and watchdog processes
    long enough for wall-clock time to exceed the timeout.
    """

    with locked_registry(scope) as registry:
        if registry.record is None:
            return False
        if not record_belongs_to_scope(registry.record, scope):
            registry.record = None
            return False
        for lease_id, lease in list(registry.record.leases.items()):
            if now - lease.heartbeat_at <= lease_timeout_seconds:
                continue
            if launcher_process_matches(lease):
                lease.heartbeat_at = now
                continue
            registry.record.leases.pop(lease_id, None)
        if registry.record.leases:
            return True
        _terminate_record(registry.record)
        registry.record = None
        return False


def make_launcher_lease(lease_id: str, *, now: float | None = None) -> Lease:
    """Create a lease for the current launcher process."""

    timestamp = time.time() if now is None else now
    pid = os.getpid()
    return Lease(lease_id, pid, timestamp, process_identity(pid))


def launcher_process_matches(lease: Lease) -> bool:
    """Return true if the lease still belongs to the original live launcher."""

    if lease.launcher_identity is None:
        return False
    return process_identity(lease.launcher_pid) == lease.launcher_identity


def release_lease_and_shutdown_if_empty(scope: Scope, lease_id: str) -> ShutdownStats:
    """Release one launcher lease and stop the scoped server when it is unused."""

    with locked_registry(scope) as registry:
        if registry.record is None:
            return ShutdownStats(
                sessions_before=0,
                sessions_closed=0,
                sessions_remaining=0,
                server_was_running=False,
                server_stopped=False,
            )
        if not record_belongs_to_scope(registry.record, scope):
            registry.record = None
            return ShutdownStats(
                sessions_before=0,
                sessions_closed=0,
                sessions_remaining=0,
                server_was_running=False,
                server_stopped=False,
            )
        sessions_before = len(registry.record.leases)
        sessions_closed = 1 if lease_id in registry.record.leases else 0
        registry.record.leases.pop(lease_id, None)
        sessions_remaining = len(registry.record.leases)
        if sessions_remaining:
            return ShutdownStats(
                sessions_before=sessions_before,
                sessions_closed=sessions_closed,
                sessions_remaining=sessions_remaining,
                server_was_running=True,
                server_stopped=False,
            )
        _terminate_record(registry.record)
        registry.record = None
        return ShutdownStats(
            sessions_before=sessions_before,
            sessions_closed=sessions_closed,
            sessions_remaining=0,
            server_was_running=True,
            server_stopped=True,
        )


def run_watchdog(scope: Scope) -> int:
    """Run cleanup until the scoped server no longer needs a watchdog."""

    while True:
        keep_running = cleanup_once(
            scope,
            now=time.time(),
            lease_timeout_seconds=LEASE_TIMEOUT_SECONDS,
        )
        if not keep_running:
            return 0
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


def ensure_watchdog(scope: Scope) -> None:
    """Ensure exactly one live watchdog is recorded for a scope."""

    with locked_registry(scope) as registry:
        if registry.record is None:
            return
        if registry.record.watchdog_pid and _pid_identity_matches(
            registry.record.watchdog_pid,
            registry.record.watchdog_identity,
        ):
            return
        env = os.environ.copy()
        env["PYTHONPATH"] = _pythonpath_with_repo_root(env.get("PYTHONPATH"))
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "local_dev.serena_mcp_management.serena_mcp.watchdog",
                str(scope.project_root),
                scope.client_type,
            ],
            cwd=str(_REPO_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        registry.record.watchdog_pid = proc.pid
        registry.record.watchdog_identity = process_identity(proc.pid)


def _pythonpath_with_repo_root(current: str | None) -> str:
    repo_root = str(_REPO_ROOT)
    if not current:
        return repo_root
    parts = current.split(os.pathsep)
    if repo_root in parts:
        return current
    return os.pathsep.join([repo_root, current])


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
    if not pid_is_alive(pid):
        return False
    return process_identity(pid) == expected_identity


if __name__ == "__main__":
    raise SystemExit(run_watchdog(Scope(Path(sys.argv[1]), sys.argv[2])))
