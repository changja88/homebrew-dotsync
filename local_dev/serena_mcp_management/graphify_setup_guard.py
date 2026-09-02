"""Remember Graphify setup actions that succeeded but the probe still cannot see.

The preflight asks "set up Graphify integration/hooks?" whenever the probe
says missing. If ``graphify ... install`` then exits 0 and the probe *still*
says missing, the probe is out of step with this Graphify version — asking
again next launch would only repeat the same no-op install forever. So the
launcher records that outcome here, keyed by project, component, Graphify
version and a fingerprint of the files involved, and stays quiet until any of
those change (a new Graphify version, or someone touching the files).

State lives under the launcher runtime root, never inside the project.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from local_dev.serena_mcp_management.serena_mcp.paths import (
    open_private_runtime_file,
    runtime_root_path,
)

GUARD_FILE_NAME = "graphify-setup-guard.json"


def guard_file_path() -> Path:
    return runtime_root_path() / GUARD_FILE_NAME


def _key(project_root: Path, component: str, graphify_version: str | None) -> str:
    return f"{project_root.resolve()}::{component}::{graphify_version or 'unknown'}"


def _load() -> dict[str, dict]:
    try:
        data = json.loads(guard_file_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def is_suppressed(
    project_root: Path,
    component: str,
    graphify_version: str | None,
    fingerprint: str,
) -> bool:
    """True when this exact setup state was already installed-but-undetected."""
    entry = _load().get(_key(project_root, component, graphify_version))
    return entry is not None and entry.get("fingerprint") == fingerprint


def record(
    project_root: Path,
    component: str,
    graphify_version: str | None,
    fingerprint: str,
) -> bool:
    """Persist the outcome; returns False (never raises) when it cannot."""
    entries = _load()
    entries[_key(project_root, component, graphify_version)] = {
        "fingerprint": fingerprint,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    try:
        with open_private_runtime_file(guard_file_path()) as handle:
            json.dump(entries, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except (OSError, ValueError):
        return False
    return True
