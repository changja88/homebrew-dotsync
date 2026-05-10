# Serena MCP Lifecycle Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the target lifecycle in `local_dev/docs/serena-mcp-lifecycle-spec.md` so scoped Serena MCP servers are reused while live leases exist, and stale/wrong/orphan same-scope processes are cleaned without touching other scopes.

**Architecture:** Keep the existing registry/lease/watchdog model. Add small focused helpers for scope ownership, shared termination, and Serena process discovery, then call them from `server.ensure_server()` and watchdog cleanup paths. Startup reconciliation is the main place that handles registry-less same-scope orphan Serena servers.

**Tech Stack:** Python 3 stdlib only, pytest, existing `local_dev.serena_mcp_management.serena_mcp` modules.

---

## File Structure

- Modify: `local_dev/serena_mcp_management/serena_mcp/registry.py`
  - Add a scope ownership helper for `ServerRecord`.
- Create: `local_dev/serena_mcp_management/serena_mcp/termination.py`
  - Own process-group termination, individual PID fallback, wait, and SIGKILL escalation.
- Create: `local_dev/serena_mcp_management/serena_mcp/processes.py`
  - Own parsing and listing of `serena start-mcp-server` processes.
- Create: `local_dev/serena_mcp_management/serena_mcp/diagnostics.py`
  - Own structured lifecycle snapshots for inspection and tests.
- Modify: `local_dev/serena_mcp_management/serena_mcp/paths.py`
  - Own client-to-Serena-context mapping used by both server startup and process matching.
- Modify: `local_dev/serena_mcp_management/serena_mcp/server.py`
  - Use wrong-scope registry protection, shared termination, and startup orphan reconciliation.
- Modify: `local_dev/serena_mcp_management/serena_mcp/watchdog.py`
  - Use wrong-scope registry protection and shared termination.
- Test: `local_dev/tests/test_serena_registry.py`
  - Cover scope ownership helper.
- Test: `local_dev/tests/test_serena_termination.py`
  - Cover SIGTERM/SIGKILL and fallback behavior.
- Test: `local_dev/tests/test_serena_processes.py`
  - Cover Serena process command parsing and fail-closed behavior.
- Test: `local_dev/tests/test_serena_diagnostics.py`
  - Cover registry, lease, stale lease, and orphan snapshot fields.
- Modify: `local_dev/tests/test_serena_server.py`
  - Cover wrong-scope registry protection and startup same-scope orphan cleanup.
- Modify: `local_dev/tests/test_serena_watchdog.py`
  - Cover wrong-scope registry protection in watchdog cleanup paths.

Run all commands from the repository root: `/Users/hyun/Desktop/homebrew-dotsync`.

## Task 1: Scope Ownership Guard

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_mcp/registry.py`
- Modify: `local_dev/tests/test_serena_registry.py`

- [ ] **Step 1: Write failing tests for record scope ownership**

Append these tests to `local_dev/tests/test_serena_registry.py`:

```python
from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.registry import (
    Lease,
    ServerRecord,
    record_belongs_to_scope,
)


def _record_for_scope(scope: Scope) -> ServerRecord:
    return ServerRecord(
        server_pid=111,
        mcp_url="http://127.0.0.1:9000/mcp",
        dashboard_url="http://127.0.0.1:24000",
        project_root=str(scope.project_root),
        client_type=scope.client_type,
        started_at=1.0,
        leases={"lease": Lease("lease", 222, 1.0)},
        upstream_mcp_url="http://127.0.0.1:9001/mcp",
        proxy_pid=333,
    )


def test_record_belongs_to_scope_accepts_matching_scope(tmp_path):
    scope = Scope(tmp_path / "repo", "codex")

    assert record_belongs_to_scope(_record_for_scope(scope), scope) is True


def test_record_belongs_to_scope_rejects_wrong_project(tmp_path):
    scope = Scope(tmp_path / "repo", "codex")
    record = _record_for_scope(Scope(tmp_path / "other", "codex"))

    assert record_belongs_to_scope(record, scope) is False


def test_record_belongs_to_scope_rejects_wrong_client(tmp_path):
    scope = Scope(tmp_path / "repo", "codex")
    record = _record_for_scope(Scope(tmp_path / "repo", "claude"))

    assert record_belongs_to_scope(record, scope) is False
```

- [ ] **Step 2: Run targeted tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_registry.py::test_record_belongs_to_scope_accepts_matching_scope local_dev/tests/test_serena_registry.py::test_record_belongs_to_scope_rejects_wrong_project local_dev/tests/test_serena_registry.py::test_record_belongs_to_scope_rejects_wrong_client -q
```

