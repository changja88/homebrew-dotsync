"""Process-boundary integration tests for the shared Serena lifecycle."""
from __future__ import annotations

import gc
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import warnings
from dataclasses import dataclass
from email.message import Message
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_dev.serena_mcp_management.external_cli import serena_server_command
from local_dev.serena_mcp_management.serena_mcp import health, server
from local_dev.serena_mcp_management.serena_mcp.health import process_identity
from local_dev.serena_mcp_management.serena_mcp.paths import Scope, state_dir_for
from local_dev.serena_mcp_management.serena_mcp.registry import (
    Lease,
    ServerRecord,
    locked_registry,
    read_registry_record,
)
from local_dev.serena_mcp_management.serena_mcp.termination import terminate_pid
from local_dev.serena_mcp_management.serena_mcp.watchdog import (
    make_launcher_lease,
    release_lease_and_shutdown_if_empty,
)


AGENT_LAUNCHER_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_SCRIPT = (
    AGENT_LAUNCHER_ROOT
    / "local_dev"
    / "serena_mcp_management"
    / "serena_agent_launcher.py"
)
WAIT_SECONDS = 15.0
MCP_PROTOCOL_VERSION = "2024-11-05"
_MODULE_RUNTIME_DIRECTORY: tempfile.TemporaryDirectory[str] | None = None
_MODULE_RUNTIME_ENVIRONMENT = None


def setUpModule() -> None:
    global _MODULE_RUNTIME_DIRECTORY, _MODULE_RUNTIME_ENVIRONMENT
    _MODULE_RUNTIME_DIRECTORY = tempfile.TemporaryDirectory()
    runtime_root = str(
        Path(_MODULE_RUNTIME_DIRECTORY.name).resolve() / "launcher-runtime"
    )
    _MODULE_RUNTIME_ENVIRONMENT = patch.dict(
        os.environ,
        {"SERENA_AGENT_RUNTIME_ROOT": runtime_root},
        clear=False,
    )
    _MODULE_RUNTIME_ENVIRONMENT.start()


def tearDownModule() -> None:
    if _MODULE_RUNTIME_ENVIRONMENT is not None:
        _MODULE_RUNTIME_ENVIRONMENT.stop()
    if _MODULE_RUNTIME_DIRECTORY is not None:
        _MODULE_RUNTIME_DIRECTORY.cleanup()


FAKE_SERENA_SOURCE = r'''#!/usr/bin/env python3
import argparse
import json
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

with open(os.environ["FAKE_SERENA_INVOCATION_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"pid": os.getpid(), "argv": sys.argv[1:]}) + "\n")

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("command")
parser.add_argument("--project", required=True)
parser.add_argument("--port", required=True, type=int)
args, _unknown = parser.parse_known_args()

with open(os.environ["FAKE_SERENA_START_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"pid": os.getpid(), "project": args.project, "port": args.port}) + "\n")

class Handler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        if self.path == "/get_config_overview":
            self._json(200, {"active_project": {"path": os.path.realpath(args.project)}})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        method = request.get("method")
        if self.headers.get("Mcp-Session-Id") and self.headers.get("MCP-Protocol-Version") != "2024-11-05":
            self._json(400, {"error": "missing negotiated protocol header"})
            return
        if method == "initialize":
            self._json(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake-serena", "version": "1"},
                    },
                },
                session_id=str(uuid.uuid4()),
            )
            return
        if method == "tools/list":
            self._json(
                200,
                {"jsonrpc": "2.0", "id": request.get("id"), "result": {"tools": []}},
            )
            return
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self, status, payload, session_id=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if session_id is not None:
            self.send_header("Mcp-Session-Id", session_id)
        self.end_headers()
        self.wfile.write(body)

server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
print(f"Dashboard available at http://127.0.0.1:{args.port}/dashboard", flush=True)
server.serve_forever()
'''


FAKE_CLIENT_SOURCE = r'''#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

argv = sys.argv[1:]
mcp_url = None
for index, value in enumerate(argv):
    if value == "-c" and index + 1 < len(argv):
        setting = argv[index + 1]
        if setting.startswith("mcp_servers.serena.url="):
            mcp_url = setting.split("=", 1)[1].strip('"')
    if value.startswith("--mcp-config="):
        config_path = Path(value.split("=", 1)[1])
        config = json.loads(config_path.read_text())
        mcp_url = config["mcpServers"]["serena"]["url"]

Path(os.environ["FAKE_CLIENT_ARGV_FILE"]).write_text(
    json.dumps({"argv": argv, "mcp_url": mcp_url})
)
Path(os.environ["FAKE_CLIENT_READY_FILE"]).touch()
exit_file = Path(os.environ["FAKE_CLIENT_EXIT_FILE"])
while not exit_file.exists():
    time.sleep(0.02)
'''


@dataclass(frozen=True, slots=True)
class _ManagedProcess:
    pid: int
    identity: str


@dataclass(slots=True)
class _OwnedPopen:
    process: subprocess.Popen[bytes]
    identity: str | None = None


@dataclass(slots=True)
class _LauncherProcess:
    process: subprocess.Popen[str]
    identity: str | None
    argv_file: Path
    ready_file: Path
    exit_file: Path


