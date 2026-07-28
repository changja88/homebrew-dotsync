"""Structured Serena MCP lifecycle diagnostics."""
from __future__ import annotations

from dataclasses import dataclass

from local_dev.serena_mcp_management.serena_mcp.paths import (
    Scope,
    client_type_for_serena_context,
)
from local_dev.serena_mcp_management.serena_mcp.processes import (
    ProcessScanError,
    scan_serena_mcp_processes,
)
from local_dev.serena_mcp_management.serena_mcp.registry import (
    locked_registry,
    read_registry_record,
    record_belongs_to_scope,
)


@dataclass(frozen=True, slots=True)
class GlobalLifecycleSnapshot:
    """Machine-wide Serena MCP inventory for preflight diagnostics."""

    ps_server_count: int
    managed_server_count: int
    orphan_server_count: int
    lease_count: int
    stale_lease_count: int
    scan_failed: bool = False


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
