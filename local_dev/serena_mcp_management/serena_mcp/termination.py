"""Shared process termination helpers for Serena MCP lifecycle."""
from __future__ import annotations

import os
import signal
import time

from local_dev.serena_mcp_management.serena_mcp.health import pid_is_alive


def terminate_pid(pid: int, *, timeout: float = 5.0) -> None:
    """Terminate a process group, falling back to PID kill and SIGKILL."""

    if pid <= 0:
        return
    if not _send(pid, signal.SIGTERM):
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_is_alive(pid):
            return
        time.sleep(0.1)
    _send(pid, signal.SIGKILL)


def _send(pid: int, sig: signal.Signals) -> bool:
    try:
        os.killpg(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        try:
            os.kill(pid, sig)
            return True
        except ProcessLookupError:
            return False
