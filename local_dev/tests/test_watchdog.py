"""Lifecycle contracts for generation-safe shared Serena leases."""
from __future__ import annotations

import os
import threading
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import call, patch

from local_dev.serena_mcp_management import serena_agent_launcher as launcher
from local_dev.serena_mcp_management.serena_mcp import server, watchdog
from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.registry import (
    Lease,
    ServerRecord,
    locked_registry,
    read_registry_record,
)
from local_dev.serena_mcp_management.serena_mcp import termination


_RUNTIME_DIRECTORY: tempfile.TemporaryDirectory[str] | None = None
_RUNTIME_ENVIRONMENT = None


def setUpModule() -> None:
    global _RUNTIME_DIRECTORY, _RUNTIME_ENVIRONMENT
    _RUNTIME_DIRECTORY = tempfile.TemporaryDirectory()
    runtime_root = str(Path(_RUNTIME_DIRECTORY.name).resolve() / "runtime")
    _RUNTIME_ENVIRONMENT = patch.dict(
        os.environ,
        {"SERENA_AGENT_RUNTIME_ROOT": runtime_root},
        clear=False,
    )
    _RUNTIME_ENVIRONMENT.start()


def tearDownModule() -> None:
    if _RUNTIME_ENVIRONMENT is not None:
        _RUNTIME_ENVIRONMENT.stop()
    if _RUNTIME_DIRECTORY is not None:
        _RUNTIME_DIRECTORY.cleanup()


class LauncherLeaseTests(unittest.TestCase):
    def test_launcher_lease_keeps_the_validated_client_label(self) -> None:
        """Accepting an unknown client would make lease diagnostics untrustworthy."""

        with patch.object(watchdog.os, "getpid", return_value=101), patch.object(
            watchdog, "process_identity", return_value="launcher-identity"
        ):
            lease = watchdog.make_launcher_lease("codex-1", "codex", now=123.0)

        self.assertEqual(lease.client_type, "codex")
        self.assertEqual(lease.launcher_pid, 101)
        self.assertEqual(lease.launcher_identity, "launcher-identity")
        self.assertEqual(lease.heartbeat_at, 123.0)
        with self.assertRaisesRegex(ValueError, "unsupported client type"):
            watchdog.make_launcher_lease("bad-1", "cursor", now=123.0)


class LeaseReleaseTests(unittest.TestCase):
    def test_three_leases_release_independently_and_only_last_stops_server(self) -> None:
        """Stopping after a non-final exit would disconnect the remaining clients."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            record = _record(
                root,
                instance_id="instance-a",
                leases={
                    "claude-1": _lease("claude-1", "claude"),
                    "claude-2": _lease("claude-2", "claude"),
                    "codex-1": _lease("codex-1", "codex"),
                },
            )
            _persist(scope, record)

            with patch.object(watchdog, "_terminate_record") as terminate:
                first = watchdog.release_lease_and_shutdown_if_empty(
                    scope, "claude-1", "instance-a"
                )
                second = watchdog.release_lease_and_shutdown_if_empty(
                    scope, "codex-1", "instance-a"
                )
                final = watchdog.release_lease_and_shutdown_if_empty(
                    scope, "claude-2", "instance-a"
                )

            self.assertEqual((first.sessions_remaining, first.server_stopped), (2, False))
            self.assertEqual((second.sessions_remaining, second.server_stopped), (1, False))
            self.assertEqual((final.sessions_remaining, final.server_stopped), (0, True))
            terminate.assert_called_once()
            self.assertIsNone(read_registry_record(scope))

    def test_release_for_another_server_instance_leaves_current_record_untouched(self) -> None:
        """An old launcher must not release or stop its replacement server."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            record = _record(
                root,
                instance_id="instance-b",
                leases={"codex-1": _lease("codex-1", "codex")},
            )
            _persist(scope, record)

            with patch.object(watchdog, "_terminate_record") as terminate:
                stats = watchdog.release_lease_and_shutdown_if_empty(
                    scope, "codex-1", "instance-a"
                )

            self.assertEqual(
                (stats.sessions_before, stats.sessions_closed, stats.sessions_remaining),
                (0, 0, 0),
            )
            self.assertFalse(stats.server_stopped)
            terminate.assert_not_called()
            self.assertEqual(read_registry_record(scope), record)


