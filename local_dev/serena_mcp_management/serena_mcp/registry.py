"""Locked JSON registry for shared Serena MCP server state."""
from __future__ import annotations

import fcntl
import json
import math
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from local_dev.serena_mcp_management.serena_mcp.paths import (
    Scope,
    ensure_private_runtime_directory,
    state_dir_for,
    validate_private_runtime_directory,
)

REGISTRY_VERSION = 2


@dataclass(slots=True)
class Lease:
    """A launcher session lease."""

    lease_id: str
    client_type: str
    launcher_pid: int
    heartbeat_at: float
    launcher_identity: str | None = None


@dataclass(slots=True)
class ServerRecord:
    """A live or candidate Serena server record."""

    server_instance_id: str
    server_pid: int
    mcp_url: str
    dashboard_url: str
    project_root: str
    context_profile: str
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

    try:
        path = registry_path(scope)
        validate_private_runtime_directory(path.parent)
    except (OSError, ValueError):
        return None
    if not os.path.lexists(path):
        return None
    lock = lock_path(scope)
    if not os.path.lexists(lock):
        return _load_record(path)
    try:
        with _open_secure_runtime_file(lock, os.O_RDONLY) as handle:
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

    state_dir = state_dir_for(scope)
    ensure_private_runtime_directory(state_dir)
    handle = _open_secure_runtime_file(lock_path(scope), os.O_RDWR | os.O_CREAT)
    locked = False
    primary_error: BaseException | None = None
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        locked = True
        path = registry_path(scope)
        registry = Registry(path=path, record=_load_record(path))
        yield registry
        _write_record(path, registry.record)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if locked:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError as exc:
                if primary_error is not None:
                    primary_error.add_note(f"registry unlock cleanup failed: {exc}")
        try:
            handle.close()
        except OSError as exc:
            if primary_error is not None:
                primary_error.add_note(f"registry lock close cleanup failed: {exc}")


def touch_lease(registry: Registry, lease: Lease) -> None:
    """Add or refresh a lease on the current server record."""

    if registry.record is None:
        return
    registry.record.leases[lease.lease_id] = lease


def refresh_existing_lease(
    registry: Registry, *, lease: Lease, server_instance_id: str
) -> bool:
    """Refresh a present lease only when it belongs to the same server instance."""

    record = registry.record
    if record is None or record.server_instance_id != server_instance_id:
        return False
    if lease.lease_id not in record.leases:
        return False
    record.leases[lease.lease_id] = lease
    return True


def record_belongs_to_scope(record: ServerRecord, scope: Scope) -> bool:
    """Return true when a registry record belongs to the current scope."""

    return (
        record.project_root == str(scope.project_root)
        and record.context_profile == scope.context_profile
    )


def _load_record(path: Path) -> ServerRecord | None:
    if not os.path.lexists(path):
        return None
    try:
        with _open_secure_runtime_file(path, os.O_RDONLY) as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if (
        data.get("version") != REGISTRY_VERSION
        or not isinstance(data.get("version"), int)
    ):
        return None
    record = data.get("record")
    if not isinstance(record, dict):
        return None
    return _parse_record(record)


