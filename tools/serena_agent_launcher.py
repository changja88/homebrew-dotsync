"""Launch Codex or Claude with a scoped Serena MCP server."""
from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from tools.serena_mcp.paths import Scope, find_project_root
from tools.serena_mcp.registry import Lease, locked_registry, remove_lease, touch_lease
from tools.serena_mcp.server import ensure_server
from tools.serena_mcp.watchdog import HEARTBEAT_INTERVAL_SECONDS, shutdown_if_no_leases


def infer_client_type(program_name: str) -> str:
    """Infer wrapper client type from argv0 or SERENA_AGENT_CLIENT."""

    name = Path(program_name).name
    if name in {"codex", "claude"}:
        return name
    raise RuntimeError(f"unsupported wrapper name: {program_name}")


def find_real_binary(client_type: str) -> str:
    """Find the real agent binary, avoiding the wrapper itself."""

    env_name = f"SERENA_REAL_{client_type.upper()}"
    override = os.environ.get(env_name)
    if override:
        path = Path(override)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise RuntimeError(f"{env_name} points to a non-executable path: {override}")
    current = Path(sys.argv[0]).resolve()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / client_type
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        if candidate.resolve() != current:
            return str(candidate)
    fallback = Path("/opt/homebrew/bin") / client_type
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    raise RuntimeError(f"could not find real {client_type} binary outside the wrapper")


def build_child_command(
    *,
    client_type: str,
    real_binary: str,
    mcp_url: str,
    child_args: list[str],
) -> tuple[list[str], Callable[[], None]]:
    """Build a child command and cleanup callback for temporary files."""

    if client_type == "codex":
        return [
            real_binary,
            "-c",
            f'mcp_servers.serena.url="{mcp_url}"',
            *child_args,
        ], lambda: None
    if client_type == "claude":
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        with handle:
            json.dump(
                {
                    "mcpServers": {
                        "serena": {
                            "type": "http",
                            "url": mcp_url,
                        }
                    }
                },
                handle,
            )
        path = handle.name

        def cleanup() -> None:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

        return [real_binary, f"--mcp-config={path}", *child_args], cleanup
    raise RuntimeError(f"unsupported client type: {client_type}")


def main(argv: list[str] | None = None) -> int:
    """Run the scoped Serena launcher."""

    args = list(sys.argv[1:] if argv is None else argv)
    client_type = infer_client_type(os.environ.get("SERENA_AGENT_CLIENT", sys.argv[0]))
    project_root = find_project_root(Path.cwd())
    scope = Scope(project_root, client_type)
    lease_id = str(uuid.uuid4())
    lease = Lease(lease_id, os.getpid(), time.time())
    record = ensure_server(scope, lease)
    stop = threading.Event()
    cleanup: Callable[[], None] = lambda: None
    child: subprocess.Popen | None = None
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(scope, lease_id, stop),
        daemon=True,
    )
    heartbeat.start()

    try:
        real_binary = find_real_binary(client_type)
        cmd, cleanup = build_child_command(
            client_type=client_type,
            real_binary=real_binary,
            mcp_url=record.mcp_url,
            child_args=args,
        )
        child = subprocess.Popen(cmd, cwd=str(project_root))

        def shutdown(signum=None, frame=None):
            stop.set()
            if child is not None and child.poll() is None:
                child.terminate()
            _remove_lease_and_shutdown_if_empty(scope, lease_id)

        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, shutdown)
        atexit.register(lambda: _remove_lease_and_shutdown_if_empty(scope, lease_id))
        return int(child.wait())
    finally:
        stop.set()
        cleanup()
        _remove_lease_and_shutdown_if_empty(scope, lease_id)


def _heartbeat_loop(scope: Scope, lease_id: str, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
        with locked_registry(scope) as registry:
            if registry.record is None or lease_id not in registry.record.leases:
                return
            touch_lease(registry, Lease(lease_id, os.getpid(), time.time()))


def _remove_lease_and_shutdown_if_empty(scope: Scope, lease_id: str) -> None:
    with locked_registry(scope) as registry:
        remove_lease(registry, lease_id)
    shutdown_if_no_leases(scope)


if __name__ == "__main__":
    raise SystemExit(main())