class StaleLeaseTests(unittest.TestCase):
    def test_stale_lease_with_live_matching_identity_is_refreshed(self) -> None:
        """Sleep/wake must not evict a live launcher just because its clock is stale."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            lease = Lease("codex-1", "codex", 101, 10.0, "launcher-start")
            _persist(scope, _record(root, instance_id="instance-a", leases={lease.lease_id: lease}))

            with patch.object(watchdog, "process_identity", return_value="launcher-start"), patch.object(
                watchdog, "_terminate_record"
            ) as terminate:
                keep_running = watchdog.cleanup_once(
                    scope, now=100.0, lease_timeout_seconds=30.0
                )

            refreshed = read_registry_record(scope)
            self.assertTrue(keep_running)
            self.assertEqual(refreshed.leases[lease.lease_id].heartbeat_at, 100.0)  # type: ignore[union-attr]
            terminate.assert_not_called()

    def test_stale_lease_with_mismatched_identity_is_evicted_and_terminates_expected_processes(self) -> None:
        """A reused launcher PID may be evicted, but server PIDs keep identity guards."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            stale = Lease("claude-1", "claude", 202, 10.0, "old-launcher-start")
            _persist(scope, _record(root, instance_id="instance-a", leases={stale.lease_id: stale}))

            with patch.object(watchdog, "process_identity", return_value="reused-pid-start"), patch.object(
                watchdog, "terminate_pid"
            ) as terminate:
                keep_running = watchdog.cleanup_once(
                    scope, now=100.0, lease_timeout_seconds=30.0
                )

            self.assertFalse(keep_running)
            self.assertIsNone(read_registry_record(scope))
            self.assertEqual(
                terminate.call_args_list,
                [
                    call(404, expected_identity="proxy-start"),
                    call(303, expected_identity="server-start"),
                ],
            )


class TerminationIdentityTests(unittest.TestCase):
    def test_missing_expected_identity_sends_no_signal(self) -> None:
        """Generic termination without immutable identity must fail closed."""

        with (
            patch.object(termination.os, "killpg") as killpg,
            patch.object(termination.os, "kill") as kill,
        ):
            termination.terminate_pid(303, expected_identity=None)

        killpg.assert_not_called()
        kill.assert_not_called()

    def test_mismatched_process_identity_sends_no_signal(self) -> None:
        """A reused PID must not receive even a first termination signal."""

        with (
            patch.object(termination, "process_identity", return_value="reused-start"),
            patch.object(termination.os, "killpg") as killpg,
            patch.object(termination.os, "kill") as kill,
        ):
            termination.terminate_pid(303, expected_identity="original-start")

        killpg.assert_not_called()
        kill.assert_not_called()