@dataclass(frozen=True, slots=True)
class _FakeExecutables:
    bin_dir: Path
    client: Path
    invocation_log: Path
    server_start_log: Path


@dataclass(frozen=True, slots=True)
class _RawPsProbe:
    available: bool
    identity: str | None
    detail: str


class FakeSharedServerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        _require_production_process_identity(self)

    def test_production_process_identity_matches_raw_ps(self) -> None:
        """The current process must have the platform's immutable identity."""

        probe = _raw_ps_probe(os.getpid())
        self.assertTrue(probe.available)
        self.assertIsNotNone(probe.identity)
        actual = process_identity(os.getpid())
        self.assertIsNotNone(actual)
        if sys.platform == "darwin":
            self.assertRegex(actual or "", r"^darwin:\d+:\d{6}$")
        elif sys.platform.startswith("linux"):
            self.assertRegex(actual or "", r"^linux:\d+$")
        else:
            self.assertEqual(actual, f"ps:{probe.identity}")

    def test_two_claude_and_one_codex_share_then_keep_two_keep_one_and_stop(self) -> None:
        """A non-final launcher exit must never terminate shared managed processes."""

        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw)
            worktree = _make_worktree(temporary_root / "worktree", opted_in=True)
            fakes = _make_fake_executables(temporary_root)
            launchers: list[_LauncherProcess] = []
            managed: set[_ManagedProcess] = set()
            try:
                codex = _launch_fake_client(
                    worktree, "codex", "codex-1", fakes, launchers
                )
                _wait_for_file(codex.ready_file, "Codex fake client readiness")

                claude_one = _launch_fake_client(
                    worktree, "claude", "claude-1", fakes, launchers
                )
                _wait_for_file(claude_one.ready_file, "first Claude fake client readiness")

                claude_two = _launch_fake_client(
                    worktree, "claude", "claude-2", fakes, launchers
                )
                _wait_for_file(claude_two.ready_file, "second Claude fake client readiness")

                scope = Scope(worktree)
                record = _wait_for_record(scope, lease_count=3)
                _capture_record(managed, record)
                self.assertEqual(
                    sorted(lease.client_type for lease in record.leases.values()),
                    ["claude", "claude", "codex"],
                )
                child_records = [_read_client_record(item.argv_file) for item in launchers]
                self.assertEqual(
                    {item["mcp_url"] for item in child_records},
                    {record.mcp_url},
                )
                self.assertTrue(all(item["mcp_url"] in json.dumps(item) for item in child_records))
                _assert_managed_processes_alive(self, managed)

                fake_init = _mcp_json_request(
                    record.mcp_url,
                    {
                        "jsonrpc": "2.0",
                        "id": 41,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": MCP_PROTOCOL_VERSION,
                            "capabilities": {},
                            "clientInfo": {"name": "task7-fake", "version": "1"},
                        },
                    },
                )
                fake_protocol = _negotiated_protocol(fake_init)
                self.assertEqual(fake_protocol, MCP_PROTOCOL_VERSION)
                self.assertIsNotNone(fake_init.session_id)
                _mcp_json_request(
                    record.mcp_url,
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    session_id=fake_init.session_id,
                    protocol_version=fake_protocol,
                )
                fake_tools = _mcp_json_request(
                    record.mcp_url,
                    {"jsonrpc": "2.0", "id": 42, "method": "tools/list"},
                    session_id=fake_init.session_id,
                    protocol_version=fake_protocol,
                )
                self.assertEqual(fake_tools.payload.get("id"), 42)
                _mcp_delete(record.mcp_url, fake_init.session_id, fake_protocol)

                _exit_launcher(claude_one)
                kept_two = _wait_for_record(scope, lease_count=2)
                _capture_record(managed, kept_two)
                self.assertEqual(kept_two.server_instance_id, record.server_instance_id)
                self.assertEqual(
                    sorted(lease.client_type for lease in kept_two.leases.values()),
                    ["claude", "codex"],
                )
                _assert_managed_processes_alive(self, managed)

                _exit_launcher(codex)
                kept_one = _wait_for_record(scope, lease_count=1)
                _capture_record(managed, kept_one)
                self.assertEqual(kept_one.server_instance_id, record.server_instance_id)
                self.assertEqual(
                    [lease.client_type for lease in kept_one.leases.values()],
                    ["claude"],
                )
                _assert_managed_processes_alive(self, managed)

                _exit_launcher(claude_two)
                _wait_until(
                    lambda: read_registry_record(scope) is None,
                    "final release to remove the registry record",
                )
                _wait_for_processes_dead(managed)
                self.assertEqual(len(fakes.invocation_log.read_text().splitlines()), 1)
                self.assertEqual(len(fakes.server_start_log.read_text().splitlines()), 1)
            finally:
                _cleanup_launchers_and_scopes(launchers, [worktree], managed)

    def test_different_worktree_roots_use_distinct_scopes_urls_and_processes(self) -> None:
        """Canonical roots must not share an endpoint or runtime process set."""

        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw)
            first_root = _make_worktree(temporary_root / "first", opted_in=True)
            second_root = _make_worktree(temporary_root / "second", opted_in=True)
            fakes = _make_fake_executables(temporary_root)
            launchers: list[_LauncherProcess] = []
            managed: set[_ManagedProcess] = set()
            try:
                first = _launch_fake_client(
                    first_root, "codex", "first", fakes, launchers
                )
                _wait_for_file(first.ready_file, "first worktree client readiness")
                first_record = _wait_for_record(Scope(first_root), lease_count=1)
                _capture_record(managed, first_record)
                second = _launch_fake_client(
                    second_root, "codex", "second", fakes, launchers
                )
                _wait_for_file(second.ready_file, "second worktree client readiness")
                second_record = _wait_for_record(Scope(second_root), lease_count=1)
                _capture_record(managed, second_record)

                self.assertNotEqual(first_record.server_instance_id, second_record.server_instance_id)
                self.assertNotEqual(first_record.mcp_url, second_record.mcp_url)
                self.assertNotEqual(first_record.server_pid, second_record.server_pid)
                self.assertNotEqual(first_record.proxy_pid, second_record.proxy_pid)
                self.assertNotEqual(first_record.watchdog_pid, second_record.watchdog_pid)
                self.assertEqual(
                    _read_client_record(first.argv_file)["mcp_url"], first_record.mcp_url
                )
                self.assertEqual(
                    _read_client_record(second.argv_file)["mcp_url"], second_record.mcp_url
                )
                _assert_managed_processes_alive(self, managed)

                _exit_launcher(first)
                _exit_launcher(second)
                _wait_until(
                    lambda: read_registry_record(Scope(first_root)) is None,
                    "first worktree final release",
                )
                _wait_until(
                    lambda: read_registry_record(Scope(second_root)) is None,
                    "second worktree final release",
                )
                _wait_for_processes_dead(managed)
            finally:
                _cleanup_launchers_and_scopes(
                    launchers, [first_root, second_root], managed
                )

    def test_noninteractive_opt_out_launches_bare_without_starting_fake_serena(self) -> None:
        """A .git boundary without the exact opt-in marker must remain Serena-free."""

        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw)
            worktree = _make_worktree(temporary_root / "opt-out", opted_in=False)
            fakes = _make_fake_executables(temporary_root)
            launchers: list[_LauncherProcess] = []
            try:
                launcher = _launch_fake_client(
                    worktree, "codex", "bare", fakes, launchers
                )
                _wait_for_file(launcher.ready_file, "bare client readiness")
                child_record = _read_client_record(launcher.argv_file)

                self.assertIsNone(child_record["mcp_url"])
                self.assertEqual(child_record["argv"], ["--integration-session=bare"])
                self.assertFalse(fakes.invocation_log.exists())
                self.assertFalse(fakes.server_start_log.exists())
                self.assertFalse((worktree / ".serena").exists())

                _exit_launcher(launcher)
                self.assertEqual(launcher.process.returncode, 0)
            finally:
                _cleanup_launchers_and_scopes(launchers, [worktree], set())

    def test_stale_opted_in_ancestor_hint_keeps_nested_worktree_bare(self) -> None:
        """A stale shell hint cannot create any Serena state for a nested boundary."""

        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw)
            ancestor = _make_worktree(temporary_root / "ancestor", opted_in=True)
            nested = _make_worktree(ancestor / "nested", opted_in=False)
            fakes = _make_fake_executables(temporary_root)
            launchers: list[_LauncherProcess] = []
            try:
                launched = _launch_fake_client(
                    nested,
                    "codex",
                    "stale-root",
                    fakes,
                    launchers,
                    root_hint=ancestor,
                )
                _wait_for_file(launched.ready_file, "stale-root bare client readiness")
                child_record = _read_client_record(launched.argv_file)

                self.assertIsNone(child_record["mcp_url"])
                self.assertFalse(fakes.invocation_log.exists())
                self.assertFalse(fakes.server_start_log.exists())
                self.assertFalse((nested / ".serena").exists())
                self.assertFalse(state_dir_for(Scope(nested)).exists())

                _exit_launcher(launched)
                self.assertEqual(launched.process.returncode, 0)
            finally:
                _cleanup_launchers_and_scopes(launchers, [], set())


class RealSerenaSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        _require_production_process_identity(self)
        self._expected_warning_processes: set[_ManagedProcess] = set()
        warning_context = warnings.catch_warnings(record=True)
        self._caught_warnings = warning_context.__enter__()
        warnings.simplefilter("always", ResourceWarning)
        self.addCleanup(warning_context.__exit__, None, None, None)
        self.addCleanup(self._assert_only_detached_popen_resource_warnings)

    def _assert_only_detached_popen_resource_warnings(self) -> None:
        gc.collect()
        unexpected = _unaccounted_resource_warnings(
            self._caught_warnings,
            self._expected_warning_processes,
        )
        self.assertEqual(unexpected, [])

    def test_two_real_mcp_sessions_survive_one_session_and_launcher_release(self) -> None:
        """Closing one real MCP session and lease must leave the other usable."""

        resolved_command = serena_server_command()
        if resolved_command is None:
            self.skipTest("existing direct Serena CLI is unavailable; install/download is forbidden")

        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw)
            worktree = _make_worktree(temporary_root / "real-smoke", opted_in=True)
            scope = Scope(worktree)
            managed = self._expected_warning_processes
            lease_ids = ("real-codex-session", "real-claude-session")
            environment = {
                "SERENA_HOME": str(temporary_root / "serena-home"),
                "SERENA_AGENT_RUNTIME_ROOT": str(
                    temporary_root / "launcher-runtime"
                ),
                "XDG_STATE_HOME": str(temporary_root / "xdg-state"),
                "XDG_CACHE_HOME": str(temporary_root / "xdg-cache"),
            }
            try:
                with patch.dict(os.environ, environment, clear=False):
                    codex_lease = make_launcher_lease(lease_ids[0], "codex")
                    first = server.ensure_server(scope, codex_lease)
                    _capture_record(managed, first, require_watchdog=False)
                    _capture_record(managed, _require_current_record(scope))
                    claude_lease = make_launcher_lease(lease_ids[1], "claude")
                    shared = server.ensure_server(scope, claude_lease)
                    _capture_record(managed, shared)

                    self.assertEqual(shared.server_instance_id, first.server_instance_id)
                    self.assertEqual(shared.mcp_url, first.mcp_url)
                    self.assertEqual(shared.server_pid, first.server_pid)
                    self.assertEqual(len(shared.leases), 2)

                    codex_init = _mcp_json_request(
                        shared.mcp_url,
                        {
                            "jsonrpc": "2.0",
                            "id": 101,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": MCP_PROTOCOL_VERSION,
                                "capabilities": {},
                                "clientInfo": {"name": "task7-codex", "version": "1"},
                            },
                        },
                    )
                    claude_init = _mcp_json_request(
                        shared.mcp_url,
                        {
                            "jsonrpc": "2.0",
                            "id": 202,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": MCP_PROTOCOL_VERSION,
                                "capabilities": {},
                                "clientInfo": {"name": "task7-claude", "version": "1"},
                            },
                        },
                    )
                    codex_session = codex_init.session_id
                    claude_session = claude_init.session_id
                    codex_protocol = _negotiated_protocol(codex_init)
                    claude_protocol = _negotiated_protocol(claude_init)
                    self.assertEqual(codex_protocol, MCP_PROTOCOL_VERSION)
                    self.assertEqual(claude_protocol, MCP_PROTOCOL_VERSION)
                    self.assertTrue(codex_session)
                    self.assertTrue(claude_session)
                    self.assertNotEqual(codex_session, claude_session)
                    _mcp_json_request(
                        shared.mcp_url,
                        {"jsonrpc": "2.0", "method": "notifications/initialized"},
                        session_id=codex_session,
                        protocol_version=codex_protocol,
                    )
                    _mcp_json_request(
                        shared.mcp_url,
                        {"jsonrpc": "2.0", "method": "notifications/initialized"},
                        session_id=claude_session,
                        protocol_version=claude_protocol,
                    )

                    _mcp_delete(shared.mcp_url, codex_session, codex_protocol)
                    first_release = release_lease_and_shutdown_if_empty(
                        scope, lease_ids[0], shared.server_instance_id
                    )
                    self.assertEqual(first_release.sessions_remaining, 1)
                    self.assertFalse(first_release.server_stopped)
                    _assert_managed_processes_alive(self, managed)

                    tools = _mcp_json_request(
                        shared.mcp_url,
                        {"jsonrpc": "2.0", "id": 303, "method": "tools/list"},
                        session_id=claude_session,
                        protocol_version=claude_protocol,
                    )
                    self.assertEqual(tools.payload.get("id"), 303)
                    self.assertIsInstance(tools.payload.get("result", {}).get("tools"), list)

                    final_release = release_lease_and_shutdown_if_empty(
                        scope, lease_ids[1], shared.server_instance_id
                    )
                    self.assertTrue(final_release.server_stopped)
                    self.assertEqual(final_release.sessions_remaining, 0)
                    self.assertIsNone(read_registry_record(scope))
                    _wait_for_processes_dead(managed)
            finally:
                primary_exception_active = sys.exc_info()[0] is not None
                cleanup_error: BaseException | None = None
                try:
                    _cleanup_scope(scope, managed)
                except BaseException as exc:
                    cleanup_error = exc
                _terminate_managed_processes(managed)
                _wait_for_processes_dead(managed)
                if cleanup_error is not None and not primary_exception_active:
                    raise cleanup_error


