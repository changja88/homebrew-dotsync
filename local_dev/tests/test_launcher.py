"""Launcher opt-in gates and bare-child fallback contracts."""
from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from local_dev.serena_mcp_management import serena_agent_launcher as launcher
from local_dev.serena_mcp_management.serena_mcp.registry import Lease
from local_dev.serena_mcp_management.serena_mcp.watchdog import make_launcher_lease


@contextmanager
def _launcher_environment(
    root: Path,
    *,
    interactive: bool,
    patch_project_root: bool = True,
) -> None:
    """Provide the minimum deterministic environment for one launcher run."""

    values = {
        "SERENA_AGENT_CLIENT": "codex",
        "SERENA_AGENT_INTERACTIVE": "1" if interactive else "0",
        "SERENA_AGENT_PROJECT_ROOT": str(root),
        "SERENA_AGENT_CLEAR_BEFORE_CHILD": "0",
    }
    with patch.dict(os.environ, values, clear=False):
        if patch_project_root:
            with patch.object(launcher, "find_project_root", return_value=root.resolve()):
                yield
        else:
            yield


class LauncherOptInTests(unittest.TestCase):
    def test_stale_ancestor_root_hint_cannot_opt_in_nested_worktree(self) -> None:
        """Python must recompute the nearest boundary before trusting a shell hint."""

        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw)
            ancestor = temporary_root / "ancestor"
            nested = ancestor / "nested"
            child = nested / "src"
            (ancestor / ".git").mkdir(parents=True)
            (ancestor / ".serena").mkdir()
            (ancestor / ".serena" / "project.yml").write_text(
                "project_name: ancestor\n"
            )
            (nested / ".git").mkdir(parents=True)
            child.mkdir()
            original_cwd = Path.cwd()
            os.chdir(child)
            try:
                with (
                    _launcher_environment(
                        ancestor,
                        interactive=False,
                        patch_project_root=False,
                    ),
                    patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                    patch.object(launcher, "_launch_bare_child", return_value=31) as bare,
                    patch.object(
                        launcher, "serena_server_command", return_value=["serena"]
                    ) as server_command,
                    patch.object(launcher, "_run_serena_cli_install_v2") as install,
                    patch.object(launcher, "_serena_project_create") as create,
                    patch.object(
                        launcher,
                        "ensure_server",
                        side_effect=AssertionError("nested worktree must stay bare"),
                    ) as ensure,
                ):
                    result = launcher._main_v2([])
            finally:
                os.chdir(original_cwd)

            self.assertEqual(result, 31)
            bare.assert_called_once_with(
                [], client_type="codex", real_binary="/fake/codex"
            )
            server_command.assert_not_called()
            install.assert_not_called()
            create.assert_not_called()
            ensure.assert_not_called()

    def test_ready_presentation_failure_does_not_discard_an_acquired_record(self) -> None:
        """A UI write after acquire must not turn a live lease into bare fallback."""

        class ReadyWriteFailure:
            def write(self, text: str) -> int:
                if "ready" in text:
                    raise OSError("terminal unavailable")
                return len(text)

            def flush(self) -> None:
                pass

        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        scope = launcher.Scope(root)
        lease = Lease("lease-a", "codex", 1, 1.0, "identity")
        record = _record()
        ticker = MagicMock()

        with (
            patch.object(launcher, "ensure_server", return_value=record),
            patch.object(launcher, "SpinnerTicker", return_value=ticker),
        ):
            actual = launcher._start_mcp_with_spinner(
                scope=scope,
                lease=lease,
                stream=ReadyWriteFailure(),
            )

        self.assertIs(actual, record)
        ticker.stop.assert_called_once()

    def test_release_runs_when_shutdown_spinner_construction_fails(self) -> None:
        """Final release cannot depend on constructing its optional presentation ticker."""

        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        scope = launcher.Scope(root)
        shutdown = MagicMock(return_value=_stats())
        with patch.object(launcher, "SpinnerTicker", side_effect=OSError("ticker unavailable")):
            stats = launcher._stop_mcp_with_spinner(
                scope=scope,
                lease_id="lease-a",
                server_instance_id="instance-a",
                stream=StringIO(),
                shutdown_fn=shutdown,
            )

        self.assertIs(stats, shutdown.return_value)
        shutdown.assert_called_once_with(scope, "lease-a", "instance-a")

    def test_shutdown_spinner_reports_kept_sessions_for_nonfinal_release(self) -> None:
        """A non-final lease release must not claim the shared server stopped."""

        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        scope = launcher.Scope(root)
        output = StringIO()
        stats = launcher.ShutdownStats(
            sessions_before=3,
            sessions_closed=1,
            sessions_remaining=2,
            server_was_running=True,
            server_stopped=False,
        )
        shutdown = MagicMock(return_value=stats)
        ticker = MagicMock()
        with patch.object(launcher, "SpinnerTicker", return_value=ticker):
            actual = launcher._stop_mcp_with_spinner(
                scope=scope,
                lease_id="lease-a",
                server_instance_id="instance-a",
                stream=output,
                shutdown_fn=shutdown,
            )

        self.assertIs(actual, stats)
        self.assertIn("kept (2 sessions)", output.getvalue())
        self.assertNotIn("stopped shared worktree server", output.getvalue())

    def test_shutdown_presentation_keyboard_interrupt_runs_release_then_propagates(self) -> None:
        """Ctrl-C in shutdown UI must not skip release, then remains visible to callers."""

        class InterruptingOutput:
            def write(self, _text: str) -> int:
                raise KeyboardInterrupt("shutdown UI interrupted")

            def flush(self) -> None:
                pass

        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        scope = launcher.Scope(root)
        shutdown = MagicMock(return_value=_stats())
        with self.assertRaisesRegex(KeyboardInterrupt, "shutdown UI interrupted"):
            launcher._stop_mcp_with_spinner(
                scope=scope,
                lease_id="lease-a",
                server_instance_id="instance-a",
                stream=InterruptingOutput(),
                shutdown_fn=shutdown,
            )

        shutdown.assert_called_once_with(scope, "lease-a", "instance-a")

    def test_shutdown_spinner_keyboard_interrupt_runs_release_then_propagates(self) -> None:
        """Ticker setup is optional presentation, not a prerequisite for final release."""

        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        scope = launcher.Scope(root)
        shutdown = MagicMock(return_value=_stats())
        with patch.object(launcher, "SpinnerTicker", side_effect=KeyboardInterrupt("ticker interrupted")):
            with self.assertRaisesRegex(KeyboardInterrupt, "ticker interrupted"):
                launcher._stop_mcp_with_spinner(
                    scope=scope,
                    lease_id="lease-a",
                    server_instance_id="instance-a",
                    stream=StringIO(),
                    shutdown_fn=shutdown,
                )

        shutdown.assert_called_once_with(scope, "lease-a", "instance-a")

    def test_shutdown_callback_error_has_priority_over_presentation_interrupt(self) -> None:
        """A real release failure outranks an optional shutdown UI interruption."""

        class InterruptingOutput:
            def write(self, _text: str) -> int:
                raise KeyboardInterrupt("shutdown UI interrupted")

            def flush(self) -> None:
                pass

        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        scope = launcher.Scope(root)
        shutdown = MagicMock(side_effect=RuntimeError("release failed"))
        with self.assertRaisesRegex(RuntimeError, "release failed"):
            launcher._stop_mcp_with_spinner(
                scope=scope,
                lease_id="lease-a",
                server_instance_id="instance-a",
                stream=InterruptingOutput(),
                shutdown_fn=shutdown,
            )

        shutdown.assert_called_once_with(scope, "lease-a", "instance-a")

    def test_shutdown_callback_error_survives_ticker_cleanup_keyboard_interrupt(self) -> None:
        """Ticker cleanup is optional and cannot replace a real shutdown failure."""

        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        scope = launcher.Scope(root)
        ticker = MagicMock()
        ticker.stop.side_effect = KeyboardInterrupt("ticker cleanup interrupted")
        shutdown = MagicMock(side_effect=RuntimeError("release failed"))
        with patch.object(launcher, "SpinnerTicker", return_value=ticker):
            with self.assertRaisesRegex(RuntimeError, "release failed"):
                launcher._stop_mcp_with_spinner(
                    scope=scope,
                    lease_id="lease-a",
                    server_instance_id="instance-a",
                    stream=StringIO(),
                    shutdown_fn=shutdown,
                )

        shutdown.assert_called_once_with(scope, "lease-a", "instance-a")

    def test_start_callback_error_survives_ticker_cleanup_keyboard_interrupt(self) -> None:
        """The startup spinner cleanup follows the same callback-error precedence."""

        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root))
        scope = launcher.Scope(root)
        lease = Lease("lease-a", "codex", 1, 1.0, "identity")
        ticker = MagicMock()
        ticker.stop.side_effect = KeyboardInterrupt("ticker cleanup interrupted")
        ensure = MagicMock(side_effect=RuntimeError("server failed"))
        with (
            patch.object(launcher, "SpinnerTicker", return_value=ticker),
            patch.object(launcher, "ensure_server", ensure),
        ):
            with self.assertRaisesRegex(RuntimeError, "server failed"):
                launcher._start_mcp_with_spinner(scope=scope, lease=lease, stream=StringIO())

        ensure.assert_called_once_with(scope, lease)

    def test_noninteractive_missing_marker_launches_bare_without_serena_calls(self) -> None:
        """Implicit noninteractive startup must not resolve or manage Serena."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "worktree"
            (root / ".git").mkdir(parents=True)
            record = _record()
            child = _child()
            with (
                _launcher_environment(root, interactive=False),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "_launch_bare_child", return_value=17) as bare,
                patch.object(launcher, "serena_server_command") as server_command,
                patch.object(launcher, "_run_serena_cli_install_v2") as install,
                patch.object(launcher, "_serena_project_create") as create,
                patch.object(launcher, "ensure_server", return_value=record) as ensure,
                patch.object(launcher, "_heartbeat_loop") as heartbeat,
                patch.object(launcher, "_remove_lease_and_shutdown_if_empty") as release,
                patch.object(launcher, "make_launcher_lease"),
                patch.object(launcher, "build_child_command", return_value=(["/fake/codex"], lambda: None)),
                patch.object(launcher.subprocess, "Popen", return_value=child),
                patch.object(launcher.threading, "Thread"),
                patch.object(launcher.signal, "signal"),
            ):
                result = launcher._main_v2([])

            self.assertEqual(result, 17)
            bare.assert_called_once_with([], client_type="codex", real_binary="/fake/codex")
            server_command.assert_not_called()
            install.assert_not_called()
            create.assert_not_called()
            ensure.assert_not_called()
            heartbeat.assert_not_called()
            release.assert_not_called()

    def test_interactive_init_decline_launches_bare_before_cli_resolution(self) -> None:
        """Declining initialization must not turn into an install or server prompt."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "worktree"
            (root / ".git").mkdir(parents=True)
            with (
                _launcher_environment(root, interactive=True),
                patch.object(launcher, "_render_preflight_overview_v2"),
                patch.object(launcher, "_run_preflight_v2", return_value=0),
                patch.object(launcher, "_run_session_choice_v2", return_value="keep"),
                patch.object(launcher, "confirm", return_value=False),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "_launch_bare_child", return_value=18) as bare,
                patch.object(launcher, "serena_server_command") as server_command,
                patch.object(launcher, "_run_serena_cli_install_v2") as install,
                patch.object(launcher, "_serena_project_create") as create,
                patch.object(launcher, "ensure_server") as ensure,
            ):
                result = launcher._main_v2([])

            self.assertEqual(result, 18)
            bare.assert_called_once_with([], client_type="codex", real_binary="/fake/codex")
            server_command.assert_not_called()
            install.assert_not_called()
            create.assert_not_called()
            ensure.assert_not_called()

    def test_interactive_init_accept_creates_marker_then_acquires_shared_server(self) -> None:
        """Creation after a resolved CLI must opt in the exact worktree before startup."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "worktree"
            (root / ".git").mkdir(parents=True)
            record = _record()
            events: list[str] = []

            def create_marker(project_root: Path) -> tuple[int, str]:
                self.assertEqual(project_root, root.resolve())
                events.append("create")
                (project_root / ".serena").mkdir()
                (project_root / ".serena" / "project.yml").write_text("project_name: test\n")
                return 0, ""

            def opt_in_state(project_root: Path) -> bool:
                present = (project_root / ".serena" / "project.yml").is_file()
                events.append(f"marker:{present}")
                return present

            def resolve_serena() -> list[str]:
                events.append("cli")
                return ["serena"]

            child = _child()
            fake_thread = MagicMock()
            start = MagicMock(side_effect=lambda scope, lease: launcher.ensure_server(scope, lease))
            lease = Lease("lease-a", "codex", 1, 1.0, "identity")
            stop = MagicMock(return_value=_stats())
            with (
                _launcher_environment(root, interactive=True),
                patch.object(launcher, "_render_preflight_overview_v2"),
                patch.object(launcher, "_run_preflight_v2", return_value=0),
                patch.object(launcher, "_run_session_choice_v2", return_value="keep"),
                patch.object(launcher, "confirm", return_value=True),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "serena_opted_in", side_effect=opt_in_state),
                patch.object(launcher, "serena_server_command", side_effect=resolve_serena) as server_command,
                patch.object(launcher, "_serena_project_create", side_effect=create_marker) as create,
                patch.object(launcher, "ensure_server", return_value=record) as ensure,
                patch.object(launcher, "_start_mcp_with_spinner", side_effect=start),
                patch.object(launcher.uuid, "uuid4", return_value="lease-a"),
                patch.object(launcher, "make_launcher_lease", return_value=lease),
                patch.object(launcher, "build_child_command", return_value=(["/fake/codex"], lambda: None)),
                patch.object(launcher.subprocess, "Popen", return_value=child),
                patch.object(launcher.threading, "Thread", return_value=fake_thread) as thread_constructor,
                patch.object(launcher.signal, "signal"),
                patch.object(launcher, "open_dashboard_if_requested"),
                patch.object(launcher, "_render_summary_v2"),
                patch.object(launcher, "_stop_mcp_with_spinner", side_effect=stop),
            ):
                result = launcher._main_v2([])

            self.assertEqual(result, 0)
            self.assertTrue((root / ".serena" / "project.yml").is_file())
            server_command.assert_called()
            create.assert_called_once_with(root.resolve())
            self.assertEqual(events[:4], ["marker:False", "cli", "create", "marker:True"])
            ensure.assert_called_once()
            self.assertEqual(start.call_args.kwargs["scope"].project_root, root.resolve())
            fake_thread.start.assert_called_once()
            self.assertEqual(thread_constructor.call_args.kwargs["target"], launcher._heartbeat_loop)
            self.assertEqual(
                thread_constructor.call_args.kwargs["args"][1:3],
                ("lease-a", record.server_instance_id),
            )
            stop.assert_called_once_with(
                scope=launcher.Scope(root),
                lease_id="lease-a",
                server_instance_id=record.server_instance_id,
            )

    def test_install_decline_after_init_consent_keeps_marker_absent_and_launches_bare(self) -> None:
        """No project marker may be written if the needed CLI install is declined."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "worktree"
            (root / ".git").mkdir(parents=True)
            with (
                _launcher_environment(root, interactive=True),
                patch.object(launcher, "_render_preflight_overview_v2"),
                patch.object(launcher, "_run_preflight_v2", return_value=0),
                patch.object(launcher, "_run_session_choice_v2", return_value="keep"),
                patch.object(launcher, "confirm", return_value=True),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "serena_server_command", return_value=None),
                patch.object(launcher, "_run_serena_cli_install_v2", return_value="declined") as install,
                patch.object(launcher, "_serena_project_create", return_value=(1, "cli unavailable")) as create,
                patch.object(launcher, "ensure_server") as ensure,
                patch.object(launcher, "_launch_bare_child", return_value=19) as bare,
            ):
                result = launcher._main_v2([])

            self.assertEqual(result, 19)
            install.assert_called_once()
            create.assert_not_called()
            ensure.assert_not_called()
            self.assertFalse((root / ".serena" / "project.yml").exists())
            bare.assert_called_once_with([], client_type="codex", real_binary="/fake/codex")

    def test_already_opted_in_skips_initialization_and_starts_server(self) -> None:
        """An existing exact-root marker remains the normal shared-server path."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "worktree"
            (root / ".git").mkdir(parents=True)
            (root / ".serena").mkdir()
            (root / ".serena" / "project.yml").write_text("project_name: test\n")
            record = _record()
            child = _child()
            fake_thread = MagicMock()
            with (
                _launcher_environment(root, interactive=False),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "_run_serena_init_v2") as initialize,
                patch.object(launcher, "_run_serena_cli_install_v2") as install,
                patch.object(launcher, "serena_server_command", return_value=["serena"]),
                patch.object(launcher, "ensure_server", return_value=record) as ensure,
                patch.object(launcher, "make_launcher_lease"),
                patch.object(launcher, "build_child_command", return_value=(["/fake/codex"], lambda: None)),
                patch.object(launcher.subprocess, "Popen", return_value=child),
                patch.object(launcher.threading, "Thread", return_value=fake_thread),
                patch.object(launcher.signal, "signal"),
                patch.object(launcher, "_remove_lease_and_shutdown_if_empty", return_value=SimpleNamespace(
                    server_stopped=True, server_was_running=True, sessions_remaining=0
                )),
            ):
                result = launcher._main_v2([])

            self.assertEqual(result, 0)
            initialize.assert_not_called()
            install.assert_not_called()
            ensure.assert_called_once()
            fake_thread.start.assert_called_once()

    def test_acquired_server_releases_once_when_heartbeat_start_fails(self) -> None:
        """A failed heartbeat start must release the acquired server generation once."""

        with tempfile.TemporaryDirectory() as raw:
            root = _make_opted_in_root(Path(raw))
            record = _record()
            thread = MagicMock()
            thread.start.side_effect = RuntimeError("thread start failed")
            lease = Lease("lease-a", "codex", 1, 1.0, "identity")
            with (
                _launcher_environment(root, interactive=False),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "serena_server_command", return_value=["serena"]),
                patch.object(launcher.uuid, "uuid4", return_value="lease-a"),
                patch.object(launcher, "make_launcher_lease", return_value=lease),
                patch.object(launcher, "ensure_server", return_value=record),
                patch.object(launcher.threading, "Thread", return_value=thread),
                patch.object(launcher, "build_child_command") as build,
                patch.object(launcher, "_remove_lease_and_shutdown_if_empty", return_value=_stats()) as release,
            ):
                with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                    launcher._main_v2([])

            build.assert_not_called()
            release.assert_called_once_with(launcher.Scope(root), "lease-a", "instance-a")

    def test_cleanup_failure_keeps_child_result_and_releases_once(self) -> None:
        """Temporary-client cleanup failure must not mask a completed child's exit code."""

        with tempfile.TemporaryDirectory() as raw:
            root = _make_opted_in_root(Path(raw))
            record = _record()
            child = _child(returncode=23)
            cleanup = MagicMock(side_effect=KeyboardInterrupt("temp cleanup interrupted"))
            lease = Lease("lease-a", "codex", 1, 1.0, "identity")
            with (
                _launcher_environment(root, interactive=False),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "serena_server_command", return_value=["serena"]),
                patch.object(launcher.uuid, "uuid4", return_value="lease-a"),
                patch.object(launcher, "make_launcher_lease", return_value=lease),
                patch.object(launcher, "ensure_server", return_value=record),
                patch.object(launcher.threading, "Thread"),
                patch.object(launcher, "build_child_command", return_value=(["/fake/codex"], cleanup)),
                patch.object(launcher.subprocess, "Popen", return_value=child),
                patch.object(launcher.signal, "signal"),
                patch.object(launcher, "_remove_lease_and_shutdown_if_empty", return_value=_stats()) as release,
            ):
                result = launcher._main_v2([])

            self.assertEqual(result, 23)
            cleanup.assert_called_once()
            release.assert_called_once_with(launcher.Scope(root), "lease-a", "instance-a")

    def test_primary_child_error_wins_over_cleanup_and_release_errors(self) -> None:
        """Cleanup or final-release errors must not replace the child failure."""

        with tempfile.TemporaryDirectory() as raw:
            root = _make_opted_in_root(Path(raw))
            record = _record()
            child = _child()
            child.wait.side_effect = ValueError("child failed")
            cleanup = MagicMock(side_effect=OSError("temp cleanup failed"))
            lease = Lease("lease-a", "codex", 1, 1.0, "identity")
            with (
                _launcher_environment(root, interactive=False),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "serena_server_command", return_value=["serena"]),
                patch.object(launcher.uuid, "uuid4", return_value="lease-a"),
                patch.object(launcher, "make_launcher_lease", return_value=lease),
                patch.object(launcher, "ensure_server", return_value=record),
                patch.object(launcher.threading, "Thread"),
                patch.object(launcher, "build_child_command", return_value=(["/fake/codex"], cleanup)),
                patch.object(launcher.subprocess, "Popen", return_value=child),
                patch.object(launcher.signal, "signal"),
                patch.object(
                    launcher,
                    "_remove_lease_and_shutdown_if_empty",
                    side_effect=RuntimeError("release failed"),
                ) as release,
            ):
                with self.assertRaisesRegex(ValueError, "child failed"):
                    launcher._main_v2([])

            cleanup.assert_called_once()
            release.assert_called_once_with(launcher.Scope(root), "lease-a", "instance-a")

    def test_signal_handler_failure_terminates_and_reaps_owned_child_before_release(self) -> None:
        """A post-Popen setup error cannot leave the directly owned agent alive."""

        with tempfile.TemporaryDirectory() as raw:
            root = _make_opted_in_root(Path(raw))
            record = _record()
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            self.addCleanup(_stop_real_child, child)
            lease = Lease("lease-a", "codex", 1, 1.0, "identity")
            with (
                _launcher_environment(root, interactive=False),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "serena_server_command", return_value=["serena"]),
                patch.object(launcher.uuid, "uuid4", return_value="lease-a"),
                patch.object(launcher, "make_launcher_lease", return_value=lease),
                patch.object(launcher, "ensure_server", return_value=record),
                patch.object(launcher.threading, "Thread"),
                patch.object(
                    launcher,
                    "build_child_command",
                    return_value=(["/fake/codex"], lambda: None),
                ),
                patch.object(launcher.subprocess, "Popen", return_value=child) as popen,
                patch.object(
                    launcher.signal,
                    "signal",
                    side_effect=RuntimeError("signal handler failed"),
                ),
                patch.object(
                    launcher,
                    "_remove_lease_and_shutdown_if_empty",
                    side_effect=RuntimeError("release failed"),
                ) as release,
            ):
                with self.assertRaisesRegex(RuntimeError, "signal handler failed"):
                    launcher._main_v2([])

            self.assertIsNotNone(child.poll())
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            release.assert_called_once_with(
                launcher.Scope(root), "lease-a", "instance-a"
            )

    def test_wait_baseexception_terminates_and_reaps_owned_child_before_release(self) -> None:
        """A BaseException from wait remains primary after child and lease cleanup."""

        with tempfile.TemporaryDirectory() as raw:
            root = _make_opted_in_root(Path(raw))
            record = _record()
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            self.addCleanup(_stop_real_child, child)
            real_wait = child.wait
            wait_calls = 0

            def interrupted_wait(*args, **kwargs):
                nonlocal wait_calls
                wait_calls += 1
                if wait_calls == 1:
                    raise KeyboardInterrupt("child wait interrupted")
                return real_wait(*args, **kwargs)

            lease = Lease("lease-a", "codex", 1, 1.0, "identity")
            with (
                _launcher_environment(root, interactive=False),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "serena_server_command", return_value=["serena"]),
                patch.object(launcher.uuid, "uuid4", return_value="lease-a"),
                patch.object(launcher, "make_launcher_lease", return_value=lease),
                patch.object(launcher, "ensure_server", return_value=record),
                patch.object(launcher.threading, "Thread"),
                patch.object(
                    launcher,
                    "build_child_command",
                    return_value=(["/fake/codex"], lambda: None),
                ),
                patch.object(launcher.subprocess, "Popen", return_value=child),
                patch.object(child, "wait", side_effect=interrupted_wait),
                patch.object(launcher.signal, "signal"),
                patch.object(
                    launcher,
                    "_remove_lease_and_shutdown_if_empty",
                    side_effect=RuntimeError("release failed"),
                ) as release,
            ):
                with self.assertRaisesRegex(KeyboardInterrupt, "child wait interrupted"):
                    launcher._main_v2([])

            self.assertIsNotNone(child.poll())
            self.assertGreaterEqual(wait_calls, 2)
            release.assert_called_once_with(
                launcher.Scope(root), "lease-a", "instance-a"
            )

    def test_normal_release_failure_is_reported_to_the_caller(self) -> None:
        """A final-release failure after normal child exit must not be reported as success."""

        with tempfile.TemporaryDirectory() as raw:
            root = _make_opted_in_root(Path(raw))
            record = _record()
            lease = Lease("lease-a", "codex", 1, 1.0, "identity")
            with (
                _launcher_environment(root, interactive=False),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "serena_server_command", return_value=["serena"]),
                patch.object(launcher.uuid, "uuid4", return_value="lease-a"),
                patch.object(launcher, "make_launcher_lease", return_value=lease),
                patch.object(launcher, "ensure_server", return_value=record),
                patch.object(launcher.threading, "Thread"),
                patch.object(launcher, "build_child_command", return_value=(["/fake/codex"], lambda: None)),
                patch.object(launcher.subprocess, "Popen", return_value=_child()),
                patch.object(launcher.signal, "signal"),
                patch.object(
                    launcher,
                    "_remove_lease_and_shutdown_if_empty",
                    side_effect=RuntimeError("release failed"),
                ) as release,
            ):
                with self.assertRaisesRegex(RuntimeError, "release failed"):
                    launcher._main_v2([])

            release.assert_called_once_with(launcher.Scope(root), "lease-a", "instance-a")

    def test_launcher_passes_record_url_and_instance_to_both_adapters_and_lifecycle(self) -> None:
        """One shared record drives Codex/Claude injection, heartbeat, and release."""

        record = _record()
        codex_command, codex_cleanup = launcher.build_child_command(
            client_type="codex",
            real_binary="/fake/codex",
            mcp_url=record.mcp_url,
            child_args=["resume"],
        )
        claude_command, claude_cleanup = launcher.build_child_command(
            client_type="claude",
            real_binary="/fake/claude",
            mcp_url=record.mcp_url,
            child_args=["--continue"],
        )
        try:
            self.assertEqual(codex_command[2], f'mcp_servers.serena.url="{record.mcp_url}"')
            config_path = Path(claude_command[1].split("=", 1)[1])
            self.assertEqual(json.loads(config_path.read_text())["mcpServers"]["serena"]["url"], record.mcp_url)
        finally:
            codex_cleanup()
            claude_cleanup()

        with patch(
            "local_dev.serena_mcp_management.serena_mcp.watchdog.process_identity",
            return_value="launcher-identity",
        ), patch(
            "local_dev.serena_mcp_management.serena_mcp.watchdog.os.getpid",
            return_value=123,
        ):
            lease = make_launcher_lease("lease-a", "codex", now=1.0)

        self.assertEqual(lease.client_type, "codex")

    def test_interactive_ticker_failure_after_acquire_keeps_launch_and_releases_once(self) -> None:
        """Post-acquire spinner failure must not bare-fallback and leak the lease."""

        with tempfile.TemporaryDirectory() as raw:
            root = _make_opted_in_root(Path(raw))
            record = _record()
            child = _child()
            ticker = MagicMock()
            ticker.stop.side_effect = OSError("spinner stop failed")
            lease = Lease("lease-a", "codex", 1, 1.0, "identity")
            with (
                _launcher_environment(root, interactive=True),
                patch.object(launcher, "_render_preflight_overview_v2"),
                patch.object(launcher, "_run_preflight_v2", return_value=0),
                patch.object(launcher, "_run_session_choice_v2", return_value="keep"),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "serena_server_command", return_value=["serena"]),
                patch.object(launcher.uuid, "uuid4", return_value="lease-a"),
                patch.object(launcher, "make_launcher_lease", return_value=lease),
                patch.object(launcher, "ensure_server", return_value=record),
                patch.object(launcher, "SpinnerTicker", return_value=ticker),
                patch.object(launcher.threading, "Thread"),
                patch.object(launcher, "build_child_command", return_value=(["/fake/codex"], lambda: None)),
                patch.object(launcher.subprocess, "Popen", return_value=child),
                patch.object(launcher.signal, "signal"),
                patch.object(launcher, "open_dashboard_if_requested"),
                patch.object(launcher, "_render_summary_v2"),
                patch.object(launcher, "_launch_bare_child") as bare,
                patch.object(launcher, "_remove_lease_and_shutdown_if_empty", return_value=_stats()) as release,
            ):
                result = launcher._main_v2([])

            self.assertEqual(result, 0)
            bare.assert_not_called()
            release.assert_called_once_with(launcher.Scope(root), "lease-a", "instance-a")

    def test_post_acquisition_keyboard_interrupt_releases_once_without_bare_launch(self) -> None:
        """Ctrl-C after acquire releases its record before preventing child launch."""

        with tempfile.TemporaryDirectory() as raw:
            root = _make_opted_in_root(Path(raw))
            record = _record()
            ticker = MagicMock()
            ticker.stop.side_effect = KeyboardInterrupt("ready UI interrupted")
            lease = Lease("lease-a", "codex", 1, 1.0, "identity")
            with (
                _launcher_environment(root, interactive=True),
                patch.object(launcher, "_render_preflight_overview_v2"),
                patch.object(launcher, "_run_preflight_v2", return_value=0),
                patch.object(launcher, "_run_session_choice_v2", return_value="keep"),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "serena_server_command", return_value=["serena"]),
                patch.object(launcher.uuid, "uuid4", return_value="lease-a"),
                patch.object(launcher, "make_launcher_lease", return_value=lease),
                patch.object(launcher, "ensure_server", return_value=record),
                patch.object(launcher, "SpinnerTicker", return_value=ticker),
                patch.object(launcher, "build_child_command") as build,
                patch.object(launcher, "_launch_bare_child") as bare,
                patch.object(launcher, "_remove_lease_and_shutdown_if_empty", return_value=_stats()) as release,
            ):
                with self.assertRaisesRegex(KeyboardInterrupt, "ready UI interrupted"):
                    launcher._main_v2([])

            build.assert_not_called()
            bare.assert_not_called()
            release.assert_called_once_with(launcher.Scope(root), "lease-a", "instance-a")

    def test_install_failure_after_init_consent_keeps_marker_absent_and_launches_bare(self) -> None:
        """An unsuccessful install must not create a marker or register a lease."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "worktree"
            (root / ".git").mkdir(parents=True)
            with (
                _launcher_environment(root, interactive=True),
                patch.object(launcher, "confirm", return_value=True),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "serena_server_command", return_value=None),
                patch.object(launcher, "_run_serena_cli_install_v2", return_value="failed") as install,
                patch.object(launcher, "_serena_project_create") as create,
                patch.object(launcher, "ensure_server") as ensure,
                patch.object(launcher, "_launch_bare_child", return_value=21) as bare,
            ):
                result = launcher._main_v2([])

            self.assertEqual(result, 21)
            install.assert_called_once()
            create.assert_not_called()
            ensure.assert_not_called()
            self.assertFalse((root / ".serena" / "project.yml").exists())
            bare.assert_called_once_with([], client_type="codex", real_binary="/fake/codex")

    def test_shared_server_failure_warns_and_launches_bare_without_lease_cleanup(self) -> None:
        """A failed acquire has no registered lease for heartbeat or release to touch."""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "worktree"
            (root / ".git").mkdir(parents=True)
            (root / ".serena").mkdir()
            (root / ".serena" / "project.yml").write_text("project_name: test\n")
            out = StringIO()
            with (
                _launcher_environment(root, interactive=False),
                patch.object(launcher, "find_real_binary", return_value="/fake/codex"),
                patch.object(launcher, "serena_server_command", return_value=["serena"]),
                patch.object(launcher, "ensure_server", side_effect=RuntimeError("unhealthy")) as ensure,
                patch.object(launcher, "_launch_bare_child", return_value=20) as bare,
                patch.object(launcher.threading, "Thread") as thread,
                patch.object(launcher, "_remove_lease_and_shutdown_if_empty") as release,
                patch.object(launcher.sys, "stdout", out),
            ):
                result = launcher._main_v2([])

            self.assertEqual(result, 20)
            ensure.assert_called_once()
            bare.assert_called_once_with([], client_type="codex", real_binary="/fake/codex")
            thread.assert_not_called()
            release.assert_not_called()
            self.assertEqual(out.getvalue().count("shared worktree server unavailable"), 1)


def _make_opted_in_root(parent: Path) -> Path:
    root = parent / "worktree"
    (root / ".git").mkdir(parents=True)
    (root / ".serena").mkdir()
    (root / ".serena" / "project.yml").write_text("project_name: test\n")
    return root


def _record() -> SimpleNamespace:
    return SimpleNamespace(
        mcp_url="http://127.0.0.1:9999/mcp",
        dashboard_url="http://127.0.0.1:9999/dashboard",
        server_instance_id="instance-a",
    )


def _child(*, returncode: int = 0) -> MagicMock:
    child = MagicMock()
    child.wait.return_value = returncode
    child.poll.return_value = 0
    return child


def _stats() -> SimpleNamespace:
    return SimpleNamespace(server_stopped=True, server_was_running=True, sessions_remaining=0)


def _stop_real_child(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is None:
        child.terminate()
    try:
        child.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=2.0)


if __name__ == "__main__":
    unittest.main()
