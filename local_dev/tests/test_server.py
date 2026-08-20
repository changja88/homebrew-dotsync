"""Contracts for the shared Serena server lifecycle."""
from __future__ import annotations

import os
import select
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from local_dev.serena_mcp_management.serena_mcp import processes as processes_module
from local_dev.serena_mcp_management.serena_mcp.paths import Scope, shared_context_path
from local_dev.serena_mcp_management.serena_mcp.registry import (
    Lease,
    ServerRecord,
    locked_registry,
    read_registry_record,
)
from local_dev.serena_mcp_management.serena_mcp import registry as registry_module
from local_dev.serena_mcp_management.serena_mcp import server, watchdog
from local_dev.serena_mcp_management.serena_mcp.health import process_identity
from local_dev.serena_mcp_management.serena_mcp.paths import state_dir_for


class _RuntimeRootTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._runtime_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._runtime_directory.cleanup)
        runtime_root = str(Path(self._runtime_directory.name).resolve() / "runtime")
        environment = patch.dict(
            os.environ,
            {"SERENA_AGENT_RUNTIME_ROOT": runtime_root},
            clear=False,
        )
        environment.start()
        self.addCleanup(environment.stop)


class ServerCommandTests(_RuntimeRootTestCase):
    def test_host_port_lock_lives_in_private_runtime_root(self) -> None:
        """The global port allocator lock must not use a predictable /tmp path."""

        runtime_root = Path(os.environ["SERENA_AGENT_RUNTIME_ROOT"])
        with patch.object(server, "_find_free_port", return_value=9123):
            self.assertEqual(server._find_free_port_with_host_lock(), 9123)

        lock_path = runtime_root / "host-ports.lock"
        self.assertTrue(lock_path.is_file())
        self.assertEqual(lock_path.stat().st_mode & 0o777, 0o600)

    def test_start_serena_process_uses_canonical_worktree_and_bundled_shared_context(self) -> None:
        """The shared server command must never select a client-specific context."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            process = MagicMock(pid=1234)

            with (
                patch.object(server, "serena_server_command", return_value=["serena"]),
                patch.object(server.subprocess, "Popen", return_value=process) as popen,
            ):
                server._start_serena_process(scope, 9123)

            argv = popen.call_args.args[0]
            self.assertEqual(argv[argv.index("--project") + 1], str(root.resolve()))
            self.assertEqual(argv[argv.index("--context") + 1], str(shared_context_path()))
            self.assertEqual(argv[argv.index("--transport") + 1], "streamable-http")
            self.assertNotIn("codex", argv)
            self.assertNotIn("claude-code", argv)


class ServerReuseTests(_RuntimeRootTestCase):
    def test_healthy_server_reuses_one_record_for_codex_and_claude_leases(self) -> None:
        """A client label adds a lease; it must not start a second server."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            codex = Lease("codex-lease", "codex", 101, 1.0, "codex-id")
            existing = _record(root, leases={codex.lease_id: codex})
            with locked_registry(scope) as registry:
                registry.record = existing

            claude = Lease("claude-lease", "claude", 202, 2.0, "claude-id")
            with (
                patch.object(server, "server_is_healthy", return_value=True),
                patch.object(server, "_start_healthy_server") as start,
                patch.object(server, "ensure_watchdog"),
            ):
                reused = server.ensure_server(scope, claude)

            start.assert_not_called()
            self.assertEqual(reused.server_instance_id, existing.server_instance_id)
            self.assertEqual(reused.mcp_url, existing.mcp_url)
            self.assertEqual(reused.server_pid, existing.server_pid)
            self.assertEqual(reused.proxy_pid, existing.proxy_pid)
            self.assertEqual(set(reused.leases), {"codex-lease", "claude-lease"})
            self.assertEqual(reused.leases["claude-lease"].client_type, "claude")

    def test_prior_shared_context_process_without_private_record_is_not_terminated(self) -> None:
        """An argv match alone cannot authorize killing a draining prior generation."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            lease = Lease("codex-lease", "codex", 101, 1.0, "codex-id")
            discovered = subprocess.CompletedProcess(
                args=["ps"],
                returncode=0,
                stdout=(
                    f"777 serena start-mcp-server --project {root.resolve()} "
                    f"--context {shared_context_path()}\n"
                ),
                stderr="",
            )
            replacement = _record(root, leases={lease.lease_id: lease})

            with (
                patch.object(
                    processes_module.subprocess,
                    "run",
                    return_value=discovered,
                ),
                patch.object(
                    processes_module,
                    "process_identity",
                    return_value="prior-id",
                ),
                patch.object(server, "_terminate_pid") as terminate,
                patch.object(server, "_start_healthy_server", return_value=replacement),
                patch.object(server, "ensure_watchdog"),
            ):
                server.ensure_server(scope, lease)

            terminate.assert_not_called()

    def test_healthy_private_generation_does_not_sweep_second_unowned_process(self) -> None:
        """The private record authorizes only its own PID plus identity."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            existing_lease = Lease("claude-lease", "claude", 101, 1.0, "old-id")
            existing = _record(
                root,
                leases={existing_lease.lease_id: existing_lease},
            )
            with locked_registry(scope) as registry:
                registry.record = existing
            joining = Lease("codex-lease", "codex", 202, 2.0, "new-id")
            discovered = subprocess.CompletedProcess(
                args=["ps"],
                returncode=0,
                stdout=(
                    f"778 serena start-mcp-server --project {root.resolve()} "
                    f"--context {shared_context_path()}\n"
                ),
                stderr="",
            )

            with (
                patch.object(server, "server_is_healthy", return_value=True),
                patch.object(
                    processes_module.subprocess,
                    "run",
                    return_value=discovered,
                ),
                patch.object(
                    processes_module,
                    "process_identity",
                    return_value="unowned-id",
                ),
                patch.object(server, "_terminate_pid") as terminate,
                patch.object(server, "ensure_watchdog"),
            ):
                server.ensure_server(scope, joining)

            terminate.assert_not_called()

    def test_new_server_assigns_a_uuid_instance_id(self) -> None:
        """Every replacement server needs a fresh generation identifier."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            lease = Lease("codex-lease", "codex", 101, 1.0, "codex-id")
            serena_process = MagicMock(pid=1001)
            serena_process.poll.return_value = None
            proxy_process = MagicMock(pid=1002)
            proxy_process.poll.return_value = None

            with (
                patch.object(server, "_find_free_port_with_host_lock", side_effect=[9001, 9002]),
                patch.object(server, "_start_serena_process", return_value=serena_process),
                patch.object(server, "_discover_dashboard_url", return_value="http://127.0.0.1:9001/dashboard"),
                patch.object(server, "_start_proxy_process", return_value=proxy_process),
                patch.object(server, "process_identity", side_effect=["server-id", "proxy-id"]),
                patch.object(server, "_wait_until_healthy"),
            ):
                record = server._start_healthy_server(scope, lease)

            self.assertEqual(record.context_profile, scope.context_profile)
            self.assertEqual(record.project_root, str(scope.project_root))
            self.assertEqual(record.leases[lease.lease_id], lease)
            self.assertEqual(str(uuid.UUID(record.server_instance_id)), record.server_instance_id)

    def test_failed_start_with_no_process_identity_never_attempts_termination(self) -> None:
        """Identity capture failure must reap every directly owned Serena child."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            lease = Lease("codex-lease", "codex", 101, 1.0, "codex-id")
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    start_new_session=True,
                )
                for _index in range(3)
            ]
            for process in processes:
                self.addCleanup(_stop_owned_process, process)

            with (
                patch.object(server, "_find_free_port_with_host_lock", return_value=9001),
                patch.object(server, "_start_serena_process", side_effect=processes),
                patch.object(server, "process_identity", return_value=None),
                patch.object(server, "_discover_dashboard_url") as discover,
                patch.object(
                    server,
                    "_start_proxy_process",
                    side_effect=RuntimeError("proxy startup must not be reached"),
                ),
                patch.object(server, "IDENTITY_CAPTURE_TIMEOUT_SECONDS", 0.01, create=True),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to start healthy"):
                    server._start_healthy_server(scope, lease)

            discover.assert_not_called()
            self.assertTrue(all(process.poll() is not None for process in processes))

    def test_proxy_identity_capture_failure_reaps_proxy_and_serena_handles(self) -> None:
        """A proxy retry cannot abandon either directly owned process handle."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            lease = Lease("codex-lease", "codex", 101, 1.0, "codex-id")
            serena_processes = [
                subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    start_new_session=True,
                )
                for _index in range(3)
            ]
            proxy_processes = [
                subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    start_new_session=True,
                )
                for _index in range(3)
            ]
            for process in (*serena_processes, *proxy_processes):
                self.addCleanup(_stop_owned_process, process)
            serena_pids = {process.pid for process in serena_processes}

            with (
                patch.object(
                    server,
                    "_find_free_port_with_host_lock",
                    side_effect=[9001, 9002, 9003, 9004, 9005, 9006],
                ),
                patch.object(
                    server,
                    "_start_serena_process",
                    side_effect=serena_processes,
                ),
                patch.object(server, "_discover_dashboard_url", return_value="http://127.0.0.1:9999/dashboard"),
                patch.object(
                    server,
                    "_start_proxy_process",
                    side_effect=proxy_processes,
                ),
                patch.object(
                    server,
                    "process_identity",
                    side_effect=lambda pid: "server-id" if pid in serena_pids else None,
                ),
                patch.object(
                    server,
                    "_wait_until_healthy",
                    side_effect=RuntimeError("proxy identity missing"),
                ),
                patch.object(server, "IDENTITY_CAPTURE_TIMEOUT_SECONDS", 0.01, create=True),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to start healthy"):
                    server._start_healthy_server(scope, lease)

            self.assertTrue(
                all(
                    process.poll() is not None
                    for process in (*serena_processes, *proxy_processes)
                )
            )

    def test_watchdog_failure_after_new_server_acquisition_releases_its_only_lease(self) -> None:
        """A launcher cannot clean up a record it never received after watchdog failure."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            lease = Lease("codex-lease", "codex", 101, 1.0, "codex-id")
            started = _record(root, leases={lease.lease_id: lease})

            with (
                patch.object(server, "_start_healthy_server", return_value=started),
                patch.object(server, "ensure_watchdog", side_effect=RuntimeError("watchdog failed")),
                patch.object(watchdog, "_terminate_record") as terminate,
            ):
                with self.assertRaisesRegex(RuntimeError, "watchdog failed"):
                    server.ensure_server(scope, lease)

            terminate.assert_called_once()
            self.assertIsNone(read_registry_record(scope))

    def test_watchdog_failure_reaps_new_server_and_proxy_direct_children(self) -> None:
        """Generation rollback must not abandon the new server/proxy Popen handles."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            lease = Lease("codex-lease", "codex", 101, 1.0, "codex-id")
            server_process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            proxy_process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            self.addCleanup(_stop_owned_process, server_process)
            self.addCleanup(_stop_owned_process, proxy_process)
            record = _record(root, leases={lease.lease_id: lease})
            record.server_pid = server_process.pid
            record.proxy_pid = proxy_process.pid
            record.server_identity = process_identity(server_process.pid)
            record.proxy_identity = process_identity(proxy_process.pid)
            started = server._StartedServer(record, server_process, proxy_process)

            with (
                patch.object(server, "_start_healthy_server", return_value=started),
                patch.object(
                    server,
                    "ensure_watchdog",
                    side_effect=RuntimeError("watchdog failed"),
                ),
                patch.object(
                    server,
                    "release_lease_and_shutdown_if_empty",
                ) as rollback,
            ):
                with self.assertRaisesRegex(RuntimeError, "watchdog failed"):
                    server.ensure_server(scope, lease)

            rollback.assert_called_once()
            self.assertIsNotNone(server_process.poll())
            self.assertIsNotNone(proxy_process.poll())

    def test_watchdog_failure_after_reusing_server_removes_only_new_lease(self) -> None:
        """Watchdog rollback must not stop a shared server with an older live lease."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            existing_lease = Lease("claude-lease", "claude", 202, 1.0, "claude-id")
            existing = _record(root, leases={existing_lease.lease_id: existing_lease})
            with locked_registry(scope) as registry:
                registry.record = existing
            joining_lease = Lease("codex-lease", "codex", 101, 2.0, "codex-id")

            with (
                patch.object(server, "server_is_healthy", return_value=True),
                patch.object(server, "ensure_watchdog", side_effect=RuntimeError("watchdog failed")),
                patch.object(watchdog, "_terminate_record") as terminate,
            ):
                with self.assertRaisesRegex(RuntimeError, "watchdog failed"):
                    server.ensure_server(scope, joining_lease)

            terminate.assert_not_called()
            persisted = read_registry_record(scope)
            self.assertEqual(set(persisted.leases), {"claude-lease"})  # type: ignore[union-attr]

    def test_watchdog_failure_remains_the_raised_error_when_rollback_also_fails(self) -> None:
        """A cleanup error must not hide the watchdog startup failure that caused it."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            lease = Lease("codex-lease", "codex", 101, 1.0, "codex-id")
            started = _record(root, leases={lease.lease_id: lease})

            with (
                patch.object(server, "_start_healthy_server", return_value=started),
                patch.object(server, "ensure_watchdog", side_effect=RuntimeError("watchdog failed")),
                patch.object(
                    server,
                    "release_lease_and_shutdown_if_empty",
                    side_effect=RuntimeError("rollback failed"),
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "watchdog failed",
                ) as raised:
                    server.ensure_server(scope, lease)

            self.assertTrue(
                any(
                    "lease rollback failed: rollback failed" in note
                    for note in getattr(raised.exception, "__notes__", ())
                )
            )

    def test_new_owned_processes_are_reaped_when_registry_temp_write_fails(self) -> None:
        """A temp-write error cannot leave a new unregistered server generation alive."""

        self._assert_new_processes_reaped_after_persistence_failure("write")

    def test_new_owned_processes_are_reaped_when_registry_replace_fails(self) -> None:
        """A replace error cannot leave a new unregistered server generation alive."""

        self._assert_new_processes_reaped_after_persistence_failure("replace")

    def test_new_owned_processes_are_reaped_when_registry_file_fsync_fails(self) -> None:
        """A pre-commit fsync error cannot leave an unregistered generation alive."""

        self._assert_new_processes_reaped_after_persistence_failure("fsync")

    def test_new_generation_remains_owned_after_post_replace_directory_fsync_failure(self) -> None:
        """A visible registry record makes later directory durability best-effort."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            lease = Lease("codex-lease", "codex", 101, 1.0, "codex-id")
            record = _record(root, leases={lease.lease_id: lease})
            started = server._StartedServer(
                record,
                MagicMock(pid=record.server_pid),
                MagicMock(pid=record.proxy_pid),
            )

            with (
                patch.object(server, "_start_healthy_server", return_value=started),
                patch.object(server, "_stop_and_reap_started_server") as stop,
                patch.object(server, "ensure_watchdog"),
                patch.object(
                    registry_module,
                    "_fsync_directory",
                    side_effect=OSError("directory fsync failed"),
                ),
            ):
                acquired = server.ensure_server(scope, lease)

            stop.assert_not_called()
            self.assertEqual(acquired.server_instance_id, record.server_instance_id)
            self.assertEqual(
                read_registry_record(scope).server_instance_id,  # type: ignore[union-attr]
                record.server_instance_id,
            )

    def test_registry_failure_remains_primary_when_owned_cleanup_also_fails(self) -> None:
        """Direct-child cleanup errors cannot hide the failed durable commit."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            lease = Lease("codex-lease", "codex", 101, 1.0, "codex-id")
            server_process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            proxy_process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            self.addCleanup(_stop_owned_process, server_process)
            self.addCleanup(_stop_owned_process, proxy_process)
            record = _record(root, leases={lease.lease_id: lease})
            record.server_pid = server_process.pid
            record.proxy_pid = proxy_process.pid
            record.server_identity = process_identity(server_process.pid)
            record.proxy_identity = process_identity(proxy_process.pid)
            started = server._StartedServer(record, server_process, proxy_process)
            stop_and_reap = server._stop_and_reap_started_server

            def reap_then_fail(candidate: server._StartedServer) -> None:
                stop_and_reap(candidate)
                raise RuntimeError("cleanup failed")

            with (
                patch.object(server, "_start_healthy_server", return_value=started),
                patch.object(server, "_stop_and_reap_started_server", side_effect=reap_then_fail),
                patch.object(
                    registry_module.os,
                    "replace",
                    side_effect=OSError("replace failed"),
                ),
            ):
                with self.assertRaisesRegex(OSError, "replace failed") as raised:
                    server.ensure_server(scope, lease)

            self.assertIsNotNone(server_process.poll())
            self.assertIsNotNone(proxy_process.poll())
            self.assertIsNone(read_registry_record(scope))
            self.assertTrue(
                any(
                    "owned server cleanup failed: cleanup failed" in note
                    for note in getattr(raised.exception, "__notes__", ())
                )
            )

    def test_reused_server_is_not_terminated_when_new_lease_persistence_fails(self) -> None:
        """A failed joining lease must not terminate the already durable generation."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            existing_lease = Lease("claude-lease", "claude", 202, 1.0, "claude-id")
            existing = _record(root, leases={existing_lease.lease_id: existing_lease})
            with locked_registry(scope) as registry:
                registry.record = existing
            joining_lease = Lease("codex-lease", "codex", 101, 2.0, "codex-id")

            with (
                patch.object(server, "server_is_healthy", return_value=True),
                patch.object(server, "_terminate_record") as terminate,
                patch.object(server, "ensure_watchdog") as ensure_watchdog,
                patch.object(
                    registry_module.os,
                    "replace",
                    side_effect=OSError("replace failed"),
                ),
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    server.ensure_server(scope, joining_lease)

            terminate.assert_not_called()
            ensure_watchdog.assert_not_called()
            persisted = read_registry_record(scope)
            self.assertEqual(set(persisted.leases), {"claude-lease"})  # type: ignore[union-attr]
            self.assertEqual(list(state_dir_for(scope).glob(".registry-*.tmp")), [])

    def test_reused_lease_is_committed_despite_post_commit_unlock_failure(self) -> None:
        """A lock cleanup error cannot report failure after the lease is visible."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            existing_lease = Lease("claude-lease", "claude", 202, 1.0, "old-id")
            existing = _record(
                root,
                leases={existing_lease.lease_id: existing_lease},
            )
            with locked_registry(scope) as registry:
                registry.record = existing
            joining = Lease("codex-lease", "codex", 101, 2.0, "new-id")
            real_flock = registry_module.fcntl.flock

            def fail_unlock(fd: int, operation: int) -> None:
                if operation == registry_module.fcntl.LOCK_UN:
                    raise OSError("unlock failed")
                real_flock(fd, operation)

            with (
                patch.object(server, "server_is_healthy", return_value=True),
                patch.object(server, "ensure_watchdog"),
                patch.object(registry_module.fcntl, "flock", side_effect=fail_unlock),
            ):
                acquired = server.ensure_server(scope, joining)

            self.assertEqual(set(acquired.leases), {"claude-lease", "codex-lease"})
            self.assertEqual(
                set(read_registry_record(scope).leases),  # type: ignore[union-attr]
                {"claude-lease", "codex-lease"},
            )

    def _assert_new_processes_reaped_after_persistence_failure(
        self, failure: str
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            lease = Lease("codex-lease", "codex", 101, 1.0, "codex-id")
            server_process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            proxy_process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            self.addCleanup(_stop_owned_process, server_process)
            self.addCleanup(_stop_owned_process, proxy_process)
            record = _record(root, leases={lease.lease_id: lease})
            record.server_pid = server_process.pid
            record.proxy_pid = proxy_process.pid
            record.server_identity = process_identity(server_process.pid)
            record.proxy_identity = process_identity(proxy_process.pid)
            started = server._StartedServer(record, server_process, proxy_process)
            if failure == "write":
                persistence_patch = patch.object(
                    registry_module.json,
                    "dump",
                    side_effect=OSError("temp write failed"),
                )
                message = "temp write failed"
            elif failure == "fsync":
                persistence_patch = patch.object(
                    registry_module.os,
                    "fsync",
                    side_effect=OSError("file fsync failed"),
                )
                message = "file fsync failed"
            else:
                persistence_patch = patch.object(
                    registry_module.os,
                    "replace",
                    side_effect=OSError("replace failed"),
                )
                message = "replace failed"

            with (
                patch.object(server, "_start_healthy_server", return_value=started),
                patch.object(server, "ensure_watchdog") as ensure_watchdog,
                persistence_patch,
            ):
                with self.assertRaisesRegex(OSError, message):
                    server.ensure_server(scope, lease)

            ensure_watchdog.assert_not_called()
            self.assertIsNotNone(server_process.poll())
            self.assertIsNotNone(proxy_process.poll())
            self.assertIsNone(read_registry_record(scope))
            self.assertEqual(list(state_dir_for(scope).glob(".registry-*.tmp")), [])


class WatchdogCommandTests(_RuntimeRootTestCase):
    def test_watchdog_uses_scope_profile_not_a_client_type(self) -> None:
        """The watchdog follows shared server identity rather than a client label."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            record = _record(root, leases={})
            process = MagicMock(pid=303)
            with locked_registry(scope) as registry:
                registry.record = record

            with (
                patch.object(watchdog.subprocess, "Popen", return_value=process) as popen,
                patch.object(
                    watchdog,
                    "_wait_for_watchdog_readiness",
                    return_value="watchdog-id",
                ),
            ):
                watchdog.ensure_watchdog(scope)

            argv = popen.call_args.args[0]
            self.assertEqual(argv[3:5], [str(scope.project_root), scope.context_profile])
            self.assertIn("--ready-fd", argv)
            self.assertEqual(
                popen.call_args.kwargs["pass_fds"],
                (int(argv[argv.index("--ready-fd") + 1]),),
            )
            self.assertNotIn("codex", argv)
            self.assertNotIn("claude", argv)

    def test_watchdog_cli_emits_readiness_after_argument_parsing(self) -> None:
        """The inherited readiness pipe proves the watchdog CLI reached startup."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            read_fd, write_fd = os.pipe()
            environment = os.environ.copy()
            environment["PYTHONPATH"] = watchdog._pythonpath_with_repo_root(
                environment.get("PYTHONPATH")
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "local_dev.serena_mcp_management.serena_mcp.watchdog",
                    str(scope.project_root),
                    scope.context_profile,
                    "--ready-fd",
                    str(write_fd),
                ],
                cwd=str(watchdog._REPO_ROOT),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                pass_fds=(write_fd,),
                start_new_session=True,
            )
            self.addCleanup(_stop_owned_process, process)
            os.close(write_fd)
            try:
                readable, _, _ = select.select([read_fd], [], [], 5.0)
                self.assertEqual(readable, [read_fd])
                self.assertEqual(os.read(read_fd, 1), b"R")
                process.communicate(timeout=5.0)
            finally:
                os.close(read_fd)

            self.assertEqual(process.returncode, 0)

    def test_watchdog_readiness_failure_reaps_owned_child_without_recording_it(self) -> None:
        """A child that never completes startup cannot become an untracked watchdog."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            record = _record(root, leases={})
            with locked_registry(scope) as registry:
                registry.record = record
            process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            self.addCleanup(_stop_owned_process, process)

            with (
                patch.object(watchdog.subprocess, "Popen", return_value=process),
                patch.object(
                    watchdog,
                    "_wait_for_watchdog_readiness",
                    side_effect=RuntimeError("watchdog did not become ready"),
                    create=True,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                    watchdog.ensure_watchdog(scope)

            self.assertIsNotNone(process.poll())
            persisted = read_registry_record(scope)
            self.assertIsNone(persisted.watchdog_pid)  # type: ignore[union-attr]
            self.assertIsNone(persisted.watchdog_identity)  # type: ignore[union-attr]

    def test_watchdog_persistence_failure_reaps_owned_ready_child(self) -> None:
        """A ready watchdog is still owned until its identity is durably recorded."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            record = _record(root, leases={})
            with locked_registry(scope) as registry:
                registry.record = record
            process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            self.addCleanup(_stop_owned_process, process)
            identity = process_identity(process.pid)

            with (
                patch.object(watchdog.subprocess, "Popen", return_value=process),
                patch.object(
                    watchdog,
                    "_wait_for_watchdog_readiness",
                    return_value=identity,
                    create=True,
                ),
                patch.object(
                    registry_module.os,
                    "replace",
                    side_effect=OSError("watchdog persist failed"),
                ),
            ):
                with self.assertRaisesRegex(OSError, "watchdog persist failed"):
                    watchdog.ensure_watchdog(scope)

            self.assertIsNotNone(process.poll())
            persisted = read_registry_record(scope)
            self.assertIsNone(persisted.watchdog_pid)  # type: ignore[union-attr]
            self.assertIsNone(persisted.watchdog_identity)  # type: ignore[union-attr]

    def test_watchdog_cleanup_failure_never_masks_persistence_failure(self) -> None:
        """Owned-child cleanup context is attached without replacing commit failure."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            with locked_registry(scope) as registry:
                registry.record = _record(root, leases={})
            process = MagicMock(pid=505)

            with (
                patch.object(watchdog.subprocess, "Popen", return_value=process),
                patch.object(
                    watchdog,
                    "_wait_for_watchdog_readiness",
                    return_value="watchdog-id",
                ),
                patch.object(
                    registry_module.os,
                    "replace",
                    side_effect=OSError("watchdog persist failed"),
                ),
                patch.object(
                    watchdog,
                    "_stop_and_reap_owned_watchdog",
                    side_effect=RuntimeError("watchdog cleanup failed"),
                ),
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "watchdog persist failed",
                ) as raised:
                    watchdog.ensure_watchdog(scope)

            self.assertTrue(
                any(
                    "owned watchdog cleanup failed: watchdog cleanup failed" in note
                    for note in getattr(raised.exception, "__notes__", ())
                )
            )
            persisted = read_registry_record(scope)
            self.assertIsNone(persisted.watchdog_pid)  # type: ignore[union-attr]

    def test_watchdog_record_remains_committed_despite_lock_close_failure(self) -> None:
        """A ready persisted watchdog transfers ownership before lock close cleanup."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            record = _record(root, leases={})
            with locked_registry(scope) as registry:
                registry.record = record
            process = MagicMock(pid=505)
            real_open = registry_module._open_secure_runtime_file

            def fail_lock_close(path: Path, flags: int):
                handle = real_open(path, flags)
                if path.name == "registry.lock":
                    return _CloseFailureHandle(handle)
                return handle

            with (
                patch.object(watchdog.subprocess, "Popen", return_value=process),
                patch.object(
                    watchdog,
                    "_wait_for_watchdog_readiness",
                    return_value="watchdog-id",
                ),
                patch.object(
                    watchdog,
                    "_stop_and_reap_owned_watchdog",
                ) as stop,
                patch.object(
                    registry_module,
                    "_open_secure_runtime_file",
                    side_effect=fail_lock_close,
                ),
            ):
                watchdog.ensure_watchdog(scope)

            stop.assert_not_called()
            persisted = read_registry_record(scope)
            self.assertEqual(persisted.watchdog_pid, 505)  # type: ignore[union-attr]
            self.assertEqual(  # type: ignore[union-attr]
                persisted.watchdog_identity,
                "watchdog-id",
            )


def _record(root: Path, *, leases: dict[str, Lease]) -> ServerRecord:
    return ServerRecord(
        server_instance_id="7a8b9c0d-1111-4222-8333-444455556666",
        server_pid=303,
        mcp_url="http://127.0.0.1:9123/mcp",
        dashboard_url="http://127.0.0.1:9123/dashboard",
        project_root=str(root.resolve()),
        context_profile="dotsync-shared-cli-v1",
        started_at=50.0,
        leases=leases,
        upstream_mcp_url="http://127.0.0.1:9001/mcp",
        proxy_pid=404,
        server_identity="server-id",
        proxy_identity="proxy-id",
    )


def _stop_owned_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


class _CloseFailureHandle:
    def __init__(self, handle) -> None:
        self._handle = handle

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def fileno(self) -> int:
        return self._handle.fileno()

    def close(self) -> None:
        self._handle.close()
        raise OSError("lock close failed")


if __name__ == "__main__":
    unittest.main()
