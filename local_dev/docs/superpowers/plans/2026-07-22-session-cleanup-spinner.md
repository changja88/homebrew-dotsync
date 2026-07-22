# Session Cleanup Spinner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Animate the existing session-cleanup progress row for the complete duration of synchronous cleanup without changing any cleanup or launch behavior.

**Architecture:** Extend the existing inline-row renderer with an optional spinner frame, then wrap `_run_explicit_session_cleanup_v2` with the existing `SpinnerTicker`. The cleanup stays on the caller thread; the ticker only redraws the same terminal row and is joined before the final result row is written.

**Tech Stack:** Python 3.12+, stdlib `threading`, existing `SpinnerTicker` and TUI renderer, pytest.

## Global Constraints

- Change only the private `local_dev` launcher; do not change the public `dotsync` package, root README, root Makefile, or Homebrew formula.
- Do not change cleanup eligibility, discovery, deletion order, command count, command timeouts, failure handling, partial-mutation reporting, or child-launch continuation.
- Keep runtime dependencies stdlib-only.
- Preserve the existing yellow session accent and terminal glyph vocabulary.
- Preserve all pre-existing worktree changes and stage only the spinner-specific hunks for implementation commits.

---

### Task 1: Make Inline Spinner Frames Selectable

**Files:**
- Modify: `local_dev/serena_mcp_management/ui.py:294-313`
- Test: `local_dev/tests/test_ui_renderer.py:141-153`

**Interfaces:**
- Consumes: `_marker_for(status: ItemStatus, *, spin_frame: int = 0, accent: str = PURPLE) -> str`.
- Produces: `render_inline_row(label: str, value: str, *, status: ItemStatus, accent: str | None = None, spin_frame: int = 0) -> str`.

- [ ] **Step 1: Write the failing renderer test**

Add this test after `test_render_inline_row_colors_session_start_with_requested_accent`:

```python
def test_render_inline_row_uses_requested_spinner_frame():
    first = _strip_ansi(
        render_inline_row(
            "sessions",
            "deleting inactive sessions",
            status="spin",
            accent=YELLOW,
            spin_frame=0,
        )
    )
    second = _strip_ansi(
        render_inline_row(
            "sessions",
            "deleting inactive sessions",
            status="spin",
            accent=YELLOW,
            spin_frame=1,
        )
    )

    assert first.startswith("  ⠋ sessions")
    assert second.startswith("  ⠙ sessions")
```

- [ ] **Step 2: Run the new test and confirm Red**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_ui_renderer.py::test_render_inline_row_uses_requested_spinner_frame \
  -v
```

Expected: FAIL with `TypeError: render_inline_row() got an unexpected keyword argument 'spin_frame'`.

- [ ] **Step 3: Pass the requested frame to the existing marker renderer**

Change `render_inline_row` to:

```python
def render_inline_row(
    label: str,
    value: str,
    *,
    status: ItemStatus,
    accent: str | None = None,
    spin_frame: int = 0,
) -> str:
    """Render one BoxModel-style row as a standalone line (no surrounding box).

    Used by the launcher to surface post-install state changes below the
    preflight overview. Redrawing the full box would flash the banner art
    again and push the original overview out of view; an inline row keeps
    the chronological flow intact and matches the row format inside the
    box so the visual style stays consistent.
    """
    marker = _marker_for(
        status,
        spin_frame=spin_frame,
        accent=accent or PURPLE,
    )
    label_color = accent or MINT
    label_text = _ansi(label_color, f"{label:<10}")
    value_text = _ansi(accent, value) if accent is not None else value
    return f"  {marker} {label_text}  {value_text}\n"
```

- [ ] **Step 4: Run the renderer tests and confirm Green**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_ui_renderer.py -q
```

Expected: all tests in `test_ui_renderer.py` PASS.

- [ ] **Step 5: Review and commit only Task 1 hunks**

Run:

```bash
git diff --check -- \
  local_dev/serena_mcp_management/ui.py \
  local_dev/tests/test_ui_renderer.py
git add -p \
  local_dev/serena_mcp_management/ui.py \
  local_dev/tests/test_ui_renderer.py
git diff --cached --check
git commit -m "feat(local_dev): support inline spinner frames"
```

Expected: one commit containing only the renderer parameter and its focused test.

---

### Task 2: Animate Explicit Session Cleanup

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py:1576-1645`
- Test: `local_dev/tests/test_launcher_phases.py:1948-2110`

**Interfaces:**
- Consumes: `SpinnerTicker(on_tick: Callable[[int], None], interval: float = 0.1)`, `SpinnerTicker.start() -> None`, `SpinnerTicker.stop() -> None`, and Task 1's `render_inline_row(..., spin_frame: int = 0) -> str`.
- Produces: unchanged `_run_explicit_session_cleanup_v2(*, client: str, real_binary: str, stream: TextIO | None = None) -> CleanupResult` with an in-place animated progress row.

- [ ] **Step 1: Write failing launcher tests for animation and exception cleanup**

Add these tests before the existing explicit-session-cleanup tests:

```python
def test_explicit_session_cleanup_animates_until_result(monkeypatch):
    out = io.StringIO()

    class FakeSpinnerTicker:
        def __init__(self, *, on_tick, interval):
            assert interval == 0.1
            self._on_tick = on_tick

        def start(self):
            self._on_tick(1)
            self._on_tick(2)

        def stop(self):
            out.write("<ticker-stopped>")

    monkeypatch.setattr(launcher, "SpinnerTicker", FakeSpinnerTicker)
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
        lambda inventory, codex_binary: launcher.CleanupResult(deleted=1),
    )

    result = launcher._run_explicit_session_cleanup_v2(
        client="codex",
        real_binary="/fake/codex",
        stream=out,
    )

    text = _strip_ansi(out.getvalue())
    assert result.succeeded
    assert "\r  ⠙ sessions" in text
    assert "\r  ⠹ sessions" in text
    assert text.index("<ticker-stopped>") < text.index("✓ sessions")


def test_explicit_session_cleanup_stops_spinner_when_scan_raises(monkeypatch):
    stopped = []

    class FakeSpinnerTicker:
        def __init__(self, *, on_tick, interval):
            self._on_tick = on_tick

        def start(self):
            self._on_tick(1)

        def stop(self):
            stopped.append(True)

    def raise_scan_error(**kwargs):
        raise RuntimeError("injected scan failure")

    monkeypatch.setattr(launcher, "SpinnerTicker", FakeSpinnerTicker)
    monkeypatch.setattr(launcher, "scan_inventory", raise_scan_error)

    result = launcher._run_explicit_session_cleanup_v2(
        client="codex",
        real_binary="/fake/codex",
        stream=io.StringIO(),
    )

    assert not result.succeeded
    assert result.error == "injected scan failure"
    assert stopped == [True]
```

- [ ] **Step 2: Run the new launcher tests and confirm Red**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_launcher_phases.py::test_explicit_session_cleanup_animates_until_result \
  local_dev/tests/test_launcher_phases.py::test_explicit_session_cleanup_stops_spinner_when_scan_raises \
  -v
```

Expected: both tests FAIL because `_run_explicit_session_cleanup_v2` does not construct or stop `SpinnerTicker`, and the output contains only the static first frame.

- [ ] **Step 3: Wrap the unchanged cleanup body with the ticker**

Replace `_run_explicit_session_cleanup_v2` with:

```python
def _run_explicit_session_cleanup_v2(
    *,
    client: str,
    real_binary: str,
    stream: TextIO | None = None,
) -> CleanupResult:
    """Delete all inactive sessions using a fresh product-scoped scan."""
    out = stream if stream is not None else sys.stdout
    progress_value = (
        f"deleting inactive {client} sessions · running preserved"
    )

    def on_tick(frame: int) -> None:
        row = render_inline_row(
            "sessions",
            progress_value,
            status="spin",
            accent=YELLOW,
            spin_frame=frame,
        ).removesuffix("\n")
        out.write(f"\r{row}\x1b[K")
        out.flush()

    on_tick(0)
    ticker = SpinnerTicker(on_tick=on_tick, interval=0.1)
    ticker.start()
    try:
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
    finally:
        ticker.stop()

    status = "done" if result.succeeded else "warn"
    deleted_label = "sessions deleted"
    if not result.succeeded:
        deleted_label = "sessions fully deleted"
    value = (
        f"{result.deleted} {deleted_label} · "
        f"{result.preserved_running} running preserved"
    )
    if result.partial_mutations:
        operation_label = (
            "operation" if result.partial_mutations == 1 else "operations"
        )
        details = result.partial_mutation_details[:3]
        detail_value = "; ".join(details)
        remainder = result.partial_mutations - len(details)
        if remainder > 0:
            detail_value = f"{detail_value}; +{remainder} more"
        value = (
            f"{value} · partial mutation: {result.partial_mutations} "
            f"{operation_label} completed"
        )
        if detail_value:
            value = f"{value} ({detail_value})"
    if not result.succeeded:
        value = f"{value} · failed · {result.error}"
    out.write("\r\x1b[K")
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

- [ ] **Step 4: Run focused launcher and UI tests and confirm Green**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_launcher_phases.py \
  local_dev/tests/test_ui_renderer.py \
  local_dev/tests/test_ui_progress.py \
  -q
```

Expected: all selected tests PASS.

- [ ] **Step 5: Run the complete private-launcher suite**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests -q
```

Expected: all `local_dev` tests PASS with no failures or errors.

- [ ] **Step 6: Review and commit only Task 2 hunks**

Run:

```bash
git diff --check -- \
  local_dev/serena_mcp_management/serena_agent_launcher.py \
  local_dev/tests/test_launcher_phases.py
git add -p \
  local_dev/serena_mcp_management/serena_agent_launcher.py \
  local_dev/tests/test_launcher_phases.py
git diff --cached --check
git commit -m "fix(local_dev): animate session cleanup progress"
```

Expected: one commit containing only the explicit-cleanup spinner and its focused tests.

- [ ] **Step 7: Mirror the verified launcher to the stable runtime location**

Run:

```bash
make -C local_dev install-shim
```

Expected: exit status 0 and the managed `~/.zshrc` block still points to `/Users/hyun/Desktop/dotsync_config/agent_launcher`.

- [ ] **Step 8: Verify the mirrored source files byte-for-byte**

Run:

```bash
cmp -s \
  local_dev/serena_mcp_management/serena_agent_launcher.py \
  /Users/hyun/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/serena_agent_launcher.py
cmp -s \
  local_dev/serena_mcp_management/ui.py \
  /Users/hyun/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/ui.py
```

Expected: both commands exit 0 with no output.
