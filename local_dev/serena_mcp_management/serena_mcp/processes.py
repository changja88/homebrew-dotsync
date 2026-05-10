"""Discover Serena MCP server processes for scope reconciliation."""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from local_dev.serena_mcp_management.serena_mcp.paths import Scope, serena_context_for


@dataclass(frozen=True, slots=True)
class SerenaMcpProcess:
    """A parsed `serena start-mcp-server` process."""

    pid: int
    project_root: Path
    context: str
    command: str


def list_serena_mcp_processes() -> list[SerenaMcpProcess]:
    """Return parseable Serena MCP server processes."""

    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    processes: list[SerenaMcpProcess] = []
    for line in proc.stdout.splitlines():
        pid_text, _, command = line.strip().partition(" ")
        if not pid_text.isdigit() or not command:
            continue
        parsed = parse_serena_mcp_process(int(pid_text), command)
        if parsed is not None:
            processes.append(parsed)
    return processes


def parse_serena_mcp_process(pid: int, command: str) -> SerenaMcpProcess | None:
    """Parse one process command line, failing closed when scope is unclear."""

    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not _is_serena_start_mcp_server(argv):
        return None
    project = _option_value(argv, "--project")
    context = _option_value(argv, "--context")
    if not project or not context:
        return None
    return SerenaMcpProcess(
        pid=pid,
        project_root=Path(project).resolve(),
        context=context,
        command=command,
    )


def process_matches_scope(process: SerenaMcpProcess, scope: Scope) -> bool:
    """Return true when a parsed process belongs to a launcher scope."""

    return (
        process.project_root == scope.project_root
        and process.context == serena_context_for(scope.client_type)
    )


def _is_serena_start_mcp_server(argv: list[str]) -> bool:
    for index, value in enumerate(argv[:-1]):
        if Path(value).name == "serena" and argv[index + 1] == "start-mcp-server":
            return True
    return False


def _option_value(argv: list[str], option: str) -> str | None:
    prefix = option + "="
    for index, value in enumerate(argv):
        if value == option:
            if index + 1 >= len(argv):
                return None
            return argv[index + 1]
        if value.startswith(prefix):
            return value[len(prefix):]
    return None
