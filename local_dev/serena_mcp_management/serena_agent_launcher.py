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
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, TextIO

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from local_dev.serena_mcp_management.external_cli import (
    dotsync_command,
    graphify_command,
    graphify_install_command,
    homebrew_node_command,
    node_command,
    node_install_command,
    serena_install_command,
    serena_oneshot_command,
    serena_server_command,
)
from local_dev.serena_mcp_management.node_preflight import (
    HOMEBREW_NODE_PATH,
    NodeNeed,
    node_need,
)
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
    MINT,
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


def _launch_bare_child(
    args: list[str],
    *,
    tab_account: "TabAccount | None" = None,
    stream: TextIO | None = None,
) -> int:
    """Run the real agent binary without the scoped Serena MCP server.

    Still honors a resolved per-tab account: injects its token and states the
    identity, so the token path works even when Serena is unavailable."""

    client_type = infer_client_type(os.environ.get("SERENA_AGENT_CLIENT", sys.argv[0]))
    real_binary = find_real_binary(client_type)
    if os.environ.get("SERENA_AGENT_CLEAR_BEFORE_CHILD") == "1":
        clear_terminal_before_child()
    if tab_account is not None:
        if stream is not None:
            _emit_tab_identity(stream, tab_account.name)
        env = _child_env_with_token(os.environ.copy(), tab_account.token)
        return int(subprocess.run([real_binary, *args], env=env).returncode)
    return int(subprocess.run([real_binary, *args]).returncode)


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

    tab_account: "TabAccount | None" = None
    if interactive:
        _render_preflight_overview_v2()
        _run_serena_cli_install_v2()
        tab_account = _resolve_tab_account_v2(
            infer_client_type(os.environ.get("SERENA_AGENT_CLIENT", sys.argv[0])),
            stream=out,
        )

    serena_state = _run_serena_init_v2() if interactive else "managed"

    if interactive:
        rc = _run_preflight_v2(serena_state=serena_state)
        if rc != 0:
            return rc

    if interactive and not _run_final_confirm_v2():
        return 130

    if serena_state in {"skipped", "failed"}:
        warnings.append(f"serena project create {serena_state}")
        return _launch_bare_child(args, tab_account=tab_account, stream=out)

    if serena_server_command() is None:
        out.write(
            "  ! serena    unavailable . serena CLI not found —"
            " launching without scoped server\n"
        )
        out.flush()
        return _launch_bare_child(args, tab_account=tab_account, stream=out)

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
        if tab_account is not None:
            _emit_tab_identity(out, tab_account.name)
            child = subprocess.Popen(
                cmd,
                cwd=str(project_root),
                env=_child_env_with_token(os.environ.copy(), tab_account.token),
            )
        else:
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
        return "user skill at ~/.codex/skills/graphify", "done"
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


_INSTALL_PROGRESS_LIMIT = 58


def _install_progress_value(line: str) -> str | None:
    """Map one line of `uv tool install` output to a compact progress value.

    `+ pkg==ver` / `- pkg==ver` 줄은 패키지 토큰만 남기고, 그 외 비어 있지
    않은 줄은 그대로 쓴다. 빈 줄은 None (직전 값 유지).
    """
    text = line.strip()
    if not text:
        return None
    if text.startswith(("+ ", "- ")):
        text = text[2:].strip()
    if len(text) > _INSTALL_PROGRESS_LIMIT:
        text = text[: _INSTALL_PROGRESS_LIMIT - 1] + "…"
    return text


