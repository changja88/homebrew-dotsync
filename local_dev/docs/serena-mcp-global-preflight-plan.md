# Serena MCP Global Preflight UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** preflight 박스에 전체 머신 기준 Serena MCP upstream process 수와 launcher-managed 부분집합 상태를 한 줄로 표시한다.

**Architecture:** 전역 현황은 `ps`에서 발견한 `serena start-mcp-server` process 목록을 기준으로 만든다. 각 process의 `--project`/`--context`를 scope로 역매핑한 뒤, 해당 scope registry를 read-only로 읽어 PID identity가 일치하는 경우만 managed로 센다. UI는 `ps[...] -> managed[...] . orphan[...] . leases[...] . stale[...]` 한 줄을 `serena` 행 바로 아래에 추가한다.

**Tech Stack:** Python 3.12 stdlib, pytest, existing `local_dev.serena_mcp_management` launcher modules.

---

## File Structure

- Modify: `local_dev/serena_mcp_management/serena_mcp/paths.py`
  - Serena context(`codex`, `claude-code`)를 launcher client type(`codex`, `claude`)으로 되돌리는 helper를 추가한다.
- Modify: `local_dev/serena_mcp_management/serena_mcp/processes.py`
  - 기존 tolerant scanner는 유지하고, preflight diagnostics가 scan 실패를 구분할 수 있는 strict scanner를 추가한다.
- Modify: `local_dev/serena_mcp_management/serena_mcp/registry.py`
  - preflight global scan이 다른 프로젝트 디렉터리에 새 state dir이나 lock 파일을 만들지 않도록 passive registry loader를 추가한다.
- Modify: `local_dev/serena_mcp_management/serena_mcp/diagnostics.py`
  - `GlobalLifecycleSnapshot`와 `snapshot_global_lifecycle()`을 추가한다.
- Modify: `local_dev/serena_mcp_management/ui.py`
  - MCP inventory 전용 컬러 formatter를 추가하고, 긴 one-line row가 박스 border를 넘지 않도록 렌더 width를 동적으로 계산한다.
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py`
  - `_preflight_box()`에 `serena mcp` 행을 추가한다.
- Test: `local_dev/tests/test_serena_diagnostics.py`
  - 전역 ps/managed/orphan/lease/stale 집계의 단위 테스트를 추가한다.
- Test: `local_dev/tests/test_ui_renderer.py`
  - MCP inventory formatter의 plain text와 risk 컬러링을 검증한다.
- Test: `local_dev/tests/test_launcher_phases.py`
  - preflight 박스에 새 행이 원하는 위치와 marker로 렌더링되는지 검증한다.

## Invariants

- `ps_server_count = managed_server_count + orphan_server_count`가 항상 성립해야 한다.
- `managed_server_count`는 `ps`에서 발견된 upstream Serena server의 부분집합이다.
- registry에만 있고 `ps`에 없는 dead server PID는 `managed`에 포함하지 않는다.
- proxy/watchdog process는 `ps_server_count`에 포함하지 않는다.
- preflight global scan은 registry 조회를 위해 다른 프로젝트에 새 `.serena/dotsync-mcp/...` 디렉터리를 만들면 안 된다.
- `orphan > 0` 또는 `stale > 0`이면 `serena mcp` 행은 warning marker를 쓴다.
- `ps` scan 자체가 실패하면 `ps[0 servers]`처럼 오해될 수 있는 값을 만들지 않고 `scan unavailable` warning으로 표시한다.

---

### Task 1: Add Read-Only Global Lifecycle Diagnostics

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_mcp/paths.py`
- Modify: `local_dev/serena_mcp_management/serena_mcp/processes.py`
- Modify: `local_dev/serena_mcp_management/serena_mcp/registry.py`
- Modify: `local_dev/serena_mcp_management/serena_mcp/diagnostics.py`
- Test: `local_dev/tests/test_serena_diagnostics.py`

- [ ] **Step 1: Write failing tests for global lifecycle counts**

Append these tests to `local_dev/tests/test_serena_diagnostics.py`:

