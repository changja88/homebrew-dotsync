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


def test_snapshot_global_lifecycle_counts_ps_managed_orphan_and_leases(monkeypatch, tmp_path):
    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )

    codex_scope = Scope(tmp_path / "repo-a", "codex")
    claude_scope = Scope(tmp_path / "repo-b", "claude")

    with locked_registry(codex_scope) as registry:
        registry.record = ServerRecord(
            server_pid=111,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(codex_scope.project_root),
            client_type=codex_scope.client_type,
            started_at=1.0,
            leases={
                "live-a": Lease("live-a", 1001, 95.0, "launcher-a"),
                "stale-a": Lease("stale-a", 1002, 1.0, "launcher-b"),
            },
            upstream_mcp_url="http://127.0.0.1:9000/mcp",
            proxy_pid=112,
            server_identity="identity-111",
            proxy_identity="identity-112",
        )
    with locked_registry(claude_scope) as registry:
        registry.record = ServerRecord(
            server_pid=333,
            mcp_url="http://127.0.0.1:9010/mcp",
            dashboard_url="http://127.0.0.1:24010",
            project_root=str(claude_scope.project_root),
            client_type=claude_scope.client_type,
            started_at=2.0,
            leases={"live-b": Lease("live-b", 1003, 99.0, "launcher-c")},
            upstream_mcp_url="http://127.0.0.1:9010/mcp",
            proxy_pid=334,
            server_identity="identity-333",
            proxy_identity="identity-334",
        )

    processes = [
        SerenaMcpProcess(
            111,
            codex_scope.project_root,
            "codex",
            "managed codex",
            "identity-111",
        ),
        SerenaMcpProcess(
            222,
            tmp_path / "repo-c",
            "codex",
            "orphan codex",
            "identity-222",
        ),
        SerenaMcpProcess(
            333,
            claude_scope.project_root,
            "claude-code",
            "managed claude",
            "identity-333",
        ),
    ]
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        lambda: processes,
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.ps_server_count == 3
    assert snapshot.managed_server_count == 2
    assert snapshot.orphan_server_count == 1
    assert snapshot.lease_count == 3
    assert snapshot.stale_lease_count == 1


def test_snapshot_global_lifecycle_does_not_create_registry_dirs_for_orphans(
    monkeypatch,
    tmp_path,
):
    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )

    orphan_project = tmp_path / "repo-orphan"
    orphan_project.mkdir()
    processes = [
        SerenaMcpProcess(222, orphan_project, "codex", "orphan codex", "identity-222"),
    ]
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        lambda: processes,
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.ps_server_count == 1
    assert snapshot.managed_server_count == 0
    assert snapshot.orphan_server_count == 1
    assert not (orphan_project / ".serena" / "dotsync-mcp").exists()


def test_snapshot_global_lifecycle_requires_matching_server_identity(monkeypatch, tmp_path):
    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )

    scope = Scope(tmp_path / "repo", "codex")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=111,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(scope.project_root),
            client_type=scope.client_type,
            started_at=1.0,
            leases={"live": Lease("live", 1001, 95.0, "launcher-a")},
            upstream_mcp_url="http://127.0.0.1:9000/mcp",
            proxy_pid=112,
            server_identity="old-identity",
            proxy_identity="identity-112",
        )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        lambda: [
            SerenaMcpProcess(
                111,
                scope.project_root,
                "codex",
                "pid reused",
                "new-identity",
            ),
        ],
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.ps_server_count == 1
    assert snapshot.managed_server_count == 0
    assert snapshot.orphan_server_count == 1
    assert snapshot.lease_count == 0
    assert snapshot.stale_lease_count == 0


def test_snapshot_global_lifecycle_ignores_registry_records_not_seen_in_ps(
    monkeypatch,
    tmp_path,
):
    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )

    scope = Scope(tmp_path / "repo", "codex")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=111,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(scope.project_root),
            client_type=scope.client_type,
            started_at=1.0,
            leases={"stale": Lease("stale", 1001, 1.0, "launcher-a")},
            upstream_mcp_url="http://127.0.0.1:9000/mcp",
            proxy_pid=112,
            server_identity="identity-111",
            proxy_identity="identity-112",
        )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        lambda: [],
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.ps_server_count == 0
    assert snapshot.managed_server_count == 0
    assert snapshot.orphan_server_count == 0
    assert snapshot.lease_count == 0
    assert snapshot.stale_lease_count == 0


def test_snapshot_global_lifecycle_does_not_create_missing_registry_lock(
    monkeypatch,
    tmp_path,
):
    import json

    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )
    from local_dev.serena_mcp_management.serena_mcp.registry import registry_path

    scope = Scope(tmp_path / "repo", "codex")
    path = registry_path(scope)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "record": {
                    "server_pid": 111,
                    "mcp_url": "http://127.0.0.1:9000/mcp",
                    "dashboard_url": "http://127.0.0.1:24000",
                    "project_root": str(scope.project_root),
                    "client_type": scope.client_type,
                    "started_at": 1.0,
                    "leases": {},
                    "upstream_mcp_url": "http://127.0.0.1:9000/mcp",
                    "proxy_pid": 112,
                    "server_identity": "identity-111",
                    "proxy_identity": "identity-112",
                },
            }
        )
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        lambda: [
            SerenaMcpProcess(
                111,
                scope.project_root,
                "codex",
                "managed",
                "identity-111",
            ),
        ],
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.managed_server_count == 1
    assert not path.with_name("registry.lock").exists()


def test_snapshot_global_lifecycle_reports_scan_failure(monkeypatch):
    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )
    from local_dev.serena_mcp_management.serena_mcp.processes import ProcessScanError

    def fail_scan():
        raise ProcessScanError("ps failed")

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        fail_scan,
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.scan_failed is True
    assert snapshot.ps_server_count == 0
    assert snapshot.managed_server_count == 0
    assert snapshot.orphan_server_count == 0


def test_snapshot_global_lifecycle_treats_malformed_registry_as_orphan(
    monkeypatch,
    tmp_path,
):
    import json

    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )
    from local_dev.serena_mcp_management.serena_mcp.registry import registry_path

    scope = Scope(tmp_path / "repo", "codex")
    path = registry_path(scope)
    path.parent.mkdir(parents=True)
    path.with_name("registry.lock").write_text("")
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "record": {
                    "server_pid": 111,
                    "mcp_url": "http://127.0.0.1:9000/mcp",
                    "dashboard_url": "http://127.0.0.1:24000",
                    "project_root": str(scope.project_root),
                    "client_type": scope.client_type,
                    "started_at": 1.0,
                    "leases": ["not", "a", "dict"],
                    "server_identity": "identity-111",
                },
            }
        )
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        lambda: [
            SerenaMcpProcess(
                111,
                scope.project_root,
                "codex",
                "managed",
                "identity-111",
            ),
        ],
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.ps_server_count == 1
    assert snapshot.managed_server_count == 0
    assert snapshot.orphan_server_count == 1
