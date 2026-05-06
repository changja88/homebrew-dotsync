import os
import time

from tools.serena_mcp.paths import Scope
from tools.serena_mcp.registry import Lease, ServerRecord, locked_registry
from tools.serena_mcp.watchdog import cleanup_once, ensure_watchdog, shutdown_if_no_leases


def test_cleanup_once_removes_stale_leases(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    terminated = []
    monkeypatch.setattr("tools.serena_mcp.watchdog._terminate_pid", terminated.append)
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=12345,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="codex",
            started_at=time.time(),
            leases={"old": Lease("old", 999999, time.time() - 999)},
        )

    cleanup_once(scope, now=time.time(), lease_timeout_seconds=1)

    with locked_registry(scope) as registry:
        assert registry.record is None
    assert terminated == [12345]


def test_cleanup_once_keeps_active_lease(tmp_path):
    scope = Scope(tmp_path, "claude")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=os.getpid(),
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="claude",
            started_at=time.time(),
            leases={"live": Lease("live", os.getpid(), time.time())},
        )

    cleanup_once(scope, now=time.time(), lease_timeout_seconds=60)

    with locked_registry(scope) as registry:
        assert registry.record is not None
        assert "live" in registry.record.leases


def test_shutdown_if_no_leases_keeps_server_when_sibling_lease_exists(tmp_path):
    scope = Scope(tmp_path, "codex")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=os.getpid(),
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="codex",
            started_at=time.time(),
            leases={"sibling": Lease("sibling", os.getpid(), time.time())},
        )

    assert shutdown_if_no_leases(scope) is True

    with locked_registry(scope) as registry:
        assert registry.record is not None
        assert "sibling" in registry.record.leases


def test_ensure_watchdog_does_not_spawn_duplicate_when_pid_alive(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "claude")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=os.getpid(),
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="claude",
            started_at=time.time(),
            leases={"live": Lease("live", os.getpid(), time.time())},
            watchdog_pid=777,
        )
    monkeypatch.setattr("tools.serena_mcp.watchdog.pid_is_alive", lambda pid: True)
    calls = []
    monkeypatch.setattr("tools.serena_mcp.watchdog.subprocess.Popen", lambda *a, **k: calls.append(a))

    ensure_watchdog(scope)

    assert calls == []
