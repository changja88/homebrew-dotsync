import os
import subprocess

from tools.serena_mcp.paths import Scope
from tools.serena_mcp.registry import Lease, ServerRecord, locked_registry
from tools.serena_mcp.server import _discover_dashboard_url, _start_serena_process, ensure_server, serena_context_for


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
        stored_lease = registry.record.leases["lease-a"]
        assert stored_lease.lease_id == lease.lease_id
        assert stored_lease.launcher_pid == lease.launcher_pid
        assert stored_lease.heartbeat_at >= lease.heartbeat_at


def test_ensure_server_refreshes_initial_lease_after_slow_startup(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "claude")

    class Proc:
        pid = os.getpid()

    lease = Lease("lease-a", os.getpid(), 1.0)
    monkeypatch.setattr("tools.serena_mcp.server.server_is_healthy", lambda r, s: False)
    monkeypatch.setattr("tools.serena_mcp.server._find_free_port_with_host_lock", lambda: 9001)
    monkeypatch.setattr("tools.serena_mcp.server._start_serena_process", lambda scope, port: Proc())
    monkeypatch.setattr("tools.serena_mcp.server._discover_dashboard_url", lambda proc: "http://127.0.0.1:24001")
    monkeypatch.setattr("tools.serena_mcp.server._wait_until_healthy", lambda record, scope: None)
    monkeypatch.setattr("tools.serena_mcp.server.ensure_watchdog", lambda scope: None)
    monkeypatch.setattr("tools.serena_mcp.server.time.time", lambda: 100.0)

    ensure_server(scope, lease)

    with locked_registry(scope) as registry:
        assert registry.record is not None
        assert registry.record.leases["lease-a"].heartbeat_at == 100.0


def test_serena_context_maps_claude_client_to_claude_code():
    assert serena_context_for("codex") == "codex"
    assert serena_context_for("claude") == "claude-code"


def test_start_serena_process_redirects_output_to_scope_log(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    calls = []

    class Proc:
        pid = 123

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return Proc()

    monkeypatch.setattr("tools.serena_mcp.server.subprocess.Popen", fake_popen)

    proc = _start_serena_process(scope, 9012)

    assert proc.dotsync_log_path == scope.project_root / ".serena" / "dotsync-mcp" / "codex" / "serena-server.log"
    assert calls
    kwargs = calls[0][1]
    assert kwargs["stdout"] is not subprocess.PIPE
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["text"] is True
    assert kwargs["start_new_session"] is True
    assert kwargs["cwd"] == str(scope.project_root)


def test_discover_dashboard_url_reads_redirected_log(tmp_path):
    log_path = tmp_path / "serena-server.log"
    log_path.write_text("INFO Serena web dashboard started at http://127.0.0.1:24284/dashboard/index.html\n")

    class Proc:
        pid = 123
        dotsync_log_path = log_path

        def poll(self):
            return None

    assert _discover_dashboard_url(Proc(), timeout=0.1) == "http://127.0.0.1:24284"


def test_discover_dashboard_url_ignores_mcp_url_before_dashboard_url(tmp_path):
    log_path = tmp_path / "serena-server.log"
    log_path.write_text(
        "INFO MCP server listening at http://127.0.0.1:19000/mcp\n"
        "INFO Serena web dashboard started at http://127.0.0.1:24284/dashboard/index.html\n"
    )

    class Proc:
        pid = 123
        dotsync_log_path = log_path

        def poll(self):
            return None

    assert _discover_dashboard_url(Proc(), timeout=0.1) == "http://127.0.0.1:24284"
