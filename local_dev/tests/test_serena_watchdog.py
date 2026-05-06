import os
import sys
import time
from pathlib import Path

from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.registry import Lease, ServerRecord, locked_registry
from local_dev.serena_mcp_management.serena_mcp.watchdog import (
    cleanup_once,
    ensure_watchdog,
    release_lease_and_shutdown_if_empty,
    shutdown_if_no_leases,
)


def test_cleanup_once_removes_stale_leases(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    terminated = []
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog._terminate_pid", terminated.append)
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


def test_release_lease_reports_remaining_sibling_leases(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    terminated = []
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog._terminate_pid", terminated.append)
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=12345,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="codex",
            started_at=time.time(),
            leases={
                "exiting": Lease("exiting", os.getpid(), time.time()),
                "sibling": Lease("sibling", os.getpid(), time.time()),
            },
        )

    stats = release_lease_and_shutdown_if_empty(scope, "exiting")

    assert stats.sessions_before == 2
    assert stats.sessions_closed == 1
    assert stats.sessions_remaining == 1
    assert stats.server_stopped is False
    assert stats.server_was_running is True
    assert terminated == []
    with locked_registry(scope) as registry:
        assert registry.record is not None
        assert set(registry.record.leases) == {"sibling"}


def test_release_lease_stops_server_when_last_lease_exits(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    terminated = []
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog._terminate_pid", terminated.append)
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=12345,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="codex",
            started_at=time.time(),
            leases={"exiting": Lease("exiting", os.getpid(), time.time())},
        )

    stats = release_lease_and_shutdown_if_empty(scope, "exiting")

    assert stats.sessions_before == 1
    assert stats.sessions_closed == 1
    assert stats.sessions_remaining == 0
    assert stats.server_stopped is True
    assert stats.server_was_running is True
    assert terminated == [12345]
    with locked_registry(scope) as registry:
        assert registry.record is None


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
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog.pid_is_alive", lambda pid: True)
    calls = []
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog.subprocess.Popen", lambda *a, **k: calls.append(a))

    ensure_watchdog(scope)

    assert calls == []


def test_ensure_watchdog_runs_from_repo_root_with_import_path(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=os.getpid(),
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="codex",
            started_at=time.time(),
            leases={"live": Lease("live", os.getpid(), time.time())},
        )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog.pid_is_alive", lambda pid: False)

    calls = []

    class Proc:
        pid = 4321

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return Proc()

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog.subprocess.Popen", fake_popen)

    ensure_watchdog(scope)

    assert calls
    args, kwargs = calls[0]
    command = args[0]
    repo_root = Path(__file__).resolve().parents[2]
    assert command[:3] == [sys.executable, "-m", "local_dev.serena_mcp_management.serena_mcp.watchdog"]
    assert kwargs["cwd"] == str(repo_root)
    assert kwargs["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(repo_root)
    with locked_registry(scope) as registry:
        assert registry.record is not None
        assert registry.record.watchdog_pid == 4321
