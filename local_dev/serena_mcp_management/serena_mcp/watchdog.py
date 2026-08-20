"""Watchdog for stale Serena MCP session leases."""
from __future__ import annotations

import argparse
import os
import select
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
WATCHDOG_READY_TIMEOUT_SECONDS = 5.0


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


def make_launcher_lease(
    lease_id: str, client_type: str, *, now: float | None = None
) -> Lease:
    """Create a lease for the current launcher process."""

    if client_type not in {"codex", "claude"}:
        raise ValueError(f"unsupported client type: {client_type}")
    timestamp = time.time() if now is None else now
    pid = os.getpid()
    return Lease(lease_id, client_type, pid, timestamp, process_identity(pid))


def launcher_process_matches(lease: Lease) -> bool:
    """Return true if the lease still belongs to the original live launcher."""

    if lease.launcher_identity is None:
        return False
    return process_identity(lease.launcher_pid) == lease.launcher_identity


def release_lease_and_shutdown_if_empty(
    scope: Scope, lease_id: str, server_instance_id: str
) -> ShutdownStats:
    """Release one launcher lease and stop the scoped server when it is unused."""

    with locked_registry(scope) as registry:
        if registry.record is None:
            return _no_shutdown_stats()
        if not record_belongs_to_scope(registry.record, scope):
            registry.record = None
            return _no_shutdown_stats()
        if registry.record.server_instance_id != server_instance_id:
            return _no_shutdown_stats()
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


def _no_shutdown_stats() -> ShutdownStats:
    return ShutdownStats(
        sessions_before=0,
        sessions_closed=0,
        sessions_remaining=0,
        server_was_running=False,
        server_stopped=False,
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

    proc: subprocess.Popen | None = None
    read_fd: int | None = None
    write_fd: int | None = None
    try:
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
            read_fd, write_fd = os.pipe()
            try:
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "local_dev.serena_mcp_management.serena_mcp.watchdog",
                        str(scope.project_root),
                        scope.context_profile,
                        "--ready-fd",
                        str(write_fd),
                    ],
                    cwd=str(_REPO_ROOT),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    pass_fds=(write_fd,),
                )
            finally:
                if write_fd is not None:
                    os.close(write_fd)
                    write_fd = None
            identity = _wait_for_watchdog_readiness(proc, read_fd)
            registry.record.watchdog_pid = proc.pid
            registry.record.watchdog_identity = identity
    except BaseException as primary_error:
        if proc is not None:
            try:
                _stop_and_reap_owned_watchdog(proc)
            except BaseException as cleanup_error:
                primary_error.add_note(
                    f"owned watchdog cleanup failed: {cleanup_error}"
                )
        raise
    finally:
        if write_fd is not None:
            os.close(write_fd)
        if read_fd is not None:
            os.close(read_fd)


def _wait_for_watchdog_readiness(
    process: subprocess.Popen,
    ready_fd: int,
    *,
    timeout: float = WATCHDOG_READY_TIMEOUT_SECONDS,
) -> str:
    deadline = time.monotonic() + timeout
    ready = False
    identity: str | None = None
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            process.wait()
            raise RuntimeError(
                f"watchdog exited before readiness with status {returncode}"
            )
        if identity is None:
            identity = process_identity(process.pid)
        readable, _, _ = select.select([ready_fd], [], [], 0.05)
        if readable:
            ready = os.read(ready_fd, 1) == b"R"
        if ready and identity is not None:
            return identity
    raise RuntimeError("watchdog did not become ready before timeout")


def _stop_and_reap_owned_watchdog(
    process: subprocess.Popen,
    *,
    timeout: float = 2.0,
) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=timeout)


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
    if expected_identity is None:
        return
    terminate_pid(pid, expected_identity=expected_identity)


def _pid_identity_matches(pid: int, expected_identity: str | None) -> bool:
    if expected_identity is None:
        return False
    if not pid_is_alive(pid):
        return False
    return process_identity(pid) == expected_identity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Watch shared Serena leases.")
    parser.add_argument("project_root", type=Path)
    parser.add_argument("context_profile")
    parser.add_argument("--ready-fd", type=int, required=True)
    args = parser.parse_args(argv)
    scope = Scope(args.project_root, args.context_profile)
    os.write(args.ready_fd, b"R")
    os.close(args.ready_fd)
    return run_watchdog(scope)


if __name__ == "__main__":
    raise SystemExit(main())