Expected: FAIL with `ImportError` or `AttributeError` for `record_belongs_to_scope`.

- [ ] **Step 3: Implement `record_belongs_to_scope`**

Add to `local_dev/serena_mcp_management/serena_mcp/registry.py`:

```python
def record_belongs_to_scope(record: ServerRecord, scope: Scope) -> bool:
    """Return true when a registry record belongs to the current scope."""

    return (
        record.project_root == str(scope.project_root)
        and record.client_type == scope.client_type
    )
```

- [ ] **Step 4: Run targeted tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_registry.py::test_record_belongs_to_scope_accepts_matching_scope local_dev/tests/test_serena_registry.py::test_record_belongs_to_scope_rejects_wrong_project local_dev/tests/test_serena_registry.py::test_record_belongs_to_scope_rejects_wrong_client -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add local_dev/serena_mcp_management/serena_mcp/registry.py local_dev/tests/test_serena_registry.py
git commit -m "feat: add serena registry scope ownership"
```

## Task 2: Wrong-Scope Registry Safety

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_mcp/server.py`
- Modify: `local_dev/serena_mcp_management/serena_mcp/watchdog.py`
- Modify: `local_dev/tests/test_serena_server.py`
- Modify: `local_dev/tests/test_serena_watchdog.py`

- [ ] **Step 1: Write failing server test for wrong-project registry**

Append to `local_dev/tests/test_serena_server.py`:

```python
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
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._start_healthy_server", lambda scope_arg, lease_arg: replacement)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.ensure_watchdog", lambda scope_arg: None)

    assert ensure_server(scope, lease) == replacement
    assert terminated == []
```

- [ ] **Step 2: Write failing watchdog test for wrong-client registry**

Append to `local_dev/tests/test_serena_watchdog.py`:

```python
def test_cleanup_once_discards_wrong_client_record_without_terminating_pids(monkeypatch, tmp_path):
    scope = Scope(tmp_path / "repo", "codex")
    wrong_scope = Scope(tmp_path / "repo", "claude")
    terminated = []
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog._terminate_pid", terminated.append)

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
```

- [ ] **Step 3: Run targeted tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_server.py::test_ensure_server_discards_wrong_project_record_without_terminating_pids local_dev/tests/test_serena_watchdog.py::test_cleanup_once_discards_wrong_client_record_without_terminating_pids -q
```

Expected: FAIL because current code terminates recorded PIDs.

- [ ] **Step 4: Implement wrong-scope guards**

In `server.py`, import the helper:

```python
from local_dev.serena_mcp_management.serena_mcp.registry import (
    Lease,
    ServerRecord,
    locked_registry,
    record_belongs_to_scope,
    touch_lease,
)
```

Change `ensure_server()` so wrong-scope records are discarded without termination:

```python
        if registry.record and not record_belongs_to_scope(registry.record, scope):
            registry.record = None
        if registry.record and server_is_healthy(registry.record, scope):
            touch_lease(registry, fresh_lease)
            record = registry.record
        else:
            if registry.record:
                _terminate_record(registry.record)
                registry.record = None
            record = _start_healthy_server(scope, fresh_lease)
            registry.record = record
```

In `watchdog.py`, import `record_belongs_to_scope`:

```python
from local_dev.serena_mcp_management.serena_mcp.registry import (
    Lease,
    ServerRecord,
    locked_registry,
    record_belongs_to_scope,
)
```

At the top of `cleanup_once()`, `shutdown_if_no_leases()`, and `release_lease_and_shutdown_if_empty()`, after `registry.record is None` checks, discard wrong-scope records:

```python
        if not record_belongs_to_scope(registry.record, scope):
            registry.record = None
            return False
```

For `release_lease_and_shutdown_if_empty()`, return:

```python
            return ShutdownStats(
                sessions_before=0,
                sessions_closed=0,
                sessions_remaining=0,
                server_was_running=False,
                server_stopped=False,
            )
```

- [ ] **Step 5: Run targeted tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_server.py::test_ensure_server_discards_wrong_project_record_without_terminating_pids local_dev/tests/test_serena_watchdog.py::test_cleanup_once_discards_wrong_client_record_without_terminating_pids -q
```

Expected: PASS.

