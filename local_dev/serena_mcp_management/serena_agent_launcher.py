"""Launch Codex or Claude with a scoped Serena MCP server."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from local_dev.serena_mcp_management.serena_mcp.diagnostics import snapshot_global_lifecycle
from local_dev.serena_mcp_management.serena_mcp.paths import Scope, find_project_root
from local_dev.serena_mcp_management.serena_mcp.registry import locked_registry, touch_lease
from local_dev.serena_mcp_management.serena_mcp.server import ensure_server
from local_dev.serena_mcp_management.serena_mcp.watchdog import (
    HEARTBEAT_INTERVAL_SECONDS,
    LEASE_TIMEOUT_SECONDS,
    ShutdownStats,
    make_launcher_lease,
    release_lease_and_shutdown_if_empty,
)
from local_dev.serena_mcp_management.session_inventory import (
    AgentInventory,
    cleanup_inventory,
    scan_inventory,
)
from local_dev.serena_mcp_management.ui import (
    PINK,
    PURPLE,
    BoxModel,
    BoxRenderer,
    Item,
    SpinnerTicker,
    confirm,
    render_inline_row,
    style_criteria,
    style_count,
    style_inventory_counts,
    style_mcp_inventory,
    style_spinner,
)


@dataclass
class CleanupResult:
    """Result of a cleanup operation."""

    deleted: int
    memory_files_reset: int


@dataclass
class LaunchPrepSummary:
    """Summary of the v2 launch-prep phase."""

    cleanup_deleted: int
    cleanup_memory_files_reset: int


def _codex_home_from_env() -> Path:
    """Return a safe absolute Codex home path from CODEX_HOME or the default."""
    value = os.environ.get("CODEX_HOME")
    codex_home = Path(value).expanduser() if value else Path.home() / ".codex"
    if not codex_home.is_absolute():
        raise ValueError("codex_home must be absolute")
    return codex_home


def _inventory_for_preflight(client: str, project_root: str) -> AgentInventory:
    cwd = os.getcwd()
    codex_home = Path.home() / ".codex" if client == "claude" else _codex_home_from_env()
    return scan_inventory(
        client=client,
        cwd=cwd,
        project_root=project_root or cwd,
        home=Path.home(),
        codex_home=codex_home,
    )


def _sessions_value(inventory: AgentInventory) -> str:
    sessions = inventory.sessions
    return style_inventory_counts(
        f"{inventory.client} {sessions.total} total . "
        f"{sessions.to_delete} to delete . {sessions.to_keep} to keep"
    )


def _memory_value(inventory: AgentInventory) -> str:
    memory = inventory.memory
    return style_inventory_counts(
        f"{inventory.client} {memory.total} total . "
        f"{memory.to_reset} to reset . {memory.to_keep} to keep"
    )


def _run_cleanup_claude() -> CleanupResult:
    """Clean up old Claude sessions and memory files.

    Returns:
        CleanupResult with deleted count and memory files reset count.
    """
    cwd = os.getcwd()
    inventory = cleanup_inventory(
        client="claude",
        cwd=cwd,
        project_root=os.environ.get("SERENA_AGENT_PROJECT_ROOT", cwd),
        home=Path.home(),
        codex_home=Path.home() / ".codex",
    )

    return CleanupResult(
        deleted=inventory.sessions.to_delete,
        memory_files_reset=inventory.memory.to_reset,
    )


def _run_cleanup_codex(codex_home: Path, cwd: str) -> CleanupResult:
    """Clean up old Codex sessions and memory files.

    Returns:
        CleanupResult with deleted count and memory files reset count.
    """
    inventory = cleanup_inventory(
        client="codex",
        cwd=cwd,
        project_root=os.environ.get("SERENA_AGENT_PROJECT_ROOT", cwd),
        home=Path.home(),
        codex_home=codex_home,
    )

    return CleanupResult(
        deleted=inventory.sessions.to_delete,
        memory_files_reset=inventory.memory.to_reset,
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
    return _main_v2(args)


def _run_launch_prep_v2(
    *,
    stream: TextIO | None = None,
) -> LaunchPrepSummary:
    """Run the v2 launch-prep phase with cleanup execution.

    This phase:
    1. Detects the client type and runs cleanup (claude or codex)
    2. Outputs the cleanup results in a formatted row
    3. Returns a summary of what was cleaned

    Returns:
        LaunchPrepSummary with cleanup counts.
    """
    out = stream if stream is not None else sys.stdout
    client = os.environ.get("SERENA_AGENT_CLIENT", "codex")

    if client == "claude":
        result = _run_cleanup_claude()
    elif client == "codex":
        codex_home = _codex_home_from_env()
        result = _run_cleanup_codex(codex_home, os.getcwd())
    else:
        raise RuntimeError(f"unsupported launcher name: {client}")

    out.write(
        f"  ✓ cleanup     {result.deleted} sessions deleted . "
        f"{result.memory_files_reset} memory files reset\n"
    )
    out.flush()

    return LaunchPrepSummary(
        cleanup_deleted=result.deleted,
        cleanup_memory_files_reset=result.memory_files_reset,
    )


def _start_mcp_with_spinner(
    *,
    scope,
    lease,
    stream: TextIO | None = None,
):
    """Start the MCP server with a spinner ticker for visual feedback.

    This function wraps ensure_server with a spinner that updates in-place
    while the server is starting. On success, displays the MCP URL. On
    failure, displays the error message.

    Args:
        scope: The Scope object for the server.
        lease: The Lease object for the server.
        stream: Output stream (defaults to sys.stdout).

    Returns:
        The server record on success.

    Raises:
        Any exception from ensure_server.
    """
    out = stream if stream is not None else sys.stdout
    out.write(f"  \x1b[{PURPLE}m·\x1b[0m serena     preparing scoped server")
    out.flush()
    frame_state = {"frame": 0}

    def on_tick(frame: int) -> None:
        frame_state["frame"] = frame
        out.write(f"\r  {style_spinner(frame)} serena     preparing scoped server")
        out.flush()

    ticker = SpinnerTicker(on_tick=on_tick, interval=0.1)
    ticker.start()
    try:
        record = ensure_server(scope, lease)
    except Exception as exc:
        ticker.stop()
        out.write(f"\r  \x1b[33m!\x1b[0m serena     failed     . {exc}\n")
        out.flush()
        raise
    ticker.stop()
    out.write(f"\r  \x1b[{PINK}m✓\x1b[0m serena     ready      . {record.mcp_url}\n")
    out.flush()
    return record


def _format_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = int(seconds - minutes * 60)
    if minutes == 0:
        return f"{secs}s"
    return f"{minutes}m {secs}s"


def _render_summary_v2(
    *,
    stream,
    client: str,
    duration_seconds: float,
    cleanup_deleted: int,
    cleanup_memory_files_reset: int,
    mcp_lifecycle: str,
    warnings: list[str],
) -> None:
    items = [
        Item(id="duration", label="duration",
             value=_format_duration(duration_seconds), status="done"),
        Item(id="cleanup", label="cleanup",
             value=style_count(
                 f"{cleanup_deleted} sessions deleted . "
                 f"{cleanup_memory_files_reset} memory files reset"
             ),
             status="done"),
        Item(id="mcp", label="serena", value=f"server {mcp_lifecycle}", status="done"),
    ]
    for index, message in enumerate(warnings):
        items.append(Item(id=f"warn-{index}", label="warning",
                          value=message, status="warn"))
    model = BoxModel(phase="summary", title=client, items=items)
    BoxRenderer(stream=stream).draw(model)


def _main_v2(args: list[str]) -> int:
    """v2 box-model TUI flow."""
    started_at = time.time()
    warnings: list[str] = []
    interactive = os.environ.get("SERENA_AGENT_INTERACTIVE") == "1"
    out = sys.stdout

    if interactive:
        _render_preflight_overview_v2()

    serena_state = _run_serena_init_v2() if interactive else "managed"

    if interactive:
        rc = _run_preflight_v2(serena_state=serena_state)
        if rc != 0:
            return rc

    if interactive and not _run_final_confirm_v2():
        return 130

    if serena_state in {"skipped", "failed"}:
        warnings.append(f"serena project create {serena_state}")
        client_type = infer_client_type(os.environ.get("SERENA_AGENT_CLIENT", sys.argv[0]))
        real_binary = find_real_binary(client_type)
        if os.environ.get("SERENA_AGENT_CLEAR_BEFORE_CHILD") == "1":
            clear_terminal_before_child()
        return int(subprocess.run([real_binary, *args]).returncode)

    summary_state = _run_launch_prep_v2() if interactive else None

    client_type = infer_client_type(os.environ.get("SERENA_AGENT_CLIENT", sys.argv[0]))
    project_root = _project_root_from_environment() or find_project_root(Path.cwd())
    scope = Scope(project_root, client_type)
    lease_id = str(uuid.uuid4())
    lease = make_launcher_lease(lease_id)

    record = _start_mcp_with_spinner(scope=scope, lease=lease) if interactive \
        else ensure_server(scope, lease)

    stop = threading.Event()
    cleanup: Callable[[], None] = lambda: None
    child: subprocess.Popen | None = None
    heartbeat = threading.Thread(
        target=_heartbeat_loop, args=(scope, lease_id, stop), daemon=True,
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
        rc = int(child.wait())
    finally:
        stop.set()
        cleanup()
        if interactive:
            try:
                stats = _stop_mcp_with_spinner(scope=scope, lease_id=lease_id)
            except Exception:
                stats = None
        else:
            stats = _remove_lease_and_shutdown_if_empty(scope, lease_id)

    if interactive:
        if stats is None:
            mcp_lifecycle = "unknown"
        elif stats.server_stopped:
            mcp_lifecycle = "stopped"
        elif stats.server_was_running:
            mcp_lifecycle = f"kept ({stats.sessions_remaining} sessions)"
        else:
            mcp_lifecycle = "none"
        cleanup_deleted = summary_state.cleanup_deleted if summary_state else 0
        cleanup_memory = summary_state.cleanup_memory_files_reset if summary_state else 0
        _render_summary_v2(
            stream=out,
            client=client_type,
            duration_seconds=time.time() - started_at,
            cleanup_deleted=cleanup_deleted,
            cleanup_memory_files_reset=cleanup_memory,
            mcp_lifecycle=mcp_lifecycle,
            warnings=warnings,
        )
    return rc


def _project_root_from_environment() -> Path | None:
    value = os.environ.get("SERENA_AGENT_PROJECT_ROOT")
    if not value:
        return None
    return Path(value).resolve()



def _short_path(path: str) -> str:
    """Convert an absolute path to a tilde-abbreviated version."""
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def _graphify_global_value(client: str, status: str) -> tuple[str, str]:
    """Return (value, item_status) for the graphify global preflight row."""
    if status == "installed":
        if client == "claude":
            return "user skill at ~/.claude/skills/graphify", "done"
        return "user skill at ~/.agents/skills/graphify", "done"
    cmd = "graphify install" if client == "claude" else "graphify install --platform codex"
    return f'not installed . run "{cmd}"', "warn"


def _graphify_graph_value(client: str, status: str) -> tuple[str, str]:
    """Return (value, item_status) for the graphify graph preflight row."""
    if status == "built":
        return "graphify-out/graph.json present", "done"
    invocation = "/graphify ." if client == "claude" else "$graphify ."
    return f'no graph . run "{invocation}" in your agent session', "warn"


def _graphify_integration_value(client: str, status: str) -> tuple[str, str]:
    """Return (value, item_status) for the graphify integration preflight row."""
    if status == "installed":
        if client == "claude":
            return "CLAUDE.md + .claude/settings.json registered", "done"
        return "AGENTS.md + .codex/hooks.json registered", "done"
    cmd = "graphify claude install" if client == "claude" else "graphify codex install"
    return f'not configured . run "{cmd}"', "warn"


def _graphify_hook_value(status: str) -> tuple[str, str]:
    """Return (value, item_status) for the graphify hook preflight row."""
    if status == "installed":
        return "post-commit + post-checkout hooks installed", "done"
    return 'hooks not installed . run "graphify hook install"', "warn"


def _serena_mcp_status(snapshot) -> str:
    if snapshot.scan_failed:
        return "warn"
    if snapshot.orphan_server_count > 0 or snapshot.stale_lease_count > 0:
        return "warn"
    if snapshot.ps_server_count == 0:
        return "info"
    return "done"


def _preflight_box() -> BoxModel:
    """Build a BoxModel for the v2 preflight phase."""
    client = os.environ.get("SERENA_AGENT_CLIENT", "codex")
    project_root = os.environ.get("SERENA_AGENT_PROJECT_ROOT", "")
    serena_status = os.environ.get("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")

    global_status = os.environ.get(
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS", "unknown"
    )
    graph_status = os.environ.get(
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS", "unknown"
    )
    integration_status = os.environ.get(
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "unknown"
    )
    hook_status = os.environ.get(
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "unknown"
    )

    serena_value = (
        "managed by scoped launcher"
        if serena_status == "managed"
        else "project config missing"
    )
    serena_item_status = "done" if serena_status == "managed" else "warn"
    try:
        mcp_snapshot = snapshot_global_lifecycle(
            now=time.time(),
            stale_after_seconds=LEASE_TIMEOUT_SECONDS,
        )
        if mcp_snapshot.scan_failed:
            serena_mcp_value = "scan unavailable"
            serena_mcp_status = "warn"
        else:
            serena_mcp_value = style_mcp_inventory(
                ps_servers=mcp_snapshot.ps_server_count,
                managed_servers=mcp_snapshot.managed_server_count,
                orphan_servers=mcp_snapshot.orphan_server_count,
                leases=mcp_snapshot.lease_count,
                stale_leases=mcp_snapshot.stale_lease_count,
            )
            serena_mcp_status = _serena_mcp_status(mcp_snapshot)
    except Exception:
        serena_mcp_value = "scan unavailable"
        serena_mcp_status = "warn"

    global_value, global_item_status = _graphify_global_value(client, global_status)
    graph_value, graph_item_status = _graphify_graph_value(client, graph_status)
    integration_value, integration_item_status = _graphify_integration_value(
        client, integration_status
    )
    hook_value, hook_item_status = _graphify_hook_value(hook_status)
    try:
        inventory = _inventory_for_preflight(client, project_root)
        sessions_value = _sessions_value(inventory)
        sessions_item_status = "info"
        memory_value = _memory_value(inventory)
        criteria_value = style_criteria(inventory.criteria)
    except Exception as exc:
        detail = str(exc) or exc.__class__.__name__
        sessions_value = f"scan unavailable: {detail}"
        sessions_item_status = "warn"
        memory_value = style_inventory_counts(f"{client} 0 total . 0 to reset . 0 to keep")
        criteria_value = style_criteria("scan unavailable")

    items = [
        Item(
            id="workspace",
            label="workspace",
            value=_short_path(project_root),
            status="info",
        ),
        Item(id="serena", label="serena", value=serena_value, status=serena_item_status),
        Item(
            id="serena-mcp",
            label="serena mcp",
            value=serena_mcp_value,
            status=serena_mcp_status,
        ),
        Item(
            id="graphify-global",
            label="graphify global",
            value=global_value,
            status=global_item_status,
        ),
        Item(
            id="graphify-graph",
            label="graphify graph",
            value=graph_value,
            status=graph_item_status,
        ),
        Item(
            id="graphify-integration",
            label="graphify integration",
            value=integration_value,
            status=integration_item_status,
        ),
        Item(
            id="graphify-hook",
            label="graphify hook",
            value=hook_value,
            status=hook_item_status,
        ),
        Item(
            id="context",
            label="context",
            value="claude-code" if client == "claude" else "codex",
            status="info",
        ),
        Item(
            id="sessions",
            label="sessions",
            value=sessions_value,
            status=sessions_item_status,
        ),
        Item(id="memory", label="memory", value=memory_value, status="info"),
        Item(id="criteria", label="criteria", value=criteria_value, status="info"),
    ]
    return BoxModel(phase="preflight", title=client, items=items)


def _graphify_hook_install(project_root: Path) -> int:
    """Run `graphify hook install` for the given project root.

    Returns the exit code. 2 indicates graphify is not on PATH.
    """
    if shutil.which("graphify") is None:
        return 2
    proc = subprocess.run(
        ["graphify", "hook", "install"],
        cwd=str(project_root),
        check=False,
    )
    return proc.returncode


def _graphify_global_install(client: str) -> int:
    """Run `graphify install` (or `graphify install --platform codex`) for the user.

    Returns the exit code. 2 indicates graphify is not on PATH.
    """
    if shutil.which("graphify") is None:
        return 2
    cmd = ["graphify", "install"]
    if client == "codex":
        cmd.extend(["--platform", "codex"])
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


def _graphify_integration_install(project_root: Path, client: str) -> int:
    """Run `graphify {claude,codex} install` inside the project.

    Returns the exit code. 2 indicates graphify is not on PATH.
    """
    if shutil.which("graphify") is None:
        return 2
    subcommand = "claude" if client == "claude" else "codex"
    proc = subprocess.run(
        ["graphify", subcommand, "install"],
        cwd=str(project_root),
        check=False,
    )
    return proc.returncode


def _run_preflight_v2(
    *,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
    serena_state: str = "managed",
    install_graphify_global: Callable[[str], int] | None = None,
    install_graphify_integration: Callable[[Path, str], int] | None = None,
    install_graphify_hooks: Callable[[Path], int] | None = None,
) -> int:
    """Run the v2 preflight phase with confirmation prompt.

    ``serena_state`` is the result returned by ``_run_serena_init_v2``
    (one of ``managed``/``created``/``skipped``/``failed``). It feeds the
    integration prompt's dynamic default — graphify integration only
    defaults to Yes when both Serena and graphify-global are in place.

    Returns:
        0 if interactive mode is off or user confirms, 130 if user aborts.
    """
    if os.environ.get("SERENA_AGENT_INTERACTIVE") != "1":
        return 0
    out = stream if stream is not None else sys.stdout
    install_global_fn = install_graphify_global or _graphify_global_install
    install_integration_fn = install_graphify_integration or _graphify_integration_install
    install_hook_fn = install_graphify_hooks or _graphify_hook_install

    client = os.environ.get("SERENA_AGENT_CLIENT", "codex")
    project_root = Path(os.environ.get("SERENA_AGENT_PROJECT_ROOT", ".")).resolve()

    def _emit(label: str, value: str, *, ok: bool) -> None:
        out.write(render_inline_row(label, value, status="done" if ok else "warn"))
        out.flush()

    global_status = os.environ.get(
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS", "unknown"
    )
    global_done = global_status not in {"missing", "unknown"}
    if global_status == "missing":
        cmd = "graphify install" if client == "claude" else "graphify install --platform codex"
        if confirm(
            f"Run `{cmd}` to install the graphify skill globally?",
            default=False,
            stream=out,
            input_fn=input_fn,
        ):
            rc = install_global_fn(client)
            if rc == 0:
                global_done = True
                if client == "claude":
                    _emit("graphify global", "user skill at ~/.claude/skills/graphify", ok=True)
                else:
                    _emit("graphify global", "user skill at ~/.agents/skills/graphify", ok=True)
            else:
                _emit("graphify global", f"global install failed (exit {rc})", ok=False)

    integration_status = os.environ.get(
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "unknown"
    )
    integration_present = integration_status == "installed"
    if integration_status == "missing":
        cmd = (
            "graphify claude install" if client == "claude" else "graphify codex install"
        )
        serena_done = serena_state in {"managed", "created"}
        integration_default = serena_done and global_done
        if confirm(
            f"Run `{cmd}` to wire graphify into this project?",
            default=integration_default,
            stream=out,
            input_fn=input_fn,
        ):
            rc = install_integration_fn(project_root, client)
            if rc == 0:
                integration_present = True
                if client == "claude":
                    _emit("graphify integration",
                          "CLAUDE.md + .claude/settings.json registered", ok=True)
                else:
                    _emit("graphify integration",
                          "AGENTS.md + .codex/hooks.json registered", ok=True)
            else:
                _emit("graphify integration",
                      f"integration install failed (exit {rc})", ok=False)

    hook_status = os.environ.get(
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "unknown"
    )
    if hook_status == "missing" and integration_present:
        if confirm(
            "Install graphify hooks for this project?",
            default=True,
            stream=out,
            input_fn=input_fn,
        ):
            rc = install_hook_fn(project_root)
            if rc == 0:
                _emit("graphify hook",
                      "post-commit + post-checkout hooks installed", ok=True)
            else:
                _emit("graphify hook", f"hook install failed (exit {rc})", ok=False)

    return 0


def _render_preflight_overview_v2(*, stream: TextIO | None = None) -> None:
    """Draw the preflight box once as the workspace overview, before any prompts.

    The box is drawn exactly once. Subsequent steps (serena init, graphify
    prompts, install results) print as plain lines below the box — never as
    a redrawn box. Redrawing would push the original overview out of view
    and flash the banner art again right before the final 'Run <client>?'
    prompt; instead, post-install state changes inside `_run_preflight_v2`
    are surfaced with `render_inline_row` so the chronological flow stays
    intact and the visual style still matches the box's row format.
    """
    if os.environ.get("SERENA_AGENT_INTERACTIVE") != "1":
        return
    out = stream if stream is not None else sys.stdout
    BoxRenderer(stream=out).draw(_preflight_box())


def _run_final_confirm_v2(
    *,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
) -> bool:
    """Final 'Run <client>?' gate, asked once after every setup question.

    Returns True if interactive mode is off or the user confirms; False if the
    user declines the launch.
    """
    if os.environ.get("SERENA_AGENT_INTERACTIVE") != "1":
        return True
    out = stream if stream is not None else sys.stdout
    client = os.environ.get("SERENA_AGENT_CLIENT", "codex")
    return confirm(
        f"Run {client}?",
        default=True,
        stream=out,
        input_fn=input_fn,
    )


def _serena_project_create(project_root: Path) -> int:
    """Run `serena project create <root>` feeding default answers via `yes ""`.

    Returns:
        0 on success, non-zero on failure.
    """
    if shutil.which("serena") is None:
        return 2
    yes_proc = subprocess.Popen(["yes", ""], stdout=subprocess.PIPE)
    try:
        proc = subprocess.run(
            ["serena", "project", "create", str(project_root)],
            stdin=yes_proc.stdout,
            check=False,
        )
    finally:
        if yes_proc.stdout is not None:
            yes_proc.stdout.close()
        yes_proc.terminate()
        yes_proc.wait()
    return proc.returncode


def _run_serena_init_v2(
    *,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
) -> str:
    """Run optional v2 serena-init phase.

    Returns one of: 'managed', 'created', 'skipped', 'failed'.
    """
    serena_status = os.environ.get("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    if serena_status != "missing":
        return "managed"

    out = stream if stream is not None else sys.stdout
    project_root = Path(os.environ.get("SERENA_AGENT_PROJECT_ROOT", ".")).resolve()

    if not confirm(
        "Initialize Serena for this project?",
        default=False,
        stream=out,
        input_fn=input_fn,
    ):
        out.write("  ! serena    skipped   . launching without Serena project config\n")
        out.flush()
        return "skipped"

    rc = _serena_project_create(project_root)
    if rc != 0 or not (project_root / ".serena" / "project.yml").exists():
        out.write("  ! serena    failed    . launching without Serena project config\n")
        out.flush()
        return "failed"
    os.environ["SERENA_AGENT_PREFLIGHT_SERENA_STATUS"] = "managed"
    return "created"


def _heartbeat_loop(scope: Scope, lease_id: str, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
        if not _touch_lease_if_record_exists(scope, lease_id, stop):
            return


def _touch_lease_if_record_exists(
    scope: Scope,
    lease_id: str,
    stop: threading.Event,
    *,
    now: float | None = None,
) -> bool:
    """Refresh or reattach this launcher's lease if its server record still exists."""

    with locked_registry(scope) as registry:
        if registry.record is None or stop.is_set():
            return False
        touch_lease(registry, make_launcher_lease(lease_id, now=now))
        return True