class HealthResourceRegressionTests(unittest.TestCase):
    def test_transient_mcp_http_error_is_closed(self) -> None:
        """Readiness retries must not leak an HTTPError response object."""

        error = HTTPError(
            "http://127.0.0.1:1/mcp",
            502,
            "Bad Gateway",
            Message(),
            BytesIO(b"temporary upstream failure"),
        )
        with patch.object(health, "urlopen", side_effect=error):
            self.assertFalse(health.http_endpoint_alive("http://127.0.0.1:1/mcp"))

        self.assertTrue(error.fp.closed)

    def test_transient_dashboard_http_error_is_closed(self) -> None:
        """Dashboard readiness retries must close their failed HTTP response."""

        error = HTTPError(
            "http://127.0.0.1:1/get_config_overview",
            502,
            "Bad Gateway",
            Message(),
            BytesIO(b"temporary upstream failure"),
        )
        with patch.object(health, "urlopen", side_effect=error):
            self.assertFalse(
                health.dashboard_matches_project(
                    "http://127.0.0.1:1/dashboard", Path("/tmp/project")
                )
            )

        self.assertTrue(error.fp.closed)


class DetachedPopenWarningAccountingTests(unittest.TestCase):
    def test_warning_for_unowned_pid_is_rejected(self) -> None:
        """A standard subprocess warning is not evidence of owned cleanup."""

        warning = warnings.WarningMessage(
            ResourceWarning("subprocess 999999 is still running"),
            ResourceWarning,
            __file__,
            1,
        )

        unexpected = _unaccounted_resource_warnings([warning], set())

        self.assertEqual(
            unexpected,
            ["ResourceWarning: subprocess 999999 is still running"],
        )

class ProcessOwnershipRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        _require_production_process_identity(self)

    def test_cleanup_owns_and_stops_the_current_registry_generation(self) -> None:
        """Cleanup must not ignore a replacement generation found in the registry."""

        with tempfile.TemporaryDirectory() as raw:
            root = _make_worktree(Path(raw) / "replacement", opted_in=True)
            scope = Scope(root)
            owned: list[_OwnedPopen] = []
            managed: set[_ManagedProcess] = set()
            try:
                replacement = _make_owned_process_record(scope, owned)
                with locked_registry(scope) as registry:
                    registry.record = replacement
                _cleanup_scope(scope, managed)

                self.assertIsNone(read_registry_record(scope))
                self.assertEqual(
                    managed,
                    {
                        _ManagedProcess(item.process.pid, item.identity)
                        for item in owned
                    },
                )
                _wait_for_processes_dead(managed)
            finally:
                _reap_owned_processes(owned)

    def test_cleanup_waits_for_contended_registry_then_cleans_record(self) -> None:
        """A busy registry lock must not look like an absent temporary scope."""

        with tempfile.TemporaryDirectory() as raw:
            root = _make_worktree(Path(raw) / "contended", opted_in=True)
            scope = Scope(root)
            owned: list[_OwnedPopen] = []
            managed: set[_ManagedProcess] = set()
            cleanup_started = threading.Event()
            cleanup_failures: list[BaseException] = []
            cleanup_thread: threading.Thread | None = None

            def cleanup() -> None:
                cleanup_started.set()
                try:
                    _cleanup_scope(scope, managed)
                except BaseException as exc:
                    cleanup_failures.append(exc)

            try:
                replacement = _make_owned_process_record(scope, owned)
                with locked_registry(scope) as registry:
                    registry.record = replacement
                    cleanup_thread = threading.Thread(target=cleanup)
                    cleanup_thread.start()
                    self.assertTrue(cleanup_started.wait(timeout=1.0))
                    time.sleep(0.1)
                    self.assertTrue(cleanup_thread.is_alive())
                    self.assertEqual(managed, set())

                cleanup_thread.join(timeout=5.0)
                self.assertFalse(cleanup_thread.is_alive())
                self.assertEqual(cleanup_failures, [])
                self.assertIsNone(read_registry_record(scope))
                self.assertEqual(len(managed), 3)
                _wait_for_processes_dead(managed)
            finally:
                if cleanup_thread is not None:
                    cleanup_thread.join(timeout=5.0)
                _reap_owned_processes(owned)
                if cleanup_thread is not None and cleanup_thread.is_alive():
                    raise AssertionError("contended cleanup thread did not converge")

    def test_launcher_identity_capture_failure_keeps_and_reaps_the_popen(self) -> None:
        """An identity-wait failure must not lose the just-created launcher handle."""

        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw)
            worktree = _make_worktree(temporary_root / "early-failure", opted_in=False)
            fakes = _make_fake_executables(temporary_root)
            launchers: list[_LauncherProcess] = []

            with patch(
                f"{__name__}._wait_for_owned_process_identity",
                side_effect=RuntimeError("identity capture failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "identity capture failed"):
                    _launch_fake_client(
                        worktree, "codex", "early-failure", fakes, launchers
                    )

            self.assertEqual(len(launchers), 1)
            self.assertIsNotNone(launchers[0].process.returncode)
            self.assertFalse(_raw_ps_probe(launchers[0].process.pid).identity)

@dataclass(frozen=True, slots=True)
class _McpResponse:
    payload: dict[str, object]
    session_id: str | None


def _make_worktree(root: Path, *, opted_in: bool) -> Path:
    root.mkdir(parents=True)
    (root / ".git").mkdir()
    if opted_in:
        (root / ".serena").mkdir()
        (root / ".serena" / "project.yml").write_text(
            f"project_name: {root.name}\nlanguage_servers: []\n"
        )
    return root.resolve()


def _make_fake_executables(root: Path) -> _FakeExecutables:
    fake_bin = root / "fake-bin"
    fake_bin.mkdir()
    fake_serena = fake_bin / "serena"
    fake_client = fake_bin / "fake-agent-client"
    fake_serena.write_text(textwrap.dedent(FAKE_SERENA_SOURCE))
    fake_client.write_text(textwrap.dedent(FAKE_CLIENT_SOURCE))
    fake_serena.chmod(0o755)
    fake_client.chmod(0o755)
    return _FakeExecutables(
        bin_dir=fake_bin,
        client=fake_client,
        invocation_log=root / "fake-serena-invocations.jsonl",
        server_start_log=root / "fake-serena-starts.jsonl",
    )


def _launch_fake_client(
    worktree: Path,
    client_type: str,
    label: str,
    fakes: _FakeExecutables,
    owned_launchers: list[_LauncherProcess],
    *,
    root_hint: Path | None = None,
) -> _LauncherProcess:
    control_dir = fakes.bin_dir.parent / f"control-{label}"
    control_dir.mkdir()
    argv_file = control_dir / "argv.json"
    ready_file = control_dir / "ready"
    exit_file = control_dir / "exit"
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment.update(
        {
            "PATH": os.pathsep.join((str(fakes.bin_dir), environment.get("PATH", ""))),
            "PYTHONPATH": os.pathsep.join(
                part for part in (str(AGENT_LAUNCHER_ROOT), existing_pythonpath) if part
            ),
            "SERENA_HOME": str(fakes.bin_dir.parent / "serena-home"),
            "SERENA_AGENT_RUNTIME_ROOT": environment[
                "SERENA_AGENT_RUNTIME_ROOT"
            ],
            "SERENA_AGENT_CLIENT": client_type,
            "SERENA_AGENT_INTERACTIVE": "0",
            "SERENA_AGENT_PROJECT_ROOT": str(root_hint or worktree),
            "SERENA_AGENT_CLEAR_BEFORE_CHILD": "0",
            f"SERENA_REAL_{client_type.upper()}": str(fakes.client),
            "FAKE_SERENA_INVOCATION_LOG": str(fakes.invocation_log),
            "FAKE_SERENA_START_LOG": str(fakes.server_start_log),
            "FAKE_CLIENT_ARGV_FILE": str(argv_file),
            "FAKE_CLIENT_READY_FILE": str(ready_file),
            "FAKE_CLIENT_EXIT_FILE": str(exit_file),
        }
    )
    process = subprocess.Popen(
        [sys.executable, str(LAUNCHER_SCRIPT), f"--integration-session={label}"],
        cwd=worktree,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    owned = _LauncherProcess(process, None, argv_file, ready_file, exit_file)
    owned_launchers.append(owned)
    try:
        owned.identity = _wait_for_owned_process_identity(process)
        return owned
    except BaseException:
        _terminate_owned_launcher(owned)
        raise


def _raw_ps_probe(pid: int) -> _RawPsProbe:
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-o", "lstart=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return _RawPsProbe(False, None, str(exc))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"ps exited {result.returncode}"
        return _RawPsProbe(False, None, detail)
    line = result.stdout.strip()
    if not line:
        return _RawPsProbe(True, None, "ps returned no process row")
    stat, separator, identity = line.partition(" ")
    identity = identity.strip()
    if not separator or "Z" in stat or not identity:
        return _RawPsProbe(True, None, f"unusable ps row: {line}")
    return _RawPsProbe(True, identity, "ok")


def _require_production_process_identity(test: unittest.TestCase) -> str:
    probe = _raw_ps_probe(os.getpid())
    if not probe.available:
        test.skipTest(f"raw process table unavailable: {probe.detail}")
    test.assertIsNotNone(probe.identity, probe.detail)
    actual = process_identity(os.getpid())
    test.assertIsNotNone(
        actual,
        "raw ps is usable but production process_identity rejected the current process",
    )
    if sys.platform == "darwin":
        test.assertRegex(actual or "", r"^darwin:\d+:\d{6}$")
    elif sys.platform.startswith("linux"):
        test.assertRegex(actual or "", r"^linux:\d+$")
    else:
        test.assertEqual(actual, f"ps:{probe.identity}")
    return actual


def _wait_for_owned_process_identity(process: subprocess.Popen[str]) -> str:
    identity: str | None = None

    def ready() -> bool:
        nonlocal identity
        if process.poll() is not None:
            output, _stderr = process.communicate()
            raise AssertionError(
                f"launcher exited before identity capture with {process.returncode}:\n{output}"
            )
        identity = process_identity(process.pid)
        return identity is not None

    _wait_until(ready, f"process identity for launcher PID {process.pid}")
    return identity  # type: ignore[return-value]


def _wait_for_file(path: Path, description: str) -> None:
    _wait_until(path.is_file, description)


def _wait_for_record(scope: Scope, *, lease_count: int):
    record = None

    def ready() -> bool:
        nonlocal record
        record = read_registry_record(scope)
        return record is not None and len(record.leases) == lease_count

    _wait_until(ready, f"registry to contain {lease_count} leases")
    return record


def _wait_for_identity(pid: int) -> str:
    identity: str | None = None

    def ready() -> bool:
        nonlocal identity
        identity = process_identity(pid)
        return identity is not None

    _wait_until(ready, f"process identity for PID {pid}")
    return identity  # type: ignore[return-value]


def _spawn_owned_sleep_process(owned: list[_OwnedPopen]) -> _OwnedPopen:
    item = _OwnedPopen(
        subprocess.Popen(["sleep", "60"], start_new_session=True)
    )
    owned.append(item)
    item.identity = _wait_for_identity(item.process.pid)
    return item


def _make_owned_process_record(
    scope: Scope, owned: list[_OwnedPopen]
) -> ServerRecord:
    server_process = _spawn_owned_sleep_process(owned)
    proxy_process = _spawn_owned_sleep_process(owned)
    watchdog_process = _spawn_owned_sleep_process(owned)
    identities = tuple(item.identity for item in owned[-3:])
    if not all(identity is not None for identity in identities):
        raise AssertionError("owned process identity capture did not complete")
    lease = Lease(
        "replacement-lease",
        "codex",
        os.getpid(),
        time.time(),
        process_identity(os.getpid()),
    )
    return ServerRecord(
        server_instance_id="replacement-generation",
        server_pid=server_process.process.pid,
        mcp_url="http://127.0.0.1:1/mcp",
        dashboard_url="http://127.0.0.1:1/dashboard",
        project_root=str(scope.project_root),
        context_profile=scope.context_profile,
        started_at=time.time(),
        leases={lease.lease_id: lease},
        upstream_mcp_url="http://127.0.0.1:2/mcp",
        proxy_pid=proxy_process.process.pid,
        watchdog_pid=watchdog_process.process.pid,
        server_identity=server_process.identity,
        proxy_identity=proxy_process.identity,
        watchdog_identity=watchdog_process.identity,
    )


def _reap_owned_processes(owned: list[_OwnedPopen]) -> None:
    failures: list[str] = []
    for item in owned:
        process = item.process
        if process.poll() is None:
            identity = item.identity or process_identity(process.pid)
            if identity is not None:
                item.identity = identity
                terminate_pid(process.pid, expected_identity=identity, timeout=0.5)
            else:
                first_probe = _raw_ps_probe(process.pid)
                second_probe = _raw_ps_probe(process.pid)
                if (
                    first_probe.available
                    and first_probe.identity is not None
                    and first_probe.identity == second_probe.identity
                ):
                    os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            first_probe = _raw_ps_probe(process.pid)
            second_probe = _raw_ps_probe(process.pid)
            expected_identity = item.identity or first_probe.identity
            if (
                expected_identity is not None
                and first_probe.identity == expected_identity
                and second_probe.identity == expected_identity
            ):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                failures.append(f"PID {process.pid} did not exit and reap")
        if process.returncode is None:
            failures.append(f"PID {process.pid} has no reaped return code")
    if failures:
        raise AssertionError("; ".join(failures))


def _wait_until(predicate, description: str, *, timeout: float = WAIT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {description}")


def _read_client_record(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def _exit_launcher(launcher: _LauncherProcess) -> None:
    launcher.exit_file.touch()
    try:
        output, _stderr = launcher.process.communicate(timeout=WAIT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError("launcher did not exit after its fake client was released") from exc
    if launcher.process.returncode != 0:
        raise AssertionError(
            f"launcher exited with {launcher.process.returncode}:\n{output}"
        )


def _capture_record(
    managed: set[_ManagedProcess],
    record: ServerRecord,
    *,
    require_watchdog: bool = True,
) -> None:
    values = (
        (record.server_pid, record.server_identity),
        (record.proxy_pid, record.proxy_identity),
        (record.watchdog_pid, record.watchdog_identity),
    )
    for pid, identity in values:
        if pid is not None and identity is not None:
            managed.add(_ManagedProcess(pid, identity))
    required_values = values if require_watchdog else values[:2]
    if not all(
        pid is not None and identity is not None for pid, identity in required_values
    ):
        raise AssertionError("healthy registry record lacks a managed PID/start identity")


def _require_current_record(scope: Scope) -> ServerRecord:
    record = _blocking_registry_snapshot(scope)
    if record is None:
        raise AssertionError("scope registry disappeared before process ownership capture")
    return record


def _blocking_registry_snapshot(scope: Scope) -> ServerRecord | None:
    """Read one authoritative snapshot, waiting for the scope lock if necessary."""

    with locked_registry(scope) as registry:
        return registry.record


def _assert_managed_processes_alive(
    test: unittest.TestCase, managed: set[_ManagedProcess]
) -> None:
    for item in managed:
        test.assertEqual(process_identity(item.pid), item.identity)


def _wait_for_processes_dead(managed: set[_ManagedProcess]) -> None:
    for item in managed:
        _wait_until(
            lambda item=item: process_identity(item.pid) != item.identity,
            f"identity-matched managed PID {item.pid} to stop",
        )


def _unaccounted_resource_warnings(
    caught: list[warnings.WarningMessage],
    expected_processes: set[_ManagedProcess],
) -> list[str]:
    unexpected: list[str] = []
    for warning in caught:
        rendered = f"{warning.category.__name__}: {warning.message}"
        if warning.category is not ResourceWarning:
            unexpected.append(rendered)
            continue
        match = re.fullmatch(
            r"subprocess (?P<pid>\d+) is still running",
            str(warning.message),
        )
        if match is None:
            unexpected.append(rendered)
            continue
        pid = int(match.group("pid"))
        owned_identities = {
            item.identity for item in expected_processes if item.pid == pid
        }
        if not owned_identities:
            unexpected.append(rendered)
            continue
        current_identity = process_identity(pid)
        if current_identity in owned_identities:
            unexpected.append(rendered)
    return unexpected


def _cleanup_launchers_and_scopes(
    launchers: list[_LauncherProcess],
    roots: list[Path],
    managed: set[_ManagedProcess],
) -> None:
    for launcher in launchers:
        _terminate_owned_launcher(launcher)
    for root in roots:
        _cleanup_scope(Scope(root), managed)
    _terminate_managed_processes(managed)


def _terminate_owned_launcher(launcher: _LauncherProcess) -> None:
    process = launcher.process
    if process.poll() is None:
        identity = launcher.identity or process_identity(process.pid)
        if identity is not None:
            launcher.identity = identity
            terminate_pid(process.pid, expected_identity=identity, timeout=1.0)
        else:
            first_probe = _raw_ps_probe(process.pid)
            second_probe = _raw_ps_probe(process.pid)
            if (
                first_probe.available
                and first_probe.identity is not None
                and first_probe.identity == second_probe.identity
            ):
                os.killpg(process.pid, signal.SIGTERM)
    try:
        process.communicate(timeout=5.0)
    except subprocess.TimeoutExpired:
        identity = launcher.identity or process_identity(process.pid)
        if identity is not None:
            terminate_pid(process.pid, expected_identity=identity, timeout=0.2)
        process.communicate(timeout=2.0)


def _cleanup_scope(scope: Scope, managed: set[_ManagedProcess]) -> None:
    consecutive_empty_snapshots = 0
    for _attempt in range(8):
        record = _blocking_registry_snapshot(scope)
        if record is None:
            _terminate_managed_processes(managed)
            _wait_for_processes_dead(managed)
            consecutive_empty_snapshots += 1
            if consecutive_empty_snapshots >= 2:
                return
            time.sleep(0.02)
            continue
        consecutive_empty_snapshots = 0
        _capture_record(managed, record)
        for lease_id in tuple(record.leases):
            try:
                release_lease_and_shutdown_if_empty(
                    scope, lease_id, record.server_instance_id
                )
            except BaseException:
                break
        remaining = _blocking_registry_snapshot(scope)
        if remaining is None:
            _terminate_managed_processes(managed)
            _wait_for_processes_dead(managed)
            continue
        if remaining.server_instance_id != record.server_instance_id:
            continue
        _capture_record(managed, remaining)
        _terminate_managed_processes(managed)
        _wait_for_processes_dead(managed)
        with locked_registry(scope) as registry:
            if (
                registry.record is not None
                and registry.record.server_instance_id == remaining.server_instance_id
            ):
                registry.record = None
    if _blocking_registry_snapshot(scope) is not None:
        raise AssertionError("failed to clear the current temporary-scope generation")
    _wait_for_processes_dead(managed)


def _terminate_managed_processes(managed: set[_ManagedProcess]) -> None:
    for item in managed:
        if process_identity(item.pid) == item.identity:
            terminate_pid(item.pid, expected_identity=item.identity, timeout=1.0)


def _mcp_json_request(
    url: str,
    payload: dict[str, object],
    *,
    session_id: str | None = None,
    protocol_version: str | None = None,
) -> _McpResponse:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
        if protocol_version is None:
            raise ValueError("session requests require the negotiated MCP protocol version")
        headers["MCP-Protocol-Version"] = protocol_version
    request = Request(url, data=body, headers=headers, method="POST")
    with urlopen(request, timeout=10.0) as response:
        response_body = response.read().decode("utf-8")
        returned_session = response.headers.get("Mcp-Session-Id")
    return _McpResponse(_decode_mcp_body(response_body), returned_session)


def _negotiated_protocol(response: _McpResponse) -> str:
    result = response.payload.get("result")
    if not isinstance(result, dict):
        raise AssertionError(f"initialize response lacks a result object: {response.payload}")
    protocol_version = result.get("protocolVersion")
    if not isinstance(protocol_version, str) or not protocol_version:
        raise AssertionError(
            f"initialize response lacks a negotiated protocol version: {response.payload}"
        )
    return protocol_version


def _decode_mcp_body(body: str) -> dict[str, object]:
    if not body:
        return {}
    stripped = body.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    data_lines = [line.removeprefix("data: ") for line in body.splitlines() if line.startswith("data: ")]
    if not data_lines:
        raise AssertionError(f"MCP response was neither JSON nor SSE data: {body[:300]}")
    return json.loads(data_lines[-1])


def _mcp_delete(url: str, session_id: str, protocol_version: str) -> None:
    request = Request(
        url,
        headers={
            "Mcp-Session-Id": session_id,
            "MCP-Protocol-Version": protocol_version,
        },
        method="DELETE",
    )
    with urlopen(request, timeout=5.0) as response:
        if response.status != 200:
            raise AssertionError(f"proxy DELETE returned HTTP {response.status}")


if __name__ == "__main__":
    unittest.main()