```python
def test_snapshot_global_lifecycle_counts_ps_managed_orphan_and_leases(monkeypatch, tmp_path):
    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )

    codex_scope = Scope(tmp_path / "repo-a", "codex")
    claude_scope = Scope(tmp_path / "repo-b", "claude")

    with locked_registry(codex_scope) as registry:
        registry.record = ServerRecord(
            server_pid=111,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(codex_scope.project_root),
            client_type=codex_scope.client_type,
            started_at=1.0,
            leases={
                "live-a": Lease("live-a", 1001, 95.0, "launcher-a"),
                "stale-a": Lease("stale-a", 1002, 1.0, "launcher-b"),
            },
            upstream_mcp_url="http://127.0.0.1:9000/mcp",
            proxy_pid=112,
            server_identity="identity-111",
            proxy_identity="identity-112",
        )
    with locked_registry(claude_scope) as registry:
        registry.record = ServerRecord(
            server_pid=333,
            mcp_url="http://127.0.0.1:9010/mcp",
            dashboard_url="http://127.0.0.1:24010",
            project_root=str(claude_scope.project_root),
            client_type=claude_scope.client_type,
            started_at=2.0,
            leases={"live-b": Lease("live-b", 1003, 99.0, "launcher-c")},
            upstream_mcp_url="http://127.0.0.1:9010/mcp",
            proxy_pid=334,
            server_identity="identity-333",
            proxy_identity="identity-334",
        )

    processes = [
        SerenaMcpProcess(111, codex_scope.project_root, "codex", "managed codex", "identity-111"),
        SerenaMcpProcess(222, tmp_path / "repo-c", "codex", "orphan codex", "identity-222"),
        SerenaMcpProcess(333, claude_scope.project_root, "claude-code", "managed claude", "identity-333"),
    ]
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        lambda: processes,
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.ps_server_count == 3
    assert snapshot.managed_server_count == 2
    assert snapshot.orphan_server_count == 1
    assert snapshot.lease_count == 3
    assert snapshot.stale_lease_count == 1
```

Append this test to ensure registry reads stay passive:

```python
def test_snapshot_global_lifecycle_does_not_create_registry_dirs_for_orphans(monkeypatch, tmp_path):
    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )

    orphan_project = tmp_path / "repo-orphan"
    orphan_project.mkdir()
    processes = [
        SerenaMcpProcess(222, orphan_project, "codex", "orphan codex", "identity-222"),
    ]
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        lambda: processes,
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.ps_server_count == 1
    assert snapshot.managed_server_count == 0
    assert snapshot.orphan_server_count == 1
    assert not (orphan_project / ".serena" / "dotsync-mcp").exists()
```

Append this test for identity mismatch:

```python
def test_snapshot_global_lifecycle_requires_matching_server_identity(monkeypatch, tmp_path):
    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )

    scope = Scope(tmp_path / "repo", "codex")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=111,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(scope.project_root),
            client_type=scope.client_type,
            started_at=1.0,
            leases={"live": Lease("live", 1001, 95.0, "launcher-a")},
            upstream_mcp_url="http://127.0.0.1:9000/mcp",
            proxy_pid=112,
            server_identity="old-identity",
            proxy_identity="identity-112",
        )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        lambda: [
            SerenaMcpProcess(111, scope.project_root, "codex", "pid reused", "new-identity"),
        ],
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.ps_server_count == 1
    assert snapshot.managed_server_count == 0
    assert snapshot.orphan_server_count == 1
    assert snapshot.lease_count == 0
    assert snapshot.stale_lease_count == 0
```

Append this test for registry-only dead records:

```python
def test_snapshot_global_lifecycle_ignores_registry_records_not_seen_in_ps(monkeypatch, tmp_path):
    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )

    scope = Scope(tmp_path / "repo", "codex")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=111,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(scope.project_root),
            client_type=scope.client_type,
            started_at=1.0,
            leases={"stale": Lease("stale", 1001, 1.0, "launcher-a")},
            upstream_mcp_url="http://127.0.0.1:9000/mcp",
            proxy_pid=112,
            server_identity="identity-111",
            proxy_identity="identity-112",
        )
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        lambda: [],
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.ps_server_count == 0
    assert snapshot.managed_server_count == 0
    assert snapshot.orphan_server_count == 0
    assert snapshot.lease_count == 0
    assert snapshot.stale_lease_count == 0
```

