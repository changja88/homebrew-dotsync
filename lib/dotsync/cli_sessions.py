"""`dotsync sessions …`: the CLI group. Sub-commands are added by the tasks
that implement them; session commands need no sync folder."""

from __future__ import annotations

import argparse
import sys

from dotsync import cli_json, ui
from dotsync.cli_json import JSONCommandError
from dotsync.sessions import claude

TOOLS = ("claude", "codex")


def add_parser(sub) -> None:
    sessions = sub.add_parser(
        "sessions", help="list, delete and recover Claude Code and Codex sessions"
    )
    cmds = sessions.add_subparsers(dest="sessions_cmd", required=True)
    list_cmd = cmds.add_parser("list", help="list a tool's sessions, grouped by project")
    list_cmd.add_argument("tool", choices=TOOLS)
    list_cmd.add_argument("--json", action="store_true", help="print one JSON object")


def run(args: argparse.Namespace) -> int:
    if args.sessions_cmd == "list":
        return _list(args)
    raise NotImplementedError(args.sessions_cmd)


def _list(args: argparse.Namespace) -> int:
    if args.json:
        return cli_json.emit(lambda: _list_payload(args.tool))
    try:
        payload = _list_payload(args.tool)
    except JSONCommandError as e:
        ui.error(str(e))
        return 1
    for group in payload["groups"]:
        print(
            f"{group['name']}  {group['sessions']} sessions  "
            f"{_size(group['bytes'])}  {group['running']} running"
        )
    return 0


def _list_payload(tool: str) -> dict:
    if tool == "claude":
        return claude.list_payload()
    raise JSONCommandError("unsupported", f"listing {tool} sessions is not available yet")


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    raise AssertionError("unreachable")


def progress(done: int, total: int) -> None:
    """One progress line on stderr; stdout keeps the one JSON object."""
    print(f"dotsync-progress {done}/{total}", file=sys.stderr, flush=True)
