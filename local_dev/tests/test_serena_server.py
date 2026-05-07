import os
import subprocess
import sys

import pytest

import local_dev.serena_mcp_management.serena_mcp.server as server

from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.registry import Lease, ServerRecord, locked_registry
from local_dev.serena_mcp_management.serena_mcp.server import (
    _discover_dashboard_url,
    _start_healthy_server,
    _start_serena_process,
    ensure_server,
    serena_context_for,
)


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
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.server_is_healthy", lambda r, s: True)
    popen_calls = []
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._start_serena_process", lambda *a, **k: popen_calls.append(a))
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.ensure_watchdog", lambda scope: None)

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
        def __init__(self, pid):
            self.pid = pid

    lease = Lease("lease-a", os.getpid(), 10.0)
    ports = iter([9001, 9002])
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.server_is_healthy", lambda r, s: False)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._find_free_port_with_host_lock", lambda: next(ports))
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._start_serena_process", lambda scope, port: Proc(111))
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._start_proxy_process",
        lambda scope, port, upstream_url: Proc(222),
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._discover_dashboard_url", lambda proc: "http://127.0.0.1:24001")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._wait_until_healthy", lambda record, scope: None)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._terminate_pid", lambda pid: None)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.ensure_watchdog", lambda scope: None)

    record = ensure_server(scope, lease)

    assert record.server_pid == 111
    assert record.proxy_pid == 222
    assert record.upstream_mcp_url == "http://127.0.0.1:9001/mcp"
    assert record.mcp_url == "http://127.0.0.1:9002/mcp"
    with locked_registry(scope) as registry:
        assert registry.record is not None
        stored_lease = registry.record.leases["lease-a"]
        assert stored_lease.lease_id == lease.lease_id
        assert stored_lease.launcher_pid == lease.launcher_pid
        assert stored_lease.heartbeat_at >= lease.heartbeat_at


def test_ensure_server_refreshes_initial_lease_after_slow_startup(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "claude")

    class Proc:
        def __init__(self, pid):
            self.pid = pid

    lease = Lease("lease-a", os.getpid(), 1.0)
    ports = iter([9001, 9002])
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.server_is_healthy", lambda r, s: False)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._find_free_port_with_host_lock", lambda: next(ports))
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._start_serena_process", lambda scope, port: Proc(111))
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._start_proxy_process",
        lambda scope, port, upstream_url: Proc(222),
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._discover_dashboard_url", lambda proc: "http://127.0.0.1:24001")
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._wait_until_healthy", lambda record, scope: None)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.ensure_watchdog", lambda scope: None)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.time.time", lambda: 100.0)

    ensure_server(scope, lease)

    with locked_registry(scope) as registry:
        assert registry.record is not None
        assert registry.record.proxy_pid == 222
        assert registry.record.upstream_mcp_url == "http://127.0.0.1:9001/mcp"
        assert registry.record.mcp_url == "http://127.0.0.1:9002/mcp"
        assert registry.record.leases["lease-a"].heartbeat_at == 100.0


def test_start_healthy_server_exposes_proxy_url_and_tracks_upstream(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    lease = Lease("lease-a", os.getpid(), 10.0)
    ports = iter([9000, 9001])
    serena_calls = []
    proxy_calls = []

    class Proc:
        def __init__(self, pid):
            self.pid = pid

    def fake_start_serena_process(scope_arg, port):
        serena_calls.append((scope_arg, port))
        return Proc(111)

    def fake_start_proxy_process(scope_arg, port, upstream_url):
        proxy_calls.append((scope_arg, port, upstream_url))
        return Proc(222)

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._find_free_port_with_host_lock",
        lambda: next(ports),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._start_serena_process",
        fake_start_serena_process,
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._start_proxy_process",
        fake_start_proxy_process,
        raising=False,
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._discover_dashboard_url",
        lambda proc: "http://127.0.0.1:24001",
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._wait_until_healthy",
        lambda record, scope: None,
    )

    record = _start_healthy_server(scope, lease)

    assert record.server_pid == 111
    assert record.proxy_pid == 222
    assert record.upstream_mcp_url == "http://127.0.0.1:9000/mcp"
    assert record.mcp_url == "http://127.0.0.1:9001/mcp"
    assert serena_calls == [(scope, 9000)]
    assert proxy_calls == [(scope, 9001, "http://127.0.0.1:9000/mcp")]


def test_start_healthy_server_terminates_proxy_and_upstream_when_health_fails(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    lease = Lease("lease-a", os.getpid(), 10.0)
    ports = iter([9000, 9001, 9002, 9003, 9004, 9005])
    terminated_pids = []

    class Proc:
        def __init__(self, pid):
            self.pid = pid

    def fake_start_serena_process(scope_arg, port):
        return Proc(111 + port - 9000)

    def fake_start_proxy_process(scope_arg, port, upstream_url):
        return Proc(222 + port - 9001)

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._find_free_port_with_host_lock",
        lambda: next(ports),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._start_serena_process",
        fake_start_serena_process,
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._start_proxy_process",
        fake_start_proxy_process,
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._discover_dashboard_url",
        lambda proc: "http://127.0.0.1:24001",
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._wait_until_healthy",
        lambda record, scope: (_ for _ in ()).throw(RuntimeError("not healthy")),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._terminate_pid",
        lambda pid: terminated_pids.append(pid),
    )

    with pytest.raises(RuntimeError, match="failed to start healthy Serena MCP server"):
        _start_healthy_server(scope, lease)

    assert terminated_pids == [222, 111, 224, 113, 226, 115]


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

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.subprocess.Popen", fake_popen)

    proc = _start_serena_process(scope, 9012)

    assert proc.dotsync_log_path == scope.project_root / ".serena" / "dotsync-mcp" / "codex" / "serena-server.log"
    assert calls
    kwargs = calls[0][1]
    assert kwargs["stdout"] is not subprocess.PIPE
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["text"] is True
    assert kwargs["start_new_session"] is True
    assert kwargs["cwd"] == str(scope.project_root)


def test_start_proxy_process_uses_module_cli_and_scope_log(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    calls = []

    class Proc:
        pid = 222

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return Proc()

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.subprocess.Popen", fake_popen)

    proc = server._start_proxy_process(scope, 9013, "http://127.0.0.1:9012/mcp")

    assert proc.dotsync_log_path == scope.project_root / ".serena" / "dotsync-mcp" / "codex" / "serena-proxy.log"
    assert calls
    command = calls[0][0][0]
    kwargs = calls[0][1]
    assert command == [
        sys.executable,
        "-m",
        "local_dev.serena_mcp_management.serena_mcp.proxy",
        "--host",
        "127.0.0.1",
        "--port",
        "9013",
        "--upstream-url",
        "http://127.0.0.1:9012/mcp",
        "--log-path",
        str(proc.dotsync_log_path),
    ]
    assert kwargs["stdout"] is not subprocess.PIPE
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["text"] is True
    assert kwargs["start_new_session"] is True
    assert kwargs["cwd"] == str(server.REPO_ROOT)


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
