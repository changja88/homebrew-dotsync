from local_dev.serena_mcp_management.serena_mcp.diagnostics import snapshot_lifecycle
from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.processes import SerenaMcpProcess
from local_dev.serena_mcp_management.serena_mcp.registry import Lease, ServerRecord, locked_registry


def test_snapshot_lifecycle_reports_registry_and_stale_lease_counts(monkeypatch, tmp_path):
    scope = Scope(tmp_path / "repo", "codex")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=111,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(scope.project_root),
            client_type=scope.client_type,
            started_at=1.0,
            leases={
                "live": Lease("live", 1001, 95.0, "live identity"),
                "stale": Lease("stale", 1002, 1.0, "stale identity"),
            },
            watchdog_pid=333,
            upstream_mcp_url="http://127.0.0.1:9001/mcp",
            proxy_pid=222,
        )

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.list_serena_mcp_processes",
        lambda: [],
    )

    snapshot = snapshot_lifecycle(scope, now=100.0, stale_after_seconds=30.0)

    assert snapshot.project_root == str(scope.project_root)
    assert snapshot.client_type == "codex"
    assert snapshot.registry_path.endswith(".serena/dotsync-mcp/codex/registry.json")
    assert snapshot.registered_server_pid == 111
    assert snapshot.registered_proxy_pid == 222
    assert snapshot.registered_watchdog_pid == 333
    assert snapshot.lease_count == 2
    assert snapshot.stale_lease_count == 1
    assert snapshot.live_launcher_identities == ["live identity", "stale identity"]
    assert snapshot.same_scope_orphan_pids == []


def test_snapshot_lifecycle_reports_same_scope_orphan_candidates(monkeypatch, tmp_path):
    scope = Scope(tmp_path / "repo", "codex")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=111,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(scope.project_root),
            client_type=scope.client_type,
            started_at=1.0,
            leases={},
            upstream_mcp_url="http://127.0.0.1:9001/mcp",
            proxy_pid=222,
        )
    processes = [
        SerenaMcpProcess(111, scope.project_root, "codex", "registered"),
        SerenaMcpProcess(333, scope.project_root, "codex", "orphan"),
        SerenaMcpProcess(444, tmp_path / "other", "codex", "other project"),
    ]
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.list_serena_mcp_processes",
        lambda: processes,
    )

    snapshot = snapshot_lifecycle(scope, now=100.0, stale_after_seconds=30.0)

    assert snapshot.same_scope_orphan_pids == [333]
