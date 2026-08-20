"""Launch Codex or Claude with a scoped Serena MCP server."""
from __future__ import annotations

import json
import os
import re
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
from typing import Literal, TextIO, TypedDict

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from local_dev.serena_mcp_management.external_cli import (
    graphify_command,
    graphify_install_command,
    graphify_upgrade_command,
    homebrew_node_command,
    node_command,
    node_install_command,
    serena_install_command,
    serena_oneshot_command,
    serena_server_command,
)
from local_dev.serena_mcp_management.codex_reset import (
    CodexResetResult,
    reset_all_codex_data,
    scan_codex_session_catalog,
)
from local_dev.serena_mcp_management.claude_reset import (
    ClaudeResetResult,
    reset_all_claude_data,
)
from local_dev.serena_mcp_management.memory_management import (
    MemoryInventory,
    scan_memory_inventory,
)
from local_dev.serena_mcp_management.graphify_version import (
    MINIMUM_VERSION as GRAPHIFY_MINIMUM_VERSION,
    installed_version as inspect_graphify_version,
    latest_version as latest_graphify_version,
    version_key as graphify_version_key,
)
from local_dev.serena_mcp_management.node_preflight import (
    HOMEBREW_NODE_PATH,
    NodeNeed,
    node_need,
)
from local_dev.serena_mcp_management.serena_mcp.diagnostics import snapshot_global_lifecycle
from local_dev.serena_mcp_management.serena_mcp.paths import (
    Scope,
    find_project_root,
    serena_opted_in,
)
from local_dev.serena_mcp_management.serena_mcp.registry import (
    locked_registry,
    record_belongs_to_scope,
    refresh_existing_lease,
)
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
    CountStats,
    RETENTION_DAYS,
    scan_claude_inventory,
)
from local_dev.serena_mcp_management.ui import (
    MINT,
    PINK,
    PURPLE,
    AMBER,
    BoxModel,
    BoxRenderer,
    Item,
    SelectOption,
    SpinnerTicker,
    confirm,
    render_inline_row,
    select_option,
    style_action_value,
    style_memory_tree,
    style_mcp_inventory,
    style_session_tree,
    style_spinner,
)
from local_dev.serena_mcp_management.worktree_setup import (
    WorktreeSetupError,
    install_worktree_setup_hook,
    worktree_setup_available,
    worktree_setup_installed,
)


@dataclass(frozen=True)
class LaunchPrepSummary:
    """Summary of the v2 launch-prep phase."""

    cleanup_deleted: int = 0
    conversation_reset: bool = False
    reset_trace_targets: int = 0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class InventorySnapshot:
    """Session and memory results shared by preflight display and cleanup."""

    inventory: AgentInventory | None
    error: str | None = None
    memory_inventory: MemoryInventory | None = None
    memory_error: str | None = None


class _AcquiredRecordPresentationInterrupted(Exception):
    """Carry a record across a post-acquisition KeyboardInterrupt boundary."""

    def __init__(self, record, interrupt: KeyboardInterrupt) -> None:
        super().__init__(str(interrupt))
        self.record = record
        self.interrupt = interrupt


class _MemoryScanKwargs(TypedDict):
    client: str
    home: Path
    codex_home: Path
    claude_config_dir: Path | None


def _codex_home_from_env() -> Path:
    """Return a safe absolute Codex home path from CODEX_HOME or the default."""
    value = os.environ.get("CODEX_HOME")
    codex_home = Path(value).expanduser() if value else Path.home() / ".codex"
    if not codex_home.is_absolute():
        raise ValueError("codex_home must be absolute")
    return codex_home


def _memory_scan_kwargs(client: str) -> _MemoryScanKwargs:
    codex_home = Path.home() / ".codex" if client == "claude" else _codex_home_from_env()
    claude_config_value = os.environ.get("CLAUDE_CONFIG_DIR")
    if claude_config_value == "":
        raise ValueError("CLAUDE_CONFIG_DIR must not be empty")
    claude_config_dir = (
        Path(claude_config_value).expanduser() if claude_config_value else None
    )
    return {
        "client": client,
        "home": Path.home(),
        "codex_home": codex_home,
        "claude_config_dir": claude_config_dir,
    }


def _inventory_for_preflight(client: str, project_root: str) -> AgentInventory:
    if client == "codex":
        scan_kwargs = _memory_scan_kwargs("codex")
        catalog = scan_codex_session_catalog(
            home=scan_kwargs["home"],
            codex_home=scan_kwargs["codex_home"],
        )
        record_total = sum(
            len(owner.delete_ids)
            for session in catalog.sessions
            for owner in session.owners
        )
        return AgentInventory(
            client="codex",
            sessions=CountStats(
                total=len(catalog.sessions),
                to_delete=0,
                to_keep=len(catalog.sessions),
            ),
            records=CountStats(
                total=record_total,
                to_delete=0,
                to_keep=record_total,
            ),
            criteria="sessions: all known homes + full reset only",
            warnings=catalog.warnings,
        )
    scan_kwargs = _memory_scan_kwargs("claude")
    inventory = scan_claude_inventory(
        home=scan_kwargs["home"],
        claude_config_dir=scan_kwargs["claude_config_dir"],
    )
    records = inventory.records or inventory.sessions
    return AgentInventory(
        client="claude",
        sessions=CountStats(
            total=inventory.sessions.total,
            to_delete=0,
            to_keep=inventory.sessions.total,
        ),
        records=CountStats(
            total=records.total,
            to_delete=0,
            to_keep=records.total,
        ),
        criteria="sessions: all projects + full reset only",
        warnings=inventory.warnings,
    )


