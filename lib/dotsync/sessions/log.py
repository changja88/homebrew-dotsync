"""The result log of a delete, recover or cleanup: ids, paths and outcomes,
never contents."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotsync.cli_json import now_iso
from dotsync.sessions import roots


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
