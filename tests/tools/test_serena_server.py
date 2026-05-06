import os

from tools.serena_mcp.paths import Scope
from tools.serena_mcp.registry import Lease, ServerRecord, locked_registry
from tools.serena_mcp.server import ensure_server, serena_context_for


def test_ensure_server_reuses_healthy_record(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    record = ServerRecord(
        server_pid=os.getpid(),
        mcp_url="http://127.0.0.1:9000/mcp",
        dashboard_url="http://127.0.0.1:24000",
        project_root=str(tmp_path.resolve()),
        client_type="codex",
        started_at=1.0,
        leases={},
    )
    with locked_registry(scope) as registry:
        registry.record = record

    lease = Lease("lease-a", os.getpid(), 10.0)
    monkeypatch.setattr("tools.serena_mcp.server.server_is_healthy", lambda r, s: True)
    popen_calls = []
    monkeypatch.setattr("tools.serena_mcp.server._start_serena_process", lambda *a, **k: popen_calls.append(a))
    monkeypatch.setattr("tools.serena_mcp.server.ensure_watchdog", lambda scope: None)

    assert ensure_server(scope, lease).mcp_url == record.mcp_url
    assert popen_calls == []
    with locked_registry(scope) as registry:
        assert registry.record is not None
        assert "lease-a" in registry.record.leases


def test_ensure_server_replaces_unhealthy_record(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "claude")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=999999,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="claude",
            started_at=1.0,
            leases={},
        )

    class Proc:
        pid = os.getpid()

    lease = Lease("lease-a", os.getpid(), 10.0)
    monkeypatch.setattr("tools.serena_mcp.server.server_is_healthy", lambda r, s: False)
    monkeypatch.setattr("tools.serena_mcp.server._find_free_port_with_host_lock", lambda: 9001)
    monkeypatch.setattr("tools.serena_mcp.server._start_serena_process", lambda scope, port: Proc())
    monkeypatch.setattr("tools.serena_mcp.server._discover_dashboard_url", lambda proc: "http://127.0.0.1:24001")
    monkeypatch.setattr("tools.serena_mcp.server._wait_until_healthy", lambda record, scope: None)
    monkeypatch.setattr("tools.serena_mcp.server._terminate_pid", lambda pid: None)
    monkeypatch.setattr("tools.serena_mcp.server.ensure_watchdog", lambda scope: None)

    record = ensure_server(scope, lease)

    assert record.mcp_url == "http://127.0.0.1:9001/mcp"
    with locked_registry(scope) as registry:
        assert registry.record is not None
        assert registry.record.leases == {"lease-a": lease}


def test_serena_context_maps_claude_client_to_claude_code():
    assert serena_context_for("codex") == "codex"
    assert serena_context_for("claude") == "claude-code"
