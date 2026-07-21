# Agent Startup Cleanup Choices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every interactive Codex and Claude launch ask independent memory and session cleanup questions, default to memory preservation plus five-day session retention, and optionally delete every inactive session while preserving running sessions.

**Architecture:** Keep the launcher as the orchestration boundary, extend the generic selector with semantic accents, and reuse the existing session inventory/cleanup safety gates. Codex full cleanup changes only the inventory policy and keeps official CLI deletion; Claude full cleanup adds exact UUID-scoped bundle discovery, live-marker/open-file protection, immutable manifests, and bounded filesystem deletion.

**Tech Stack:** Python 3.12+ standard library, pytest, macOS `/bin/ps` and `lsof`, official `codex delete --force`, Claude `cleanupPeriodDays`, graphify, zsh launcher shim.

## Global Constraints

- Change only `local_dev/`; do not modify public `dotsync` code, root README, root Makefile, or the Homebrew formula.
- Runtime dependencies stay Python-standard-library-only.
- Ask both questions before any deletion; Ctrl+C at either question returns 130 and mutates nothing.
- Memory defaults to keep. Sessions default to no full deletion while the existing strict five-day retention still runs.
- Full session deletion is limited to the selected product and preserves every session proven to be running.
- Codex persistent deletion uses only `codex delete --force <UUID>`; never edit Codex JSONL or SQLite directly.
- Claude cleanup deletes only exact valid session UUID bundles; never use `claude project purge --all`.
- Explicit cleanup is fail-closed. Default five-day cleanup remains best-effort and does not block launch on warnings.
- A zero-target explicit action is a successful no-op.
- Memory prompts and action rows use purple; session prompts and action rows use yellow.
- Do not run destructive smoke checks against the user's live Codex or Claude data.
- Preserve the user's existing `AGENTS.md` and `.superpowers/` worktree changes.
- Treat the current orphan-session changes in `session_inventory.py`, `session_cleanup.py`, and their tests as the required baseline; do not revert them.
- Stage only exact task paths. Never use `git add -A` or `git add .` in this dirty worktree.

---

### Task 1: Freeze the Existing Orphan-Session Baseline

**Files:**
- Existing modified: `local_dev/serena_mcp_management/session_inventory.py`
- Existing modified: `local_dev/serena_mcp_management/session_cleanup.py`
- Existing modified: `local_dev/tests/test_session_inventory.py`
- Existing modified: `local_dev/tests/test_session_cleanup.py`
- Existing modified: `local_dev/docs/superpowers/plans/2026-07-20-agent-session-retention.md`
- Existing untracked: `local_dev/docs/superpowers/plans/2026-07-21-codex-orphan-session-cleanup.md`

**Interfaces:**
- Consumes: the already implemented `OwnerDeletePlan.local_delete_ids` and missing-parent synthetic logical roots.
- Produces: a tested clean commit that the new all-inactive Codex policy can safely extend.

- [ ] **Step 1: Verify the known baseline**

Run:

```bash
git diff --check -- local_dev/serena_mcp_management/session_inventory.py local_dev/serena_mcp_management/session_cleanup.py local_dev/tests/test_session_inventory.py local_dev/tests/test_session_cleanup.py local_dev/docs/superpowers/plans/2026-07-20-agent-session-retention.md
git status --short
```

Expected: no whitespace errors; `AGENTS.md` and `.superpowers/` remain visible but are not selected for this task.

- [ ] **Step 2: Run the focused baseline tests**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_session_inventory.py local_dev/tests/test_session_cleanup.py -q
```

Expected: all tests PASS, including missing-parent cleanup and descendant-before-parent official deletion.

- [ ] **Step 3: Commit only the verified baseline**

```bash
git add -- local_dev/serena_mcp_management/session_inventory.py local_dev/serena_mcp_management/session_cleanup.py local_dev/tests/test_session_inventory.py local_dev/tests/test_session_cleanup.py local_dev/docs/superpowers/plans/2026-07-20-agent-session-retention.md local_dev/docs/superpowers/plans/2026-07-21-codex-orphan-session-cleanup.md
git diff --cached --name-status
git commit -m "fix(local_dev): clean complete Codex orphan groups"
```

Expected: the staged list contains exactly the six paths above; user-owned dirty paths remain unstaged.

---

### Task 2: Add Semantic Selector and Action-Row Colors

**Files:**
- Modify: `local_dev/serena_mcp_management/ui.py:89-91,217-233,283-294,425-583`
- Modify: `local_dev/tests/test_ui_prompts.py:69-201`
- Modify: `local_dev/tests/test_ui_renderer.py`

**Interfaces:**
- Consumes: existing `PURPLE`, `SelectOption`, `ItemStatus`, and `select_option()` behavior.
- Produces: `YELLOW = "33"`; `select_option(..., accent: str = PURPLE) -> str`; `render_inline_row(..., accent: str | None = None) -> str`; `style_action_value(value: str, *, accent: str) -> str`.

- [ ] **Step 1: Write failing selector-accent tests**

Add to `test_ui_prompts.py`:

```python
def test_select_option_line_mode_uses_explicit_session_accent():
    stream = io.StringIO()
    result = select_option(
        "Delete Codex sessions before launch?",
        options=(
            SelectOption("retention_5d", "No full deletion"),
            SelectOption("delete_inactive", "Delete all inactive sessions"),
        ),
        accent=ui.YELLOW,
        stream=stream,
        input_fn=lambda: "",
    )

    assert result == "retention_5d"
    output = stream.getvalue()
    assert f"\x1b[{ui.YELLOW}m>\x1b[0m" in output
    assert f"\x1b[{ui.YELLOW}m1. No full deletion\x1b[0m" in output


def test_select_option_arrow_uses_explicit_session_accent(monkeypatch):
    stream = io.StringIO()
    old_attrs = ["old-terminal-state"]
    reads = iter((b"\r",))
    monkeypatch.setattr(ui.termios, "tcgetattr", lambda fd: old_attrs)
    monkeypatch.setattr(ui.tty, "setcbreak", lambda fd: None)
    monkeypatch.setattr(ui.os, "read", lambda fd, size: next(reads))
    monkeypatch.setattr(ui.termios, "tcsetattr", lambda *args: None)

    selected = ui._read_select_arrow(
        "Delete Codex sessions before launch?",
        options=(SelectOption("retention_5d", "No full deletion"),),
        cursor=0,
        stream=stream,
        fd=7,
        accent=ui.YELLOW,
    )

    assert selected == "retention_5d"
    assert stream.getvalue().endswith(
        f"  \x1b[{ui.YELLOW}m?\x1b[0m "
        "Delete Codex sessions before launch? "
        f"\x1b[{ui.YELLOW}mNo full deletion\x1b[0m\n"
    )