def _memory_inventory_for_preflight(client: str) -> MemoryInventory:
    return scan_memory_inventory(**_memory_scan_kwargs(client))


def _sessions_value(inventory: AgentInventory) -> str:
    records = inventory.records or inventory.sessions
    groups = None
    cleanup_note = ""
    if inventory.client == "codex":
        groups = (
            inventory.sessions.total,
            inventory.sessions.to_delete,
            inventory.sessions.to_keep,
        )
    else:
        cleanup_note = "native Claude cleanup"
    if inventory.criteria.endswith("full reset only"):
        return style_session_tree(
            client=inventory.client,
            groups=groups,
            records=(
                records.total,
                records.to_delete,
                records.to_keep,
            ),
            condition="full reset on confirmation · no automatic deletion",
        )
    return style_session_tree(
        client=inventory.client,
        groups=groups,
        records=(records.total, records.to_delete, records.to_keep),
        condition=f"inactive longer than {RETENTION_DAYS} days",
        cleanup_note=cleanup_note,
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
    client_type: str | None = None,
    real_binary: str | None = None,
) -> int:
    """Run the real agent binary without the scoped Serena MCP server."""

    resolved_client = client_type or infer_client_type(
        os.environ.get("SERENA_AGENT_CLIENT", sys.argv[0])
    )
    resolved_binary = real_binary or find_real_binary(resolved_client)
    child_args = list(args)
    if os.environ.get("SERENA_AGENT_CLEAR_BEFORE_CHILD") == "1":
        clear_terminal_before_child()
    return int(subprocess.run([resolved_binary, *child_args]).returncode)


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
    try:
        return _main_v2(args)
    except KeyboardInterrupt:
        sys.stdout.write("\r\x1b[J  ! cancelled\n")
        sys.stdout.flush()
        return 130


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
    out.write(f"  \x1b[{PURPLE}m·\x1b[0m serena     preparing shared worktree server")
    out.flush()
    frame_state = {"frame": 0}

    def on_tick(frame: int) -> None:
        frame_state["frame"] = frame
        out.write(f"\r  {style_spinner(frame)} serena     preparing shared worktree server")
        out.flush()

    ticker = SpinnerTicker(on_tick=on_tick, interval=0.1)
    ticker.start()
    try:
        record = ensure_server(scope, lease)
    except BaseException:
        try:
            ticker.stop()
        except BaseException:
            pass
        raise
    presentation_interrupt: KeyboardInterrupt | None = None
    try:
        ticker.stop()
    except KeyboardInterrupt as exc:
        presentation_interrupt = exc
    except Exception:
        pass
    if presentation_interrupt is None:
        try:
            out.write(f"\r  \x1b[{PINK}m✓\x1b[0m serena     ready      . {record.mcp_url}\n")
            out.flush()
        except KeyboardInterrupt as exc:
            presentation_interrupt = exc
        except Exception:
            pass
    if presentation_interrupt is not None:
        raise _AcquiredRecordPresentationInterrupted(record, presentation_interrupt) from presentation_interrupt
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
    mcp_lifecycle: str,
    warnings: list[str],
    conversation_reset: bool = False,
    reset_trace_targets: int = 0,
) -> None:
    if conversation_reset:
        sessions_value = (
            f"{cleanup_deleted} sessions deleted · "
            f"{reset_trace_targets} conversation-state targets reset"
        )
    else:
        sessions_value = "sessions and memories kept"
    items = [
        Item(
            id="duration",
            label="duration",
            value=_format_duration(duration_seconds),
            status="done",
        ),
        Item(
            id="sessions",
            label="sessions",
            value=style_action_value(sessions_value, accent=AMBER),
            status="done",
        ),
        Item(
            id="mcp",
            label="serena",
            value=f"shared worktree server {mcp_lifecycle}",
            status="done",
        ),
    ]
    for index, message in enumerate(warnings):
        items.append(
            Item(
                id=f"warn-{index}",
                label="warning",
                value=message,
                status="warn",
            )
        )
    model = BoxModel(phase="summary", title=client, items=items)
    BoxRenderer(stream=stream).draw(model)


