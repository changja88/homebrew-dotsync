"""The result log of a delete, recover or cleanup: ids, paths and outcomes,
never contents."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotsync.cli_json import now_iso
from dotsync.sessions import roots, safety


def write(command: str, tool: str, started_at: str, result: dict) -> Path:
    directory = roots.log_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"sessions-{stamp}-{os.getpid()}.json"
    doc = {
        "command": command,
        "tool": tool,
        "started_at": started_at,
        "finished_at": now_iso(),
        "result": result,
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    path.chmod(0o600)
    return path


_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def deleted_ids(tool: str) -> set[str]:
    """The ids the logs show dotsync deleted for `tool`: a delete's sessions
    whose outcome is `deleted`, with the ids in their rollout paths (their
    sub-sessions), and what a cleanup removed. A log that cannot be read, or
    is of another shape, shows none."""
    ids: set[str] = set()
    for path in roots.log_dir().glob("sessions-*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        if not isinstance(doc, dict) or doc.get("tool") != tool or not isinstance(doc.get("result"), dict):
            continue
        result = doc["result"]
        if doc.get("command") == "delete":
            ids |= _deleted_in(result)
        elif doc.get("command") == "cleanup" and isinstance(result.get("ids"), dict):
            # Most items log a list of ids; the two JSONL ones {id: lines}.
            ids |= {i for removed in result["ids"].values() if isinstance(removed, (list, dict)) for i in removed
                    if isinstance(i, str) and safety.is_valid_id(i)}
    return ids


def _deleted_in(result: dict) -> set[str]:
    ids: set[str] = set()
    paths = result.get("paths") if isinstance(result.get("paths"), dict) else {}
    for entry in result.get("results") or []:
        if not isinstance(entry, dict) or entry.get("outcome") != "deleted" or not safety.is_valid_id(entry.get("id")):
            continue
        ids.add(entry["id"])
        rollouts = paths.get(entry["id"], {}).get("rollouts") if isinstance(paths.get(entry["id"]), dict) else None
        for rollout in rollouts if isinstance(rollouts, list) else []:
            if isinstance(rollout, str):
                ids |= set(_ID.findall(rollout.rsplit("/", 1)[-1]))
    return ids
