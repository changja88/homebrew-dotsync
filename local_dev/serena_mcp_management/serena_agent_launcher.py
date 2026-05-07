"""Launch Codex or Claude with a scoped Serena MCP server."""
from __future__ import annotations

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
from typing import TextIO

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from local_dev.serena_mcp_management.serena_mcp.paths import Scope, find_project_root
from local_dev.serena_mcp_management.serena_mcp.registry import Lease, locked_registry, touch_lease
from local_dev.serena_mcp_management.serena_mcp.server import ensure_server
from local_dev.serena_mcp_management.serena_mcp.watchdog import (
    HEARTBEAT_INTERVAL_SECONDS,
    ShutdownStats,
    release_lease_and_shutdown_if_empty,
)
from local_dev.serena_mcp_management.ui import (
    BoxModel,
    BoxRenderer,
    Item,
    confirm,
)


def infer_client_type(program_name: str) -> str:
    """Infer launcher client type from argv0 or SERENA_AGENT_CLIENT."""

    name = Path(program_name).name
    if name in {"codex", "claude"}:
        return name
    raise RuntimeError(f"unsupported launcher name: {program_name}")


def find_real_binary(client_type: str) -> str:
    """Find the real agent binary, avoiding the zsh shim itself."""

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
    raise RuntimeError(f"could not find real {client_type} binary outside the zsh shim")


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


def format_launch_status(*, client_type: str, project_root: str, mcp_url: str) -> str:
    """Return the visible launch status line for interactive agent starts."""

    return f"serena launcher: {client_type} project={project_root} mcp={mcp_url}"


def format_shutdown_status(stats: ShutdownStats) -> str:
    """Return the visible shutdown status row for interactive agent exits."""

    if not stats.server_was_running:
        server_state = "none"
    elif stats.server_stopped:
        server_state = "stopped"
    else:
        server_state = "kept"
    detail = (
        f"sessions_before={stats.sessions_before} "
        f"closed={stats.sessions_closed} "
        f"remaining={stats.sessions_remaining} "
        f"server={server_state}"
    )
    return f"  * {'serena':<10} {'done':<10}. {detail}"


def format_shutdown_progress_status(detail: str) -> str:
    """Return a visible MCP shutdown progress row."""

    return f"  * {'serena':<10} {'shutdown':<10}. {detail}"


def format_mcp_progress_status(state: str, detail: str) -> str:
    """Return a visible MCP startup progress row."""

    phase = "mcp" if state == "pending" else state
    return f"  * {'serena':<10} {phase:<10}. {detail}"


def clear_terminal_before_child() -> None:
    """Clear the preflight/progress terminal output before opening the agent TUI."""

    print("\x1b[3J\x1b[H\x1b[2J", end="", flush=True)