def _main_v2(args: list[str]) -> int:
    """v2 box-model TUI flow."""
    started_at = time.time()
    warnings: list[str] = []
    interactive = os.environ.get("SERENA_AGENT_INTERACTIVE") == "1"
    out = sys.stdout
    client_type = infer_client_type(
        os.environ.get("SERENA_AGENT_CLIENT", sys.argv[0])
    )
    discovered_root = find_project_root(Path.cwd())
    root_hint = _project_root_from_environment()
    project_root = root_hint if root_hint == discovered_root else discovered_root
    os.environ["SERENA_AGENT_PROJECT_ROOT"] = str(project_root)

    real_binary = find_real_binary(client_type)
    if interactive:
        _render_preflight_overview_v2()

    if not serena_opted_in(project_root):
        if not interactive:
            return _launch_bare_child(
                args,
                client_type=client_type,
                real_binary=real_binary,
            )
        serena_state = _run_serena_init_v2(project_root=project_root)
    else:
        serena_state = "managed"

    if interactive:
        rc = _run_preflight_v2(serena_state=serena_state)
        if rc != 0:
            return rc
        _run_worktree_setup_v2(project_root)

    session_choice = _run_session_choice_v2()

    if interactive and session_choice == "reset_all":
        if client_type == "codex":
            reset_result = _run_codex_reset_v2(
                stream=out,
                child_args=tuple(args),
                working_directory=project_root,
            )
            reset_trace_targets = reset_result.deleted_trace_targets
        else:
            reset_result = _run_claude_reset_v2(
                stream=out,
                project_root=project_root,
            )
            reset_trace_targets = (
                reset_result.deleted_memory_stores
                + reset_result.deleted_residual_targets
            )
        reset_warnings = list(reset_result.warnings)
        if not reset_result.succeeded:
            reset_warnings.append(
                f"sessions: {reset_result.error or f'{client_type.title()} reset failed'}"
            )
        summary_state = LaunchPrepSummary(
            cleanup_deleted=reset_result.deleted_sessions,
            conversation_reset=True,
            reset_trace_targets=reset_trace_targets,
            warnings=tuple(reset_warnings),
        )
        if not reset_result.succeeded:
            return 1
    elif interactive:
        summary_state = LaunchPrepSummary()
    else:
        summary_state = None
    if summary_state is not None:
        warnings.extend(summary_state.warnings)

    serena_ready = (
        serena_state in {"managed", "created"}
        and serena_opted_in(project_root)
    )
    if not serena_ready:
        if serena_state != "skipped":
            warnings.append(f"serena project create {serena_state}")
        return _launch_bare_child(
            args,
            client_type=client_type,
            real_binary=real_binary,
        )

    if serena_server_command() is None:
        out.write(
            "  ! serena    unavailable . serena CLI not found —"
            " launching without scoped server\n"
        )
        out.flush()
        return _launch_bare_child(
            args,
            client_type=client_type,
            real_binary=real_binary,
        )

    scope = Scope(project_root)
    lease_id = str(uuid.uuid4())
    lease = make_launcher_lease(lease_id, client_type)
    stop: threading.Event | None = None
    cleanup: Callable[[], None] = lambda: None
    child: subprocess.Popen | None = None
    stats: ShutdownStats | None = None

    try:
        record = (
            _start_mcp_with_spinner(scope=scope, lease=lease)
            if interactive
            else ensure_server(scope, lease)
        )
    except _AcquiredRecordPresentationInterrupted as exc:
        try:
            _release_acquired_record(
                interactive=interactive,
                scope=scope,
                lease_id=lease_id,
                server_instance_id=exc.record.server_instance_id,
            )
        except KeyboardInterrupt:
            pass
        raise exc.interrupt
    except Exception as exc:
        message = f"shared worktree server unavailable: {exc}"
        warnings.append(message)
        out.write(f"  ! serena    unavailable . {message}\n")
        out.flush()
        return _launch_bare_child(
            args,
            client_type=client_type,
            real_binary=real_binary,
        )

    try:
        stop = threading.Event()
        heartbeat = threading.Thread(
            target=_heartbeat_loop,
            args=(scope, lease_id, record.server_instance_id, stop),
            daemon=True,
        )
        heartbeat.start()
        cmd, cleanup = build_child_command(
            client_type=client_type,
            real_binary=real_binary,
            mcp_url=record.mcp_url,
            child_args=args,
        )
        open_dashboard_if_requested(record.dashboard_url)
        if os.environ.get("SERENA_AGENT_CLEAR_BEFORE_CHILD") == "1":
            clear_terminal_before_child()
        child = subprocess.Popen(
            cmd,
            cwd=str(project_root),
            start_new_session=True,
        )

        def shutdown(signum=None, frame=None):
            stop.set()
            if child is not None and child.poll() is None:
                child.terminate()

        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, shutdown)
        rc = int(child.wait())
    finally:
        primary_exception_active = sys.exc_info()[0] is not None
        if stop is not None:
            stop.set()
        child_cleanup_error: BaseException | None = None
        if child is not None and child.poll() is None:
            try:
                _terminate_and_reap_owned_child(child)
            except BaseException as exc:
                child_cleanup_error = exc
                warnings.append(f"client process cleanup failed: {exc}")
        try:
            cleanup()
        except BaseException as exc:
            warnings.append(f"client MCP config cleanup failed: {exc}")
        release_error: BaseException | None = None
        try:
            stats = _release_acquired_record(
                interactive=interactive,
                scope=scope,
                lease_id=lease_id,
                server_instance_id=record.server_instance_id,
            )
        except BaseException as exc:
            release_error = exc
        if release_error is not None:
            warnings.append(f"serena lease release failed: {release_error}")
            if not primary_exception_active:
                if child_cleanup_error is None:
                    raise release_error
        if child_cleanup_error is not None and not primary_exception_active:
            raise child_cleanup_error

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
        conversation_reset = (
            summary_state.conversation_reset if summary_state else False
        )
        reset_trace_targets = (
            summary_state.reset_trace_targets if summary_state else 0
        )
        _render_summary_v2(
            stream=out,
            client=client_type,
            duration_seconds=time.time() - started_at,
            cleanup_deleted=cleanup_deleted,
            mcp_lifecycle=mcp_lifecycle,
            warnings=warnings,
            conversation_reset=conversation_reset,
            reset_trace_targets=reset_trace_targets,
        )
    return rc