def _remove_lease_and_shutdown_if_empty(scope: Scope, lease_id: str) -> ShutdownStats:
    return release_lease_and_shutdown_if_empty(scope, lease_id)


def _stop_mcp_with_spinner(
    *,
    scope,
    lease_id: str,
    stream=None,
    shutdown_fn=None,
):
    """Run lease release + MCP shutdown with a single-line spinner."""
    out = stream if stream is not None else sys.stdout
    fn = shutdown_fn if shutdown_fn is not None else _remove_lease_and_shutdown_if_empty
    out.write(f"  \x1b[{PURPLE}m·\x1b[0m serena     stopping scoped server")
    out.flush()

    def on_tick(frame: int) -> None:
        out.write(f"\r  {style_spinner(frame)} serena     stopping scoped server")
        out.flush()

    ticker = SpinnerTicker(on_tick=on_tick, interval=0.1)
    ticker.start()
    try:
        stats = fn(scope, lease_id)
    except Exception as exc:
        ticker.stop()
        out.write(f"\r  \x1b[33m!\x1b[0m serena     shutdown failed . {exc}\n")
        out.flush()
        raise
    ticker.stop()
    out.write(f"\r  \x1b[{PINK}m✓\x1b[0m serena     stopped scoped server\n")
    out.flush()
    return stats


if __name__ == "__main__":
    raise SystemExit(main())
