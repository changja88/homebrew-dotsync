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
    SPINNER_FRAMES,
    SpinnerTicker,
    confirm,
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


def _jq_available() -> bool:
    """Check if jq is available in the system."""
    return shutil.which("jq") is not None


def _claude_project_dir() -> Path:
    """Get the Claude project directory for the current working directory."""
    cwd = os.getcwd()
    encoded = cwd.replace("/", "-")
    return Path(os.path.expanduser("~/.claude/projects")) / encoded


def _run_cleanup_claude(proj_dir: Path) -> CleanupResult:
    """Clean up old Claude sessions and memory files.

    Deletes:
    - *.jsonl files older than 3 days (find -mtime +3)
    - Per-jsonl UUID directories (named after jsonl stem)
    - Entire memory directory at the end

    Returns:
        CleanupResult with deleted count and memory files reset count.
    """
    deleted = 0
    memory_files_reset = 0
    mem_dir = proj_dir / "memory"

    # Count memory files BEFORE deletion
    if mem_dir.is_dir():
        memory_files_reset = sum(1 for _ in mem_dir.rglob("*") if _.is_file())

    if proj_dir.is_dir():
        cutoff = time.time() - 3 * 86400  # 3 days in seconds
        for jsonl in proj_dir.glob("*.jsonl"):
            if jsonl.stat().st_mtime < cutoff:
                # Delete the .jsonl file
                uuid_dir = proj_dir / jsonl.stem
                jsonl.unlink(missing_ok=True)
                # Delete the corresponding UUID directory
                if uuid_dir.is_dir():
                    shutil.rmtree(uuid_dir, ignore_errors=True)
                deleted += 1

        # Delete the entire memory directory
        if mem_dir.is_dir():
            shutil.rmtree(mem_dir, ignore_errors=True)

    return CleanupResult(deleted=deleted, memory_files_reset=memory_files_reset)


def _run_cleanup_codex(codex_home: Path, cwd: str) -> CleanupResult:
    """Clean up old Codex sessions and memory files.

    Sessions are scanned only if jq is available.
    Matches sessions where:
    - type == "session_meta"
    - payload.cwd == cwd
    - mtime > 3 days

    Always deletes memories directory at the end.

    Returns:
        CleanupResult with deleted count and memory files reset count.
    """
    deleted = 0
    memory_files_reset = 0
    sessions_dir = codex_home / "sessions"
    mem_dir = codex_home / "memories"

    # Count memory files BEFORE deletion
    if mem_dir.is_dir():
        memory_files_reset = sum(1 for _ in mem_dir.rglob("*") if _.is_file())

    # Only scan sessions if jq is available
    if sessions_dir.is_dir() and _jq_available():
        cutoff = time.time() - 3 * 86400  # 3 days in seconds
        for jsonl in sessions_dir.glob("*.jsonl"):
            # Check if file matches the jq filter: type == "session_meta" && payload.cwd == $cwd
            try:
                proc = subprocess.run(
                    ["jq", "-e", "--arg", "cwd", cwd,
                     "select(.type == \"session_meta\" and .payload.cwd == $cwd)",
                     str(jsonl)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                if proc.returncode != 0:
                    # Filter didn't match
                    continue
            except FileNotFoundError:
                # jq not found, skip this file
                continue

            # Check if file is old enough
            if jsonl.stat().st_mtime < cutoff:
                jsonl.unlink(missing_ok=True)
                deleted += 1

    # Always delete the entire memories directory
    if mem_dir.is_dir():
        shutil.rmtree(mem_dir, ignore_errors=True)

    return CleanupResult(deleted=deleted, memory_files_reset=memory_files_reset)


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
        result = _run_cleanup_claude(_claude_project_dir())
    else:
        codex_home = Path(
            os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))
        )
        result = _run_cleanup_codex(codex_home, os.getcwd())

    out.write(
        f"  ✓ cleanup     {result.deleted} deleted . "
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
    out.write("  · serena     preparing scoped server")
    out.flush()
    frame_state = {"frame": 0}

    def on_tick(frame: int) -> None:
        frame_state["frame"] = frame
        spinner = SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]
        out.write(f"\r  {spinner} serena     preparing scoped server")
        out.flush()

    ticker = SpinnerTicker(on_tick=on_tick, interval=0.1)
    ticker.start()
    try:
        record = ensure_server(scope, lease)
    except Exception as exc:
        ticker.stop()
        out.write(f"\r  ! serena     failed     . {exc}\n")
        out.flush()
        raise
    ticker.stop()
    out.write(f"\r  ✓ serena     ready      . {record.mcp_url}\n")
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
             value=(f"{cleanup_deleted} deleted . "
                    f"{cleanup_memory_files_reset} memory files reset"),
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
        rc = _run_preflight_v2()
        if rc != 0:
            return rc

    serena_state = _run_serena_init_v2() if interactive else "managed"
    if serena_state in {"skipped", "failed"}:
        warnings.append(f"serena project create {serena_state}")
        client_type = infer_client_type(os.environ.get("SERENA_AGENT_CLIENT", sys.argv[0]))
        real_binary = find_real_binary(client_type)
        return int(subprocess.run([real_binary, *args]).returncode)

    summary_state = _run_launch_prep_v2() if interactive else None

    client_type = infer_client_type(os.environ.get("SERENA_AGENT_CLIENT", sys.argv[0]))
    project_root = _project_root_from_environment() or find_project_root(Path.cwd())
    scope = Scope(project_root, client_type)
    lease_id = str(uuid.uuid4())
    lease = Lease(lease_id, os.getpid(), time.time())

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
    input_fn: Callable[[], str] = input,
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
    return "created"


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