def _project_root_from_environment() -> Path | None:
    value = os.environ.get("SERENA_AGENT_PROJECT_ROOT")
    if not value:
        return None
    return Path(value).resolve()


def _terminate_and_reap_owned_child(
    child: subprocess.Popen,
    *,
    timeout: float = 3.0,
) -> None:
    """Stop and reap the directly owned agent process group."""

    if child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            if child.poll() is None:
                child.terminate()
    try:
        child.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    if child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            if child.poll() is None:
                child.kill()
    child.wait(timeout=timeout)



def _short_path(path: str) -> str:
    """Convert an absolute path to a tilde-abbreviated version."""
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def _graphify_cli_value(status: str) -> tuple[str, str]:
    """Return (value, item_status) for the graphify cli preflight row.

    Every other graphify row is a file-presence probe, so all four keep
    reporting ✓ when only the executable disappears (a clean macOS reinstall
    restores dotfiles but not `uv tool` installs). Without this row the screen
    carries no clue while `graphify query` is in fact unusable.
    """
    if status == "installed":
        return "graphify on PATH", "done"
    return 'cli not installed . run "uv tool install graphifyy"', "warn"


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
    return 'hooks missing or outdated . run "graphify hook install"', "warn"


def _serena_mcp_status(snapshot) -> str:
    if snapshot.scan_failed:
        return "warn"
    if snapshot.orphan_server_count > 0 or snapshot.stale_lease_count > 0:
        return "warn"
    if snapshot.ps_server_count == 0:
        return "info"
    return "done"


def _capture_inventory_snapshot(
    client: str,
    project_root: str,
) -> InventorySnapshot:
    inventory = None
    error = None
    try:
        inventory = _inventory_for_preflight(client, project_root)
    except Exception as exc:
        error = str(exc) or exc.__class__.__name__

    memory_inventory = None
    memory_error = None
    try:
        memory_inventory = _memory_inventory_for_preflight(client)
    except Exception as exc:
        memory_error = str(exc) or exc.__class__.__name__

    return InventorySnapshot(
        inventory=inventory,
        error=error,
        memory_inventory=memory_inventory,
        memory_error=memory_error,
    )


