import os

import pytest

import local_dev.serena_mcp_management.serena_mcp.server as server

from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.registry import Lease, ServerRecord, locked_registry
from local_dev.serena_mcp_management.serena_mcp.server import (
    _discover_dashboard_url,
    _start_healthy_server,
    _start_serena_process,
    ensure_server,
)


def test_ensure_server_reuses_healthy_record(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    record = ServerRecord(
        server_pid=111,
        mcp_url="http://127.0.0.1:9000/mcp",
        dashboard_url="http://127.0.0.1:24000",
        project_root=str(tmp_path.resolve()),
        client_type="codex",
        started_at=1.0,
        leases={},
        upstream_mcp_url="http://127.0.0.1:9001/mcp",
        proxy_pid=222,
        server_identity="serena identity",
        proxy_identity="proxy identity",
    )
    with locked_registry(scope) as registry:
        registry.record = record

    lease = Lease("lease-a", os.getpid(), 10.0)
    checked_pids = []
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server.pid_is_alive",
        lambda pid: checked_pids.append(pid) or True,
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.http_endpoint_alive", lambda url: True)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server.process_identity",
        lambda pid: {111: "serena identity", 222: "proxy identity"}[pid],
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server.dashboard_matches_project",
        lambda dashboard_url, project_root: True,
    )
    popen_calls = []
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._start_serena_process", lambda *a, **k: popen_calls.append(a))
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.ensure_watchdog", lambda scope: None)

    assert ensure_server(scope, lease).mcp_url == record.mcp_url
    assert checked_pids == [111, 222]
    assert popen_calls == []
    with locked_registry(scope) as registry:
        assert registry.record is not None
        assert "lease-a" in registry.record.leases


def test_server_health_rejects_reused_server_pid_identity(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    record = ServerRecord(
        server_pid=111,
        mcp_url="http://127.0.0.1:9000/mcp",
        dashboard_url="http://127.0.0.1:24000",
        project_root=str(tmp_path.resolve()),
        client_type="codex",
        started_at=1.0,
        leases={},
        upstream_mcp_url="http://127.0.0.1:9001/mcp",
        proxy_pid=222,
        server_identity="original serena identity",
        proxy_identity="proxy identity",
    )

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.pid_is_alive", lambda pid: True)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server.process_identity",
        lambda pid: "different identity" if pid == 111 else "proxy identity",
        raising=False,
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.http_endpoint_alive", lambda url: True)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server.dashboard_matches_project",
        lambda dashboard_url, project_root: True,
    )

    assert server.server_is_healthy(record, scope) is False


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


def test_ensure_server_discards_wrong_project_record_without_terminating_pids(monkeypatch, tmp_path):
    scope = Scope(tmp_path / "current", "codex")
    wrong_scope = Scope(tmp_path / "other", "codex")
    lease = Lease("lease-a", os.getpid(), 10.0)
    terminated = []

    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=111,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(wrong_scope.project_root),
            client_type=wrong_scope.client_type,
            started_at=1.0,
            leases={},
            upstream_mcp_url="http://127.0.0.1:9001/mcp",
            proxy_pid=222,
        )

    replacement = ServerRecord(
        server_pid=333,
        mcp_url="http://127.0.0.1:9002/mcp",
        dashboard_url="http://127.0.0.1:24001",
        project_root=str(scope.project_root),
        client_type=scope.client_type,
        started_at=2.0,
        leases={"lease-a": lease},
        upstream_mcp_url="http://127.0.0.1:9003/mcp",
        proxy_pid=444,
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._terminate_pid", terminated.append)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._start_healthy_server",
        lambda scope_arg, lease_arg: replacement,
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.ensure_watchdog", lambda scope_arg: None)

    assert ensure_server(scope, lease) == replacement
    assert terminated == []


def test_ensure_server_refreshes_initial_lease_after_slow_startup(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "claude")

    class Proc:
        def __init__(self, pid):
            self.pid = pid

    launcher_identity = "Fri May  8 10:00:00 2026 /usr/bin/python launcher"
    lease = Lease("lease-a", os.getpid(), 1.0, launcher_identity)
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
        stored_lease = registry.record.leases["lease-a"]
        assert stored_lease.heartbeat_at == 100.0
        assert stored_lease.launcher_identity == launcher_identity


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


def test_start_healthy_server_records_process_identities(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    lease = Lease("lease-a", os.getpid(), 10.0)
    ports = iter([9000, 9001])

    class Proc:
        def __init__(self, pid):
            self.pid = pid

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._find_free_port_with_host_lock",
        lambda: next(ports),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._start_serena_process",
        lambda scope_arg, port: Proc(111),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._start_proxy_process",
        lambda scope_arg, port, upstream_url: Proc(222),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server.process_identity",
        lambda pid: {111: "serena identity", 222: "proxy identity"}[pid],
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

    assert record.server_identity == "serena identity"
    assert record.proxy_identity == "proxy identity"


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
        lambda pid, *, expected_identity=None: terminated_pids.append(pid),
    )

    with pytest.raises(RuntimeError, match="failed to start healthy Serena MCP server"):
        _start_healthy_server(scope, lease)

    assert terminated_pids == [222, 111, 224, 113, 226, 115]


def test_ensure_server_terminates_registryless_same_scope_orphan_before_start(monkeypatch, tmp_path):
    scope = Scope(tmp_path / "repo", "codex")
    lease = Lease("lease-a", os.getpid(), 10.0)
    terminated = []

    replacement = ServerRecord(
        server_pid=333,
        mcp_url="http://127.0.0.1:9002/mcp",
        dashboard_url="http://127.0.0.1:24001",
        project_root=str(scope.project_root),
        client_type=scope.client_type,
        started_at=2.0,
        leases={"lease-a": lease},
        upstream_mcp_url="http://127.0.0.1:9003/mcp",
        proxy_pid=444,
    )
    orphan = server.SerenaMcpProcess(
        pid=111,
        project_root=scope.project_root,
        context="codex",
        command="serena start-mcp-server",
        identity="orphan identity",
    )

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.list_serena_mcp_processes", lambda: [orphan])
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._terminate_pid",
        lambda pid, *, expected_identity=None: terminated.append(pid),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._start_healthy_server",
        lambda scope_arg, lease_arg: replacement,
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.ensure_watchdog", lambda scope_arg: None)

    assert ensure_server(scope, lease) == replacement
    assert terminated == [111]


def test_ensure_server_preserves_other_project_and_other_client_processes(monkeypatch, tmp_path):
    scope = Scope(tmp_path / "repo", "codex")
    other_project = Scope(tmp_path / "other", "codex")
    other_client = Scope(tmp_path / "repo", "claude")
    lease = Lease("lease-a", os.getpid(), 10.0)
    terminated = []

    replacement = ServerRecord(
        server_pid=333,
        mcp_url="http://127.0.0.1:9002/mcp",
        dashboard_url="http://127.0.0.1:24001",
        project_root=str(scope.project_root),
        client_type=scope.client_type,
        started_at=2.0,
        leases={"lease-a": lease},
        upstream_mcp_url="http://127.0.0.1:9003/mcp",
        proxy_pid=444,
    )
    processes = [
        server.SerenaMcpProcess(111, other_project.project_root, "codex", "serena start-mcp-server"),
        server.SerenaMcpProcess(222, other_client.project_root, "claude-code", "serena start-mcp-server"),
    ]

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.list_serena_mcp_processes", lambda: processes)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._terminate_pid", terminated.append)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._start_healthy_server",
        lambda scope_arg, lease_arg: replacement,
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.ensure_watchdog", lambda scope_arg: None)

    ensure_server(scope, lease)

    assert terminated == []


