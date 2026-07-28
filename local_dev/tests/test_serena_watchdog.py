import os
import sys
import time
from pathlib import Path

from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.registry import Lease, ServerRecord, locked_registry
from local_dev.serena_mcp_management.serena_mcp.watchdog import (
    cleanup_once,
    ensure_watchdog,
    launcher_process_matches,
    release_lease_and_shutdown_if_empty,
)


def _append_terminated(terminated):
    return lambda pid, *, expected_identity=None: terminated.append(pid)


def test_cleanup_once_removes_stale_leases(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    terminated = []
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog._terminate_pid",
        _append_terminated(terminated),
    )
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=12345,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="codex",
            started_at=time.time(),
            leases={"old": Lease("old", 999999, time.time() - 999)},
            server_identity="serena identity",
        )

    cleanup_once(scope, now=time.time(), lease_timeout_seconds=1)

    with locked_registry(scope) as registry:
        assert registry.record is None
    assert terminated == [12345]


def test_cleanup_once_removes_stale_last_lease_and_stops_proxy_then_upstream(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    terminated = []
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog._terminate_pid",
        _append_terminated(terminated),
    )
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=12345,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="codex",
            started_at=time.time(),
            leases={"old": Lease("old", 999999, time.time() - 999)},
            upstream_mcp_url="http://127.0.0.1:9001/mcp",
            proxy_pid=222,
            server_identity="serena identity",
            proxy_identity="proxy identity",
        )

    cleanup_once(scope, now=time.time(), lease_timeout_seconds=1)

    with locked_registry(scope) as registry:
        assert registry.record is None
    assert terminated == [222, 12345]


def test_cleanup_once_refreshes_stale_live_identity_matched_lease(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "claude")
    terminated = []
    now = time.time()
    launcher_identity = "Fri May  8 10:00:00 2026 /usr/bin/python launcher"
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog._terminate_pid",
        _append_terminated(terminated),
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog.pid_is_alive", lambda pid: False)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog.process_identity",
        lambda pid: "watchdog identity",
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog.process_identity",
        lambda pid: launcher_identity if pid == 1234 else None,
        raising=False,
    )
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=111,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="claude",
            started_at=now - 3600,
            leases={"sleeping": Lease("sleeping", 1234, now - 3600, launcher_identity)},
            upstream_mcp_url="http://127.0.0.1:9001/mcp",
            proxy_pid=222,
        )

    keep_running = cleanup_once(scope, now=now, lease_timeout_seconds=30)

    assert keep_running is True
    assert terminated == []
    with locked_registry(scope) as registry:
        assert registry.record is not None
        assert set(registry.record.leases) == {"sleeping"}
        assert registry.record.leases["sleeping"].heartbeat_at == now


def test_cleanup_once_drops_stale_wrong_identity_last_lease(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "claude")
    terminated = []
    now = time.time()
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog._terminate_pid",
        _append_terminated(terminated),
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog.pid_is_alive", lambda pid: True)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog.process_identity",
        lambda pid: "different launcher identity",
        raising=False,
    )
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=111,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="claude",
            started_at=now - 3600,
            leases={"stale": Lease("stale", 1234, now - 3600, "original launcher identity")},
            upstream_mcp_url="http://127.0.0.1:9001/mcp",
            proxy_pid=222,
            server_identity="serena identity",
            proxy_identity="proxy identity",
        )

    keep_running = cleanup_once(scope, now=now, lease_timeout_seconds=30)

    assert keep_running is False
    assert terminated == [222, 111]
    with locked_registry(scope) as registry:
        assert registry.record is None


def test_cleanup_once_keeps_active_lease(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "claude")
    terminated = []
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog._terminate_pid",
        _append_terminated(terminated),
    )
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
    assert terminated == []


def test_launcher_process_matches_rejects_identityless_legacy_lease(monkeypatch):
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog.pid_is_alive", lambda pid: True)

    lease = Lease("legacy", 1234, time.time() - 3600, None)

    assert launcher_process_matches(lease) is False


def test_cleanup_once_discards_wrong_client_record_without_terminating_pids(monkeypatch, tmp_path):
    scope = Scope(tmp_path / "repo", "codex")
    wrong_scope = Scope(tmp_path / "repo", "claude")
    terminated = []
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog._terminate_pid",
        _append_terminated(terminated),
    )

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

    assert cleanup_once(scope, now=time.time(), lease_timeout_seconds=1) is False
    assert terminated == []
    with locked_registry(scope) as registry:
        assert registry.record is None


def test_release_lease_reports_remaining_sibling_leases(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    terminated = []
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog._terminate_pid",
        _append_terminated(terminated),
    )
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
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog._terminate_pid",
        _append_terminated(terminated),
    )
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=12345,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="codex",
            started_at=time.time(),
            leases={"exiting": Lease("exiting", os.getpid(), time.time())},
            server_identity="serena identity",
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


def test_release_lease_stops_proxy_then_upstream_when_last_lease_exits(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    terminated = []
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog._terminate_pid",
        _append_terminated(terminated),
    )
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=12345,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path.resolve()),
            client_type="codex",
            started_at=time.time(),
            leases={"exiting": Lease("exiting", os.getpid(), time.time())},
            upstream_mcp_url="http://127.0.0.1:9001/mcp",
            proxy_pid=222,
            server_identity="serena identity",
            proxy_identity="proxy identity",
        )

    stats = release_lease_and_shutdown_if_empty(scope, "exiting")

    assert stats.sessions_before == 1
    assert stats.sessions_closed == 1
    assert stats.sessions_remaining == 0
    assert stats.server_stopped is True
    assert stats.server_was_running is True
    assert terminated == [222, 12345]
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
            watchdog_identity="watchdog identity",
        )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog.pid_is_alive", lambda pid: True)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog.process_identity",
        lambda pid: "watchdog identity",
    )
    calls = []
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog.subprocess.Popen", lambda *a, **k: calls.append(a))

    ensure_watchdog(scope)

    assert calls == []


def test_ensure_watchdog_spawns_when_recorded_pid_identity_mismatches(monkeypatch, tmp_path):
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
            watchdog_identity="old watchdog identity",
        )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog.pid_is_alive", lambda pid: True)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog.process_identity",
        lambda pid: "new unrelated identity",
    )
    calls = []

    class Proc:
        pid = 4321

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog.subprocess.Popen",
        lambda *a, **k: calls.append((a, k)) or Proc(),
    )

    ensure_watchdog(scope)

    assert calls
    with locked_registry(scope) as registry:
        assert registry.record is not None
        assert registry.record.watchdog_pid == 4321
        assert registry.record.watchdog_identity == "new unrelated identity"


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
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.watchdog.process_identity",
        lambda pid: "watchdog identity",
    )

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
        assert registry.record.watchdog_identity == "watchdog identity"