Append this test for passive registry reads:

```python
def test_snapshot_global_lifecycle_does_not_create_missing_registry_lock(monkeypatch, tmp_path):
    import json

    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )
    from local_dev.serena_mcp_management.serena_mcp.registry import registry_path

    scope = Scope(tmp_path / "repo", "codex")
    path = registry_path(scope)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "version": 1,
        "record": {
            "server_pid": 111,
            "mcp_url": "http://127.0.0.1:9000/mcp",
            "dashboard_url": "http://127.0.0.1:24000",
            "project_root": str(scope.project_root),
            "client_type": scope.client_type,
            "started_at": 1.0,
            "leases": {},
            "upstream_mcp_url": "http://127.0.0.1:9000/mcp",
            "proxy_pid": 112,
            "server_identity": "identity-111",
            "proxy_identity": "identity-112",
        },
    }))
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        lambda: [
            SerenaMcpProcess(111, scope.project_root, "codex", "managed", "identity-111"),
        ],
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.managed_server_count == 1
    assert not path.with_name("registry.lock").exists()
```

Append this test for scan failure:

```python
def test_snapshot_global_lifecycle_reports_scan_failure(monkeypatch):
    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )
    from local_dev.serena_mcp_management.serena_mcp.processes import ProcessScanError

    def fail_scan():
        raise ProcessScanError("ps failed")

    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        fail_scan,
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.scan_failed is True
    assert snapshot.ps_server_count == 0
    assert snapshot.managed_server_count == 0
    assert snapshot.orphan_server_count == 0
```

Append this test for malformed registry content:

```python
def test_snapshot_global_lifecycle_treats_malformed_registry_as_orphan(monkeypatch, tmp_path):
    import json

    from local_dev.serena_mcp_management.serena_mcp.diagnostics import (
        snapshot_global_lifecycle,
    )
    from local_dev.serena_mcp_management.serena_mcp.registry import registry_path

    scope = Scope(tmp_path / "repo", "codex")
    path = registry_path(scope)
    path.parent.mkdir(parents=True)
    path.with_name("registry.lock").write_text("")
    path.write_text(json.dumps({
        "version": 1,
        "record": {
            "server_pid": 111,
            "mcp_url": "http://127.0.0.1:9000/mcp",
            "dashboard_url": "http://127.0.0.1:24000",
            "project_root": str(scope.project_root),
            "client_type": scope.client_type,
            "started_at": 1.0,
            "leases": ["not", "a", "dict"],
            "server_identity": "identity-111",
        },
    }))
    monkeypatch.setattr(
        "local_dev.serena_mcp_management.serena_mcp.diagnostics.scan_serena_mcp_processes",
        lambda: [
            SerenaMcpProcess(111, scope.project_root, "codex", "managed", "identity-111"),
        ],
    )

    snapshot = snapshot_global_lifecycle(now=100.0, stale_after_seconds=30.0)

    assert snapshot.ps_server_count == 1
    assert snapshot.managed_server_count == 0
    assert snapshot.orphan_server_count == 1
```

- [ ] **Step 2: Run diagnostics tests and confirm failure**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_diagnostics.py -q
```

Expected: failure because `snapshot_global_lifecycle` does not exist.

- [ ] **Step 3: Add reverse context mapping**

Modify `local_dev/serena_mcp_management/serena_mcp/paths.py`:

```python
def client_type_for_serena_context(context: str) -> str | None:
    """Map a Serena context name back to a launcher client type."""

    if context == "codex":
        return "codex"
    if context == "claude-code":
        return "claude"
    return None
```

- [ ] **Step 4: Add strict process scanner for diagnostics**

Modify `local_dev/serena_mcp_management/serena_mcp/processes.py` so the existing tolerant function keeps its behavior while diagnostics can distinguish scan failure:

```python
class ProcessScanError(RuntimeError):
    """Raised when the process table cannot be scanned."""
```

Replace the body of `list_serena_mcp_processes()` with:

```python
def list_serena_mcp_processes() -> list[SerenaMcpProcess]:
    """Return parseable Serena MCP server processes, or [] when ps is unavailable."""

    try:
        return scan_serena_mcp_processes()
    except ProcessScanError:
        return []
