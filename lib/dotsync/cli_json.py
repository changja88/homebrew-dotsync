"""`--json` output of the sync commands, for the dotsync app.

The contract is the one `dotsync account --json` follows: exactly one JSON
object on stdout and nothing else — `ui` and app output go to stderr — exit 0
with the command's object, or exit 1 with
``{"error": {"code": "<code>", "message": "<text>"}}``.
"""

from __future__ import annotations

import contextlib
import json
import sys
from datetime import datetime, timezone
from typing import Callable

from dotsync import diffinfo
from dotsync.config import Config, ConfigError
from dotsync.plan import Change


def emit(work: Callable[[], dict]) -> int:
    """Run `work` with stdout sent to stderr, then print its object — or the
    error envelope for what it raised — as the one line on stdout."""
    from dotsync.accounts import AccountError
    from dotsync.locks import LockBusy

    out = sys.stdout
    try:
        with contextlib.redirect_stdout(sys.stderr):
            payload = work()
        status = 0
    except ConfigError as e:
        payload, status = _error("config", e), 1
    except LockBusy as e:
        payload, status = _error("busy", e), 1
    except AccountError as e:
        payload, status = _error(e.code, e), 1
    except KeyboardInterrupt:
        payload, status = _error("cancelled", "the command was cancelled"), 1
    except Exception as e:  # the dotsync app reads every answer as JSON
        payload, status = _error("failed", e), 1
    print(json.dumps(payload, ensure_ascii=False), file=out)
    return status


def _error(code: str, message: object) -> dict:
    return {"error": {"code": code, "message": str(message)}}


def now_iso() -> str:
    """UTC with whole seconds, as Swift's .iso8601 decoding expects."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def change_json(change: Change, *, with_diff: bool) -> dict:
    entry = {
        "label": change.label,
        "kind": change.kind,
        "details": change.details,
        "file_changes": list(change.file_changes),
        "diffable": change.diffable,
    }
    if with_diff and change.kind in ("create", "update", "remove"):
        entry["diff"] = change_diff_text(change)
    return entry


def status_json(cfg: Config) -> dict:
    """Every supported app in registry order: the tracked ones with their
    sync state, the others with tracked=false so the app can offer them."""
    from dotsync.apps import APP_CLASSES, build_app

    apps = []
    for cls in APP_CLASSES:
        entry = {"name": cls.name, "description": cls.description, "tracked": cls.name in cfg.apps}
        if entry["tracked"]:
            entry.update(_tracked_status(build_app, cls.name, cfg))
        apps.append(entry)
    return {"dir": str(cfg.dir), "dir_source": cfg.dir_source, "checked_at": now_iso(), "apps": apps}


def _tracked_status(build_app, name: str, cfg: Config) -> dict:
    # One app failing (an unreadable file, a broken tool) must not hide the rest.
    try:
        app = build_app(name, cfg)
        status = app.status(cfg.dir)
    except Exception as e:
        return {"state": "unknown", "details": str(e), "direction": "", "reason": "error", "changes": []}
    changes: list[dict] = []
    if status.state == "dirty":
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            changes = [
                change_json(c, with_diff=False)
                for c in app.plan_from(cfg.dir).changes
                if c.is_change
            ]
    return {
        "state": status.state,
        "details": status.details,
        "direction": status.direction,
        "reason": status.reason,
        "changes": changes,
    }


def change_diff_text(change: Change) -> str:
    if not change.diffable:
        return "(semantic change — no file diff)"
    unavailable = "(diff unavailable: no on-disk copy to compare)"
    if change.file_changes:  # tree mirror: 파일별 diff
        if change.source is None or change.dest is None:
            return unavailable
        return tree_diff_text(change)
    if change.kind == "update":
        if change.source is None or change.dest is None:
            return unavailable
        return diffinfo.unified_diff_text(change.source, change.dest)
    if change.kind == "create":
        if change.source is None:
            return unavailable
        return diffinfo.full_file_lines(change.source, "+")
    if change.dest is None:
        return unavailable
    return diffinfo.full_file_lines(change.dest, "-")


def tree_diff_text(change: Change) -> str:
    blocks: "list[str]" = []
    for entry in change.file_changes:
        symbol, _, rel = entry.partition(" ")
        if symbol == "+":
            block = diffinfo.full_file_lines(change.source / rel, "+")
        elif symbol == "−":
            block = diffinfo.full_file_lines(change.dest / rel, "-")
        elif symbol == "~":
            block = diffinfo.unified_diff_text(change.source / rel, change.dest / rel)
        else:
            continue
        blocks.append(f"◦ {rel}\n{block}")
    return "\n\n".join(blocks)
