"""Structured Serena MCP lifecycle diagnostics."""
from __future__ import annotations

from dataclasses import dataclass

from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.processes import (
    list_serena_mcp_processes,
    process_matches_scope,
)
from local_dev.serena_mcp_management.serena_mcp.registry import locked_registry, registry_path


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