- [ ] **Step 6: Run existing lifecycle tests**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_server.py local_dev/tests/test_serena_watchdog.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```bash
git add local_dev/serena_mcp_management/serena_mcp/server.py local_dev/serena_mcp_management/serena_mcp/watchdog.py local_dev/tests/test_serena_server.py local_dev/tests/test_serena_watchdog.py
git commit -m "fix: protect serena wrong-scope registry records"
```

## Task 3: Shared Termination Primitive

**Files:**
- Create: `local_dev/serena_mcp_management/serena_mcp/termination.py`
- Create: `local_dev/tests/test_serena_termination.py`
- Modify: `local_dev/serena_mcp_management/serena_mcp/server.py`
- Modify: `local_dev/serena_mcp_management/serena_mcp/watchdog.py`
- Modify: `local_dev/tests/test_serena_server.py`
- Modify: `local_dev/tests/test_serena_watchdog.py`

- [ ] **Step 1: Write failing termination tests**

Create `local_dev/tests/test_serena_termination.py`:

```python
import signal

from local_dev.serena_mcp_management.serena_mcp.termination import terminate_pid


def test_terminate_pid_sends_sigterm_then_sigkill_when_process_survives(monkeypatch):
    calls = []
    alive_checks = iter([True, True, False])
    sleeps = []

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.os.killpg",
        lambda pid, sig: calls.append(("killpg", pid, sig)),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.pid_is_alive",
        lambda pid: next(alive_checks),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.time.time",
        iter([0.0, 0.1, 0.2, 10.0]).__next__,
    )

    terminate_pid(123, timeout=0.15)

    assert calls == [
        ("killpg", 123, signal.SIGTERM),
        ("killpg", 123, signal.SIGKILL),
    ]
    assert sleeps == [0.1]


def test_terminate_pid_falls_back_to_individual_pid_on_permission_error(monkeypatch):
    calls = []

    def fake_killpg(pid, sig):
        calls.append(("killpg", pid, sig))
        raise PermissionError("no process group permission")

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.termination.os.killpg", fake_killpg)
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.termination.os.kill",
        lambda pid, sig: calls.append(("kill", pid, sig)),
    )
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.termination.pid_is_alive", lambda pid: False)

    terminate_pid(123)

    assert calls == [
        ("killpg", 123, signal.SIGTERM),
        ("kill", 123, signal.SIGTERM),
    ]
```

- [ ] **Step 2: Run targeted tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_termination.py -q
```

Expected: FAIL because `termination.py` does not exist.

- [ ] **Step 3: Implement `termination.py`**

Create `local_dev/serena_mcp_management/serena_mcp/termination.py`:

```python
"""Shared process termination helpers for Serena MCP lifecycle."""
from __future__ import annotations

import os
import signal
import time

from local_dev.serena_mcp_management.serena_mcp.health import pid_is_alive


def terminate_pid(pid: int, *, timeout: float = 5.0) -> None:
    """Terminate a process group, falling back to PID kill and SIGKILL."""

    if pid <= 0:
        return
    if not _send(pid, signal.SIGTERM):
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_is_alive(pid):
            return
        time.sleep(0.1)
    _send(pid, signal.SIGKILL)


