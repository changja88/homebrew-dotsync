"""Watchdog for stale Serena MCP session leases."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from tools.serena_mcp.health import pid_is_alive
from tools.serena_mcp.paths import Scope
from tools.serena_mcp.registry import locked_registry, stale_lease_ids

HEARTBEAT_INTERVAL_SECONDS = 5.0
LEASE_TIMEOUT_SECONDS = 30.0
_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class ShutdownStats:
    """Visible summary for an agent lease release."""

    sessions_before: int
    sessions_closed: int
    sessions_remaining: int
    server_was_running: bool
    server_stopped: bool


def cleanup_once(scope: Scope, *, now: float, lease_timeout_seconds: float) -> bool:
    """Prune stale leases and stop the server when none remain."""

    with locked_registry(scope) as registry:
        if registry.record is None:
            return False
        for lease_id in stale_lease_ids(
            registry,
            now=now,
            timeout_seconds=lease_timeout_seconds,
        ):
            registry.record.leases.pop(lease_id, None)
        if registry.record.leases:
            return True
        _terminate_pid(registry.record.server_pid)
        registry.record = None
        return False


def shutdown_if_no_leases(scope: Scope) -> bool:
    """Stop the server only if there are no leases; do not prune leases."""

    with locked_registry(scope) as registry:
        if registry.record is None:
            return False
        if registry.record.leases:
            return True
        _terminate_pid(registry.record.server_pid)
        registry.record = None
        return False


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
        _terminate_pid(registry.record.server_pid)
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
        if registry.record.watchdog_pid and pid_is_alive(registry.record.watchdog_pid):
            return
        env = os.environ.copy()
        env["PYTHONPATH"] = _pythonpath_with_repo_root(env.get("PYTHONPATH"))
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.serena_mcp.watchdog",
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


def _pythonpath_with_repo_root(current: str | None) -> str:
    repo_root = str(_REPO_ROOT)
    if not current:
        return repo_root
    parts = current.split(os.pathsep)
    if repo_root in parts:
        return current
    return os.pathsep.join([repo_root, current])


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
    deadline = time.time() + 5
    while time.time() < deadline:
        if not pid_is_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return


if __name__ == "__main__":
    raise SystemExit(run_watchdog(Scope(Path(sys.argv[1]), sys.argv[2])))