```

Add:

```python
def scan_serena_mcp_processes() -> list[SerenaMcpProcess]:
    """Return parseable Serena MCP server processes, raising when ps is unavailable."""

    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        raise ProcessScanError("failed to run ps") from exc
    if proc.returncode != 0:
        raise ProcessScanError(f"ps exited with {proc.returncode}")
    processes: list[SerenaMcpProcess] = []
    for line in proc.stdout.splitlines():
        pid_text, _, command = line.strip().partition(" ")
        if not pid_text.isdigit() or not command:
            continue
        parsed = parse_serena_mcp_process(int(pid_text), command)
        if parsed is not None:
            processes.append(SerenaMcpProcess(
                pid=parsed.pid,
                project_root=parsed.project_root,
                context=parsed.context,
                command=parsed.command,
                identity=process_identity(parsed.pid),
            ))
    return processes
```

- [ ] **Step 5: Add passive registry loader**

Modify `_load_record()` in `local_dev/serena_mcp_management/serena_mcp/registry.py` to fail closed on malformed registry shapes:

```python
    except (json.JSONDecodeError, TypeError, KeyError, AttributeError):
        return None
```

Add this function to `local_dev/serena_mcp_management/serena_mcp/registry.py`:

```python
def read_registry_record(scope: Scope) -> ServerRecord | None:
    """Read a registry record without creating state directories or lock files."""

    path = registry_path(scope)
    if not path.exists():
        return None
    lock = lock_path(scope)
    if not lock.exists():
        return _load_record(path)
    try:
        with lock.open("r") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return None
            try:
                return _load_record(path)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return None
```

- [ ] **Step 6: Add global lifecycle dataclass and aggregation**

Modify `local_dev/serena_mcp_management/serena_mcp/diagnostics.py` imports while preserving the existing `snapshot_lifecycle()` dependencies:

```python
from local_dev.serena_mcp_management.serena_mcp.paths import (
    Scope,
    client_type_for_serena_context,
)
from local_dev.serena_mcp_management.serena_mcp.processes import (
    list_serena_mcp_processes,
    process_matches_scope,
    ProcessScanError,
    scan_serena_mcp_processes,
)
from local_dev.serena_mcp_management.serena_mcp.registry import (
    locked_registry,
    read_registry_record,
    record_belongs_to_scope,
    registry_path,
)
```

Add:

```python
@dataclass(frozen=True, slots=True)
class GlobalLifecycleSnapshot:
    """Machine-wide Serena MCP inventory for preflight diagnostics."""

    ps_server_count: int
    managed_server_count: int
    orphan_server_count: int
    lease_count: int
    stale_lease_count: int
    scan_failed: bool = False
```

Add:

```python
def snapshot_global_lifecycle(
    *,
    now: float,
    stale_after_seconds: float,
) -> GlobalLifecycleSnapshot:
    """Return machine-wide Serena MCP counts with managed as a ps subset."""

    try:
        processes = scan_serena_mcp_processes()
    except ProcessScanError:
        return GlobalLifecycleSnapshot(
            ps_server_count=0,
            managed_server_count=0,
            orphan_server_count=0,
            lease_count=0,
            stale_lease_count=0,
            scan_failed=True,
        )
    managed_server_count = 0
    lease_count = 0
    stale_lease_count = 0

    for process in processes:
        client_type = client_type_for_serena_context(process.context)
        if client_type is None or process.identity is None:
            continue
        scope = Scope(process.project_root, client_type)
        record = read_registry_record(scope)
        if record is None or not record_belongs_to_scope(record, scope):
            continue
        if record.server_pid != process.pid:
            continue
        if record.server_identity is None or record.server_identity != process.identity:
            continue

        managed_server_count += 1
        lease_count += len(record.leases)
        stale_lease_count += sum(
            1
            for lease in record.leases.values()
            if now - lease.heartbeat_at > stale_after_seconds
        )

    ps_server_count = len(processes)
    return GlobalLifecycleSnapshot(
        ps_server_count=ps_server_count,
        managed_server_count=managed_server_count,
        orphan_server_count=ps_server_count - managed_server_count,
        lease_count=lease_count,
        stale_lease_count=stale_lease_count,
    )