def _run_tool_install_streaming(
    cmd: list[str],
    *,
    label: str,
    stream: TextIO | None = None,
    popen_fn: Callable[..., object] | None = None,
    tick_interval: float = 0.1,
) -> int:
    """Run a `uv tool install` behind a single in-place progress row.

    uv의 패키지 벽 출력은 캡처해 숨기고, 마지막 의미 있는 줄 하나만 spinner
    행에 갱신해 보여준다. 실패(exit != 0)하면 캡처한 전체 출력을 들여쓰기
    dump로 풀어 원인을 그대로 남긴다. 마지막 상태 행(✓/!)은 호출자 몫.
    """
    out = stream if stream is not None else sys.stdout
    launch = popen_fn if popen_fn is not None else subprocess.Popen
    proc = launch(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    captured: list[str] = []
    state = {"frame": 0, "value": "installing…"}
    write_lock = threading.Lock()

    def redraw() -> None:
        with write_lock:
            label_text = f"\x1b[{MINT}m{label:<10}\x1b[0m"
            out.write(
                f"\r  {style_spinner(state['frame'])} {label_text}"
                f"  {state['value']}\x1b[K"
            )
            out.flush()

    def on_tick(frame: int) -> None:
        state["frame"] = frame
        redraw()

    ticker = SpinnerTicker(on_tick=on_tick, interval=tick_interval)
    ticker.start()
    try:
        for line in proc.stdout:
            captured.append(line)
            value = _install_progress_value(line)
            if value is not None:
                state["value"] = value
                redraw()
        rc = int(proc.wait())
    finally:
        ticker.stop()
    out.write("\r\x1b[K")
    if rc != 0:
        for line in captured:
            out.write("    " + (line if line.endswith("\n") else line + "\n"))
    out.flush()
    return rc


def _serena_cli_install(*, stream: TextIO | None = None) -> int:
    """Install the serena CLI persistently via uv tool.

    Returns the exit code. 2 indicates uv is unavailable.
    """
    cmd = serena_install_command()
    if cmd is None:
        return 2
    return _run_tool_install_streaming(cmd, label="serena cli", stream=stream)


def _graphify_cli_install(*, stream: TextIO | None = None) -> int:
    """Install the graphify CLI persistently via uv tool.

    Returns the exit code. 2 indicates uv is unavailable.
    """
    cmd = graphify_install_command()
    if cmd is None:
        return 2
    return _run_tool_install_streaming(cmd, label="graphify cli", stream=stream)


def _display_uv_command(cmd: list[str]) -> str:
    """Render an absolute-uv argv as the short `uv …` form for prompts."""
    return " ".join(["uv", *cmd[1:]])


def _run_serena_cli_install_v2(
    *,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
    install_fn: Callable[[], int] | None = None,
) -> str:
    """Offer to install the serena CLI when it cannot be resolved.

    Returns one of: 'present', 'installed', 'declined', 'failed',
    'unavailable'. 어떤 결과여도 흐름은 계속된다 — 이후 단계가 uvx
    fallback(project create)과 bare-launch 강등(scoped server)으로 처리한다.
    """
    if serena_server_command() is not None:
        return "present"
    out = stream if stream is not None else sys.stdout
    install_cmd = serena_install_command()
    if install_cmd is None:
        out.write(render_inline_row(
            "serena cli", "uv not found — cannot offer install", status="warn"))
        out.flush()
        return "unavailable"
    if not confirm(
        "serena CLI is not installed — install it? "
        f"({_display_uv_command(install_cmd)})",
        default=True,
        stream=out,
        input_fn=input_fn,
    ):
        return "declined"
    rc = install_fn() if install_fn is not None else _serena_cli_install(stream=out)
    if rc == 0 and serena_server_command() is not None:
        out.write(render_inline_row(
            "serena cli", "installed at ~/.local/bin/serena", status="done"))
        out.flush()
        return "installed"
    message = (
        f"install failed (exit {rc})"
        if rc != 0
        else "install finished but serena is still unresolvable"
    )
    out.write(render_inline_row("serena cli", message, status="warn"))
    out.flush()
    return "failed"


def _run_graphify_cli_install_v2(
    *,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
    install_fn: Callable[[], int] | None = None,
) -> str:
    """Offer to install the graphify CLI when it cannot be resolved.

    Returns one of: 'present', 'installed', 'declined', 'failed',
    'unavailable'.
    """
    if graphify_command() is not None:
        return "present"
    out = stream if stream is not None else sys.stdout
    install_cmd = graphify_install_command()
    if install_cmd is None:
        out.write(render_inline_row(
            "graphify cli", "uv not found — cannot offer install", status="warn"))
        out.flush()
        return "unavailable"
    if not confirm(
        "graphify CLI is not installed — install it? "
        f"({_display_uv_command(install_cmd)})",
        default=True,
        stream=out,
        input_fn=input_fn,
    ):
        return "declined"
    rc = install_fn() if install_fn is not None else _graphify_cli_install(stream=out)
    if rc == 0 and graphify_command() is not None:
        out.write(render_inline_row(
            "graphify cli", "installed at ~/.local/bin/graphify", status="done"))
        out.flush()
        return "installed"
    message = (
        f"install failed (exit {rc})"
        if rc != 0
        else "install finished but graphify is still unresolvable"
    )
    out.write(render_inline_row("graphify cli", message, status="warn"))
    out.flush()
    return "failed"


def _is_git_repo(project_root: Path) -> bool:
    """True if ``project_root`` is inside a git work tree (at or above).

    Matches graphify's "at or above" search: ``graphify hook install`` walks
    up for a ``.git`` and aborts with ``RuntimeError`` when none is found, so
    the launcher must gate the hook step on the same condition.
    """
    proc = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    # Inside a `.git` dir (but outside the work tree) git prints "false" yet
    # exits 0, so the exit code alone is not enough — require a "true" answer.
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _git_init(project_root: Path) -> int:
    """Run ``git init`` in ``project_root``. Returns the exit code."""
    proc = subprocess.run(
        ["git", "init", str(project_root)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode


def _graphify_hook_install(project_root: Path) -> int:
    """Run `graphify hook install` for the given project root.

    Returns the exit code. 2 indicates the graphify CLI is unavailable.
    """
    graphify = graphify_command()
    if graphify is None:
        return 2
    proc = subprocess.run(
        [*graphify, "hook", "install"],
        cwd=str(project_root),
        check=False,
    )
    return proc.returncode


def _graphify_global_install(client: str) -> int:
    """Run `graphify install` (or `graphify install --platform codex`) for the user.

    Returns the exit code. 2 indicates the graphify CLI is unavailable.
    """
    graphify = graphify_command()
    if graphify is None:
        return 2
    cmd = [*graphify, "install"]
    if client == "codex":
        cmd.extend(["--platform", "codex"])
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


def _graphify_integration_install(project_root: Path, client: str) -> int:
    """Run `graphify {claude,codex} install` inside the project.

    Returns the exit code. 2 indicates the graphify CLI is unavailable.
    """
    graphify = graphify_command()
    if graphify is None:
        return 2
    subcommand = "claude" if client == "claude" else "codex"
    proc = subprocess.run(
        [*graphify, subcommand, "install"],
        cwd=str(project_root),
        check=False,
    )
    return proc.returncode


def _client_node_need(client: str) -> NodeNeed:
    """The Node.js need for the active client's plugins/MCP/statusLine."""
    if client == "claude":
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        claude_dir = Path(config_dir) if config_dir else Path.home() / ".claude"
        return node_need(
            "claude", claude_dir=claude_dir, claude_json=Path.home() / ".claude.json"
        )
    return node_need("codex", codex_home=_codex_home_from_env())


def _homebrew_node_present() -> bool:
    """True if node exists at the homebrew path the statusLine hardcodes."""
    return homebrew_node_command() is not None


def _node_runtime_install(*, stream: TextIO | None = None) -> int:
    """Install Node.js via Homebrew. Returns the exit code; 2 means no brew."""
    cmd = node_install_command()
    if cmd is None:
        return 2
    return _run_tool_install_streaming(cmd, label="node", stream=stream)


def _node_need_unmet(
    need: NodeNeed,
    resolve_fn: Callable[[], list[str] | None],
    homebrew_fn: Callable[[], bool],
) -> bool:
    """True if any part of the node need is not satisfied.

    A generic need (npx MCP) is met by any node on PATH; a homebrew need (the
    claude-hud statusLine's hardcoded `/opt/homebrew/bin/node`) is met only by
    a node at that exact path — a PATH node elsewhere does not count.
    """
    generic_unmet = need.generic and resolve_fn() is None
    homebrew_unmet = need.homebrew and not homebrew_fn()
    return generic_unmet or homebrew_unmet


def _run_node_runtime_check_v2(
    client: str,
    *,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
    node_need_fn: Callable[[str], NodeNeed] | None = None,
    resolve_node: Callable[[], list[str] | None] | None = None,
    homebrew_node_present: Callable[[], bool] | None = None,
    install_node: Callable[..., int] | None = None,
) -> None:
    """Offer to install Node.js when the client needs it but it is missing.

    context7/playwright MCP run via `npx` and claude-hud's statusLine via a
    hardcoded `/opt/homebrew/bin/node`; all fail with `os error 2` when node is
    absent. The prompt only fires when a node-based plugin/MCP is actually
    configured and unsatisfied, so users who don't need node are never nagged.
    A node on PATH satisfies npx needs but NOT the statusLine's homebrew path,
    so both are checked independently.
    """
    need_fn = node_need_fn or _client_node_need
    resolve_fn = resolve_node or node_command
    homebrew_fn = homebrew_node_present or _homebrew_node_present
    install_fn = install_node or _node_runtime_install

    need = need_fn(client)
    if not need.any or not _node_need_unmet(need, resolve_fn, homebrew_fn):
        return

    out = stream if stream is not None else sys.stdout
    # Check installability before prompting — don't offer what we can't do
    # (mirrors the serena/graphify CLI-install prompts).
    if node_install_command() is None:
        out.write(render_inline_row(
            "node runtime", "brew not found — install node manually", status="warn"))
        out.flush()
        return

    if not confirm(
        "node-based plugins/MCP need Node.js — run `brew install node`?",
        default=True,
        stream=out,
        input_fn=input_fn,
    ):
        out.write(render_inline_row(
            "node runtime",
            "skipped — node-based plugins/MCP will not start",
            status="warn",
        ))
        out.flush()
        return

    rc = install_fn(stream=out)
    if rc == 0 and not _node_need_unmet(need, resolve_fn, homebrew_fn):
        # Report the path that actually satisfied the need, not a hardcoded
        # guess — a `brew install node` returning 0 without a durable install
        # must not be announced as success at a path that isn't there. A
        # homebrew need is verified at the hardcoded statusLine path; a generic
        # need reports wherever node resolved on PATH.
        if need.homebrew:
            where = HOMEBREW_NODE_PATH
        else:
            resolved = resolve_fn()
            where = resolved[0] if resolved else "node"
        out.write(render_inline_row(
            "node runtime", f"installed at {where}", status="done"))
    else:
        message = (
            f"node install failed (exit {rc})"
            if rc != 0
            else "install finished but node is still unresolvable"
        )
        out.write(render_inline_row("node runtime", message, status="warn"))
    out.flush()


class TabAccount(NamedTuple):
    """A resolved per-tab Claude identity: the account name + the token to inject
    as CLAUDE_CODE_OAUTH_TOKEN into that tab's child process."""

    name: str
    token: str


# Credential sources that OUTRANK CLAUDE_CODE_OAUTH_TOKEN in Claude Code's auth
# precedence — if any is left in the child env it wins and diverts identity/
# billing (API metering), silently defeating the injected subscription token.
_OVERRIDING_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)


def _child_env_with_token(base: dict, token: str) -> dict:
    """A child env = base + injected token − every higher-priority source.

    Copies `base` (never mutates it) so `PATH`, `SERENA_REAL_*`, etc. survive.
    """
    env = dict(base)
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    for key in _OVERRIDING_ENV_VARS:
        env.pop(key, None)
    return env


def _parse_account_rows(stdout: str) -> list[tuple[str, bool]]:
    """Parse `account list --porcelain` into (name, has_token) pairs.

    Format: name<TAB>active<TAB>subscription<TAB>token-flag. Tolerates a 3-column
    row from an older dotsync (treated as no token)."""
    rows: list[tuple[str, bool]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        rows.append((fields[0], len(fields) > 3 and fields[3] == "token"))
    return rows


def _emit_tab_identity(out: TextIO, name: str) -> None:
    """Make this tab's identity unmistakable: a framed row + the terminal tab
    title (via OSC). `claude auth status` can show the global keychain account,
    so the launcher must state the truth itself."""
    out.write(render_inline_row("this tab", f"{name}  · per-tab account", status="done"))
    out.write(f"\x1b]0;claude: {name}\x07")  # set terminal tab/window title
    out.flush()


def _dotsync_account_list_default(argv: list[str]) -> tuple[int, str]:
    result = subprocess.run(
        argv + ["claude", "account", "list", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=3,
    )
    return result.returncode, result.stdout


def _dotsync_account_pick_default(argv: list[str]) -> str | None:
    """Run dotsync's picker (UI on the inherited tty=stderr); capture the chosen
    name from stdout. Returns None on cancel / non-tty / failure."""
    result = subprocess.run(
        argv + ["claude", "account", "pick"], stdout=subprocess.PIPE, text=True
    )
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _dotsync_account_env_default(argv: list[str], name: str) -> str | None:
    result = subprocess.run(
        argv + ["claude", "account", "env", name],
        capture_output=True,
        text=True,
        timeout=3,
    )
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _resolve_tab_account_v2(
    client: str,
    *,
    stream: TextIO | None = None,
    resolve_fn: Callable[[], list[str] | None] | None = None,
    list_fn: Callable[[list[str]], tuple[int, str]] | None = None,
    pick_fn: Callable[[list[str]], "str | None"] | None = None,
    token_fn: Callable[[list[str], str], "str | None"] | None = None,
) -> "TabAccount | None":
    """Resolve which saved account (and token) THIS tab should use, or None.

    Fully best-effort — the primary `claude` launch is never blocked or slowed;
    a missing / old / slow / failing dotsync is swallowed. Returns a
    `TabAccount` only when a token-backed account is chosen; otherwise None (the
    tab falls back to the normal global login). A token-less account that the
    user *explicitly picked* is warned about loudly rather than silently run
    under the global login (that reproduces the "always the same account" bug).
    """
    if client != "claude":
        return None
    dotsync = (resolve_fn or dotsync_command)()
    if not dotsync:
        return None
    lister = list_fn or _dotsync_account_list_default
    try:
        rc, stdout = lister(dotsync)
    except Exception:
        return None  # timeout / spawn failure — skip, never block the launch
    if rc != 0:
        return None  # old dotsync without the subcommand, keychain error, etc.
    accounts = _parse_account_rows(stdout)
    out = stream if stream is not None else sys.stdout
    if not accounts:
        return None

    if len(accounts) == 1:
        name, has_token = accounts[0]
        if not has_token:
            # No per-tab token yet — preserve the prior single-account behavior
            # (info row, no injection). The tab uses the normal global login.
            out.write(render_inline_row("claude account", name, status="info"))
            out.flush()
            return None
        chosen = name
    else:
        picker = pick_fn or _dotsync_account_pick_default
        try:
            chosen = picker(dotsync)
        except Exception:
            return None
        if not chosen:
            return None
        if not dict(accounts).get(chosen, False):
            out.write(
                render_inline_row(
                    "claude account",
                    f"{chosen} has no per-tab token — this tab uses your global "
                    f"login. run `dotsync claude account token set {chosen}`",
                    status="warn",
                )
            )
            out.flush()
            return None

    getter = token_fn or _dotsync_account_env_default
    try:
        token = getter(dotsync, chosen)
    except Exception:
        return None
    if not token:
        return None
    return TabAccount(chosen, token)


def _run_preflight_v2(
    *,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
    serena_state: str = "managed",
    install_graphify_cli: Callable[[], int] | None = None,
    install_graphify_global: Callable[[str], int] | None = None,
    install_graphify_integration: Callable[[Path, str], int] | None = None,
    install_graphify_hooks: Callable[[Path], int] | None = None,
    is_git_repo: Callable[[Path], bool] | None = None,
    init_git: Callable[[Path], int] | None = None,
    node_need_check: Callable[[str], NodeNeed] | None = None,
    resolve_node: Callable[[], list[str] | None] | None = None,
    homebrew_node_present: Callable[[], bool] | None = None,
    install_node: Callable[..., int] | None = None,
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
    is_git_repo_fn = is_git_repo or _is_git_repo
    init_git_fn = init_git or _git_init

    client = os.environ.get("SERENA_AGENT_CLIENT", "codex")
    project_root = Path(os.environ.get("SERENA_AGENT_PROJECT_ROOT", ".")).resolve()

    def _emit(label: str, value: str, *, ok: bool) -> None:
        out.write(render_inline_row(label, value, status="done" if ok else "warn"))
        out.flush()

    # Node runtime is independent of graphify — run it first so the graphify
    # section's early-return (when its CLI is unavailable) can't skip it.
    _run_node_runtime_check_v2(
        client,
        stream=out,
        input_fn=input_fn,
        node_need_fn=node_need_check,
        resolve_node=resolve_node,
        homebrew_node_present=homebrew_node_present,
        install_node=install_node,
    )

    graphify_statuses = {
        os.environ.get("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS", "unknown"),
        os.environ.get("SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS", "unknown"),
        os.environ.get("SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "unknown"),
        os.environ.get("SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "unknown"),
    }
    if "missing" in graphify_statuses:
        # 아래 설치 액션들은 전부 graphify CLI가 필요하다 — 해석이 안 되면
        # 먼저 CLI 설치를 제안하고, 끝내 없으면 graphify 질문 전체를 건너뛴다.
        cli_state = _run_graphify_cli_install_v2(
            stream=out, input_fn=input_fn, install_fn=install_graphify_cli,
        )
        if cli_state in {"declined", "failed", "unavailable"}:
            out.write(render_inline_row(
                "graphify", "cli unavailable . skipping graphify setup",
                status="warn",
            ))
            out.flush()
            return 0

    global_status = os.environ.get(
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS", "unknown"
    )
    global_done = global_status not in {"missing", "unknown"}
    if global_status == "missing":
        cmd = "graphify install" if client == "claude" else "graphify install --platform codex"
        if confirm(
            f"graphify global skill is not installed — install it? ({cmd})",
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
                    _emit("graphify global", "user skill at ~/.codex/skills/graphify", ok=True)
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
            f"graphify is not wired into this project — set it up? ({cmd})",
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
        # graphify hooks are git post-commit/post-checkout hooks, so a git repo
        # is a hard prerequisite — `graphify hook install` aborts otherwise. When
        # the project isn't a repo yet, offer a one-line `git init` instead of
        # letting the install fail; consenting to that implies installing hooks.
        if is_git_repo_fn(project_root):
            proceed = confirm(
                "Install graphify hooks for this project?",
                default=True,
                stream=out,
                input_fn=input_fn,
            )
        elif confirm(
            "graphify hooks need a git repo — run `git init` here?",
            default=True,
            stream=out,
            input_fn=input_fn,
        ):
            rc_init = init_git_fn(project_root)
            if rc_init == 0:
                _emit("git", "initialized empty repo", ok=True)
                proceed = True
            else:
                _emit("git", f"git init failed (exit {rc_init})", ok=False)
                proceed = False
        else:
            _emit("graphify hook",
                  "skipped: needs a git repo — run `git init` first", ok=False)
            proceed = False

        if proceed:
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


def _serena_project_create(project_root: Path) -> tuple[int, str]:
    """Run `serena project create <root>` quietly, feeding default answers.

    Serena's CLI is verbose (language auto-detection, interactive language
    prompts auto-answered via `yes ""`, a stale last-project "skipping" notice,
    and a Pydantic-on-3.14 UserWarning). Capture all of it and silence the
    warning so the launcher's box UI stays clean; the caller surfaces a single
    status row and dumps this output only on failure.

    Returns:
        (exit_code, captured_output). exit_code 2 means no serena runner.
    """
    serena = serena_oneshot_command()
    if serena is None:
        return 2, ""
    env = {**os.environ, "PYTHONWARNINGS": "ignore"}
    yes_proc = subprocess.Popen(["yes", ""], stdout=subprocess.PIPE)
    try:
        proc = subprocess.run(
            [*serena, "project", "create", str(project_root)],
            stdin=yes_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
    finally:
        if yes_proc.stdout is not None:
            yes_proc.stdout.close()
        yes_proc.terminate()
        yes_proc.wait()
    return proc.returncode, proc.stdout or ""


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

    rc, output = _serena_project_create(project_root)
    if rc != 0 or not (project_root / ".serena" / "project.yml").exists():
        out.write("  ! serena    failed    . launching without Serena project config\n")
        for line in output.splitlines():
            out.write("    " + line + "\n")
        out.flush()
        return "failed"
    out.write(render_inline_row("serena", "project created", status="done"))
    out.flush()
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
