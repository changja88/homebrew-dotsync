"""`dotsync sessions …`: the commands the dotsync app's 세션 menu runs, each
answering one JSON object. Session commands need no sync folder."""

from __future__ import annotations

import argparse
import errno
import sys
from collections.abc import Callable

from dotsync import cli_json
from dotsync.sessions import claude, claude_delete, codex, codex_blockers, codex_cleanup
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
        help="with --yes: what to clean, among " + ", ".join(codex_cleanup.ITEMS)
        + ' (default: all; "" picks none, and the cache files still go)',
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
    return cli_json.emit(lambda: _list_payload(args.tool))


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
    return cli_json.emit(lambda: _delete_report(args.tool, args.ids, progress))


def _recover(args: argparse.Namespace) -> int:
    return cli_json.emit(lambda: _recover_report(args, progress))


def _cleanup(args: argparse.Namespace) -> int:
    return cli_json.emit(lambda: _cleanup_payload(args))


def _cleanup_payload(args: argparse.Namespace) -> dict:
    if args.blockers:
        return codex_blockers.detect()
    if args.dry_run:
        return codex_cleanup.dry_run_payload(progress)
    items = _cleanup_items(args.items)
    with StopSignal() as stop:
        return codex_cleanup.run_cleanup(items, args.vacuum, stop=stop, progress=progress)


def _cleanup_items(arg: str | None) -> list[str]:
    """No `--items` is all four; `--items ""` is none of them, so only the
    cache files run, and the vacuum when asked (the app's vacuum-only run).
    An empty name among others is left for `run_cleanup` to refuse as
    `bad_items`."""
    if arg is None:
        return list(codex_cleanup.ITEMS)
    return arg.split(",") if arg else []


def _recover_report(args: argparse.Namespace, on_progress: Callable[[int, int], None]) -> dict:
    if args.restore:
        return claude_delete.restore(args.id)
    with StopSignal() as stop:
        return claude_delete.delete([args.id], stop=stop, progress=on_progress, log_command="recover")


def _delete_report(tool: str, ids: list[str], on_progress: Callable[[int, int], None]) -> dict:
    sessions = claude_delete if tool == "claude" else codex
    with StopSignal() as stop:
        return sessions.delete(ids, stop=stop, progress=on_progress)


# Set once a progress line finds stderr's reader gone.
_stderr_gone = False


def progress(done: int, total: int, phase: str | None = None) -> None:
    """One progress line on stderr; stdout keeps the one JSON object. Cleanup
    names its phase (`read`, `delete`, `vacuum`) after the counts; delete and
    recover leave it out. Once
    the reader has gone (the app quit while a logout outlived its wait), the
    lines stop and the run goes on to its end and its log."""
    global _stderr_gone
    if _stderr_gone:
        return
    try:
        line = f"dotsync-progress {done}/{total}" + (f" {phase}" if phase else "")
        print(line, file=sys.stderr, flush=True)
    except OSError as e:  # BrokenPipeError is the EPIPE one
        if e.errno != errno.EPIPE:
            raise
        _stderr_gone = True