```

- [ ] **Step 7: Run diagnostics tests and confirm pass**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_serena_diagnostics.py -q
```

Expected: all diagnostics tests pass.

---

### Task 2: Add MCP Inventory Formatter With Risk Coloring

**Files:**
- Modify: `local_dev/serena_mcp_management/ui.py`
- Test: `local_dev/tests/test_ui_renderer.py`

- [ ] **Step 1: Write failing formatter tests**

Modify the import in `local_dev/tests/test_ui_renderer.py`:

```python
from local_dev.serena_mcp_management.ui import (
    BoxModel,
    BoxRenderer,
    Item,
    PINK,
    PURPLE,
    render_box,
    style_mcp_inventory,
)
```

Append:

```python
def test_style_mcp_inventory_renders_single_line_plain_text():
    text = style_mcp_inventory(
        ps_servers=3,
        managed_servers=2,
        orphan_servers=1,
        leases=3,
        stale_leases=1,
    )

    assert _strip_ansi(text) == (
        "ps[3 servers] -> managed[2 servers] . "
        "orphan[1] . leases[3] . stale[1]"
    )
```

Append:

```python
def test_style_mcp_inventory_highlights_orphan_and_stale_when_nonzero():
    text = style_mcp_inventory(
        ps_servers=3,
        managed_servers=2,
        orphan_servers=1,
        leases=3,
        stale_leases=1,
    )

    assert "\x1b[33m" in text
    assert "orphan" in text
    assert "stale" in text
```

Append:

```python
def test_render_box_expands_border_for_long_mcp_inventory_row():
    model = BoxModel(
        phase="preflight",
        title="codex",
        items=[
            Item(
                id="serena-mcp",
                label="serena mcp",
                value=style_mcp_inventory(
                    ps_servers=123,
                    managed_servers=122,
                    orphan_servers=1,
                    leases=987,
                    stale_leases=1,
                ),
                status="warn",
            ),
        ],
    )

    plain_lines = _strip_ansi(render_box(model)).splitlines()
    border_width = max(len(line.strip()) for line in plain_lines if set(line.strip()) == {"─"})
    item_width = max(len(line.strip()) for line in plain_lines if "serena mcp" in line)

    assert border_width >= item_width
```

- [ ] **Step 2: Run formatter tests and confirm failure**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_ui_renderer.py::test_style_mcp_inventory_renders_single_line_plain_text local_dev/tests/test_ui_renderer.py::test_style_mcp_inventory_highlights_orphan_and_stale_when_nonzero local_dev/tests/test_ui_renderer.py::test_render_box_expands_border_for_long_mcp_inventory_row -q
```

Expected: import failure because `style_mcp_inventory` does not exist.

- [ ] **Step 3: Implement formatter**

Add to `local_dev/serena_mcp_management/ui.py` after `style_count`:

```python
def style_mcp_inventory(
    *,
    ps_servers: int,
    managed_servers: int,
    orphan_servers: int,
    leases: int,
    stale_leases: int,
) -> str:
    """Colorize the global Serena MCP preflight inventory."""

    def normal(label: str, value: int, suffix: str = "") -> str:
        return f"{_ansi(PURPLE, label)}[{_ansi(PINK, str(value))}{suffix}]"

    def risk(label: str, value: int) -> str:
        if value > 0:
            return f"{_ansi('33', label)}[{_ansi('33', str(value))}]"
        return f"{_ansi('90', label)}[{_ansi('90', str(value))}]"

    return (
        f"{normal('ps', ps_servers, ' servers')} "
        f"{_ansi('90', '->')} "
        f"{_ansi(MINT, 'managed')}[{_ansi(PINK, str(managed_servers))} servers] . "
        f"{risk('orphan', orphan_servers)} . "
        f"{normal('leases', leases)} . "
        f"{risk('stale', stale_leases)}"
    )
```

Add these helpers near the renderer implementation:

```python
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visible_len(text: str) -> int:
    return len(_ANSI_ESCAPE_RE.sub("", text))


