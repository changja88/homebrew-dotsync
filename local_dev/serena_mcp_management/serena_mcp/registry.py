"""Locked JSON registry for scoped Serena MCP server state."""
from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from local_dev.serena_mcp_management.serena_mcp.paths import Scope, state_dir_for

REGISTRY_VERSION = 1


@dataclass(slots=True)
class Lease:
    """A launcher session lease."""

    lease_id: str
    launcher_pid: int
    heartbeat_at: float
    launcher_identity: str | None = None


@dataclass(slots=True)
class ServerRecord:
    """A live or candidate Serena server record."""

    server_pid: int
    mcp_url: str
    dashboard_url: str
    project_root: str
    client_type: str
    started_at: float
    leases: dict[str, Lease]
    watchdog_pid: int | None = None
    upstream_mcp_url: str | None = None
    proxy_pid: int | None = None
    server_identity: str | None = None
    proxy_identity: str | None = None
    watchdog_identity: str | None = None


@dataclass(slots=True)
class Registry:
    """Registry content loaded under an exclusive file lock."""

    path: Path
    record: ServerRecord | None


def registry_path(scope: Scope) -> Path:
    """Return the registry JSON path for a scope."""

    return state_dir_for(scope) / "registry.json"


def lock_path(scope: Scope) -> Path:
    """Return the registry lock path for a scope."""

    return state_dir_for(scope) / "registry.lock"


def read_registry_record(scope: Scope) -> ServerRecord | None:
    """Read a registry record without creating state directories or lock files."""

    path = registry_path(scope)
    if not path.exists():
        return None
    lock = lock_path(scope)
    if not lock.exists():
        return _load_record(path)
    try:
        with lock.open("r") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return None
            try:
                return _load_record(path)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return None


@contextmanager
def locked_registry(scope: Scope) -> Iterator[Registry]:
    """Open a scope registry under an exclusive lock and persist on exit."""

    state_dir_for(scope).mkdir(parents=True, exist_ok=True)
    with lock_path(scope).open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        path = registry_path(scope)
        registry = Registry(path=path, record=_load_record(path))
        try:
            yield registry
            _write_record(path, registry.record)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def touch_lease(registry: Registry, lease: Lease) -> None:
    """Add or refresh a lease on the current server record."""

    if registry.record is None:
        return
    registry.record.leases[lease.lease_id] = lease


def record_belongs_to_scope(record: ServerRecord, scope: Scope) -> bool:
    """Return true when a registry record belongs to the current scope."""

    return (
        record.project_root == str(scope.project_root)
        and record.client_type == scope.client_type
    )


def _load_record(path: Path) -> ServerRecord | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if data.get("version") != REGISTRY_VERSION:
            return None
        record = data.get("record")
        if not isinstance(record, dict):
            return None
        leases = {
            lease_id: Lease(**lease)
            for lease_id, lease in record.get("leases", {}).items()
        }
        record["leases"] = leases
        return ServerRecord(**record)
    except (json.JSONDecodeError, TypeError, KeyError, AttributeError):
        return None


def _write_record(path: Path, record: ServerRecord | None) -> None:
    if record is None:
        if path.exists():
            path.unlink()
        return
    payload = {"version": REGISTRY_VERSION, "record": asdict(record)}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp, path)