```

- [ ] **Step 2: Write failing action-row tests**

Update imports in `test_ui_renderer.py` and add:

```python
def test_render_inline_row_colors_session_start_with_requested_accent():
    rendered = render_inline_row(
        "sessions",
        "deleting inactive sessions",
        status="spin",
        accent=YELLOW,
    )

    assert f"\x1b[{YELLOW}m" in rendered
    assert _strip_ansi(rendered) == (
        "  ⠋ sessions    deleting inactive sessions\n"
    )


def test_style_action_value_wraps_complete_value_in_accent():
    assert style_action_value("8 sessions deleted", accent=YELLOW) == (
        f"\x1b[{YELLOW}m8 sessions deleted\x1b[0m"
    )
```

- [ ] **Step 3: Run the UI tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_ui_prompts.py local_dev/tests/test_ui_renderer.py -q
```

Expected: FAIL because `YELLOW`, `accent`, and `style_action_value` do not exist.

- [ ] **Step 4: Implement the reusable accent contract**

Add:

```python
YELLOW = "33"


def style_action_value(value: str, *, accent: str) -> str:
    return _ansi(accent, value)
```

Change the marker and standalone row contracts to:

```python
def _marker_for(
    status: ItemStatus,
    *,
    spin_frame: int = 0,
    accent: str = PURPLE,
) -> str:
    if status == "spin":
        frame = SPINNER_FRAMES[spin_frame % len(SPINNER_FRAMES)]
        return _ansi(accent, frame)
    if status == "done":
        return _ansi(PINK, "✓")
    if status == "warn":
        return _ansi(YELLOW, "!")
    if status == "skip":
        return _ansi("90", "-")
    if status == "info":
        return _ansi("90", "·")
    return _ansi("90", "o")


def render_inline_row(
    label: str,
    value: str,
    *,
    status: ItemStatus,
    accent: str | None = None,
) -> str:
    marker = _marker_for(status, accent=accent or PURPLE)
    label_color = accent or MINT
    label_text = _ansi(label_color, f"{label:<10}")
    value_text = _ansi(accent, value) if accent is not None else value
    return f"  {marker} {label_text}  {value_text}\n"
```

Thread `accent: str = PURPLE` through `select_option`, `_read_select_arrow`, and `_read_select_line`. Replace each hard-coded purple prompt/focus escape with `accent`. In line mode, color the prompt marker and numbered labels with `accent` while preserving numeric retry and empty-input default semantics.

- [ ] **Step 5: Run the UI tests and verify GREEN**

Run the Step 3 command.

Expected: all UI tests PASS.

- [ ] **Step 6: Commit the UI unit**

```bash
git add -- local_dev/serena_mcp_management/ui.py local_dev/tests/test_ui_prompts.py local_dev/tests/test_ui_renderer.py
git commit -m "feat(local_dev): color startup cleanup choices"
```

---

### Task 3: Add Explicit Session Policies and Claude Bundle Inventory

**Files:**
- Modify: `local_dev/serena_mcp_management/session_inventory.py`
- Modify: `local_dev/tests/test_session_inventory.py`
- Create: `local_dev/tests/test_claude_session_inventory.py`

**Interfaces:**
- Consumes: `FileIdentity`, `FileFingerprint`, Codex logical groups, `snapshot_open_rollouts()`, and `memory_management.running_client_processes()`.
- Produces: `SessionPolicy = Literal["retention_5d", "all_inactive"]`; `ClaudeSessionPath`; `ClaudeCleanupTarget`; `AgentInventory.policy`; `AgentInventory.claude_targets`; `AgentInventory.active_sessions`; `snapshot_active_claude_sessions()`; `snapshot_claude_manifest()`; `scan_inventory(..., policy="retention_5d", active_claude_session_ids=None)`.

- [ ] **Step 1: Write the failing Codex policy tests**

Add to `test_session_inventory.py`:

```python
def test_scan_codex_all_inactive_ignores_age_but_keeps_open_group(tmp_path):
    closed = tmp_path / ".codex/sessions/2026/07/21/closed.jsonl"
    opened = tmp_path / ".codex/sessions/2026/07/21/open.jsonl"
    _write_jsonl(closed, [_session_meta(ROOT_A)], age_days=1)
    _write_jsonl(opened, [_session_meta(ROOT_B)], age_days=1)
    opened_stat = opened.stat()

    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        now=NOW,
        policy="all_inactive",
        open_file_identities=frozenset(
            {
                FileIdentity(
                    device=opened_stat.st_dev,
                    inode=opened_stat.st_ino,
                )
            }
        ),
    )

    assert inventory.policy == "all_inactive"
    assert inventory.sessions == CountStats(total=2, to_delete=1, to_keep=1)
    assert inventory.active_sessions == 1
    assert [target.root_id for target in inventory.codex_targets] == [ROOT_A]
    assert inventory.criteria == (
        "sessions: all known homes + all inactive; running preserved"
    )


def test_scan_rejects_unknown_session_policy(tmp_path):
    with pytest.raises(ValueError, match="unsupported session policy: unknown"):
        scan_inventory(
            client="codex",
            home=tmp_path,
            codex_home=tmp_path / ".codex",
            policy="unknown",
        )
```

- [ ] **Step 2: Write failing Claude bundle tests**

Create `test_claude_session_inventory.py` with:

```python
import json
import subprocess
from pathlib import Path

import pytest

from local_dev.serena_mcp_management.memory_management import ClientProcess
from local_dev.serena_mcp_management.session_inventory import (
    ActiveSessionScanError,
    CountStats,
    FileIdentity,
    scan_inventory,
    snapshot_active_claude_sessions,
)


SESSION_A = "00000000-0000-4000-8000-000000000101"
SESSION_B = "00000000-0000-4000-8000-000000000102"
PROC_START = "Tue Jul 21 05:29:32 2026"


def _write(path: Path, value: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


def test_scan_claude_all_inactive_groups_exact_session_bundles(tmp_path):
    config = tmp_path / ".claude"
    transcript = _write(config / "projects/-repo" / f"{SESSION_A}.jsonl")
    subagent = _write(
        config / "projects/-repo" / SESSION_A / "subagents/agent.jsonl"
    )
    history = _write(config / "file-history" / SESSION_A / "file.txt")
    memory = _write(config / "projects/-repo/memory/MEMORY.md", "keep")
    settings = _write(config / "settings.json", "{}")

    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset(),
        open_file_identities=frozenset(),
    )

    assert inventory.sessions == CountStats(total=1, to_delete=1, to_keep=0)
    target = inventory.claude_targets[0]
    assert target.session_id == SESSION_A
    assert set(target.roots) == {
        transcript,
        config / "projects/-repo" / SESSION_A,
        config / "file-history" / SESSION_A,
    }
    manifest_paths = {entry.path for entry in target.manifest}
    assert subagent in manifest_paths
    assert history in manifest_paths
    assert memory not in manifest_paths
    assert settings not in manifest_paths


def test_scan_claude_all_inactive_preserves_marker_or_open_bundle(tmp_path):
    config = tmp_path / ".claude"
    _write(config / "projects/-repo" / f"{SESSION_A}.jsonl")
    opened = _write(config / "projects/-repo" / f"{SESSION_B}.jsonl")
    opened_stat = opened.stat()

    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset({SESSION_A}),
        open_file_identities=frozenset(
            {
                FileIdentity(
                    device=opened_stat.st_dev,
                    inode=opened_stat.st_ino,
                )
            }
        ),
    )

    assert inventory.sessions == CountStats(total=2, to_delete=0, to_keep=2)
    assert inventory.active_sessions == 2
    assert inventory.claude_targets == ()
```

- [ ] **Step 3: Write failing Claude marker tests**

Append:

```python
def _marker(config: Path, *, session_id: str, pid: int, start: str) -> Path:
    path = config / "sessions" / f"{pid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"sessionId": session_id, "pid": pid, "procStart": start})
    )
    return path


def test_snapshot_active_claude_sessions_requires_matching_start(tmp_path):
    config = tmp_path / ".claude"
    _marker(config, session_id=SESSION_A, pid=42, start=PROC_START)
    process = ClientProcess(
        pid=42,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )

    def run(command, **kwargs):
        assert command == ["/bin/ps", "-p", "42", "-o", "lstart="]
        return subprocess.CompletedProcess(command, 0, PROC_START + "\n", "")

    assert snapshot_active_claude_sessions(
        config,
        processes=(process,),
        run_command=run,
    ) == frozenset({SESSION_A})


def test_snapshot_active_claude_sessions_ignores_dead_stale_marker(tmp_path):
    config = tmp_path / ".claude"
    marker = _marker(config, session_id=SESSION_A, pid=42, start=PROC_START)

    assert snapshot_active_claude_sessions(
        config,
        processes=(),
        run_command=lambda *args, **kwargs: pytest.fail("ps must not run"),
    ) == frozenset()
    assert marker.exists()


def test_snapshot_active_claude_sessions_rejects_malformed_marker(tmp_path):
    marker = tmp_path / ".claude/sessions/broken.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("not-json")

    with pytest.raises(ActiveSessionScanError, match="broken.json"):
        snapshot_active_claude_sessions(
            tmp_path / ".claude",
            processes=(),
        )
```

Append the PID-reuse and symlink cases:

```python
def test_snapshot_active_claude_sessions_rejects_reused_pid(tmp_path):
    config = tmp_path / ".claude"
    _marker(config, session_id=SESSION_A, pid=42, start=PROC_START)
    process = ClientProcess(
        pid=42,
        ppid=1,
        executable="/opt/homebrew/bin/claude",
        command="/opt/homebrew/bin/claude",
    )

    def run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            "Tue Jul 21 06:00:00 2026\n",
            "",
        )

    assert snapshot_active_claude_sessions(
        config,
        processes=(process,),
        run_command=run,
    ) == frozenset()


def test_scan_claude_all_inactive_rejects_bundle_symlink(tmp_path):
    config = tmp_path / ".claude"
    bundle = config / "projects/-repo" / SESSION_A
    bundle.mkdir(parents=True)
    outside = _write(tmp_path / "outside.txt", "keep")
    (bundle / "escape").symlink_to(outside)

    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset(),
        open_file_identities=frozenset(),
    )

    assert inventory.claude_targets == ()
    assert any("symlink" in warning for warning in inventory.warnings)
    assert outside.read_text() == "keep"
```

- [ ] **Step 4: Run inventory tests and verify RED**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_session_inventory.py local_dev/tests/test_claude_session_inventory.py -q
```

Expected: FAIL because the policy, Claude targets, marker snapshot, and manifests do not exist.

- [ ] **Step 5: Add policy and bundle data models**

Add `Literal` and `stat` imports and these contracts:

```python
SessionPolicy = Literal["retention_5d", "all_inactive"]


@dataclass(frozen=True)
class ClaudeSessionPath:
    path: Path
    fingerprint: FileFingerprint
    is_directory: bool


@dataclass(frozen=True)
class ClaudeCleanupTarget:
    session_id: str
    roots: tuple[Path, ...]
    manifest: tuple[ClaudeSessionPath, ...]


@dataclass(frozen=True)
class AgentInventory:
    client: str
    sessions: CountStats
    criteria: str
    policy: SessionPolicy = "retention_5d"
    records: CountStats | None = None
    codex_targets: tuple[CodexCleanupTarget, ...] = ()
    claude_targets: tuple[ClaudeCleanupTarget, ...] = ()
    active_sessions: int = 0
    scanned_paths: tuple[Path, ...] = ()
    session_dirs: tuple[Path, ...] = ()
    claude_config_dir: Path | None = None
    warnings: tuple[str, ...] = ()
