"""Structured Serena MCP lifecycle diagnostics."""
from __future__ import annotations

from dataclasses import dataclass

from local_dev.serena_mcp_management.serena_mcp.paths import (
    Scope,
    client_type_for_serena_context,
)
from local_dev.serena_mcp_management.serena_mcp.processes import (
    ProcessScanError,
    list_serena_mcp_processes,
    process_matches_scope,
    scan_serena_mcp_processes,
)
from local_dev.serena_mcp_management.serena_mcp.registry import (
    locked_registry,
    read_registry_record,
    record_belongs_to_scope,
    registry_path,
)


@dataclass(frozen=True, slots=True)
class LifecycleSnapshot:
    """Scope-local lifecycle state for diagnostics."""

    project_root: str
    client_type: str
    registry_path: str
    registered_server_pid: int | None
    registered_proxy_pid: int | None
    registered_watchdog_pid: int | None
    lease_count: int
    stale_lease_count: int
    live_launcher_identities: list[str]
    same_scope_orphan_pids: list[int]


@dataclass(frozen=True, slots=True)
class GlobalLifecycleSnapshot:
    """Machine-wide Serena MCP inventory for preflight diagnostics."""

    ps_server_count: int
    managed_server_count: int
    orphan_server_count: int
    lease_count: int
    stale_lease_count: int
    scan_failed: bool = False


def snapshot_lifecycle(
    scope: Scope,
    *,
    now: float,
    stale_after_seconds: float,
) -> LifecycleSnapshot:
    """Return a scope-local snapshot for debugging lifecycle state."""

    with locked_registry(scope) as registry:
        record = registry.record
        registered_server_pid = record.server_pid if record is not None else None
        registered_proxy_pid = record.proxy_pid if record is not None else None
        registered_watchdog_pid = record.watchdog_pid if record is not None else None
        leases = record.leases if record is not None else {}
        stale_lease_count = sum(
            1
            for lease in leases.values()
            if now - lease.heartbeat_at > stale_after_seconds
        )
        identities = sorted(
            lease.launcher_identity
            for lease in leases.values()
            if lease.launcher_identity is not None
        )
    orphan_pids = [
        process.pid
        for process in list_serena_mcp_processes()
        if process_matches_scope(process, scope)
        and process.pid != registered_server_pid
    ]
    return LifecycleSnapshot(
        project_root=str(scope.project_root),
        client_type=scope.client_type,
        registry_path=str(registry_path(scope)),
        registered_server_pid=registered_server_pid,
        registered_proxy_pid=registered_proxy_pid,
        registered_watchdog_pid=registered_watchdog_pid,
        lease_count=len(leases),
        stale_lease_count=stale_lease_count,
        live_launcher_identities=identities,
        same_scope_orphan_pids=sorted(orphan_pids),
    )


def snapshot_global_lifecycle(
    *,
    now: float,
    stale_after_seconds: float,
) -> GlobalLifecycleSnapshot:
    """Return machine-wide Serena MCP counts with managed as a ps subset."""

    try:
        processes = scan_serena_mcp_processes()
    except ProcessScanError:
        return GlobalLifecycleSnapshot(
            ps_server_count=0,
            managed_server_count=0,
            orphan_server_count=0,
            lease_count=0,
            stale_lease_count=0,
            scan_failed=True,
        )
    managed_server_count = 0
    lease_count = 0
    stale_lease_count = 0

    for process in processes:
        client_type = client_type_for_serena_context(process.context)
        if client_type is None or process.identity is None:
            continue
        scope = Scope(process.project_root, client_type)
        record = read_registry_record(scope)
        if record is None or not record_belongs_to_scope(record, scope):
            continue
        if record.server_pid != process.pid:
            continue
        if record.server_identity is None or record.server_identity != process.identity:
            continue

        managed_server_count += 1
        lease_count += len(record.leases)
        stale_lease_count += sum(
            1
            for lease in record.leases.values()
            if now - lease.heartbeat_at > stale_after_seconds
        )

    ps_server_count = len(processes)
    return GlobalLifecycleSnapshot(
        ps_server_count=ps_server_count,
        managed_server_count=managed_server_count,
        orphan_server_count=ps_server_count - managed_server_count,
        lease_count=lease_count,
        stale_lease_count=stale_lease_count,
    )
