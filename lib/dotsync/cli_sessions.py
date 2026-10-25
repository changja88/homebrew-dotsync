"""`dotsync sessions …`: the CLI group. Sub-commands are added by the tasks
that implement them; session commands need no sync folder."""

from __future__ import annotations

import argparse
import sys


def add_parser(sub) -> None:
    sessions = sub.add_parser(
        "sessions", help="list, delete and recover Claude Code and Codex sessions"
    )
    sessions.add_subparsers(dest="sessions_cmd", required=True)


def run(args: argparse.Namespace) -> int:
    # No sub-command exists yet; argparse already rejected a missing one.
    raise NotImplementedError(args.sessions_cmd)


def progress(done: int, total: int) -> None:
    """One progress line on stderr; stdout keeps the one JSON object."""
    print(f"dotsync-progress {done}/{total}", file=sys.stderr, flush=True)