```

Keep all defaulted fields after the required fields.

- [ ] **Step 6: Implement manifests and live-marker validation**

Implement `snapshot_claude_manifest(roots)` as an iterative `lstat()` walk. Record every root and descendant, sort by string path, reject symlinks and non-file/non-directory nodes with `ActiveSessionScanError`, and never use `resolve()` as deletion authority.

Implement:

```python
def snapshot_active_claude_sessions(
    config_dir: Path,
    *,
    processes: tuple[ClientProcess, ...] | None = None,
    run_command: RunCommand = subprocess.run,
) -> frozenset[str]:
    if processes is None:
        processes = running_client_processes(
            "claude",
            run_command=run_command,
        )
    processes_by_pid = {process.pid: process for process in processes}
    active: set[str] = set()
    marker_dir = config_dir / "sessions"
    if marker_dir.is_symlink():
        raise ActiveSessionScanError(
            f"unsafe Claude marker directory: {marker_dir}"
        )
    if not marker_dir.is_dir():
        return frozenset()

    for marker in sorted(marker_dir.glob("*.json")):
        if marker.is_symlink():
            raise ActiveSessionScanError(
                f"unsafe Claude session marker: {marker}"
            )
        try:
            payload = json.loads(marker.read_text())
            session_id = str(uuid.UUID(payload["sessionId"]))
            pid = payload["pid"]
            proc_start = payload["procStart"]
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            raise ActiveSessionScanError(
                f"invalid Claude session marker {marker}: {exc}"
            ) from exc
        if isinstance(pid, bool) or not isinstance(pid, int):
            raise ActiveSessionScanError(
                f"invalid Claude marker pid: {marker}"
            )
        if not isinstance(proc_start, str) or not proc_start.strip():
            raise ActiveSessionScanError(
                f"invalid Claude marker start: {marker}"
            )
        if pid not in processes_by_pid:
            continue
        command = ["/bin/ps", "-p", str(pid), "-o", "lstart="]
        result = run_command(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if (
            result.returncode == 1
            and not result.stdout.strip()
            and not result.stderr.strip()
        ):
            continue
        if result.returncode != 0 or not result.stdout.strip():
            detail = result.stderr.strip() or (
                f"ps exited {result.returncode}"
            )
            raise ActiveSessionScanError(detail)
        if result.stdout.strip() == proc_start.strip():
            active.add(session_id)
    return frozenset(active)
```

- [ ] **Step 7: Implement both scan policies**

Change the public signature to:

```python
def scan_inventory(
    *,
    client: str,
    home: Path,
    codex_home: Path,
    claude_config_dir: Path | None = None,
    orca_codex_home: Path | None = None,
    now: float | None = None,
    policy: SessionPolicy = "retention_5d",
    open_file_identities: frozenset[FileIdentity] | None = None,
    active_claude_session_ids: frozenset[str] | None = None,
) -> AgentInventory:
```

Validate the policy before client dispatch. For Codex retention, preserve the current cutoff. For Codex all-inactive, consider every valid logical group before open-file protection, count open groups in `active_sessions`, and use `sessions: all known homes + all inactive; running preserved`.

For Claude retention, preserve the existing metadata-only top-level JSONL scan. For Claude all-inactive, discover exact canonical UUID roots only from:

```text
projects/*/<uuid>.jsonl
projects/*/<uuid>/
file-history/<uuid>/
session-env/<uuid>/
tasks/<uuid>/
debug/<uuid>.txt
```

Group roots by UUID, build one manifest per group, and preserve a bundle when its ID is active or any manifest identity is open. If explicit snapshots are omitted, call `snapshot_active_claude_sessions(config_dir)` and `snapshot_open_rollouts((config_dir / "projects",))`. Ignore non-UUID siblings and auto-memory.

- [ ] **Step 8: Run inventory tests and verify GREEN**

Run the Step 4 command.

Expected: all inventory tests PASS without reading the real user home.

- [ ] **Step 9: Commit the inventory unit**

```bash
git add -- local_dev/serena_mcp_management/session_inventory.py local_dev/tests/test_session_inventory.py local_dev/tests/test_claude_session_inventory.py
git commit -m "feat(local_dev): inventory all inactive agent sessions"
```

---

### Task 4: Add Strict Codex and Bounded Claude Cleanup

**Files:**
- Modify: `local_dev/serena_mcp_management/session_cleanup.py`
- Modify: `local_dev/tests/test_session_cleanup.py`
- Create: `local_dev/tests/test_claude_session_cleanup.py`

**Interfaces:**
- Consumes: Task 3 policies, Claude targets/manifests, marker snapshots, and open-file identities.
- Produces: `CleanupResult.succeeded`, `CleanupResult.error`, `CleanupResult.preserved_running`, strict Codex behavior for `all_inactive`, and `cleanup_claude_inventory()`.

- [ ] **Step 1: Write failing strict Codex tests**

Add:

```python
def test_explicit_codex_cleanup_treats_unsafe_inventory_as_failure():
    inventory = AgentInventory(
        client="codex",
        policy="all_inactive",
        sessions=CountStats(total=1, to_delete=0, to_keep=1),
        criteria=(
            "sessions: all known homes + all inactive; running preserved"
        ),
        warnings=("parent cycle",),
    )
    calls = []

    result = cleanup_codex_inventory(
        inventory,
        codex_binary="/fake/codex",
        runner=lambda *args, **kwargs: calls.append(args),
    )

    assert not result.succeeded
    assert result.error == (
        "cannot safely inventory every inactive Codex session"
    )
    assert calls == []


def test_explicit_codex_cleanup_preserves_target_that_becomes_open(tmp_path):
    rollout = tmp_path / ".codex/sessions/2026/07/21/root.jsonl"
    _write_old_session(rollout, ROOT_A)
    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        open_file_identities=frozenset(),
    )
    stat_result = rollout.stat()

    result = cleanup_codex_inventory(
        inventory,
        codex_binary="/fake/codex",
        runner=lambda *args, **kwargs: pytest.fail("delete must not run"),
        open_file_snapshot=lambda paths: frozenset(
            {
                FileIdentity(
                    device=stat_result.st_dev,
                    inode=stat_result.st_ino,
                )
            }
        ),
    )

    assert result.succeeded
    assert result.deleted == 0
    assert result.preserved_running == 1
```

Add the explicit-policy official CLI failure case while keeping all existing
retention assertions warning-only:

```python
def test_explicit_codex_cleanup_reports_partial_cli_failure(tmp_path):
    root_b = "00000000-0000-4000-8000-000000000099"
    first = tmp_path / ".codex/sessions/2026/07/21/first.jsonl"
    second = tmp_path / ".codex/sessions/2026/07/21/second.jsonl"
    _write_old_session(first, ROOT_A)
    _write_old_session(second, root_b)
    inventory = scan_inventory(
        client="codex",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        open_file_identities=frozenset(),
    )

    def runner(command, **kwargs):
        if command[-1] == root_b:
            return subprocess.CompletedProcess(command, 1, "", "delete failed")
        return subprocess.CompletedProcess(command, 0, "", "")

    result = cleanup_codex_inventory(
        inventory,
        codex_binary="/fake/codex",
        runner=runner,
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert not result.succeeded
    assert result.deleted == 1
    assert "delete failed" in result.error
```

- [ ] **Step 2: Write failing Claude cleanup tests**

Create `test_claude_session_cleanup.py`:

```python
from dataclasses import replace
from pathlib import Path

from local_dev.serena_mcp_management.session_cleanup import (
    cleanup_claude_inventory,
)
from local_dev.serena_mcp_management.session_inventory import (
    AgentInventory,
    CountStats,
    FileIdentity,
    scan_inventory,
)


SESSION_A = "00000000-0000-4000-8000-000000000201"
SESSION_B = "00000000-0000-4000-8000-000000000202"


def _write(path: Path, text: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _inventory(tmp_path: Path):
    config = tmp_path / ".claude"
    _write(config / "projects/-repo" / f"{SESSION_A}.jsonl")
    _write(config / "projects/-repo" / SESSION_A / "subagents/a.jsonl")
    _write(config / "file-history" / SESSION_A / "file.txt")
    _write(config / "projects/-repo/memory/MEMORY.md", "keep")
    _write(config / "settings.json", "{}")
    return scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset(),
        open_file_identities=frozenset(),
    )


def test_cleanup_claude_removes_exact_inactive_bundle_only(tmp_path):
    inventory = _inventory(tmp_path)
    config = tmp_path / ".claude"

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert result.succeeded
    assert result.deleted == 1
    assert not (config / "projects/-repo" / f"{SESSION_A}.jsonl").exists()
    assert not (config / "projects/-repo" / SESSION_A).exists()
    assert not (config / "file-history" / SESSION_A).exists()
    assert (config / "projects/-repo/memory/MEMORY.md").read_text() == "keep"
    assert (config / "settings.json").read_text() == "{}"


def test_cleanup_claude_preserves_bundle_that_becomes_active(tmp_path):
    inventory = _inventory(tmp_path)

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset({SESSION_A}),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert result.succeeded
    assert result.deleted == 0
    assert result.preserved_running == 1
    assert inventory.claude_targets[0].roots[0].exists()


def test_cleanup_claude_fails_before_delete_when_manifest_changes(tmp_path):
    inventory = _inventory(tmp_path)
    target = inventory.claude_targets[0]
    bundle_dir = next(path for path in target.roots if path.is_dir())
    _write(bundle_dir / "new-tool-result.txt")

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert not result.succeeded
    assert "changed after inventory" in result.error
    assert all(path.exists() for path in target.roots)
```

Append the remaining safety cases:

```python
def test_cleanup_claude_preserves_freshly_open_transcript(tmp_path):
    inventory = _inventory(tmp_path)
    transcript = next(
        path
        for path in inventory.claude_targets[0].roots
        if path.suffix == ".jsonl"
    )
    stat_result = transcript.stat()

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(
            {
                FileIdentity(
                    device=stat_result.st_dev,
                    inode=stat_result.st_ino,
                )
            }
        ),
    )

    assert result.succeeded
    assert result.preserved_running == 1
    assert transcript.exists()


def test_cleanup_claude_zero_targets_is_success(tmp_path):
    inventory = AgentInventory(
        client="claude",
        policy="all_inactive",
        sessions=CountStats(total=0, to_delete=0, to_keep=0),
        criteria="sessions: all projects + all inactive; running preserved",
        claude_config_dir=tmp_path / ".claude",
    )

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert result.succeeded
    assert result.deleted == 0


def test_cleanup_claude_inventory_warning_fails_before_delete(tmp_path):
    inventory = _inventory(tmp_path)
    unsafe = replace(inventory, warnings=("unsafe bundle",))

    result = cleanup_claude_inventory(
        unsafe,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert not result.succeeded
    assert all(path.exists() for path in inventory.claude_targets[0].roots)


def test_cleanup_claude_reports_partial_unlink_failure(tmp_path):
    inventory_a = _inventory(tmp_path)
    config = tmp_path / ".claude"
    _write(config / "projects/-repo" / f"{SESSION_B}.jsonl")
    inventory = scan_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        policy="all_inactive",
        active_claude_session_ids=frozenset(),
        open_file_identities=frozenset(),
    )

    def unlink(path: Path) -> None:
        if path.name == f"{SESSION_B}.jsonl":
            raise OSError("disk failure")
        path.unlink()

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
        unlink=unlink,
    )

    assert len(inventory_a.claude_targets) == 1
    assert not result.succeeded
    assert result.deleted == 1
    assert "disk failure" in result.error


def test_cleanup_claude_leaves_stale_marker_files(tmp_path):
    inventory = _inventory(tmp_path)
    marker = _write(tmp_path / ".claude/sessions/999.json", "{}")

    result = cleanup_claude_inventory(
        inventory,
        active_session_snapshot=lambda config_dir: frozenset(),
        open_file_snapshot=lambda paths: frozenset(),
    )

    assert result.succeeded
    assert marker.read_text() == "{}"
```

- [ ] **Step 3: Run cleanup tests and verify RED**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_session_cleanup.py local_dev/tests/test_claude_session_cleanup.py -q
```

Expected: FAIL because strict result fields and Claude cleanup do not exist.

- [ ] **Step 4: Extend the result contract**

Use:

```python
@dataclass(frozen=True)
class CleanupResult:
    deleted: int = 0
    preserved_running: int = 0
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None
```

In `cleanup_codex_inventory()` derive `strict = inventory.policy == "all_inactive"`. Strict mode must fail before capability probing on inventory warnings, convert path/fingerprint/active-scan/probe/delete failures to `error`, preserve newly open targets with `preserved_running`, and stop on the first mutation failure. Retention mode keeps existing warning-only behavior.

- [ ] **Step 5: Implement bounded Claude cleanup**

Add:

```python
def cleanup_claude_inventory(
    inventory: AgentInventory,
    *,
    active_session_snapshot: Callable[[Path], frozenset[str]] = (
        snapshot_active_claude_sessions
    ),
    open_file_snapshot: OpenFileSnapshot = snapshot_open_rollouts,
    remove_tree: Callable[[Path], None] = shutil.rmtree,
    unlink: Callable[[Path], None] = Path.unlink,
) -> CleanupResult:
```

Execute in this order:

1. Require client `claude` and policy `all_inactive`.
2. Fail before mutation when inventory warnings exist.
3. Snapshot active marker IDs and open transcript identities.
4. Partition targets into newly running and still inactive.
5. Rebuild and compare every still-inactive manifest.
6. Fail before the first delete if any manifest differs.
7. Delete roots only after all prevalidation passes.
8. Use `unlink` for files and `remove_tree` for directories.
9. Count only completely removed bundles and return partial counts on failure.
10. Never remove `config_dir / "sessions"` markers.

Catch `ActiveSessionScanError`, `OSError`, and manifest validation errors and return a path-specific `error`.

- [ ] **Step 6: Run cleanup tests and verify GREEN**

Run the Step 3 command.

Expected: all retention and explicit cleanup tests PASS.

- [ ] **Step 7: Commit the cleanup unit**

```bash
git add -- local_dev/serena_mcp_management/session_cleanup.py local_dev/tests/test_session_cleanup.py local_dev/tests/test_claude_session_cleanup.py
git commit -m "feat(local_dev): delete inactive agent sessions safely"
```

---

### Task 5: Orchestrate Two Startup Choices Before Mutation

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py:82-88,287-337,396-602,1402-1433`
- Modify: `local_dev/tests/test_launcher_phases.py:1160-1660,2030-2060,2210-2270,2410-2445,2760-2790`
- Modify: `local_dev/tests/test_serena_launcher.py:260-430`

**Interfaces:**
- Consumes: Tasks 2-4 and existing `delete_all_memory()`.
- Produces: `_run_memory_choice_v2() -> Literal["keep", "delete"]`; `_run_session_choice_v2() -> Literal["retention_5d", "delete_inactive"]`; `_run_explicit_session_cleanup_v2()`; two-question ordering; session-specific summary wording.

- [ ] **Step 1: Write failing binary prompt tests**

Replace old keep/delete/cancel assertions with:

```python
def test_cleanup_choices_are_product_scoped_and_default_to_keep(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")

    memory_out = io.StringIO()
    session_out = io.StringIO()
    memory = launcher._run_memory_choice_v2(
        stream=memory_out,
        input_fn=lambda: "",
    )
    sessions = launcher._run_session_choice_v2(
        stream=session_out,
        input_fn=lambda: "",
    )

    assert memory == "keep"
    assert sessions == "retention_5d"
    assert "Keep all memory (default)" in _strip_ansi(
        memory_out.getvalue()
    )
    assert "Delete all Codex auto-memory" in _strip_ansi(
        memory_out.getvalue()
    )
    assert "automatic cleanup after 5 days (default)" in _strip_ansi(
        session_out.getvalue()
    )
    assert "running sessions are preserved" in _strip_ansi(
        session_out.getvalue()
    )
    assert f"\x1b[{launcher.PURPLE}m" in memory_out.getvalue()
    assert f"\x1b[{launcher.YELLOW}m" in session_out.getvalue()
```

Add the Claude scope and non-interactive defaults explicitly:

```python
def test_cleanup_choices_use_claude_product_scope(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "claude")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    memory_out = io.StringIO()
    session_out = io.StringIO()

    assert launcher._run_memory_choice_v2(
        stream=memory_out,
        input_fn=lambda: "2",
    ) == "delete"
    assert launcher._run_session_choice_v2(
        stream=session_out,
        input_fn=lambda: "2",
    ) == "delete_inactive"
    assert "Claude auto-memory" in _strip_ansi(memory_out.getvalue())
    assert "Claude sessions" in _strip_ansi(session_out.getvalue())
    assert "Codex" not in _strip_ansi(memory_out.getvalue())
    assert "Codex" not in _strip_ansi(session_out.getvalue())


def test_cleanup_choices_bypass_prompts_when_non_interactive(monkeypatch):
    monkeypatch.delenv("SERENA_AGENT_INTERACTIVE", raising=False)
    memory_out = io.StringIO()
    session_out = io.StringIO()

    assert launcher._run_memory_choice_v2(stream=memory_out) == "keep"
    assert launcher._run_session_choice_v2(
        stream=session_out
    ) == "retention_5d"
    assert memory_out.getvalue() == ""
    assert session_out.getvalue() == ""
```

- [ ] **Step 2: Write failing orchestration tests**

Replace `_run_main_for_memory_choice` with `_run_main_for_cleanup_choices`. The helper must log both choices before mutation. Add:

```python
@pytest.mark.parametrize(
    ("memory_choice", "session_choice", "expected_actions"),
    [
        ("keep", "retention_5d", ["session-retention"]),
        (
            "delete",
            "retention_5d",
            ["memory-delete", "session-retention"],
        ),
        ("keep", "delete_inactive", ["session-delete-inactive"]),
        (
            "delete",
            "delete_inactive",
            ["memory-delete", "session-delete-inactive"],
        ),
    ],
)
def test_v2_main_collects_both_choices_before_actions(
    monkeypatch,
    tmp_path,
    memory_choice,
    session_choice,
    expected_actions,
):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice=memory_choice,
        session_choice=session_choice,
    )

    assert rc == 0
    assert call_log[:5] == [
        "overview",
        "serena-init",
        "setup",
        "memory-choice",
        "session-choice",
    ]
    assert [
        entry for entry in call_log if entry in expected_actions
    ] == expected_actions
    assert call_log[-1] == "launch"
```

Extend `_run_main_for_cleanup_choices` with optional
`session_choice_exception` and `explicit_cleanup_result` arguments, then add:

```python
from local_dev.serena_mcp_management.session_inventory import (
    AgentInventory,
    CountStats,
)


def test_v2_main_session_choice_ctrl_c_precedes_memory_delete(
    monkeypatch,
    tmp_path,
):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice="delete",
        session_choice="retention_5d",
        session_choice_exception=KeyboardInterrupt(),
        call_public_main=True,
    )

    assert rc == 130
    assert call_log[-1] == "session-choice"
    assert "memory-delete" not in call_log
    assert "launch" not in call_log


def test_v2_main_explicit_cleanup_failure_stops_launch(monkeypatch, tmp_path):
    rc, call_log = _run_main_for_cleanup_choices(
        monkeypatch,
        tmp_path,
        memory_choice="keep",
        session_choice="delete_inactive",
        explicit_cleanup_result=launcher.CleanupResult(
            error="unsafe session inventory"
        ),
    )

    assert rc == 1
    assert "session-delete-inactive" in call_log
    assert "launch" not in call_log


def test_explicit_session_cleanup_reports_newly_running_session(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        launcher,
        "scan_inventory",
        lambda **kwargs: _inventory_snapshot(
            total=1,
            to_delete=1,
            to_keep=0,
        ).inventory,
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_codex_inventory",
        lambda inventory, codex_binary: launcher.CleanupResult(
            deleted=0,
            preserved_running=1,
        ),
    )
    out = io.StringIO()

    result = launcher._run_explicit_session_cleanup_v2(
        client="codex",
        real_binary="/fake/codex",
        stream=out,
    )

    assert result.succeeded
    assert result.preserved_running == 1
    assert "1 running preserved" in _strip_ansi(out.getvalue())


def test_explicit_session_cleanup_codex_uses_fresh_all_inactive_scan(
    monkeypatch,
):
    inventory = AgentInventory(
        client="codex",
        policy="all_inactive",
        sessions=CountStats(total=0),
        criteria="all inactive",
    )
    scan_calls = []
    cleanup_calls = []
    monkeypatch.setattr(
        launcher,
        "scan_inventory",
        lambda **kwargs: scan_calls.append(kwargs) or inventory,
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_codex_inventory",
        lambda value, codex_binary: cleanup_calls.append(
            (value, codex_binary)
        )
        or launcher.CleanupResult(),
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_claude_inventory",
        lambda value: pytest.fail("Claude cleanup must not run"),
    )

    result = launcher._run_explicit_session_cleanup_v2(
        client="codex",
        real_binary="/fake/codex",
        stream=io.StringIO(),
    )

    assert result.succeeded
    assert scan_calls[0]["policy"] == "all_inactive"
    assert cleanup_calls == [(inventory, "/fake/codex")]


def test_explicit_session_cleanup_claude_uses_only_claude_cleanup(
    monkeypatch,
):
    inventory = AgentInventory(
        client="claude",
        policy="all_inactive",
        sessions=CountStats(total=0),
        criteria="all inactive",
    )
    monkeypatch.setattr(
        launcher,
        "scan_inventory",
        lambda **kwargs: inventory,
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_codex_inventory",
        lambda *args, **kwargs: pytest.fail("Codex cleanup must not run"),
    )
    monkeypatch.setattr(
        launcher,
        "cleanup_claude_inventory",
        lambda value: launcher.CleanupResult(),
    )

    result = launcher._run_explicit_session_cleanup_v2(
        client="claude",
        real_binary="/fake/claude",
        stream=io.StringIO(),
    )

    assert result.succeeded
```

Update the existing `test_v2_launch_prep_codex_uses_snapshot_and_official_cleanup`
and Claude counterpart to assert the default path receives the exact preflight
inventory object and never invokes a second scan.

- [ ] **Step 3: Run launcher tests and verify RED**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py local_dev/tests/test_serena_launcher.py -q
```

Expected: FAIL because the session choice and explicit dispatch do not exist.

- [ ] **Step 4: Implement both choice functions**

Use:

```python
def _run_memory_choice_v2(
    *,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
) -> Literal["keep", "delete"]:
    if os.environ.get("SERENA_AGENT_INTERACTIVE") != "1":
        return "keep"
    out = stream if stream is not None else sys.stdout
    client = os.environ.get("SERENA_AGENT_CLIENT", "codex")
    product = "Codex" if client == "codex" else "Claude"
    choice = select_option(
        f"Delete {product} auto-memory before launch?",
        options=(
            SelectOption("keep", "Keep all memory (default)"),
            SelectOption(
                "delete",
                f"Delete all {product} auto-memory",
            ),
        ),
        default_index=0,
        accent=PURPLE,
        stream=out,
        input_fn=input_fn,
    )
    if choice not in {"keep", "delete"}:
        raise RuntimeError(f"unsupported memory choice: {choice}")
    return cast(Literal["keep", "delete"], choice)


def _run_session_choice_v2(
    *,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
) -> Literal["retention_5d", "delete_inactive"]:
    if os.environ.get("SERENA_AGENT_INTERACTIVE") != "1":
        return "retention_5d"
    out = stream if stream is not None else sys.stdout
    client = os.environ.get("SERENA_AGENT_CLIENT", "codex")
    product = "Codex" if client == "codex" else "Claude"
    choice = select_option(
        f"Delete {product} sessions before launch?",
        options=(
            SelectOption(
                "retention_5d",
                "No full deletion — automatic cleanup after 5 days "
                "(default)",
            ),
            SelectOption(
                "delete_inactive",
                "Delete all inactive sessions — running sessions "
                "are preserved",
            ),
        ),
        default_index=0,
        accent=YELLOW,
        stream=out,
        input_fn=input_fn,
    )
    if choice not in {"retention_5d", "delete_inactive"}:
        raise RuntimeError(f"unsupported session choice: {choice}")
    return cast(
        Literal["retention_5d", "delete_inactive"],
        choice,
    )
```

Import `cast`, `YELLOW`, `render_inline_row`, `style_action_value`,
`CleanupResult`, and `cleanup_claude_inventory`.

- [ ] **Step 5: Implement colored action helpers**

Extract current memory deletion into `_run_memory_action_v2(choice, client, stream) -> MemoryDeleteResult`. Print a purple spin row before explicit deletion and a purple done/warn row after it.

Add:

```python
def _run_explicit_session_cleanup_v2(
    *,
    client: str,
    real_binary: str,
    stream: TextIO | None = None,
) -> CleanupResult:
    out = stream if stream is not None else sys.stdout
    out.write(
        render_inline_row(
            "sessions",
            f"deleting inactive {client} sessions · running preserved",
            status="spin",
            accent=YELLOW,
        )
    )
    out.flush()
    try:
        inventory = scan_inventory(
            **_memory_scan_kwargs(client),
            policy="all_inactive",
        )
        result = (
            cleanup_codex_inventory(
                inventory,
                codex_binary=real_binary,
            )
            if client == "codex"
            else cleanup_claude_inventory(inventory)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        result = CleanupResult(
            error=str(exc) or exc.__class__.__name__
        )

    value = (
        f"{result.deleted} sessions deleted · "
        f"{result.preserved_running} running preserved"
    )
    status = "done" if result.succeeded else "warn"
    if not result.succeeded:
        value = f"{value} · failed · {result.error}"
    out.write(
        render_inline_row(
            "sessions",
            value,
            status=status,
            accent=YELLOW,
        )
    )
    out.flush()
    return result
```

Update `_run_launch_prep_v2()` to print yellow `sessions` start/result rows for the default five-day policy instead of generic `cleanup` rows. Retain best-effort warnings.

- [ ] **Step 6: Reorder main and update the summary**

Gather both choices before `_run_memory_action_v2`. Then dispatch:

```python
memory_choice = _run_memory_choice_v2()
session_choice = _run_session_choice_v2()

memory_result = _run_memory_action_v2(
    choice=memory_choice,
    client=client_type,
    stream=out,
)
if not memory_result.succeeded:
    return 1

real_binary = find_real_binary(client_type)
if interactive and session_choice == "delete_inactive":
    cleanup_result = _run_explicit_session_cleanup_v2(
        client=client_type,
        real_binary=real_binary,
        stream=out,
    )
    if not cleanup_result.succeeded:
        return 1
    summary_state = LaunchPrepSummary(
        cleanup_deleted=cleanup_result.deleted,
        running_preserved=cleanup_result.preserved_running,
        full_cleanup=True,
        warnings=cleanup_result.warnings,
    )
elif interactive:
    summary_state = _run_launch_prep_v2(
        snapshot=inventory_snapshot,
        real_binary=real_binary,
        stream=out,
    )
else:
    summary_state = None
```

Add `running_preserved: int = 0` and `full_cleanup: bool = False` to `LaunchPrepSummary`. Change the final item label from `cleanup` to `sessions`. Full cleanup shows deleted plus running-preserved counts; default Claude keeps native-retention wording. Style the session value yellow.

Update every launcher test that stubs `_run_memory_choice_v2` to also stub `_run_session_choice_v2` as `retention_5d` unless it exercises explicit deletion.

- [ ] **Step 7: Run launcher tests and verify GREEN**

Run the Step 3 command.

Expected: all launcher tests PASS and no test launches a real client.

- [ ] **Step 8: Commit the orchestration unit**

```bash
git add -- local_dev/serena_mcp_management/serena_agent_launcher.py local_dev/tests/test_launcher_phases.py local_dev/tests/test_serena_launcher.py
git commit -m "feat(local_dev): ask independent startup cleanup choices"
```

---

### Task 6: Document, Verify, Refresh Graph, and Deploy Safely

**Files:**
- Modify: `local_dev/README.md:81-148,197-234`
- Track plan: `local_dev/docs/superpowers/plans/2026-07-22-agent-startup-cleanup-choices.md`
- Update generated: `graphify-out/`
- Runtime mirror: `/Users/hyun/Desktop/dotsync_config/agent_launcher/`

**Interfaces:**
- Consumes: Tasks 1-5 and the approved design.
- Produces: current private documentation, green tests, refreshed graph, byte-identical runtime copies, and non-destructive smoke evidence.

- [ ] **Step 1: Update private documentation**

Replace the old three-option memory section with both exact prompts. State these exact semantics:

```text
Memory default: keep all memory.
Session default: no full deletion; sessions older than five days still use normal cleanup.
Explicit session deletion: remove all inactive selected-product sessions and preserve running sessions.
Scope: Codex launch touches Codex only; Claude launch touches Claude only.
Colors: memory purple, sessions yellow.
Cancellation: both answers are collected before any mutation.
Memory concurrency: explicit memory deletion still refuses another same-product process.
Claude safety: explicit session deletion uses exact UUID bundles and never project purge.
```

Update the session mechanism table so Claude distinguishes native five-day retention from explicit bundle deletion. Remove the obsolete absolute claim that launcher code never deletes Claude transcripts.

- [ ] **Step 2: Run focused feature tests**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_ui_prompts.py local_dev/tests/test_ui_renderer.py local_dev/tests/test_session_inventory.py local_dev/tests/test_claude_session_inventory.py local_dev/tests/test_session_cleanup.py local_dev/tests/test_claude_session_cleanup.py local_dev/tests/test_launcher_phases.py local_dev/tests/test_serena_launcher.py -q
```

Expected: all focused tests PASS.

- [ ] **Step 3: Run the full private launcher suite**

```bash
.venv/bin/python3 -m pytest local_dev/tests -q
```

Expected: all tests PASS with no real user-session deletion.

- [ ] **Step 4: Review and refresh graphify**

```bash
git diff --check
git status --short
graphify update .
```

Expected: no whitespace errors; user-owned dirty paths remain untouched.

- [ ] **Step 5: Commit docs, plan, and exact graph changes**

Resolve graph paths with `git status --short graphify-out`, then run:

```bash
git add -- local_dev/README.md local_dev/docs/superpowers/plans/2026-07-22-agent-startup-cleanup-choices.md
git add -- graphify-out
git diff --cached --name-status
git commit -m "docs(local_dev): explain startup cleanup policies"
```

Before committing, require that no `AGENTS.md` or `.superpowers/` path is staged.

- [ ] **Step 6: Deploy through the supported installer**

```bash
make -C local_dev install-shim
```

Expected: the launcher is mirrored to `/Users/hyun/Desktop/dotsync_config/agent_launcher/` and the managed zsh block is refreshed. Existing running clients are not terminated.

- [ ] **Step 7: Verify byte-identical runtime files**

```bash
cmp local_dev/serena_mcp_management/ui.py /Users/hyun/Desktop/dotsync_config/agent_launcher/serena_mcp_management/ui.py
cmp local_dev/serena_mcp_management/session_inventory.py /Users/hyun/Desktop/dotsync_config/agent_launcher/serena_mcp_management/session_inventory.py
cmp local_dev/serena_mcp_management/session_cleanup.py /Users/hyun/Desktop/dotsync_config/agent_launcher/serena_mcp_management/session_cleanup.py
cmp local_dev/serena_mcp_management/serena_agent_launcher.py /Users/hyun/Desktop/dotsync_config/agent_launcher/serena_mcp_management/serena_agent_launcher.py
zsh -n /Users/hyun/.zshrc
```

Expected: every command returns 0 with no output.

- [ ] **Step 8: Run non-destructive smoke checks only**

```bash
zsh -lic 'codex --version'
zsh -lic 'claude --version'
```

Then rerun prompt tests against temporary homes. Do not choose destructive options against the real user home.

- [ ] **Step 9: Review final state**

```bash
git status --short
git log -6 --oneline
```

Expected: feature commits are present; unrelated user paths remain untouched; report any additional remaining path explicitly.