def _write_record(path: Path, record: ServerRecord | None) -> None:
    if record is None:
        if os.path.lexists(path):
            path.unlink()
            _best_effort_fsync_directory(path.parent)
        return
    payload = {"version": REGISTRY_VERSION, "record": asdict(record)}
    fd, raw_tmp = tempfile.mkstemp(
        prefix=".registry-",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = Path(raw_tmp)
    try:
        handle = os.fdopen(fd, "w")
        fd = None
        with handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException as primary_error:
        if fd is not None:
            try:
                os.close(fd)
            except OSError as exc:
                primary_error.add_note(f"registry temp descriptor cleanup failed: {exc}")
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        except BaseException as exc:
            primary_error.add_note(f"registry temp unlink cleanup failed: {exc}")
        raise
    _best_effort_fsync_directory(path.parent)


def _best_effort_fsync_directory(path: Path) -> None:
    try:
        _fsync_directory(path)
    except OSError:
        pass


def _open_secure_runtime_file(path: Path, flags: int):
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags | nofollow, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"runtime file is not regular: {path}")
        if info.st_uid != os.geteuid():
            raise PermissionError(f"runtime file is not owned by this user: {path}")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise PermissionError(f"runtime file is not mode 0600: {path}")
        return os.fdopen(fd, "r+" if flags & os.O_RDWR else "r")
    except BaseException:
        os.close(fd)
        raise


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _parse_record(data: dict[object, object]) -> ServerRecord | None:
    required_fields = {
        "server_instance_id",
        "server_pid",
        "mcp_url",
        "dashboard_url",
        "project_root",
        "context_profile",
        "started_at",
        "leases",
    }
    optional_fields = {
        "watchdog_pid",
        "upstream_mcp_url",
        "proxy_pid",
        "server_identity",
        "proxy_identity",
        "watchdog_identity",
    }
    if set(data) - required_fields - optional_fields or not required_fields <= set(data):
        return None
    if not all(
        _is_nonempty_string(data[field])
        for field in (
            "server_instance_id",
            "mcp_url",
            "dashboard_url",
            "project_root",
            "context_profile",
        )
    ):
        return None
    if not _is_positive_int(data["server_pid"]) or not _is_finite_number(
        data["started_at"]
    ):
        return None
    if not _is_optional_positive_int(data.get("watchdog_pid")):
        return None
    if not _is_optional_positive_int(data.get("proxy_pid")):
        return None
    if not all(
        _is_optional_string(data.get(field))
        for field in (
            "upstream_mcp_url",
            "server_identity",
            "proxy_identity",
            "watchdog_identity",
        )
    ):
        return None
    raw_leases = data["leases"]
    if not isinstance(raw_leases, dict):
        return None
    leases: dict[str, Lease] = {}
    for lease_id, raw_lease in raw_leases.items():
        if not isinstance(lease_id, str) or not isinstance(raw_lease, dict):
            return None
        lease = _parse_lease(raw_lease)
        if lease is None or lease.lease_id != lease_id:
            return None
        leases[lease_id] = lease
    return ServerRecord(
        server_instance_id=data["server_instance_id"],
        server_pid=data["server_pid"],
        mcp_url=data["mcp_url"],
        dashboard_url=data["dashboard_url"],
        project_root=data["project_root"],
        context_profile=data["context_profile"],
        started_at=data["started_at"],
        leases=leases,
        watchdog_pid=data.get("watchdog_pid"),
        upstream_mcp_url=data.get("upstream_mcp_url"),
        proxy_pid=data.get("proxy_pid"),
        server_identity=data.get("server_identity"),
        proxy_identity=data.get("proxy_identity"),
        watchdog_identity=data.get("watchdog_identity"),
    )


def _parse_lease(data: dict[object, object]) -> Lease | None:
    fields = {
        "lease_id",
        "client_type",
        "launcher_pid",
        "heartbeat_at",
        "launcher_identity",
    }
    required_fields = {"lease_id", "client_type", "launcher_pid", "heartbeat_at"}
    if set(data) - fields or not required_fields <= set(data):
        return None
    if not _is_nonempty_string(data["lease_id"]) or not _is_nonempty_string(data["client_type"]):
        return None
    if not _is_positive_int(data["launcher_pid"]) or not _is_finite_number(
        data["heartbeat_at"]
    ):
        return None
    if not _is_optional_string(data.get("launcher_identity")):
        return None
    return Lease(
        lease_id=data["lease_id"],
        client_type=data["client_type"],
        launcher_pid=data["launcher_pid"],
        heartbeat_at=data["heartbeat_at"],
        launcher_identity=data.get("launcher_identity"),
    )


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_optional_string(value: object) -> bool:
    return value is None or isinstance(value, str)


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_optional_positive_int(value: object) -> bool:
    return value is None or _is_positive_int(value)


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )
