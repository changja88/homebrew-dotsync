"""Watchdog for stale Serena MCP session leases."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from tools.serena_mcp.health import pid_is_alive
from tools.serena_mcp.paths import Scope
from tools.serena_mcp.registry import locked_registry, stale_lease_ids

HEARTBEAT_INTERVAL_SECONDS = 5.0
LEASE_TIMEOUT_SECONDS = 30.0


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
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.serena_mcp.watchdog",
                str(scope.project_root),
                scope.client_type,
            ],
            cwd=str(scope.project_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        registry.record.watchdog_pid = proc.pid


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