class HeartbeatGenerationTests(unittest.TestCase):
    def test_replaced_server_rejects_heartbeat_from_old_instance_without_adding_lease(self) -> None:
        """An old heartbeat must never attach its lease to a replacement server."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            original = _lease("codex-1", "codex")
            _persist(
                scope,
                _record(root, instance_id="instance-a", leases={original.lease_id: original}),
            )
            replacement = _record(
                root,
                instance_id="instance-b",
                leases={"claude-1": _lease("claude-1", "claude")},
            )
            _persist(scope, replacement)

            refreshed = launcher._touch_lease_if_record_exists(
                scope,
                "codex-1",
                "instance-a",
                threading.Event(),
                now=200.0,
            )

            self.assertFalse(refreshed)
            self.assertEqual(read_registry_record(scope), replacement)


class FinalReleaseAcquireRaceTests(unittest.TestCase):
    def test_new_acquire_waits_for_final_release_and_persists_a_replacement_lease(self) -> None:
        """Releasing outside the registry lock could let an acquire reuse a stopping server."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            _persist(
                scope,
                _record(
                    root,
                    instance_id="instance-a",
                    leases={"claude-1": _lease("claude-1", "claude")},
                ),
            )
            entered_termination = threading.Event()
            termination_barrier = threading.Barrier(2)
            allow_termination = threading.Event()
            acquire_attempted = threading.Event()
            acquire_entered_registry = threading.Event()
            acquire_finished = threading.Event()
            failures: list[BaseException] = []
            replacement_lease = _lease("codex-1", "codex")
            replacement = _record(
                root,
                instance_id="instance-b",
                leases={replacement_lease.lease_id: replacement_lease},
            )

            def slow_terminate(_record: ServerRecord) -> None:
                entered_termination.set()
                termination_barrier.wait(timeout=2.0)
                if not allow_termination.wait(timeout=2.0):
                    raise TimeoutError("test did not release final shutdown")

            def release() -> None:
                try:
                    watchdog.release_lease_and_shutdown_if_empty(
                        scope, "claude-1", "instance-a"
                    )
                except BaseException as exc:  # pragma: no cover - surfaced below
                    failures.append(exc)

            def acquire() -> None:
                try:
                    server.ensure_server(scope, replacement_lease)
                    acquire_finished.set()
                except BaseException as exc:  # pragma: no cover - surfaced below
                    failures.append(exc)

            original_locked_registry = server.locked_registry

            @contextmanager
            def observed_locked_registry(scope_arg: Scope):
                acquire_attempted.set()
                with original_locked_registry(scope_arg) as registry:
                    acquire_entered_registry.set()
                    yield registry

            with (
                patch.object(watchdog, "_terminate_record", side_effect=slow_terminate),
                patch.object(server, "_start_healthy_server", return_value=replacement),
                patch.object(server, "ensure_watchdog"),
                patch.object(server, "locked_registry", observed_locked_registry),
            ):
                release_thread = threading.Thread(target=release)
                release_thread.start()
                self.assertTrue(entered_termination.wait(timeout=1.0))
                termination_barrier.wait(timeout=1.0)
                acquire_thread = threading.Thread(target=acquire)
                acquire_thread.start()
                self.assertTrue(acquire_attempted.wait(timeout=1.0))
                self.assertFalse(acquire_entered_registry.is_set())
                self.assertFalse(acquire_finished.is_set())
                allow_termination.set()
                release_thread.join(timeout=2.0)
                acquire_thread.join(timeout=2.0)

            self.assertFalse(release_thread.is_alive())
            self.assertFalse(acquire_thread.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue(acquire_finished.is_set())
            persisted = read_registry_record(scope)
            self.assertEqual(persisted.server_instance_id, "instance-b")  # type: ignore[union-attr]
            self.assertEqual(persisted.leases, {"codex-1": replacement_lease})  # type: ignore[union-attr]


def _lease(lease_id: str, client_type: str) -> Lease:
    return Lease(lease_id, client_type, 101, 10.0, f"{lease_id}-start")


def _record(
    root: Path,
    *,
    instance_id: str,
    leases: dict[str, Lease],
) -> ServerRecord:
    return ServerRecord(
        server_instance_id=instance_id,
        server_pid=303,
        mcp_url="http://127.0.0.1:9123/mcp",
        dashboard_url="http://127.0.0.1:9123/dashboard",
        project_root=str(root.resolve()),
        context_profile="dotsync-shared-cli-v1",
        started_at=1.0,
        leases=leases,
        upstream_mcp_url="http://127.0.0.1:9001/mcp",
        proxy_pid=404,
        server_identity="server-start",
        proxy_identity="proxy-start",
    )


def _persist(scope: Scope, record: ServerRecord) -> None:
    with locked_registry(scope) as registry:
        registry.record = record


if __name__ == "__main__":
    unittest.main()
