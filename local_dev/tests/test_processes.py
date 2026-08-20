"""Contracts for shared-context Serena process discovery and diagnostics."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_dev.serena_mcp_management.serena_mcp import diagnostics, processes
from local_dev.serena_mcp_management.serena_mcp.health import process_identity
from local_dev.serena_mcp_management.serena_mcp.paths import Scope, shared_context_path
from local_dev.serena_mcp_management.serena_mcp.processes import SerenaMcpProcess
from local_dev.serena_mcp_management.serena_mcp.registry import Lease, ServerRecord


class ProcessScopeTests(unittest.TestCase):
    def test_current_process_has_stable_runtime_identity(self) -> None:
        """The current runtime must expose one immutable nonempty identity."""

        first = process_identity(os.getpid())
        second = process_identity(os.getpid())

        self.assertIsNotNone(first)
        self.assertEqual(first, second)

    @unittest.skipUnless(sys.platform == "darwin", "Darwin libproc identity only")
    def test_darwin_identity_has_microsecond_start_precision_and_survives_reexec(self) -> None:
        """Darwin identity cannot fall back to seconds-only ps output."""

        identity = process_identity(os.getpid())
        self.assertRegex(identity or "", r"^darwin:\d+:\d{1,6}$")

        script = (
            "import os,sys; "
            "from local_dev.serena_mcp_management.serena_mcp.health import process_identity; "
            "value=process_identity(os.getpid()); print(value, flush=True); "
            "env=os.environ.copy(); marker=env.get('DOTSYNC_REEXEC_ID'); "
            "env['DOTSYNC_REEXEC_ID']=value or ''; "
            "(print(marker, flush=True) if marker is not None else "
            "os.execve(sys.executable,[sys.executable,'-c',sys.argv[1],sys.argv[1]],env))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, script],
            check=True,
            capture_output=True,
            text=True,
        )
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(set(lines), {lines[0]})
        self.assertRegex(lines[0], r"^darwin:\d+:\d{1,6}$")

    def test_process_matches_only_canonical_project_and_resolved_bundled_context(self) -> None:
        """Legacy client contexts cannot be cleaned up as shared servers."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            shared = SerenaMcpProcess(
                pid=1001,
                project_root=root.resolve(),
                context=str(shared_context_path().parent / ".." / "contexts" / "oaicompat-agent.yml"),
                command="serena start-mcp-server",
            )
            legacy_codex = SerenaMcpProcess(
                pid=1002,
                project_root=root.resolve(),
                context="codex",
                command="serena start-mcp-server",
            )
            legacy_claude = SerenaMcpProcess(
                pid=1003,
                project_root=root.resolve(),
                context="claude-code",
                command="serena start-mcp-server",
            )
            other_worktree = SerenaMcpProcess(
                pid=1004,
                project_root=(root / "other").resolve(),
                context=str(shared_context_path()),
                command="serena start-mcp-server",
            )

            self.assertTrue(processes.process_matches_scope(shared, scope))
            self.assertFalse(processes.process_matches_scope(legacy_codex, scope))
            self.assertFalse(processes.process_matches_scope(legacy_claude, scope))
            self.assertFalse(processes.process_matches_scope(other_worktree, scope))

    def test_relative_symlink_to_bundled_context_never_matches(self) -> None:
        """A relative context argument must not inherit the scanner's working directory."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            alias = Path(raw) / "shared-context.yml"
            alias.symlink_to(shared_context_path())
            scope = Scope(root)
            process = SerenaMcpProcess(
                pid=1001,
                project_root=root.resolve(),
                context=alias.name,
                command="serena start-mcp-server",
            )
            original_cwd = Path.cwd()
            os.chdir(alias.parent)
            try:
                self.assertFalse(processes.process_matches_scope(process, scope))
            finally:
                os.chdir(original_cwd)


class DiagnosticsTests(unittest.TestCase):
    def test_diagnostics_manages_only_version_two_shared_context_identity(self) -> None:
        """A legacy process remains visible but is never classified as managed."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            scope = Scope(root)
            managed = SerenaMcpProcess(
                pid=303,
                project_root=root.resolve(),
                context=str(shared_context_path()),
                command="serena start-mcp-server",
                identity="server-id",
            )
            legacy = SerenaMcpProcess(
                pid=404,
                project_root=root.resolve(),
                context="codex",
                command="serena start-mcp-server",
                identity="legacy-id",
            )
            record = _record(root)

            with (
                patch.object(diagnostics, "scan_serena_mcp_processes", return_value=[managed, legacy]),
                patch.object(diagnostics, "read_registry_record", return_value=record) as read,
            ):
                snapshot = diagnostics.snapshot_global_lifecycle(
                    now=100.0,
                    stale_after_seconds=30.0,
                )

            self.assertEqual(snapshot.ps_server_count, 2)
            self.assertEqual(snapshot.managed_server_count, 1)
            self.assertEqual(snapshot.orphan_server_count, 1)
            self.assertEqual(snapshot.lease_count, 2)
            self.assertEqual(snapshot.stale_lease_count, 1)
            self.assertEqual(read.call_args.args[0], scope)

    def test_diagnostics_requires_matching_server_identity(self) -> None:
        """A reused PID with a different identity must remain orphaned."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            process = SerenaMcpProcess(
                pid=303,
                project_root=root.resolve(),
                context=str(shared_context_path()),
                command="serena start-mcp-server",
                identity="replacement-server-id",
            )

            with (
                patch.object(diagnostics, "scan_serena_mcp_processes", return_value=[process]),
                patch.object(diagnostics, "read_registry_record", return_value=_record(root)),
            ):
                snapshot = diagnostics.snapshot_global_lifecycle(
                    now=100.0,
                    stale_after_seconds=30.0,
                )

            self.assertEqual(snapshot.ps_server_count, 1)
            self.assertEqual(snapshot.managed_server_count, 0)
            self.assertEqual(snapshot.orphan_server_count, 1)
            self.assertEqual(snapshot.lease_count, 0)

    def test_diagnostics_reports_process_scan_failure(self) -> None:
        """A failed ps boundary must be explicit rather than look like an empty scan."""

        with patch.object(
            diagnostics,
            "scan_serena_mcp_processes",
            side_effect=processes.ProcessScanError("ps failed"),
        ):
            snapshot = diagnostics.snapshot_global_lifecycle(
                now=100.0,
                stale_after_seconds=30.0,
            )

        self.assertTrue(snapshot.scan_failed)
        self.assertEqual(snapshot.ps_server_count, 0)
        self.assertEqual(snapshot.managed_server_count, 0)
        self.assertEqual(snapshot.orphan_server_count, 0)

    def test_diagnostics_does_not_manage_a_relative_symlink_context(self) -> None:
        """Diagnostics must apply the absolute-context visibility boundary."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            alias = Path(raw) / "shared-context.yml"
            alias.symlink_to(shared_context_path())
            process = SerenaMcpProcess(
                pid=303,
                project_root=root.resolve(),
                context=alias.name,
                command="serena start-mcp-server",
                identity="server-id",
            )
            original_cwd = Path.cwd()
            os.chdir(alias.parent)
            try:
                with (
                    patch.object(diagnostics, "scan_serena_mcp_processes", return_value=[process]),
                    patch.object(diagnostics, "read_registry_record") as read,
                ):
                    snapshot = diagnostics.snapshot_global_lifecycle(
                        now=100.0,
                        stale_after_seconds=30.0,
                    )
            finally:
                os.chdir(original_cwd)

            self.assertEqual(snapshot.ps_server_count, 1)
            self.assertEqual(snapshot.managed_server_count, 0)
            self.assertEqual(snapshot.orphan_server_count, 1)
            read.assert_not_called()


def _record(root: Path) -> ServerRecord:
    live_lease = Lease("codex-lease", "codex", 101, 90.0, "launcher-id")
    stale_lease = Lease("claude-lease", "claude", 102, 10.0, "launcher-id-2")
    return ServerRecord(
        server_instance_id="7a8b9c0d-1111-4222-8333-444455556666",
        server_pid=303,
        mcp_url="http://127.0.0.1:9123/mcp",
        dashboard_url="http://127.0.0.1:9123/dashboard",
        project_root=str(root.resolve()),
        context_profile="dotsync-shared-cli-v1",
        started_at=50.0,
        leases={
            live_lease.lease_id: live_lease,
            stale_lease.lease_id: stale_lease,
        },
        server_identity="server-id",
    )


if __name__ == "__main__":
    unittest.main()
