"""`dotsync sessions …`: the CLI group. Sub-commands are added by the tasks
that implement them; session commands need no sync folder."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from dotsync import cli_json, ui
from dotsync.cli_json import JSONCommandError
from dotsync.sessions import claude, codex, codex_cleanup
from dotsync.sessions.stop import StopSignal

TOOLS = ("claude", "codex")


def add_parser(sub) -> None:
    sessions = sub.add_parser(
        "sessions", help="list, delete and recover Claude Code and Codex sessions"
    )
    cmds = sessions.add_subparsers(dest="sessions_cmd", required=True)
    list_cmd = cmds.add_parser("list", help="list a tool's sessions, grouped by project")
    list_cmd.add_argument("tool", choices=TOOLS)
    list_cmd.add_argument("--json", action="store_true", help="print one JSON object")
    delete_cmd = cmds.add_parser(
        "delete", help="delete sessions after checking again that nothing uses them"
    )
    delete_cmd.add_argument("tool", choices=TOOLS)
    delete_cmd.add_argument("ids", nargs="+", metavar="id")
    delete_cmd.add_argument(
        "--json", action="store_true", help="print one JSON object; progress goes to stderr"
    )
    recover_cmd = cmds.add_parser(
        "recover", help="finish or undo a delete that stopped halfway"
    )
    recover_cmd.add_argument("tool", choices=("claude",))
    recover_cmd.add_argument("id")
    how = recover_cmd.add_mutually_exclusive_group(required=True)
    how.add_argument("--restore", action="store_true", help="rename the transcripts back")
    how.add_argument("--finish", action="store_true", help="delete what is left")
    recover_cmd.add_argument(
        "--json", action="store_true", help="print one JSON object; progress goes to stderr"
    )
    cleanup_cmd = cmds.add_parser(
        "cleanup", help="clean up what Codex leaves behind, once nothing uses it"
    )
    cleanup_cmd.add_argument("tool", choices=("codex",))
    mode = cleanup_cmd.add_mutually_exclusive_group(required=True)
    mode.add_argument("--blockers", action="store_true", help="list what uses ~/.codex")
    mode.add_argument("--dry-run", action="store_true", help="show what would be cleaned")
    mode.add_argument("--yes", action="store_true", help="clean up")
    cleanup_cmd.add_argument(
        "--items",
        metavar="a,b",
        help="with --yes: what to clean, among " + ", ".join(codex_cleanup.ITEMS) + " (default: all)",
    )
    cleanup_cmd.add_argument(
        "--vacuum", action="store_true", help="with --yes: also reclaim the history database's free space"
    )
    cleanup_cmd.add_argument(
        "--json", action="store_true", help="print one JSON object; progress goes to stderr"
    )


def run(args: argparse.Namespace) -> int:
    if args.sessions_cmd == "list":
        return _list(args)
    if args.sessions_cmd == "delete":
        return _delete(args)
    if args.sessions_cmd == "recover":
        return _recover(args)
    if args.sessions_cmd == "cleanup":
        return _cleanup(args)
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
        return claude.list_payload(_best_effort(codex.cwds))
    return codex.list_payload(_best_effort(lambda: [s.cwd for s in claude.scan() if s.cwd]))


def _best_effort(other_cwds: Callable[[], list[str]]) -> list[str]:
    """The other tool's cwds only sharpen grouping; failing to get them is not
    a reason to fail the list."""
    try:
        return other_cwds()
    except Exception:
        return []


def _delete(args: argparse.Namespace) -> int:
    if args.json:
        return cli_json.emit(lambda: _delete_report(args.tool, args.ids, progress))
    try:
        report = _delete_report(args.tool, args.ids, lambda done, total: None)
    except JSONCommandError as e:
        ui.error(str(e))
        return 1
    for result in report["results"]:
        print(_delete_line(result))
    return 0


def _recover(args: argparse.Namespace) -> int:
    if args.json:
        return cli_json.emit(lambda: _recover_report(args, progress))
    try:
        report = _recover_report(args, lambda done, total: None)
    except JSONCommandError as e:
        ui.error(str(e))
        return 1
    for result in report["results"]:
        print(_delete_line(result))
    return 0


def _cleanup(args: argparse.Namespace) -> int:
    if args.json:
        return cli_json.emit(lambda: _cleanup_payload(args, progress))
    try:
        payload = _cleanup_payload(args, None)
    except JSONCommandError as e:
        ui.error(str(e))
        return 1
    if args.dry_run:
        return _print_dry_run(payload)
    if args.yes:
        return _print_cleanup(payload)
    entries = payload["blockers"]
    for entry in entries:
        print(f"{entry['pid']}  {entry['kind']}  {entry['name']}  {entry['elapsed']}s")
    if not entries:
        print("nothing uses ~/.codex")
    return 0


def _print_dry_run(payload: dict) -> int:
    """Counts only, never an id."""
    refusal = payload["refusal"]
    if refusal:
        ui.error(f"{refusal['code']}: {refusal['message']}")
        return 1
    for item in payload["items"]:
        print(f"{item['label']}  {item['count']}")
    return 0


def _print_cleanup(report: dict) -> int:
    """One line per item: its id, outcome and count, never a session id."""
    for item in report["items"]:
        parts = [item["id"], item["outcome"], str(item["count"])]
        parts += [item[key] for key in ("reason", "message") if key in item]
        print("  ".join(parts))
    return 0


def _cleanup_payload(
    args: argparse.Namespace, on_progress: Callable[[int, int], None] | None
) -> dict:
    if args.blockers:
        return codex_cleanup.detect()
    if args.dry_run:
        return codex_cleanup.dry_run_payload(on_progress)
    items = args.items.split(",") if args.items is not None else list(codex_cleanup.ITEMS)
    with StopSignal() as stop:
        return codex_cleanup.run_cleanup(
            items, args.vacuum, stop=stop, progress=on_progress or (lambda done, total: None)
        )


def _recover_report(args: argparse.Namespace, on_progress: Callable[[int, int], None]) -> dict:
    if args.restore:
        return claude.restore(args.id)
    with StopSignal() as stop:
        return claude.delete([args.id], stop=stop, progress=on_progress, log_command="recover")


def _delete_report(tool: str, ids: list[str], on_progress: Callable[[int, int], None]) -> dict:
    sessions = claude if tool == "claude" else codex
    with StopSignal() as stop:
        return sessions.delete(ids, stop=stop, progress=on_progress)


def _delete_line(result: dict) -> str:
    """One line per id, never a title."""
    parts = [result["id"], result["outcome"]]
    if "reason" in result:
        parts.append(result["reason"])
    if "freed_bytes" in result and result["outcome"] != "skipped":
        parts.append(f"{_size(result['freed_bytes'])} freed")
    if result.get("remaining"):
        parts.append("left " + ", ".join(result["remaining"]))
    if result.get("kept"):
        parts.append("kept " + ", ".join(result["kept"]))
    if result.get("failed"):
        parts.append(f"{len(result['failed'])} failed")
    return "  ".join(parts)


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    raise AssertionError("unreachable")


def progress(done: int, total: int) -> None:
    """One progress line on stderr; stdout keeps the one JSON object."""
    print(f"dotsync-progress {done}/{total}", file=sys.stderr, flush=True)