def _box_width_for(model: BoxModel) -> int:
    width = _BOX_WIDTH
    for item in model.items:
        row = f"  o {item.label:<10}  {item.value}"
        width = max(width, _visible_len(row) - 2)
    return width
```

Modify `render_box()` to compute and use `box_width` instead of `_BOX_WIDTH`:

```python
def render_box(model: BoxModel, *, spin_frame: int = 0) -> str:
    box_width = _box_width_for(model)
    lines: list[str] = []
    lines.append("  " + _ansi(PINK, "─" * box_width))
    lines.append("  " + _ansi(PURPLE, "─" * box_width))
    art = _HEADER_ART.get(model.title)
    if art is not None:
        for art_line in art:
            lines.append("  " + _gradient_line(art_line))
        phase_label = _ansi(PINK, f"·  {model.phase}")
        pad = max(0, box_width - len(art[-1]) - len(model.phase) - 4)
        lines.append("  " + " " * (len(art[-1]) + pad) + phase_label)
    else:
        header = f"{model.title}  ·  {model.phase}"
        lines.append("  " + _ansi(f"1;{PINK}", header))
    for item in model.items:
        marker = _marker_for(item.status, spin_frame=spin_frame)
        label = _ansi(MINT, f"{item.label:<10}")
        lines.append(f"  {marker} {label}  {item.value}")
    lines.append("  " + _ansi(PURPLE, "─" * box_width))
    lines.append("  " + _ansi(PINK, "─" * box_width))
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run formatter tests and confirm pass**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_ui_renderer.py::test_style_mcp_inventory_renders_single_line_plain_text local_dev/tests/test_ui_renderer.py::test_style_mcp_inventory_highlights_orphan_and_stale_when_nonzero local_dev/tests/test_ui_renderer.py::test_render_box_expands_border_for_long_mcp_inventory_row -q
```

Expected: all three tests pass.

---

### Task 3: Render Global MCP Row in Preflight

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py`
- Test: `local_dev/tests/test_launcher_phases.py`

- [ ] **Step 1: Write failing launcher tests**

Add this import near existing imports in `local_dev/tests/test_launcher_phases.py`:

```python
from local_dev.serena_mcp_management.serena_mcp.diagnostics import GlobalLifecycleSnapshot
```

Add this deterministic fixture near `_set_graphify_env()`:

```python
@pytest.fixture(autouse=True)
def _stub_global_mcp_snapshot(monkeypatch):
    monkeypatch.setattr(
        launcher,
        "snapshot_global_lifecycle",
        lambda **kwargs: GlobalLifecycleSnapshot(
            ps_server_count=0,
            managed_server_count=0,
            orphan_server_count=0,
            lease_count=0,
            stale_lease_count=0,
        ),
        raising=False,
    )
```

Append:

```python
def test_preflight_box_includes_global_serena_mcp_inventory(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    monkeypatch.setattr(
        launcher,
        "snapshot_global_lifecycle",
        lambda **kwargs: GlobalLifecycleSnapshot(
            ps_server_count=3,
            managed_server_count=2,
            orphan_server_count=1,
            lease_count=3,
            stale_lease_count=1,
        ),
        raising=False,
    )

    box = launcher._preflight_box()

    ids = [item.id for item in box.items]
    assert ids[ids.index("serena") + 1] == "serena-mcp"
    item = box.items[ids.index("serena-mcp")]
    assert item.label == "serena mcp"
    assert item.status == "warn"
    assert _strip_ansi(item.value) == (
        "ps[3 servers] -> managed[2 servers] . "
        "orphan[1] . leases[3] . stale[1]"
    )
```

Append:

```python
def test_preflight_box_marks_global_serena_mcp_idle_as_info(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    monkeypatch.setattr(
        launcher,
        "snapshot_global_lifecycle",
        lambda **kwargs: GlobalLifecycleSnapshot(
            ps_server_count=0,
            managed_server_count=0,
            orphan_server_count=0,
            lease_count=0,
            stale_lease_count=0,
        ),
        raising=False,
    )

    item = next(item for item in launcher._preflight_box().items if item.id == "serena-mcp")

    assert item.status == "info"
    assert _strip_ansi(item.value) == (
        "ps[0 servers] -> managed[0 servers] . "
        "orphan[0] . leases[0] . stale[0]"
    )
```