def test_ensure_server_reuses_healthy_record_and_cleans_extra_same_scope_upstream(monkeypatch, tmp_path):
    scope = Scope(tmp_path / "repo", "codex")
    lease = Lease("lease-a", os.getpid(), 10.0)
    terminated = []
    record = ServerRecord(
        server_pid=111,
        mcp_url="http://127.0.0.1:9000/mcp",
        dashboard_url="http://127.0.0.1:24000",
        project_root=str(scope.project_root),
        client_type=scope.client_type,
        started_at=1.0,
        leases={},
        upstream_mcp_url="http://127.0.0.1:9001/mcp",
        proxy_pid=222,
        server_identity="serena identity",
        proxy_identity="proxy identity",
    )
    with locked_registry(scope) as registry:
        registry.record = record
    processes = [
        server.SerenaMcpProcess(111, scope.project_root, "codex", "registered", "serena identity"),
        server.SerenaMcpProcess(333, scope.project_root, "codex", "extra", "extra identity"),
    ]

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.pid_is_alive", lambda pid: True)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.http_endpoint_alive", lambda url: True)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server.process_identity",
        lambda pid: {111: "serena identity", 222: "proxy identity"}[pid],
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server.dashboard_matches_project",
        lambda dashboard_url, project_root: True,
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.list_serena_mcp_processes", lambda: processes)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server._terminate_pid",
        lambda pid, *, expected_identity=None: terminated.append(pid),
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.ensure_watchdog", lambda scope_arg: None)

    assert ensure_server(scope, lease).server_pid == 111
    assert terminated == [333]


def test_discover_dashboard_url_reads_redirected_log(tmp_path):
    log_path = tmp_path / "serena-server.log"
    log_path.write_text("INFO Serena web dashboard started at http://127.0.0.1:24284/dashboard/index.html\n")

    class Proc:
        pid = 123
        dotsync_log_path = log_path

        def poll(self):
            return None

    assert _discover_dashboard_url(Proc(), timeout=0.1) == "http://127.0.0.1:24284"


def test_start_serena_process_uses_resolved_server_command(monkeypatch, tmp_path):
    # serena는 PATH에 없을 수 있다 — external_cli resolver가 돌려준 직접
    # 바이너리 argv로 띄워야 한다 (uvx 래퍼는 server_pid를 어긋나게 하므로 금지).
    scope = Scope(tmp_path, "codex")
    monkeypatch.setattr(server, "serena_server_command",
                        lambda: ["/u/.local/bin/serena"], raising=False)
    calls = []

    class Proc:
        pid = 123

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return Proc()

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server.subprocess.Popen",
        fake_popen,
    )

    _start_serena_process(scope, 9012)

    argv = calls[0][0][0]
    assert argv[:2] == ["/u/.local/bin/serena", "start-mcp-server"]
    assert "--project" in argv
    assert str(scope.project_root) in argv


def test_start_serena_process_raises_actionable_error_without_cli(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    monkeypatch.setattr(server, "serena_server_command",
                        lambda: None, raising=False)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.server.subprocess.Popen",
        lambda *a, **k: pytest.fail("must not spawn without a resolved CLI"),
    )
    with pytest.raises(RuntimeError, match="serena CLI"):
        _start_serena_process(scope, 9012)