def _preflight_box(
    snapshot: InventorySnapshot | None = None,
) -> BoxModel:
    """Build a BoxModel for the v2 preflight phase."""
    client = os.environ.get("SERENA_AGENT_CLIENT", "codex")
    project_root = os.environ.get("SERENA_AGENT_PROJECT_ROOT", "")
    serena_status = os.environ.get("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")

    cli_status = os.environ.get(
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_CLI_STATUS", "unknown"
    )
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

    cli_value, cli_item_status = _graphify_cli_value(cli_status)
    global_value, global_item_status = _graphify_global_value(client, global_status)
    graph_value, graph_item_status = _graphify_graph_value(client, graph_status)
    integration_value, integration_item_status = _graphify_integration_value(
        client, integration_status
    )
    hook_value, hook_item_status = _graphify_hook_value(hook_status)
    inventory_snapshot = snapshot or _capture_inventory_snapshot(client, project_root)
    if inventory_snapshot.inventory is not None:
        inventory = inventory_snapshot.inventory
        sessions_value = _sessions_value(inventory)
        sessions_item_status = "info"
    else:
        detail = inventory_snapshot.error or "inventory unavailable"
        sessions_value = f"scan unavailable: {detail}"
        sessions_item_status = "warn"

    memory_item_status: Literal["info", "warn"]
    if inventory_snapshot.memory_inventory is not None:
        memory_inventory = inventory_snapshot.memory_inventory
        memory_value = style_memory_tree(
            client=memory_inventory.client,
            stores=len(memory_inventory.stores),
            files=memory_inventory.file_count,
            scope=memory_inventory.scope,
        )
        memory_item_status = "warn" if memory_inventory.warnings else "info"
    else:
        detail = inventory_snapshot.memory_error or "inventory unavailable"
        memory_value = f"scan unavailable: {detail}"
        memory_item_status = "warn"

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
            id="graphify-cli",
            label="graphify cli",
            value=cli_value,
            status=cli_item_status,
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
            value="shared-cli (oaicompat-agent)",
            status="info",
        ),
        Item(
            id="memory",
            label="memory",
            value=memory_value,
            status=memory_item_status,
        ),
        Item(
            id="sessions",
            label="sessions",
            value=sessions_value,
            status=sessions_item_status,
        ),
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
    try:
        proc = launch(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        out.write(render_inline_row(
            label, f"could not start: {exc}", status="warn"
        ))
        out.flush()
        return 127
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
    'unavailable'. 실패하거나 거절하면 프로젝트 마커를 만들지 않고
    bare-launch로 강등한다.
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


def _run_graphify_version_check_v2(
    *,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
) -> str:
    """Check the installed Graphify version for an opted-in project."""
    installed = _graphify_installed_version()
    installed_key = graphify_version_key(installed)
    if installed is None or installed_key is None:
        return "unknown"
    out = stream if stream is not None else sys.stdout
    minimum_key = graphify_version_key(GRAPHIFY_MINIMUM_VERSION)
    assert minimum_key is not None
    below_minimum = installed_key < minimum_key
    if below_minimum:
        out.write(render_inline_row(
            "graphify version",
            f"{installed} is below minimum {GRAPHIFY_MINIMUM_VERSION} for "
            "linked-worktree-safe hooks",
            status="warn",
        ))
        out.flush()

    latest = _graphify_latest_version()
    latest_key = graphify_version_key(latest)
    if latest is None or latest_key is None:
        return "unsupported" if below_minimum else "unknown"
    if latest_key <= installed_key:
        return "unsupported" if below_minimum else "current"

    if graphify_upgrade_command() is None:
        out.write(render_inline_row(
            "graphify version",
            f"update available: {installed} → {latest} . uv not found; "
            "upgrade graphifyy manually",
            status="warn",
        ))
        out.flush()
        return "unavailable"

    if not confirm(
        f"Graphify update available: {installed} → {latest}. "
        "Upgrade Graphify now? Existing installed Graphify components "
        "will also be refreshed.",
        default=False,
        stream=out,
        input_fn=input_fn,
    ):
        return "declined"
    rc = _graphify_cli_upgrade(stream=out)
    if rc != 0:
        out.write(render_inline_row(
            "graphify version", f"upgrade failed (exit {rc})", status="warn"
        ))
        out.flush()
        return "failed"
    upgraded = _graphify_installed_version()
    if upgraded is None:
        out.write(render_inline_row(
            "graphify version",
            "upgrade finished but the installed version is unavailable",
            status="warn",
        ))
        out.flush()
        return "failed"
    upgraded_key = graphify_version_key(upgraded)
    if upgraded_key is None:
        return "failed"
    if upgraded_key <= installed_key:
        out.write(render_inline_row(
            "graphify version",
            f"upgrade finished but version is still {upgraded}",
            status="warn",
        ))
        out.flush()
        return "failed"
    out.write(render_inline_row(
        "graphify version", f"updated to {upgraded}", status="done"
    ))
    out.flush()
    return "upgraded"


def _graphify_installed_version() -> str | None:
    """Return the installed Graphify version when it can be inspected."""
    return inspect_graphify_version(graphify_command())


def _graphify_latest_version(
    *,
    cache_path: Path | None = None,
    now: float | None = None,
    fetch_version: Callable[[], str | None] | None = None,
) -> str | None:
    """Return the latest Graphify version with a 24-hour user cache."""
    return latest_graphify_version(
        cache_path=cache_path,
        now=now,
        fetch_version=fetch_version,
    )


def _graphify_cli_upgrade(*, stream: TextIO | None = None) -> int:
    """Upgrade the persistent Graphify tool installation."""
    cmd = graphify_upgrade_command()
    if cmd is None:
        return 2
    return _run_tool_install_streaming(cmd, label="graphify cli", stream=stream)


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


def _graphify_graph_create(project_root: Path) -> tuple[int, str]:
    """Create the initial code graph with Graphify's headless CLI.

    The launcher cannot invoke the interactive ``/graphify`` agent skill before
    the child agent starts. ``graphify update`` is Graphify's deterministic,
    no-LLM bootstrap for source code and creates the same graph.json consumed by
    query/explain and the post-commit hooks. Documentation and image semantics
    remain an explicit agent-side ``/graphify --update`` operation.
    """
    graphify = graphify_command()
    if graphify is None:
        return 2, "graphify CLI is unavailable\n"
    try:
        proc = subprocess.run(
            [*graphify, "update", str(project_root)],
            cwd=str(project_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return 127, f"{exc}\n"
    return proc.returncode, proc.stdout or ""


def _resolved_git_path(project_root: Path, flag: str) -> Path | None:
    """Resolve one ``git rev-parse`` path without assuming its path style."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", flag],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    path = Path(proc.stdout.strip())
    if not path.is_absolute():
        path = project_root / path
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _is_linked_worktree(project_root: Path) -> bool:
    """True when ``project_root`` is a linked, rather than primary, worktree."""
    git_dir = _resolved_git_path(project_root, "--git-dir")
    common_dir = _resolved_git_path(project_root, "--git-common-dir")
    if git_dir is not None and common_dir is not None:
        return git_dir != common_dir

    # A linked worktree normally stores a ``gitdir: ...`` pointer in a .git
    # file. If git itself cannot be executed, keep the primary-only invariant
    # by treating that file shape conservatively as a linked worktree.
    try:
        return (project_root / ".git").is_file()
    except OSError:
        return False


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


def _refresh_graphify_after_upgrade_v2(
    *,
    client: str,
    project_root: Path,
    linked_worktree: bool,
    global_status: str,
    integration_status: str,
    hook_status: str,
    install_global: Callable[[str], int],
    install_integration: Callable[[Path, str], int],
    install_hooks: Callable[[Path], int],
    stream: TextIO,
) -> None:
    """Refresh existing Graphify components covered by upgrade consent."""
    def refresh(label: str, detail: str, action: Callable[[], int]) -> None:
        try:
            rc = action()
        except OSError as exc:
            stream.write(render_inline_row(
                label, f"refresh failed: {exc}", status="warn"
            ))
            stream.flush()
            return
        if rc == 0:
            stream.write(render_inline_row(label, detail, status="done"))
        else:
            stream.write(render_inline_row(
                label, f"refresh failed (exit {rc})", status="warn"
            ))
        stream.flush()

    if global_status == "installed":
        refresh(
            "graphify global",
            "user skill refreshed",
            lambda: install_global(client),
        )

    if linked_worktree:
        if integration_status == "installed" or hook_status == "installed":
            integration_cmd = (
                "graphify claude install"
                if client == "claude"
                else "graphify codex install"
            )
            stream.write(render_inline_row(
                "graphify",
                "linked worktree remains query-only . refresh from primary: "
                f"{integration_cmd}; graphify hook install",
                status="info",
            ))
            stream.flush()
        return

    if integration_status == "installed":
        refresh(
            "graphify integration",
            "project integration refreshed",
            lambda: install_integration(project_root, client),
        )
    if hook_status == "installed":
        refresh(
            "graphify hook",
            "hooks refreshed",
            lambda: install_hooks(project_root),
        )


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
    (one of ``managed``/``created``/``skipped``/``failed``). It is accepted
    for signature compatibility with existing callers only: the integration
    prompt no longer derives its default from Serena state. A missing graph's
    explicit project opt-in makes the remaining Graphify setup prompts default
    to Yes; repair prompts for an already-built graph keep the previous safe
    default of No.

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

    graph_status = os.environ.get(
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS", "unknown"
    )
    initialize_graph = graph_status == "missing"
    linked_worktree = _is_linked_worktree(project_root)
    if initialize_graph:
        if not confirm(
            "Initialize Graphify for this project?",
            default=False,
            stream=out,
            input_fn=input_fn,
        ):
            out.write(render_inline_row(
                "graphify", "Graphify disabled for this project", status="info"
            ))
            out.flush()
            return 0
        if linked_worktree:
            out.write(render_inline_row(
                "graphify",
                "initialize Graphify from the primary checkout",
                status="warn",
            ))
            out.flush()
            return 0

    # The CLI is checked alongside the four file-presence rows, not derived from
    # them: those four probe files that survive a CLI uninstall, so a missing
    # executable alone used to leave this set at {"installed", "built"} and the
    # whole recovery block below never ran.
    graphify_statuses = {
        os.environ.get("SERENA_AGENT_PREFLIGHT_GRAPHIFY_CLI_STATUS", "unknown"),
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

    version_state = _run_graphify_version_check_v2(
        stream=out, input_fn=input_fn
    )
    if version_state == "upgraded":
        _refresh_graphify_after_upgrade_v2(
            client=client,
            project_root=project_root,
            linked_worktree=linked_worktree,
            global_status=os.environ.get(
                "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS", "unknown"
            ),
            integration_status=os.environ.get(
                "SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "unknown"
            ),
            hook_status=os.environ.get(
                "SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS", "unknown"
            ),
            install_global=install_global_fn,
            install_integration=install_integration_fn,
            install_hooks=install_hook_fn,
            stream=out,
        )

    if initialize_graph:
        graph_path = project_root / "graphify-out" / "graph.json"
        marker_existed_before_build = os.path.lexists(graph_path)
        rc, captured = _graphify_graph_create(project_root)
        if rc != 0 or not graph_path.is_file():
            if (
                rc != 0
                and not marker_existed_before_build
                and os.path.lexists(graph_path)
            ):
                try:
                    graph_path.unlink()
                except OSError as exc:
                    captured += f"could not remove partial graph marker: {exc}\n"
            for line in captured.splitlines():
                out.write(f"    {line}\n")
            detail = f"initial graph build failed (exit {rc})"
            if rc == 0:
                detail = "initial graph build failed: graph.json was not created"
            _emit("graphify graph", detail, ok=False)
            return 0
        _emit("graphify graph", "graphify-out/graph.json created", ok=True)

    global_status = os.environ.get(
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS", "unknown"
    )
    if global_status == "missing":
        cmd = "graphify install" if client == "claude" else "graphify install --platform codex"
        if confirm(
            f"graphify global skill is not installed — install it? ({cmd})",
            default=initialize_graph,
            stream=out,
            input_fn=input_fn,
        ):
            rc = install_global_fn(client)
            if rc == 0:
                if client == "claude":
                    _emit("graphify global", "user skill at ~/.claude/skills/graphify", ok=True)
                else:
                    _emit("graphify global", "user skill at ~/.codex/skills/graphify", ok=True)
            else:
                _emit("graphify global", f"global install failed (exit {rc})", ok=False)

    if linked_worktree:
        out.write(render_inline_row(
            "graphify",
            "linked worktree is query-only . run project setup from the primary checkout",
            status="info",
        ))
        out.flush()
        return 0

    integration_status = os.environ.get(
        "SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS", "unknown"
    )
    integration_present = integration_status == "installed"
    if integration_status == "missing":
        cmd = (
            "graphify claude install" if client == "claude" else "graphify codex install"
        )
        # A fresh graph was explicitly approved at the project-level gate, so
        # Enter completes that setup. Existing opted-in projects still use No
        # for repair prompts to avoid surprising project-file changes.
        integration_default = initialize_graph
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


def _run_worktree_setup_v2(
    project_root: Path,
    *,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
) -> None:
    """Offer an explicit primary-checkout hook for future worktrees."""
    if os.environ.get("SERENA_AGENT_INTERACTIVE") != "1":
        return
    if not worktree_setup_available(project_root):
        return
    if worktree_setup_installed(project_root):
        return

    scopes = [".env.local copy"]
    if (project_root / ".serena" / "project.yml").is_file():
        scopes.append("Serena config + shared memories")
    if (project_root / "graphify-out" / "graph.json").is_file():
        scopes.append("Graphify query snapshot")

    out = stream if stream is not None else sys.stdout
    out.write(render_inline_row(
        "worktree setup",
        "future linked worktrees: " + " · ".join(scopes),
        status="info",
    ))
    out.flush()
    if not confirm(
        "Set up future Git worktrees automatically?",
        default=False,
        stream=out,
        input_fn=input_fn,
    ):
        return

    try:
        install_worktree_setup_hook(project_root)
    except WorktreeSetupError as exc:
        out.write(render_inline_row(
            "worktree setup",
            f"not installed: {exc}",
            status="warn",
        ))
    else:
        out.write(render_inline_row(
            "worktree setup",
            "installed · " + " · ".join(scopes),
            status="done",
        ))
    out.flush()


def _render_preflight_overview_v2(
    *,
    stream: TextIO | None = None,
) -> InventorySnapshot | None:
    """Draw the workspace overview and return its single inventory snapshot."""

    if os.environ.get("SERENA_AGENT_INTERACTIVE") != "1":
        return None
    client = os.environ.get("SERENA_AGENT_CLIENT", "codex")
    project_root = os.environ.get("SERENA_AGENT_PROJECT_ROOT", "")
    snapshot = _capture_inventory_snapshot(client, project_root)
    out = stream if stream is not None else sys.stdout
    BoxRenderer(stream=out).draw(_preflight_box(snapshot))
    return snapshot


def _run_session_choice_v2(
    *,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
) -> Literal["keep", "reset_all"]:
    """Choose the product-wide session policy before launch."""
    if os.environ.get("SERENA_AGENT_INTERACTIVE") != "1":
        return "keep"
    out = stream if stream is not None else sys.stdout
    client = os.environ.get("SERENA_AGENT_CLIENT", "codex")
    product = "Codex" if client == "codex" else "Claude"
    reset_choice = select_option(
        f"Reset {product} sessions and memories before launch?",
        options=(
            SelectOption(
                "keep",
                "Keep all sessions and memories (default)",
            ),
            SelectOption(
                "reset",
                "Delete all sessions, memories, and conversation traces",
            ),
        ),
        default_index=0,
        accent=AMBER,
        stream=out,
        input_fn=input_fn,
    )
    if reset_choice == "keep":
        return "keep"
    if reset_choice != "reset":
        raise RuntimeError(
            f"unsupported {product} reset choice: {reset_choice}"
        )
    if client == "codex":
        confirmed = confirm(
            "Permanently delete ALL Codex sessions, memories, history, "
            "logs, snapshots, and currently running sessions? "
            "The Codex app will be restarted if it is open.",
            default=False,
            stream=out,
            input_fn=input_fn,
        )
        return "reset_all" if confirmed else "keep"
    confirmed = confirm(
        "Permanently delete all known local Claude Code sessions, memories, "
        "history, generated traces, and currently running CLI sessions?",
        default=False,
        stream=out,
        input_fn=input_fn,
    )
    return "reset_all" if confirmed else "keep"


def _run_codex_reset_v2(
    *,
    stream: TextIO | None = None,
    child_args: tuple[str, ...] = (),
    working_directory: Path | None = None,
) -> CodexResetResult:
    """Apply a confirmed full Codex conversation-state reset."""
    out = stream if stream is not None else sys.stdout
    out.write(
        render_inline_row(
            "sessions",
            "force-deleting all sessions, memories, and conversation traces",
            status="spin",
            accent=AMBER,
        )
    )
    out.flush()
    scan_kwargs = _memory_scan_kwargs("codex")
    try:
        result = reset_all_codex_data(
            home=scan_kwargs["home"],
            codex_home=scan_kwargs["codex_home"],
            working_directory=working_directory or Path.cwd(),
            cli_arguments=child_args,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        result = CodexResetResult(
            error=str(exc) or exc.__class__.__name__,
        )

    value = (
        f"{result.deleted_sessions}/{result.discovered_sessions} sessions · "
        f"{result.deleted_trace_targets} conversation-state targets deleted · "
        f"{result.terminated_processes} runtimes stopped"
    )
    if result.desktop_restarted:
        value = f"{value} · Codex app restarted"
    if not result.succeeded:
        value = f"{value} · failed · {result.error}"
    out.write(
        render_inline_row(
            "sessions",
            value,
            status="done" if result.succeeded else "warn",
            accent=AMBER,
        )
    )
    out.flush()
    return result


def _run_claude_reset_v2(
    *,
    stream: TextIO | None = None,
    project_root: Path | None = None,
) -> ClaudeResetResult:
    """Apply a confirmed full local Claude Code conversation-state reset."""
    out = stream if stream is not None else sys.stdout
    out.write(
        render_inline_row(
            "sessions",
            "deleting all known local sessions, memories, and conversation traces",
            status="spin",
            accent=AMBER,
        )
    )
    out.flush()
    try:
        scan_kwargs = _memory_scan_kwargs("claude")
        real_binary = find_real_binary("claude")
        result = reset_all_claude_data(
            home=scan_kwargs["home"],
            claude_config_dir=scan_kwargs["claude_config_dir"],
            real_claude_binary=real_binary,
            current_project_root=project_root,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        result = ClaudeResetResult(error=str(exc) or exc.__class__.__name__)

    value = (
        f"{result.deleted_sessions}/{result.discovered_sessions} sessions · "
        f"{result.deleted_memory_stores} memory stores deleted · "
        f"{result.deleted_residual_targets} conversation-state targets "
        f"deleted · {result.terminated_processes} runtimes stopped"
    )
    if not result.succeeded:
        value = f"{value} · failed · {result.error}"
    out.write(
        render_inline_row(
            "sessions",
            value,
            status="done" if result.succeeded else "warn",
            accent=AMBER,
        )
    )
    out.flush()
    return result


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
    project_root: Path,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
) -> str:
    """Run the interactive opt-in phase for one already-resolved worktree.

    A persistent Serena CLI must be usable before project creation can write
    the opt-in marker.  This keeps a declined/failed install from changing the
    worktree while still allowing the agent itself to launch bare.

    Returns one of: 'created', 'skipped', 'failed'.
    """
    out = stream if stream is not None else sys.stdout
    project_root = project_root.resolve()

    if not confirm(
        "Initialize Serena for this project?",
        default=False,
        stream=out,
        input_fn=input_fn,
    ):
        out.write("  ! serena    skipped   . launching without Serena project config\n")
        out.flush()
        return "skipped"

    if serena_server_command() is None:
        install_state = _run_serena_cli_install_v2(stream=out)
        if install_state not in {"present", "installed"} or serena_server_command() is None:
            out.write("  ! serena    unavailable . launching without Serena project config\n")
            out.flush()
            return "failed"

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


def _heartbeat_loop(
    scope: Scope,
    lease_id: str,
    server_instance_id: str,
    stop: threading.Event,
) -> None:
    while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
        if not _touch_lease_if_record_exists(scope, lease_id, server_instance_id, stop):
            return


def _touch_lease_if_record_exists(
    scope: Scope,
    lease_id: str,
    server_instance_id: str,
    stop: threading.Event,
    *,
    now: float | None = None,
) -> bool:
    """Refresh this launcher's lease only on its acquired server instance."""

    with locked_registry(scope) as registry:
        record = registry.record
        if (
            record is None
            or stop.is_set()
            or not record_belongs_to_scope(record, scope)
            or record.server_instance_id != server_instance_id
        ):
            return False
        current_lease = record.leases.get(lease_id)
        if current_lease is None:
            return False
        refreshed_lease = make_launcher_lease(
            lease_id,
            current_lease.client_type,
            now=now,
        )
        return refresh_existing_lease(
            registry,
            lease=refreshed_lease,
            server_instance_id=server_instance_id,
        )


def _remove_lease_and_shutdown_if_empty(
    scope: Scope, lease_id: str, server_instance_id: str
) -> ShutdownStats:
    return release_lease_and_shutdown_if_empty(scope, lease_id, server_instance_id)


def _release_acquired_record(
    *,
    interactive: bool,
    scope: Scope,
    lease_id: str,
    server_instance_id: str,
) -> ShutdownStats:
    """Release exactly one already-acquired server record."""

    if interactive:
        return _stop_mcp_with_spinner(
            scope=scope,
            lease_id=lease_id,
            server_instance_id=server_instance_id,
        )
    return _remove_lease_and_shutdown_if_empty(scope, lease_id, server_instance_id)


def _stop_mcp_with_spinner(
    *,
    scope,
    lease_id: str,
    server_instance_id: str,
    stream=None,
    shutdown_fn=None,
):
    """Run lease release + MCP shutdown with a single-line spinner."""
    out = stream if stream is not None else sys.stdout
    fn = shutdown_fn if shutdown_fn is not None else _remove_lease_and_shutdown_if_empty
    presentation_interrupt: KeyboardInterrupt | None = None

    try:
        out.write(f"  \x1b[{PURPLE}m·\x1b[0m serena     stopping shared worktree server")
        out.flush()
    except KeyboardInterrupt as exc:
        presentation_interrupt = exc
    except Exception:
        pass

    def on_tick(frame: int) -> None:
        try:
            out.write(f"\r  {style_spinner(frame)} serena     stopping shared worktree server")
            out.flush()
        except KeyboardInterrupt:
            pass
        except Exception:
            pass

    ticker: SpinnerTicker | None = None
    try:
        ticker = SpinnerTicker(on_tick=on_tick, interval=0.1)
        ticker.start()
    except KeyboardInterrupt as exc:
        if presentation_interrupt is None:
            presentation_interrupt = exc
        ticker = None
    except Exception:
        ticker = None
    try:
        stats = fn(scope, lease_id, server_instance_id)
    except BaseException as exc:
        if ticker is not None:
            try:
                ticker.stop()
            except BaseException:
                pass
        try:
            out.write(f"\r  \x1b[33m!\x1b[0m serena     shutdown failed . {exc}\n")
            out.flush()
        except BaseException:
            pass
        raise
    if ticker is not None:
        try:
            ticker.stop()
        except KeyboardInterrupt as exc:
            if presentation_interrupt is None:
                presentation_interrupt = exc
        except Exception:
            pass
    if presentation_interrupt is None:
        try:
            lifecycle = (
                "stopped shared worktree server"
                if stats.server_stopped
                else f"kept ({stats.sessions_remaining} sessions)"
            )
            out.write(
                f"\r  \x1b[{PINK}m✓\x1b[0m serena     {lifecycle}\n"
            )
            out.flush()
        except KeyboardInterrupt as exc:
            presentation_interrupt = exc
        except Exception:
            pass
    if presentation_interrupt is not None:
        raise presentation_interrupt
    return stats


if __name__ == "__main__":
    raise SystemExit(main())
