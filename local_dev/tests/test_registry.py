"""Behavioral tests for the shared worktree/profile registry."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from local_dev.serena_mcp_management.serena_mcp import paths as paths_module
from local_dev.serena_mcp_management.serena_mcp import registry as registry_module
from local_dev.serena_mcp_management.serena_mcp import server
from local_dev.serena_mcp_management.serena_mcp.paths import Scope, state_dir_for
from local_dev.serena_mcp_management.serena_mcp.registry import (
    REGISTRY_VERSION,
    Lease,
    ServerRecord,
    locked_registry,
    read_registry_record,
    refresh_existing_lease,
)


class SharedRegistryTests(unittest.TestCase):
    """The registry shares one server by worktree and context profile."""

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

    def test_scope_uses_shared_profile_for_identity_and_state_directory(self) -> None:
        """A client-specific state path must not split one worktree's server."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()

            runtime_root = Path(raw) / "runtime"
            scope = Scope(root)

            with patch.dict(
                os.environ,
                {"SERENA_AGENT_RUNTIME_ROOT": str(runtime_root)},
                clear=False,
            ):
                self.assertEqual(scope.key, Scope(root).key)
                self.assertEqual(state_dir_for(scope).parent.name, "dotsync-shared-cli-v1")

    def test_repository_symlinks_and_predictable_registry_temp_are_never_followed(self) -> None:
        """Repository-controlled v1/v2 state and predictable temp names stay untouched."""

        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw)
            root = temporary_root / "project"
            runtime_root = temporary_root / "runtime"
            outside = temporary_root / "outside"
            root.mkdir()
            outside.mkdir()
            sentinels = {
                name: f"outside-{name}\n".encode()
                for name in (
                    "registry.json",
                    "registry.tmp",
                    "serena-server.log",
                    "serena-proxy.log",
                )
            }
            for name, content in sentinels.items():
                (outside / name).write_bytes(content)
            old_state = (
                root
                / ".serena"
                / "dotsync-mcp"
                / "dotsync-shared-cli-v1"
            )
            old_state.parent.mkdir(parents=True)
            old_state.symlink_to(outside, target_is_directory=True)
            scope = Scope(root)
            record = _record(root, leases={})

            with patch.dict(
                os.environ,
                {"SERENA_AGENT_RUNTIME_ROOT": str(runtime_root)},
                clear=False,
            ):
                runtime_state = state_dir_for(scope)
                predictable_target = temporary_root / "predictable-target"
                predictable_target.write_text("do not replace\n")
                if runtime_state != old_state:
                    runtime_root.mkdir(mode=0o700)
                    runtime_state.parent.mkdir(mode=0o700)
                    runtime_state.mkdir(mode=0o700)
                    (runtime_state / "registry.tmp").symlink_to(predictable_target)
                with locked_registry(scope) as registry:
                    registry.record = record
                with (
                    patch.object(server, "serena_server_command", return_value=["serena"]),
                    patch.object(server.subprocess, "Popen", return_value=MagicMock(pid=101)),
                ):
                    server._start_serena_process(scope, 9123)
                    server._start_proxy_process(
                        scope,
                        9124,
                        "http://127.0.0.1:9123/mcp",
                    )

            for name, content in sentinels.items():
                self.assertEqual((outside / name).read_bytes(), content)
            self.assertEqual(predictable_target.read_text(), "do not replace\n")
            self.assertEqual(
                (runtime_state / "registry.tmp").resolve(),
                predictable_target.resolve(),
            )
            self.assertTrue((runtime_state / "registry.json").is_file())
            self.assertTrue((runtime_state / "serena-server.log").is_file())
            self.assertTrue((runtime_state / "serena-proxy.log").is_file())
            self.assertEqual(stat.S_IMODE(runtime_state.stat().st_mode), 0o700)
            for name in ("registry.json", "serena-server.log", "serena-proxy.log"):
                self.assertEqual(
                    stat.S_IMODE((runtime_state / name).stat().st_mode),
                    0o600,
                )

    def test_scope_rejects_profiles_other_than_the_launcher_owned_profile(self) -> None:
        """An alternate profile must not split state or escape the state directory."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()

            for profile in ("another-profile", "/tmp/escaped", "../escaped"):
                with self.subTest(profile=profile):
                    with self.assertRaises(ValueError):
                        Scope(root, profile)

    def test_round_trip_stores_codex_and_claude_leases_in_one_server_record(self) -> None:
        """Replacing server scope with a client field would lose shared leases."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            codex_lease = Lease("codex-lease", "codex", 101, 100.0, "codex-id")
            claude_lease = Lease("claude-lease", "claude", 202, 200.0, "claude-id")
            expected = ServerRecord(
                server_instance_id="server-1",
                server_pid=303,
                mcp_url="http://127.0.0.1:9123/mcp",
                dashboard_url="http://127.0.0.1:9123/dashboard",
                project_root=str(root.resolve()),
                context_profile="dotsync-shared-cli-v1",
                started_at=50.0,
                leases={codex_lease.lease_id: codex_lease, claude_lease.lease_id: claude_lease},
            )

            with locked_registry(scope) as registry:
                registry.record = expected

            loaded = read_registry_record(scope)
            persisted = json.loads((state_dir_for(scope) / "registry.json").read_text())

            self.assertEqual(REGISTRY_VERSION, 2)
            self.assertEqual(loaded, expected)
            self.assertEqual(set(loaded.leases), {"codex-lease", "claude-lease"})  # type: ignore[union-attr]
            self.assertEqual(loaded.server_instance_id, "server-1")  # type: ignore[union-attr]
            self.assertNotIn("client_type", persisted["record"])
            self.assertEqual(persisted["record"]["leases"]["codex-lease"]["client_type"], "codex")
            self.assertEqual(persisted["record"]["leases"]["claude-lease"]["client_type"], "claude")

    def test_shared_registry_never_migrates_or_edits_legacy_client_files(self) -> None:
        """Opening shared state must not inspect or rewrite old client registries."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            codex_path = root / ".serena" / "dotsync-mcp" / "codex" / "registry.json"
            claude_path = root / ".serena" / "dotsync-mcp" / "claude" / "registry.json"
            codex_path.parent.mkdir(parents=True)
            claude_path.parent.mkdir(parents=True)
            codex_bytes = b'{"version": 1, "record": {"server_pid": 11}}\n'
            claude_bytes = b'not valid registry json\n'
            codex_path.write_bytes(codex_bytes)
            claude_path.write_bytes(claude_bytes)
            scope = Scope(root)

            with locked_registry(scope) as registry:
                registry.record = _record(root, leases={})

            shared_registry = state_dir_for(scope) / "registry.json"
            self.assertEqual(codex_path.read_bytes(), codex_bytes)
            self.assertEqual(claude_path.read_bytes(), claude_bytes)
            self.assertTrue(shared_registry.is_file())
            self.assertNotEqual(shared_registry, codex_path)
            self.assertNotEqual(shared_registry, claude_path)

    def test_malformed_or_old_records_are_rejected_before_their_pid_is_exposed(self) -> None:
        """A malformed record cannot become a candidate for PID-based cleanup."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            registry_path = state_dir_for(scope) / "registry.json"
            registry_path.parent.mkdir(parents=True)
            invalid_payloads = (
                {"version": 1, "record": {"server_pid": 99999}},
                {
                    "version": 2,
                    "record": {
                        "server_instance_id": "server-1",
                        "server_pid": "99999",
                        "mcp_url": "http://127.0.0.1:9123/mcp",
                        "dashboard_url": "http://127.0.0.1:9123/dashboard",
                        "project_root": str(root.resolve()),
                        "context_profile": "dotsync-shared-cli-v1",
                        "started_at": 10.0,
                        "leases": {},
                    },
                },
            )

            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    registry_path.write_text(json.dumps(payload))

                    self.assertIsNone(read_registry_record(scope))

    def test_non_utf8_registry_bytes_are_rejected_before_their_pid_is_exposed(self) -> None:
        """A corrupt byte stream cannot expose a PID to later cleanup code."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            registry_path = state_dir_for(scope) / "registry.json"
            registry_path.parent.mkdir(parents=True)
            registry_path.write_bytes(b'{"version": 2, "record": "\xff"}')

            self.assertIsNone(read_registry_record(scope))

    def test_refresh_existing_lease_updates_only_matching_lease_for_same_server(self) -> None:
        """A heartbeat for another server instance must neither update nor attach a lease."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            original = Lease("codex-lease", "codex", 101, 100.0, "before")
            other = Lease("claude-lease", "claude", 202, 200.0, "other")
            missing = Lease("missing-lease", "codex", 303, 300.0, "missing")
            refreshed = Lease("codex-lease", "codex", 101, 400.0, "after")

            with locked_registry(scope) as registry:
                registry.record = _record(root, leases={original.lease_id: original, other.lease_id: other})

            with locked_registry(scope) as registry:
                self.assertFalse(
                    refresh_existing_lease(
                        registry, lease=missing, server_instance_id="server-1"
                    )
                )
                self.assertFalse(
                    refresh_existing_lease(
                        registry, lease=refreshed, server_instance_id="server-2"
                    )
                )
                self.assertTrue(
                    refresh_existing_lease(
                        registry, lease=refreshed, server_instance_id="server-1"
                    )
                )

            loaded = read_registry_record(scope)
            self.assertEqual(loaded.leases["codex-lease"], refreshed)  # type: ignore[union-attr]
            self.assertEqual(loaded.leases["claude-lease"], other)  # type: ignore[union-attr]
            self.assertNotIn("missing-lease", loaded.leases)  # type: ignore[union-attr]

    def test_precommit_payload_fsync_and_replace_failures_preserve_previous_bytes(self) -> None:
        """No pre-replace fault may expose the candidate or leave its temp file."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            previous = _record(
                root,
                leases={"old": Lease("old", "claude", 101, 1.0, "old-id")},
            )
            candidate = _record(
                root,
                leases={"new": Lease("new", "codex", 202, 2.0, "new-id")},
            )
            with locked_registry(scope) as registry:
                registry.record = previous
            path = state_dir_for(scope) / "registry.json"
            previous_bytes = path.read_bytes()

            faults = (
                (
                    "payload",
                    patch.object(
                        registry_module.json,
                        "dump",
                        side_effect=OSError("payload write failed"),
                    ),
                    "payload write failed",
                ),
                (
                    "file-fsync",
                    patch.object(
                        registry_module.os,
                        "fsync",
                        side_effect=OSError("file fsync failed"),
                    ),
                    "file fsync failed",
                ),
                (
                    "replace",
                    patch.object(
                        registry_module.os,
                        "replace",
                        side_effect=OSError("replace failed"),
                    ),
                    "replace failed",
                ),
            )
            for label, fault, message in faults:
                with self.subTest(label=label), fault:
                    with self.assertRaisesRegex(OSError, message):
                        with locked_registry(scope) as registry:
                            registry.record = candidate

                self.assertEqual(path.read_bytes(), previous_bytes)
                self.assertEqual(read_registry_record(scope), previous)
                self.assertEqual(list(path.parent.glob(".registry-*.tmp")), [])

    def test_replace_failure_does_not_close_reused_temp_descriptor_number(self) -> None:
        """Post-fdopen cleanup must not close an unrelated reused descriptor."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            previous = _record(root, leases={})
            with locked_registry(scope) as registry:
                registry.record = previous
            path = state_dir_for(scope) / "registry.json"
            previous_bytes = path.read_bytes()
            victim_path = Path(raw) / "victim"
            temp_descriptor: int | None = None
            victim_descriptor: int | None = None
            real_fdopen = registry_module.os.fdopen

            def capture_fdopen(fd: int, mode: str):
                nonlocal temp_descriptor
                temp_descriptor = fd
                return real_fdopen(fd, mode)

            def fail_replace_after_reuse(source: Path, target: Path) -> None:
                nonlocal victim_descriptor
                victim_descriptor = os.open(
                    victim_path,
                    os.O_RDWR | os.O_CREAT | os.O_TRUNC,
                    0o600,
                )
                self.assertEqual(victim_descriptor, temp_descriptor)
                raise OSError("replace failed after descriptor reuse")

            try:
                with (
                    patch.object(
                        registry_module.os,
                        "fdopen",
                        side_effect=capture_fdopen,
                    ),
                    patch.object(
                        registry_module.os,
                        "replace",
                        side_effect=fail_replace_after_reuse,
                    ),
                ):
                    with self.assertRaisesRegex(
                        OSError,
                        "replace failed after descriptor reuse",
                    ):
                        registry_module._write_record(
                            path,
                            _record(
                                root,
                                leases={
                                    "new": Lease(
                                        "new", "codex", 202, 2.0, "new-id"
                                    )
                                },
                            ),
                        )

                self.assertEqual(path.read_bytes(), previous_bytes)
                self.assertEqual(list(path.parent.glob(".registry-*.tmp")), [])
                if victim_descriptor is None:
                    self.fail("replace side effect did not open the victim")
                os.write(victim_descriptor, b"victim remains open")
                os.lseek(victim_descriptor, 0, os.SEEK_SET)
                self.assertEqual(
                    os.read(victim_descriptor, 64),
                    b"victim remains open",
                )
            finally:
                if victim_descriptor is not None:
                    try:
                        os.close(victim_descriptor)
                    except OSError:
                        pass

    def test_fdopen_failure_closes_untransferred_temp_descriptor_once(self) -> None:
        """A failed ownership transfer must close its raw descriptor exactly once."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            with locked_registry(scope) as registry:
                registry.record = _record(root, leases={})
            path = state_dir_for(scope) / "registry.json"
            temp_descriptors: list[int] = []
            close_calls: list[int] = []
            real_mkstemp = registry_module.tempfile.mkstemp
            real_close = registry_module.os.close

            def capture_mkstemp(*args, **kwargs):
                fd, raw_tmp = real_mkstemp(*args, **kwargs)
                temp_descriptors.append(fd)
                return fd, raw_tmp

            def track_close(fd: int) -> None:
                close_calls.append(fd)
                real_close(fd)

            with (
                patch.object(
                    registry_module.tempfile,
                    "mkstemp",
                    side_effect=capture_mkstemp,
                ),
                patch.object(
                    registry_module.os,
                    "fdopen",
                    side_effect=OSError("fdopen transfer failed"),
                ),
                patch.object(
                    registry_module.os,
                    "close",
                    side_effect=track_close,
                ),
            ):
                with self.assertRaisesRegex(OSError, "fdopen transfer failed"):
                    registry_module._write_record(
                        path,
                        _record(
                            root,
                            leases={
                                "new": Lease(
                                    "new", "codex", 202, 2.0, "new-id"
                                )
                            },
                        ),
                    )

            self.assertEqual(len(temp_descriptors), 1)
            self.assertEqual(close_calls, temp_descriptors)
            with self.assertRaises(OSError):
                os.fstat(temp_descriptors[0])
            self.assertEqual(list(path.parent.glob(".registry-*.tmp")), [])

    def test_temp_cleanup_failure_never_masks_payload_failure(self) -> None:
        """A best-effort temp unlink error must retain the pre-commit primary error."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            previous = _record(root, leases={})
            with locked_registry(scope) as registry:
                registry.record = previous

            with (
                patch.object(
                    registry_module.json,
                    "dump",
                    side_effect=OSError("payload write failed"),
                ),
                patch.object(
                    Path,
                    "unlink",
                    side_effect=OSError("temp cleanup failed"),
                ),
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "payload write failed",
                ) as raised:
                    with locked_registry(scope) as registry:
                        registry.record = _record(
                            root,
                            leases={
                                "new": Lease(
                                    "new", "codex", 202, 2.0, "new-id"
                                )
                            },
                        )

            self.assertEqual(read_registry_record(scope), previous)
            self.assertTrue(
                any(
                    "registry temp unlink cleanup failed: temp cleanup failed"
                    in note
                    for note in getattr(raised.exception, "__notes__", ())
                )
            )
            for temporary_path in state_dir_for(scope).glob(".registry-*.tmp"):
                temporary_path.unlink()
            self.assertEqual(
                list(state_dir_for(scope).glob(".registry-*.tmp")),
                [],
            )

    def test_target_unlink_failure_is_precommit_and_preserves_previous_record(self) -> None:
        """Clearing a record is not committed until its target unlink succeeds."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            previous = _record(root, leases={})
            with locked_registry(scope) as registry:
                registry.record = previous

            with patch.object(
                Path,
                "unlink",
                side_effect=OSError("target unlink failed"),
            ):
                with self.assertRaisesRegex(OSError, "target unlink failed"):
                    with locked_registry(scope) as registry:
                        registry.record = None

            self.assertEqual(read_registry_record(scope), previous)

    def test_lock_cleanup_failures_never_mask_precommit_replace_failure(self) -> None:
        """Unlock and close cleanup faults retain the original commit failure."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            previous = _record(root, leases={})
            with locked_registry(scope) as registry:
                registry.record = previous
            real_open = registry_module._open_secure_runtime_file
            real_flock = registry_module.fcntl.flock

            def fail_lock_close(path: Path, flags: int):
                handle = real_open(path, flags)
                if path.name == "registry.lock":
                    return _CloseFailureHandle(handle)
                return handle

            def fail_unlock(fd: int, operation: int) -> None:
                if operation == registry_module.fcntl.LOCK_UN:
                    raise OSError("unlock failed")
                real_flock(fd, operation)

            with (
                patch.object(
                    registry_module,
                    "_open_secure_runtime_file",
                    side_effect=fail_lock_close,
                ),
                patch.object(
                    registry_module.fcntl,
                    "flock",
                    side_effect=fail_unlock,
                ),
                patch.object(
                    registry_module.os,
                    "replace",
                    side_effect=OSError("replace failed"),
                ),
            ):
                with self.assertRaisesRegex(OSError, "replace failed") as raised:
                    with locked_registry(scope) as registry:
                        registry.record = _record(
                            root,
                            leases={
                                "new": Lease(
                                    "new", "codex", 202, 2.0, "new-id"
                                )
                            },
                        )

            self.assertEqual(read_registry_record(scope), previous)
            notes = getattr(raised.exception, "__notes__", ())
            self.assertTrue(any("registry unlock cleanup failed" in note for note in notes))
            self.assertTrue(
                any("registry lock close cleanup failed" in note for note in notes)
            )

    def test_directory_fsync_failure_after_target_unlink_is_committed(self) -> None:
        """A removed ephemeral registry stays a successful clear after unlink."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            with locked_registry(scope) as registry:
                registry.record = _record(root, leases={})

            with patch.object(
                registry_module,
                "_fsync_directory",
                side_effect=OSError("directory fsync failed"),
            ):
                with locked_registry(scope) as registry:
                    registry.record = None

            self.assertIsNone(read_registry_record(scope))

    def test_read_does_not_create_missing_runtime_state(self) -> None:
        """A read-only lookup cannot materialize any runtime directory."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            runtime_root = Path(os.environ["SERENA_AGENT_RUNTIME_ROOT"])

            self.assertIsNone(read_registry_record(scope))
            self.assertFalse(runtime_root.exists())

    def test_read_rejects_symlink_swapped_runtime_root(self) -> None:
        """A swapped intermediate root cannot expose otherwise valid registry bytes."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            expected = _record(root, leases={})
            with locked_registry(scope) as registry:
                registry.record = expected
            runtime_root = Path(os.environ["SERENA_AGENT_RUNTIME_ROOT"])
            relocated = runtime_root.with_name("relocated-runtime")
            runtime_root.rename(relocated)
            runtime_root.symlink_to(relocated, target_is_directory=True)

            self.assertIsNone(read_registry_record(scope))
            self.assertTrue(runtime_root.is_symlink())

    def test_read_rejects_nonprivate_runtime_component(self) -> None:
        """Every launcher-owned runtime component must remain mode 0700 on read."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            with locked_registry(scope) as registry:
                registry.record = _record(root, leases={})
            profile_dir = state_dir_for(scope).parent
            profile_dir.chmod(0o755)

            self.assertIsNone(read_registry_record(scope))
            self.assertEqual(stat.S_IMODE(profile_dir.stat().st_mode), 0o755)

    def test_read_rejects_runtime_components_not_owned_by_current_user(self) -> None:
        """Read validation must fail closed when ownership cannot be established."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            with locked_registry(scope) as registry:
                registry.record = _record(root, leases={})

            with patch.object(
                paths_module.os,
                "geteuid",
                return_value=os.geteuid() + 1,
            ):
                self.assertIsNone(read_registry_record(scope))


def _record(root: Path, *, leases: dict[str, Lease]) -> ServerRecord:
    return ServerRecord(
        server_instance_id="server-1",
        server_pid=303,
        mcp_url="http://127.0.0.1:9123/mcp",
        dashboard_url="http://127.0.0.1:9123/dashboard",
        project_root=str(root.resolve()),
        context_profile="dotsync-shared-cli-v1",
        started_at=50.0,
        leases=leases,
    )


class _CloseFailureHandle:
    def __init__(self, handle) -> None:
        self._handle = handle

    def fileno(self) -> int:
        return self._handle.fileno()

    def close(self) -> None:
        self._handle.close()
        raise OSError("lock close failed")


if __name__ == "__main__":
    unittest.main()