def open_dashboard_if_requested(dashboard_url: str) -> None:
    """Open the Serena dashboard for interactive agent sessions."""

    if os.environ.get("SERENA_AGENT_INTERACTIVE") != "1":
        return
    subprocess.run(
        ["open", dashboard_url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the scoped Serena launcher."""

    args = list(sys.argv[1:] if argv is None else argv)
    if os.environ.get("SERENA_AGENT_TUI") == "v2":
        return _main_v2(args)
    return _main_v1(args)


def _main_v1(args: list[str]) -> int:
    """Existing flow. Behavior preserved exactly."""

    client_type = infer_client_type(os.environ.get("SERENA_AGENT_CLIENT", sys.argv[0]))
    project_root = _project_root_from_environment() or find_project_root(Path.cwd())
    scope = Scope(project_root, client_type)
    lease_id = str(uuid.uuid4())
    lease = Lease(lease_id, os.getpid(), time.time())
    if _interactive_launch():
        print(format_mcp_progress_status("pending", "preparing scoped server"), flush=True)
    record = ensure_server(scope, lease)
    if _interactive_launch():
        print(format_mcp_progress_status("ready", record.mcp_url), flush=True)
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
        if not args and sys.stderr.isatty() and os.environ.get("SERENA_AGENT_QUIET") != "1":
            print(
                format_launch_status(
                    client_type=client_type,
                    project_root=str(project_root),
                    mcp_url=record.mcp_url,
                ),
                file=sys.stderr,
            )
        open_dashboard_if_requested(record.dashboard_url)
        if os.environ.get("SERENA_AGENT_CLEAR_BEFORE_CHILD") == "1":
            clear_terminal_before_child()
        child = subprocess.Popen(cmd, cwd=str(project_root))

        def shutdown(signum=None, frame=None):
            stop.set()
            if child is not None and child.poll() is None:
                child.terminate()

        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, shutdown)
        return int(child.wait())
    finally:
        if _interactive_launch():
            print(format_shutdown_progress_status("stopping scoped MCP server"), flush=True)
        stop.set()
        cleanup()
        stats = _remove_lease_and_shutdown_if_empty(scope, lease_id)
        if stats is not None and os.environ.get("SERENA_AGENT_INTERACTIVE") == "1":
            print(format_shutdown_status(stats))


def _main_v2(args: list[str]) -> int:
    """v2 box-model TUI flow. Filled out in subsequent tasks."""
    raise NotImplementedError("v2 will be implemented in tasks 7-11")


def _project_root_from_environment() -> Path | None:
    value = os.environ.get("SERENA_AGENT_PROJECT_ROOT")
    if not value:
        return None
    return Path(value).resolve()


def _interactive_launch() -> bool:
    return os.environ.get("SERENA_AGENT_INTERACTIVE") == "1"


def _short_path(path: str) -> str:
    """Convert an absolute path to a tilde-abbreviated version."""
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def _preflight_box() -> BoxModel:
    """Build a BoxModel for the v2 preflight phase."""
    client = os.environ.get("SERENA_AGENT_CLIENT", "codex")
    project_root = os.environ.get("SERENA_AGENT_PROJECT_ROOT", "")
    cleanup_value = os.environ.get("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "")
    memory_value = os.environ.get("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "")
    serena_status = os.environ.get("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    graphify_status = os.environ.get("SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS", "installed")

    serena_value = (
        "managed by scoped launcher"
        if serena_status == "managed"
        else "project config missing"
    )
    serena_item_status = "done" if serena_status == "managed" else "warn"
    graphify_value = (
        "installed . run /graphify . when you want a project graph"
        if graphify_status == "installed"
        else "not installed . install graphify, then run /graphify ."
    )
    graphify_item_status = "done" if graphify_status == "installed" else "warn"

    items = [
        Item(
            id="workspace",
            label="workspace",
            value=_short_path(project_root),
            status="done",
        ),
        Item(id="serena", label="serena", value=serena_value, status=serena_item_status),
        Item(
            id="graphify",
            label="graphify",
            value=graphify_value,
            status=graphify_item_status,
        ),
        Item(
            id="context",
            label="context",
            value="claude-code" if client == "claude" else "codex",
            status="done",
        ),
        Item(id="cleanup", label="cleanup", value=cleanup_value, status="done"),
        Item(id="memory", label="memory", value=memory_value, status="done"),
    ]
    return BoxModel(phase="preflight", title=client, items=items)


def _run_preflight_v2(
    *,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] = input,
) -> int:
    """Run the v2 preflight phase with confirmation prompt.

    Returns:
        0 if interactive mode is off or user confirms, 130 if user aborts.
    """
    if os.environ.get("SERENA_AGENT_INTERACTIVE") != "1":
        return 0
    out = stream if stream is not None else sys.stdout
    renderer = BoxRenderer(stream=out)
    model = _preflight_box()
    renderer.draw(model)
    if not confirm(
        f"Run {model.title}?",
        default=True,
        stream=out,
        input_fn=input_fn,
    ):
        return 130
    return 0


def _heartbeat_loop(scope: Scope, lease_id: str, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
        with locked_registry(scope) as registry:
            if registry.record is None or lease_id not in registry.record.leases:
                return
            touch_lease(registry, Lease(lease_id, os.getpid(), time.time()))


def _remove_lease_and_shutdown_if_empty(scope: Scope, lease_id: str) -> ShutdownStats:
    return release_lease_and_shutdown_if_empty(scope, lease_id)


if __name__ == "__main__":
    raise SystemExit(main())