Append:

```python
def test_preflight_box_marks_global_serena_mcp_scan_failure_as_warn(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    monkeypatch.setattr(
        launcher,
        "snapshot_global_lifecycle",
        lambda **kwargs: GlobalLifecycleSnapshot(
            ps_server_count=0,
            managed_server_count=0,
            orphan_server_count=0,
            lease_count=0,
            stale_lease_count=0,
            scan_failed=True,
        ),
        raising=False,
    )

    item = next(item for item in launcher._preflight_box().items if item.id == "serena-mcp")

    assert item.status == "warn"
    assert item.value == "scan unavailable"
```

Append:

```python
def test_preflight_box_marks_global_serena_mcp_clean_running_as_done(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_PROJECT_ROOT", "/repo")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE", "0 to delete . 0 to keep")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_MEMORY_VALUE", "0 files to reset")
    monkeypatch.setenv("SERENA_AGENT_PREFLIGHT_SERENA_STATUS", "managed")
    _set_graphify_env(monkeypatch)
    monkeypatch.setattr(
        launcher,
        "snapshot_global_lifecycle",
        lambda **kwargs: GlobalLifecycleSnapshot(
            ps_server_count=2,
            managed_server_count=2,
            orphan_server_count=0,
            lease_count=3,
            stale_lease_count=0,
        ),
        raising=False,
    )

    item = next(item for item in launcher._preflight_box().items if item.id == "serena-mcp")

    assert item.status == "done"
```

- [ ] **Step 2: Run launcher tests and confirm failure**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py::test_preflight_box_includes_global_serena_mcp_inventory local_dev/tests/test_launcher_phases.py::test_preflight_box_marks_global_serena_mcp_idle_as_info local_dev/tests/test_launcher_phases.py::test_preflight_box_marks_global_serena_mcp_scan_failure_as_warn local_dev/tests/test_launcher_phases.py::test_preflight_box_marks_global_serena_mcp_clean_running_as_done -q
```

Expected: failure because `_preflight_box()` has no `serena-mcp` row and `snapshot_global_lifecycle` is not imported into the launcher module.

- [ ] **Step 3: Import diagnostics and formatter in launcher**

Modify imports in `local_dev/serena_mcp_management/serena_agent_launcher.py`:

```python
from local_dev.serena_mcp_management.serena_mcp.diagnostics import snapshot_global_lifecycle
from local_dev.serena_mcp_management.serena_mcp.watchdog import (
    HEARTBEAT_INTERVAL_SECONDS,
    LEASE_TIMEOUT_SECONDS,
    ShutdownStats,
    make_launcher_lease,
    release_lease_and_shutdown_if_empty,
)
from local_dev.serena_mcp_management.ui import (
    PINK,
    PURPLE,
    BoxModel,
    BoxRenderer,
    Item,
    SpinnerTicker,
    confirm,
    render_inline_row,
    style_count,
    style_mcp_inventory,
    style_spinner,
)
```

- [ ] **Step 4: Add status helper in launcher**

Add near the graphify value helpers in `local_dev/serena_mcp_management/serena_agent_launcher.py`:

```python
def _serena_mcp_status(snapshot) -> str:
    if snapshot.scan_failed:
        return "warn"
    if snapshot.orphan_server_count > 0 or snapshot.stale_lease_count > 0:
        return "warn"
    if snapshot.ps_server_count == 0:
        return "info"
    return "done"
```

- [ ] **Step 5: Add the preflight row**

Inside `_preflight_box()` in `local_dev/serena_mcp_management/serena_agent_launcher.py`, after computing `serena_item_status`, add:

```python
    try:
        mcp_snapshot = snapshot_global_lifecycle(
            now=time.time(),
            stale_after_seconds=LEASE_TIMEOUT_SECONDS,
        )
        if mcp_snapshot.scan_failed:
            serena_mcp_value = "scan unavailable"
            serena_mcp_status = "warn"
        else:
            serena_mcp_value = style_mcp_inventory(
                ps_servers=mcp_snapshot.ps_server_count,
                managed_servers=mcp_snapshot.managed_server_count,
                orphan_servers=mcp_snapshot.orphan_server_count,
                leases=mcp_snapshot.lease_count,
                stale_leases=mcp_snapshot.stale_lease_count,
            )
            serena_mcp_status = _serena_mcp_status(mcp_snapshot)
    except Exception:
        serena_mcp_value = "scan unavailable"
        serena_mcp_status = "warn"