def _send(pid: int, sig: signal.Signals) -> bool:
    try:
        os.killpg(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        try:
            os.kill(pid, sig)
            return True
        except ProcessLookupError:
            return False
```

- [ ] **Step 4: Write failing call-site delegation tests**

Append to `local_dev/tests/test_serena_server.py`:

```python
def test_server_terminate_record_delegates_to_shared_termination(monkeypatch):
    terminated = []
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.terminate_pid", terminated.append)

    server._terminate_record(ServerRecord(
        server_pid=111,
        mcp_url="http://127.0.0.1:9000/mcp",
        dashboard_url="http://127.0.0.1:24000",
        project_root="/repo",
        client_type="codex",
        started_at=1.0,
        leases={},
        upstream_mcp_url="http://127.0.0.1:9001/mcp",
        proxy_pid=222,
    ))

    assert terminated == [222, 111]
```

Append to `local_dev/tests/test_serena_watchdog.py`:

```python
def test_watchdog_terminate_record_delegates_to_shared_termination(monkeypatch):
    terminated = []
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.watchdog.terminate_pid", terminated.append)

    from local_dev.serena_mcp_management.serena_mcp.watchdog import _terminate_record

    _terminate_record(ServerRecord(
        server_pid=111,
        mcp_url="http://127.0.0.1:9000/mcp",
        dashboard_url="http://127.0.0.1:24000",
        project_root="/repo",
        client_type="codex",
        started_at=1.0,
        leases={},
        upstream_mcp_url="http://127.0.0.1:9001/mcp",
        proxy_pid=222,
    ))

    assert terminated == [222, 111]
```

- [ ] **Step 5: Run call-site tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_server.py::test_server_terminate_record_delegates_to_shared_termination local_dev/tests/test_serena_watchdog.py::test_watchdog_terminate_record_delegates_to_shared_termination -q
```

Expected: FAIL because `server.py` and `watchdog.py` do not import `terminate_pid` yet.

- [ ] **Step 6: Use shared helper in server and watchdog**

In `server.py`, import:

```python
from local_dev.serena_mcp_management.serena_mcp.termination import terminate_pid
```

Replace `_terminate_pid()` body:

```python
def _terminate_pid(pid: int) -> None:
    terminate_pid(pid)
```

Remove now-unused `signal` import from `server.py`.

In `watchdog.py`, import:

```python
from local_dev.serena_mcp_management.serena_mcp.termination import terminate_pid
```

Replace `_terminate_pid()` body:

```python
def _terminate_pid(pid: int) -> None:
    terminate_pid(pid)
```

Remove now-unused `signal` import from `watchdog.py`.

- [ ] **Step 7: Run termination and lifecycle tests**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_termination.py local_dev/tests/test_serena_server.py local_dev/tests/test_serena_watchdog.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add local_dev/serena_mcp_management/serena_mcp/termination.py local_dev/serena_mcp_management/serena_mcp/server.py local_dev/serena_mcp_management/serena_mcp/watchdog.py local_dev/tests/test_serena_termination.py local_dev/tests/test_serena_server.py local_dev/tests/test_serena_watchdog.py
git commit -m "refactor: share serena process termination"
```

## Task 4: Serena Process Discovery

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_mcp/paths.py`
- Modify: `local_dev/serena_mcp_management/serena_mcp/server.py`
- Create: `local_dev/serena_mcp_management/serena_mcp/processes.py`
- Create: `local_dev/tests/test_serena_processes.py`
- Modify: `local_dev/tests/test_serena_paths.py`
- Modify: `local_dev/tests/test_serena_server.py`

- [ ] **Step 1: Move context mapping tests to paths**

Append to `local_dev/tests/test_serena_paths.py`:

```python
import pytest

from local_dev.serena_mcp_management.serena_mcp.paths import serena_context_for


def test_serena_context_maps_clients_to_serena_contexts():
    assert serena_context_for("codex") == "codex"
    assert serena_context_for("claude") == "claude-code"


def test_serena_context_rejects_unknown_client_type():
    with pytest.raises(ValueError, match="unsupported client type"):
        serena_context_for("unknown")
```

Remove `test_serena_context_maps_claude_client_to_claude_code()` from
`local_dev/tests/test_serena_server.py` after the new paths tests are green.
Also remove `serena_context_for` from the `from ...server import (...)` block
in `local_dev/tests/test_serena_server.py`; otherwise test collection will fail
after the function body is removed from `server.py`.

- [ ] **Step 2: Run context mapping tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_paths.py::test_serena_context_maps_clients_to_serena_contexts local_dev/tests/test_serena_paths.py::test_serena_context_rejects_unknown_client_type -q
```

Expected: FAIL with import error for missing `paths.serena_context_for`.

- [ ] **Step 3: Move `serena_context_for()` to paths**

Add to `local_dev/serena_mcp_management/serena_mcp/paths.py`:

```python
def serena_context_for(client_type: str) -> str:
    """Map a launcher client type to the Serena context name."""

    if client_type == "codex":
        return "codex"
    if client_type == "claude":
        return "claude-code"
    raise ValueError(f"unsupported client type: {client_type}")
```

In `server.py`, import it from paths:

```python
from local_dev.serena_mcp_management.serena_mcp.paths import Scope, serena_context_for, state_dir_for
```

Remove the old `serena_context_for()` function body from `server.py`. It is
acceptable that `server.serena_context_for` remains available as an imported
name, but tests should import it from `paths.py`.

- [ ] **Step 4: Run context mapping tests**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_paths.py local_dev/tests/test_serena_server.py::test_start_serena_process_redirects_output_to_scope_log -q
```

Expected: PASS.

- [ ] **Step 5: Write failing process parsing tests**

Create `local_dev/tests/test_serena_processes.py`:

```python
import shlex
from types import SimpleNamespace

from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.processes import (
    list_serena_mcp_processes,
    parse_serena_mcp_process,
    process_matches_scope,
)


def test_parse_serena_mcp_process_accepts_space_separated_options(tmp_path):
    command = (
        "/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server "
        f"--project {tmp_path} --context codex --port 12345"
    )

    proc = parse_serena_mcp_process(111, command)

    assert proc is not None
    assert proc.pid == 111
    assert proc.project_root == tmp_path.resolve()
    assert proc.context == "codex"


def test_parse_serena_mcp_process_accepts_equals_options(tmp_path):
    command = (
        "/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server "
        f"--project={tmp_path} --context=claude-code --port 12345"
    )

    proc = parse_serena_mcp_process(222, command)

    assert proc is not None
    assert proc.pid == 222
    assert proc.project_root == tmp_path.resolve()
    assert proc.context == "claude-code"


def test_parse_serena_mcp_process_accepts_quoted_project_with_spaces(tmp_path):
    project = tmp_path / "repo with spaces"
    command = (
        "/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server "
        f"--project {shlex.quote(str(project))} --context codex --port 12345"
    )

    proc = parse_serena_mcp_process(333, command)

    assert proc is not None
    assert proc.project_root == project.resolve()


def test_parse_serena_mcp_process_fails_closed_without_context(tmp_path):
    command = f"/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server --project {tmp_path}"

    assert parse_serena_mcp_process(444, command) is None


def test_parse_serena_mcp_process_fails_closed_on_bad_quoting():
    command = "/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server --project 'unterminated"

    assert parse_serena_mcp_process(555, command) is None


def test_process_matches_scope_uses_canonical_project_and_context(tmp_path):
    scope = Scope(tmp_path / "repo", "claude")
    command = (
        "/usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server "
        f"--project {scope.project_root} --context claude-code"
    )
    proc = parse_serena_mcp_process(666, command)

    assert proc is not None
    assert process_matches_scope(proc, scope) is True
    assert process_matches_scope(proc, Scope(scope.project_root, "codex")) is False


def test_list_serena_mcp_processes_ignores_unparseable_rows(monkeypatch, tmp_path):
    output = (
        "111 /usr/bin/python /Users/hyun/.local/bin/serena start-mcp-server "
        f"--project {tmp_path} --context codex\\n"
        "222 /usr/bin/python unrelated\\n"
        "bad row\\n"
    )

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.processes.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=output),
    )

    processes = list_serena_mcp_processes()

    assert [proc.pid for proc in processes] == [111]
```

- [ ] **Step 6: Run targeted process tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_processes.py -q
```

Expected: FAIL because `processes.py` does not exist.

- [ ] **Step 7: Implement process discovery**

Create `local_dev/serena_mcp_management/serena_mcp/processes.py`:

```python
"""Discover Serena MCP server processes for scope reconciliation."""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from local_dev.serena_mcp_management.serena_mcp.paths import Scope, serena_context_for


@dataclass(frozen=True, slots=True)
class SerenaMcpProcess:
    pid: int
    project_root: Path
    context: str
    command: str


def list_serena_mcp_processes() -> list[SerenaMcpProcess]:
    proc = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return []
    processes: list[SerenaMcpProcess] = []
    for line in proc.stdout.splitlines():
        pid_text, _, command = line.strip().partition(" ")
        if not pid_text.isdigit() or not command:
            continue
        parsed = parse_serena_mcp_process(int(pid_text), command)
        if parsed is not None:
            processes.append(parsed)
    return processes


def parse_serena_mcp_process(pid: int, command: str) -> SerenaMcpProcess | None:
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not _is_serena_start_mcp_server(argv):
        return None
    project = _option_value(argv, "--project")
    context = _option_value(argv, "--context")
    if not project or not context:
        return None
    return SerenaMcpProcess(
        pid=pid,
        project_root=Path(project).resolve(),
        context=context,
        command=command,
    )


def process_matches_scope(process: SerenaMcpProcess, scope: Scope) -> bool:
    return (
        process.project_root == scope.project_root
        and process.context == serena_context_for(scope.client_type)
    )


def _is_serena_start_mcp_server(argv: list[str]) -> bool:
    for index, value in enumerate(argv[:-1]):
        if Path(value).name == "serena" and argv[index + 1] == "start-mcp-server":
            return True
    return False


def _option_value(argv: list[str], option: str) -> str | None:
    prefix = option + "="
    for index, value in enumerate(argv):
        if value == option:
            if index + 1 >= len(argv):
                return None
            return argv[index + 1]
        if value.startswith(prefix):
            return value[len(prefix):]
    return None
```

- [ ] **Step 8: Run targeted tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_processes.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

Run:

```bash
git add local_dev/serena_mcp_management/serena_mcp/paths.py local_dev/serena_mcp_management/serena_mcp/server.py local_dev/serena_mcp_management/serena_mcp/processes.py local_dev/tests/test_serena_paths.py local_dev/tests/test_serena_server.py local_dev/tests/test_serena_processes.py
git commit -m "feat: discover scoped serena mcp processes"
```

## Task 5: Startup Orphan Reconciliation

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_mcp/server.py`
- Modify: `local_dev/tests/test_serena_server.py`

- [ ] **Step 1: Write failing test for registry-less same-scope orphan cleanup**

Append to `local_dev/tests/test_serena_server.py`:

```python
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
    )

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.list_serena_mcp_processes", lambda: [orphan])
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._terminate_pid", terminated.append)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._start_healthy_server", lambda scope_arg, lease_arg: replacement)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.ensure_watchdog", lambda scope_arg: None)

    assert ensure_server(scope, lease) == replacement
    assert terminated == [111]
```

- [ ] **Step 2: Write failing test preserving other-scope processes**

Append to `local_dev/tests/test_serena_server.py`:

```python
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
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._start_healthy_server", lambda scope_arg, lease_arg: replacement)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.ensure_watchdog", lambda scope_arg: None)

    ensure_server(scope, lease)

    assert terminated == []
```

- [ ] **Step 3: Write failing test preserving healthy registered server while cleaning extra same-scope upstream**

Append to `local_dev/tests/test_serena_server.py`:

```python
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
    )
    with locked_registry(scope) as registry:
        registry.record = record
    processes = [
        server.SerenaMcpProcess(111, scope.project_root, "codex", "registered"),
        server.SerenaMcpProcess(333, scope.project_root, "codex", "extra"),
    ]

    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.pid_is_alive", lambda pid: True)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.http_endpoint_alive", lambda url: True)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.dashboard_matches_project", lambda dashboard_url, project_root: True)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.list_serena_mcp_processes", lambda: processes)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server._terminate_pid", terminated.append)
    monkeypatch.setattr("local_dev.serena_mcp_management.serena_mcp.server.ensure_watchdog", lambda scope_arg: None)

    assert ensure_server(scope, lease).server_pid == 111
    assert terminated == [333]
```

- [ ] **Step 4: Run targeted tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_server.py::test_ensure_server_terminates_registryless_same_scope_orphan_before_start local_dev/tests/test_serena_server.py::test_ensure_server_preserves_other_project_and_other_client_processes local_dev/tests/test_serena_server.py::test_ensure_server_reuses_healthy_record_and_cleans_extra_same_scope_upstream -q
```

Expected: FAIL because `ensure_server()` does not scan process table and `server.SerenaMcpProcess` is not imported.

- [ ] **Step 5: Implement startup orphan reconciliation**

In `server.py`, import:

```python
from local_dev.serena_mcp_management.serena_mcp.processes import (
    SerenaMcpProcess,
    list_serena_mcp_processes,
    process_matches_scope,
)
```

Add helper:

```python
def _cleanup_same_scope_orphans(scope: Scope, *, preserve_server_pid: int | None) -> None:
    for process in list_serena_mcp_processes():
        if not process_matches_scope(process, scope):
            continue
        if preserve_server_pid is not None and process.pid == preserve_server_pid:
            continue
        _terminate_pid(process.pid)
```

Update `ensure_server()` so it reconciles before returning or starting:

```python
    with locked_registry(scope) as registry:
        fresh_lease = _fresh_lease(initial_lease)
        if registry.record and not record_belongs_to_scope(registry.record, scope):
            registry.record = None
        if registry.record and server_is_healthy(registry.record, scope):
            touch_lease(registry, fresh_lease)
            record = registry.record
            _cleanup_same_scope_orphans(scope, preserve_server_pid=record.server_pid)
        else:
            if registry.record:
                _terminate_record(registry.record)
                registry.record = None
            _cleanup_same_scope_orphans(scope, preserve_server_pid=None)
            record = _start_healthy_server(scope, fresh_lease)
            registry.record = record
```

- [ ] **Step 6: Run targeted tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_server.py::test_ensure_server_terminates_registryless_same_scope_orphan_before_start local_dev/tests/test_serena_server.py::test_ensure_server_preserves_other_project_and_other_client_processes local_dev/tests/test_serena_server.py::test_ensure_server_reuses_healthy_record_and_cleans_extra_same_scope_upstream -q
```

Expected: PASS.

- [ ] **Step 7: Run process and server suites**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_processes.py local_dev/tests/test_serena_server.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```bash
git add local_dev/serena_mcp_management/serena_mcp/server.py local_dev/tests/test_serena_server.py
git commit -m "fix: reconcile orphan serena mcp processes on startup"
```

## Task 6: Lifecycle Snapshot Diagnostics

**Files:**
- Create: `local_dev/serena_mcp_management/serena_mcp/diagnostics.py`
- Create: `local_dev/tests/test_serena_diagnostics.py`
- Modify: `local_dev/docs/serena-mcp-lifecycle-spec.md`

- [ ] **Step 1: Write failing diagnostics tests**

Create `local_dev/tests/test_serena_diagnostics.py`:

```python
from local_dev.serena_mcp_management.serena_mcp.diagnostics import snapshot_lifecycle
from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.registry import Lease, ServerRecord, locked_registry
from local_dev.serena_mcp_management.serena_mcp.processes import SerenaMcpProcess


def test_snapshot_lifecycle_reports_registry_and_stale_lease_counts(monkeypatch, tmp_path):
    scope = Scope(tmp_path / "repo", "codex")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=111,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(scope.project_root),
            client_type=scope.client_type,
            started_at=1.0,
            leases={
                "live": Lease("live", 1001, 95.0, "live identity"),
                "stale": Lease("stale", 1002, 1.0, "stale identity"),
            },
            watchdog_pid=333,
            upstream_mcp_url="http://127.0.0.1:9001/mcp",
            proxy_pid=222,
        )

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.list_serena_mcp_processes",
        lambda: [],
    )

    snapshot = snapshot_lifecycle(scope, now=100.0, stale_after_seconds=30.0)

    assert snapshot.project_root == str(scope.project_root)
    assert snapshot.client_type == "codex"
    assert snapshot.registry_path.endswith(".serena/dotsync-mcp/codex/registry.json")
    assert snapshot.registered_server_pid == 111
    assert snapshot.registered_proxy_pid == 222
    assert snapshot.registered_watchdog_pid == 333
    assert snapshot.lease_count == 2
    assert snapshot.stale_lease_count == 1
    assert snapshot.live_launcher_identities == ["live identity", "stale identity"]
    assert snapshot.same_scope_orphan_pids == []


def test_snapshot_lifecycle_reports_same_scope_orphan_candidates(monkeypatch, tmp_path):
    scope = Scope(tmp_path / "repo", "codex")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=111,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(scope.project_root),
            client_type=scope.client_type,
            started_at=1.0,
            leases={},
            upstream_mcp_url="http://127.0.0.1:9001/mcp",
            proxy_pid=222,
        )
    processes = [
        SerenaMcpProcess(111, scope.project_root, "codex", "registered"),
        SerenaMcpProcess(333, scope.project_root, "codex", "orphan"),
        SerenaMcpProcess(444, tmp_path / "other", "codex", "other project"),
    ]
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.list_serena_mcp_processes",
        lambda: processes,
    )

    snapshot = snapshot_lifecycle(scope, now=100.0, stale_after_seconds=30.0)

    assert snapshot.same_scope_orphan_pids == [333]
```

- [ ] **Step 2: Run diagnostics tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_diagnostics.py -q
```

Expected: FAIL because `diagnostics.py` does not exist.

- [ ] **Step 3: Implement diagnostics snapshot**

Create `local_dev/serena_mcp_management/serena_mcp/diagnostics.py`:

```python
"""Structured Serena MCP lifecycle diagnostics."""
from __future__ import annotations

from dataclasses import dataclass

from local_dev.serena_mcp_management.serena_mcp.paths import Scope
from local_dev.serena_mcp_management.serena_mcp.processes import (
    list_serena_mcp_processes,
    process_matches_scope,
)
from local_dev.serena_mcp_management.serena_mcp.registry import locked_registry, registry_path


@dataclass(frozen=True, slots=True)
class LifecycleSnapshot:
    project_root: str
    client_type: str
    registry_path: str
    registered_server_pid: int | None
    registered_proxy_pid: int | None
    registered_watchdog_pid: int | None
    lease_count: int
    stale_lease_count: int
    live_launcher_identities: list[str]
    same_scope_orphan_pids: list[int]


def snapshot_lifecycle(
    scope: Scope,
    *,
    now: float,
    stale_after_seconds: float,
) -> LifecycleSnapshot:
    """Return a scope-local snapshot for debugging lifecycle state."""

    with locked_registry(scope) as registry:
        record = registry.record
        registered_server_pid = record.server_pid if record is not None else None
        registered_proxy_pid = record.proxy_pid if record is not None else None
        registered_watchdog_pid = record.watchdog_pid if record is not None else None
        leases = record.leases if record is not None else {}
        stale_lease_count = sum(
            1
            for lease in leases.values()
            if now - lease.heartbeat_at > stale_after_seconds
        )
        identities = sorted(
            lease.launcher_identity
            for lease in leases.values()
            if lease.launcher_identity is not None
        )
    orphan_pids = [
        process.pid
        for process in list_serena_mcp_processes()
        if process_matches_scope(process, scope)
        and process.pid != registered_server_pid
    ]
    return LifecycleSnapshot(
        project_root=str(scope.project_root),
        client_type=scope.client_type,
        registry_path=str(registry_path(scope)),
        registered_server_pid=registered_server_pid,
        registered_proxy_pid=registered_proxy_pid,
        registered_watchdog_pid=registered_watchdog_pid,
        lease_count=len(leases),
        stale_lease_count=stale_lease_count,
        live_launcher_identities=identities,
        same_scope_orphan_pids=sorted(orphan_pids),
    )
```

- [ ] **Step 4: Run diagnostics tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_diagnostics.py -q
```

Expected: PASS.

- [ ] **Step 5: Update spec known gap after implementation**

After Tasks 1-6 are implemented, update `local_dev/docs/serena-mcp-lifecycle-spec.md`:

```markdown
## 현재 Known Gap

현재 lifecycle 코드는 registry, lease, watchdog, startup process reconciliation,
shared termination, lifecycle snapshot diagnostics를 기준으로 scope-local Serena
MCP 서버를 관리한다.

남은 gap:

- shell shim 적용 범위는 interactive no-argument `codex` / `claude` 호출로
  제한된다.
- process table parsing은 운영체제의 command text 표현에 의존하므로,
  project/context를 정확히 파싱할 수 없는 process는 fail closed로 보존한다.
```

- [ ] **Step 6: Commit diagnostics**

Run:

```bash
git add local_dev/serena_mcp_management/serena_mcp/diagnostics.py local_dev/tests/test_serena_diagnostics.py local_dev/docs/serena-mcp-lifecycle-spec.md
git commit -m "feat: add serena lifecycle diagnostics"
```

## Task 7: Final Verification

**Files:**
- Verify: `local_dev/serena_mcp_management/`
- Verify: `local_dev/tests/`
- Verify: `local_dev/docs/`

- [ ] **Step 1: Run full local_dev suite**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests -q
```

Expected: PASS. If the sandbox blocks local TCP binds with `PermissionError`, rerun the same command with the approved test escalation used for this repository.

- [ ] **Step 2: Run formatting/sanity checks**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 3: Confirm worktree state**

Run:

```bash
git status --short
```

Expected: no output. Any output means an implementation task was left
uncommitted; create the task-scoped commit before runtime promotion.

## Manual Runtime Promotion

Run this only after reviewed green tests and an explicit user approval, because
it mutates runtime files outside the repository by mirroring the launcher tree
and rewriting the managed block in `~/.zshrc`.

- [ ] **Step 1: Ask for approval to promote runtime shim**

Ask:

```text
Do you want to run `make -C local_dev install-shim` now to mirror the updated launcher and rewrite the managed ~/.zshrc block?
```

- [ ] **Step 2: Install shim after approval**

Run:

```bash
make -C local_dev install-shim
```

Expected:

```text
installed Serena zsh shim into /Users/hyun/.zshrc
backup written to /Users/hyun/.zshrc.dotsync-serena.bak
```

- [ ] **Step 3: Verify runtime mirror contains updated code**

Run:

```bash
rg -n "list_serena_mcp_processes|terminate_pid|record_belongs_to_scope|snapshot_lifecycle" /Users/hyun/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management
```

Expected: matches in the runtime mirror.

## Self-Review Checklist

- Scope coverage: wrong-scope registry, same-scope orphan, multiple leases, other-project preservation, other-client preservation, watchdog stale lease cleanup, shared termination, and fail-closed parsing are each covered by a task.
- Diagnostics coverage: lifecycle snapshot exposes registry path, registered PIDs, lease counts, stale lease counts, live launcher identities, and same-scope orphan candidates.
- No public `dotsync` files are modified.
- No runtime dependency is added.
- Tests are TDD-first for each behavior change.
- Runtime promotion uses `make -C local_dev install-shim` only after explicit approval.
