"""Shared process termination helpers for Serena MCP lifecycle."""
from __future__ import annotations

import os
import signal
import time

from local_dev.serena_mcp_management.serena_mcp.health import pid_is_alive, process_identity


def terminate_pid(
    pid: int,
    *,
    timeout: float = 5.0,
    expected_identity: str,
) -> None:
    """Terminate a process group, falling back to PID kill and SIGKILL."""

    if pid <= 0 or expected_identity is None:
        return
    if not _identity_matches(pid, expected_identity):
        return
    if not _send(pid, signal.SIGTERM):
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_is_alive(pid):
            return
        if not _identity_matches(pid, expected_identity):
            return
        time.sleep(0.1)
    if not _identity_matches(pid, expected_identity):
        return
    _send(pid, signal.SIGKILL)


def _send(pid: int, sig: signal.Signals) -> bool:
    try:
        os.killpg(pid, sig)
        return True
    except ProcessLookupError:
        if not pid_is_alive(pid):
            return False
        return _send_pid(pid, sig)
    except PermissionError:
        return _send_pid(pid, sig)


def _send_pid(pid: int, sig: signal.Signals) -> bool:
    try:
        os.kill(pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _identity_matches(pid: int, expected_identity: str) -> bool:
    if expected_identity is None:
        return False
    return process_identity(pid) == expected_identity