```

Then insert this item immediately after the existing `serena` item:

```python
        Item(
            id="serena-mcp",
            label="serena mcp",
            value=serena_mcp_value,
            status=serena_mcp_status,
        ),
```

- [ ] **Step 6: Run launcher tests and confirm pass**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py::test_preflight_box_includes_global_serena_mcp_inventory local_dev/tests/test_launcher_phases.py::test_preflight_box_marks_global_serena_mcp_idle_as_info local_dev/tests/test_launcher_phases.py::test_preflight_box_marks_global_serena_mcp_scan_failure_as_warn local_dev/tests/test_launcher_phases.py::test_preflight_box_marks_global_serena_mcp_clean_running_as_done -q
```

Expected: all four tests pass.

---

### Task 4: Verification and Runtime Safety Check

**Files:**
- No new production files beyond Tasks 1-3.

- [ ] **Step 1: Run focused lifecycle and UI tests**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_serena_diagnostics.py \
  local_dev/tests/test_ui_renderer.py \
  local_dev/tests/test_launcher_phases.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run lifecycle regression tests**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_serena_processes.py \
  local_dev/tests/test_serena_termination.py \
  local_dev/tests/test_serena_registry.py \
  local_dev/tests/test_serena_server.py \
  local_dev/tests/test_serena_watchdog.py \
  local_dev/tests/test_serena_diagnostics.py \
  -q
```

Expected: all selected lifecycle tests pass.

- [ ] **Step 3: Run full local_dev suite**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests -q
```

Expected: all tests pass outside the sandbox. If sandboxed localhost bind failures occur in proxy tests, rerun the same command with approved escalation and record that the sandbox-only failures were proxy bind permissions.

- [ ] **Step 4: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 5: Review the actual rendered plain text**

Run a targeted rendering check from Python:

```bash
.venv/bin/python3 - <<'PY'
import os
import re
from local_dev.serena_mcp_management import serena_agent_launcher as launcher
from local_dev.serena_mcp_management.ui import render_box

os.environ["SERENA_AGENT_CLIENT"] = "codex"
os.environ["SERENA_AGENT_PROJECT_ROOT"] = "/Users/hyun/Desktop/Kingdom-Server"
os.environ["SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE"] = "0 to delete . 43 to keep"
os.environ["SERENA_AGENT_PREFLIGHT_MEMORY_VALUE"] = "0 files to reset"
os.environ["SERENA_AGENT_PREFLIGHT_SERENA_STATUS"] = "managed"
os.environ["SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS"] = "installed"
os.environ["SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS"] = "built"
os.environ["SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS"] = "installed"
os.environ["SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS"] = "installed"

plain = re.sub(r"\x1b\[[0-9;]*m", "", render_box(launcher._preflight_box()))
print(plain)
PY
```

Expected: the output contains a single `serena mcp` row with this shape:

```text
serena mcp  ps[N servers] -> managed[M servers] . orphan[O] . leases[L] . stale[S]
```

---

## Self-Review

- Spec coverage: the plan preserves one-line UI, separates `ps` and `managed` using bracketed groups and `->`, keeps `managed` as a `ps` subset, and highlights `orphan`/`stale` risk.
- Placeholder scan: this plan contains concrete files, test code, implementation snippets, and commands; no unspecified implementation steps remain.
- Type consistency: `GlobalLifecycleSnapshot` field names match the planned launcher tests and formatter call sites.
- Runtime safety: read-only registry loading avoids creating state directories in projects that only appear as orphan `ps` processes.
- Agent review follow-up: passive registry access must not create `registry.lock`; preflight tests must stub global scan by default; scan failure renders `scan unavailable`; the renderer must expand the box width for the approved one-line MCP value.
