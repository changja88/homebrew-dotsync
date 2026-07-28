# Codex Session Picker Stability Implementation Plan

> **Superseded:** The Codex reset no longer exposes a session picker; the
> picker implementation and its tests were removed when reset became a
> product-wide hard reset.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove full-block flicker from the Codex multi-select picker and make the selected session count unambiguous before deletion.

**Architecture:** Keep the Codex reset and deletion backend unchanged. Make `_read_multi_select_arrow` build a stable list of visible lines, compare it with the previous frame, and rewrite only changed lines; pass the catalog total into the existing launcher confirmation wording.

**Tech Stack:** Python 3.12+, stdlib-only ANSI terminal rendering, pytest.

## Global Constraints

- A non-empty Codex session selection still resets all Codex memory, history,
  logs, and snapshots.
- Only selected logical session groups are deleted.
- `a` selects or clears every option, including options outside the viewport.
- Normal arrow, `j`/`k`, Space, and `a` input must not use erase-below
  (`CSI J`) to repaint the picker.
- Final collapse and Ctrl+C may erase the complete picker block once.
- Do not add dependencies or persistent selection logs.
- Do not alter Claude session cleanup.
- Preserve unrelated notification-guard worktree changes.

---

### Task 1: Add picker feedback and no-flicker regression tests

**Files:**
- Modify: `local_dev/tests/test_ui_prompts.py`
- Modify: `local_dev/serena_mcp_management/ui.py`

**Interfaces:**
- Consumes: `_read_multi_select_arrow(question, options, stream, fd, accent, viewport_size=12)`.
- Produces: the same ordered `tuple[str, ...]`, with live `selected/total` feedback and changed-line ANSI rendering.

- [x] **Step 1: Write a failing changed-line rendering test**

Add a test that navigates down once and confirms. Its hand-derived observable
expectations are:

```python
output = stream.getvalue()
assert output.count("\x1b[J") == 1
assert "\x1b[2K" in output
assert "(1/3 · 0/3 selected)" in _strip_ansi(output)
assert "(2/3 · 0/3 selected)" in _strip_ansi(output)
```

The production regression this catches is restoring full-block
`CSI <lines> A` plus `CSI J` rendering on every key.

- [x] **Step 2: Write a failing off-viewport select-all test**

Create 29 literal options, feed `a`, Space, Enter, and assert that the first
value is omitted while values 2 through 29 are returned. Also assert:

```python
plain = _strip_ansi(stream.getvalue())
assert "28/29 selected" in plain
assert "a select all 29" in plain
```

The production regressions this catches are selecting only the visible
viewport and hiding the actual destructive selection count.

- [x] **Step 3: Run the two tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_ui_prompts.py::test_multi_select_arrow_rewrites_only_changed_lines \
  local_dev/tests/test_ui_prompts.py::test_multi_select_arrow_selects_all_options_outside_viewport \
  -q
```

Expected: both fail because navigation currently erases the full block and
the picker does not render `selected/total`.

- [x] **Step 4: Implement the minimal changed-line renderer**

Inside `_read_multi_select_arrow`, keep `rendered_lines: tuple[str, ...] | None`.
Build each frame from a header, the current viewport rows, and a footer. The
header uses:

```python
f"({cursor + 1}/{len(options)} · {len(selected)}/{len(options)} selected)"
```

The footer uses:

```python
f"Space toggle · a select all {len(options)} · Enter confirm"
```

For updates, compare the frame against `rendered_lines`, hide the cursor with
`CSI ?25l`, move to and erase only changed lines with `CSI 2K`, return below
the block, show the cursor with `CSI ?25h`, and flush once. Keep the existing
whole-block erase only for final collapse and Ctrl+C. Collapse to
`"{len(selected)}/{len(options)} selected"`.

- [x] **Step 5: Run focused UI tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_ui_prompts.py -q
```

Expected: every UI prompt test passes.

### Task 2: Make final destructive scope explicit

**Files:**
- Modify: `local_dev/tests/test_launcher_phases.py`
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py`
- Modify: `local_dev/README.md`

**Interfaces:**
- Consumes: `selected: tuple[str, ...]` and `options: tuple[SelectOption, ...]`.
- Produces: an unchanged `CodexResetSelection`, with confirmation text containing selected and total counts.

- [x] **Step 1: Write a failing launcher confirmation assertion**

Use a catalog containing three literal sessions, select one, confirm, and
assert:

```python
assert "Delete 1 of 3 selected session(s)" in plain
```

The production regression this catches is a confirmation that omits the
catalog denominator and leaves the bulk scope ambiguous.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_launcher_phases.py::test_codex_session_picker_confirms_combined_reset \
  -q
```

Expected: FAIL because the current prompt says only `Delete 1 selected
session(s)`.

- [x] **Step 3: Implement the minimal confirmation wording**

Change only the prompt passed to `confirm`:

```python
f"Delete {len(selected)} of {len(options)} selected session(s) and "
"reset ALL Codex memory, history, logs, and snapshots?"
```

Do not change `CodexResetSelection` or deletion behavior.

- [x] **Step 4: Update internal launcher documentation**

Document the live `selected/total` header, `a select all <total>` footer, and
`selected of total` confirmation in `local_dev/README.md`. State explicitly
that selecting at least one session still resets all Codex memory and traces.

- [x] **Step 5: Run focused launcher tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py -q
```

Expected: every launcher phase test passes.

### Task 3: Verify and promote the runtime copy

**Files:**
- Verify: `local_dev/serena_mcp_management/ui.py`
- Verify: `local_dev/serena_mcp_management/serena_agent_launcher.py`
- Promote to: `~/Desktop/dotsync_config/agent_launcher/`

**Interfaces:**
- Consumes: the complete local launcher source tree.
- Produces: a runtime mirror with byte-identical picker and launcher modules.

- [x] **Step 1: Run the complete local development suite**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests -q
.venv/bin/python3 -m compileall -q local_dev/serena_mcp_management
git diff --check
```

Expected: every command exits `0`.

- [x] **Step 2: Install the verified shim**

Run:

```bash
make -C local_dev install-shim
```

Expected: the command succeeds and refreshes the stable runtime copy plus the
managed `.zshrc` block.

- [x] **Step 3: Verify source/runtime parity**

Run:

```bash
shasum -a 256 \
  local_dev/serena_mcp_management/ui.py \
  "$HOME/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/ui.py"
shasum -a 256 \
  local_dev/serena_mcp_management/serena_agent_launcher.py \
  "$HOME/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/serena_agent_launcher.py"
```

Expected: each source file and its runtime counterpart have the same digest.

- [x] **Step 4: Review the final diff without staging unrelated work**

Run:

```bash
git status --short
git diff --check
git diff -- \
  local_dev/README.md \
  local_dev/docs/superpowers/specs/2026-07-28-codex-reset-entry-choice-design.md \
  local_dev/docs/superpowers/plans/2026-07-28-codex-session-picker-stability.md \
  local_dev/serena_mcp_management/ui.py \
  local_dev/serena_mcp_management/serena_agent_launcher.py \
  local_dev/tests/test_ui_prompts.py \
  local_dev/tests/test_launcher_phases.py
```

Expected: only the approved picker feedback, rendering, documentation, and
existing Codex reset work appear; notification-guard files remain untouched.

### Task 4: Harden physical-line and cursor recovery invariants

**Files:**
- Modify: `local_dev/tests/test_ui_prompts.py`
- Modify: `local_dev/serena_mcp_management/ui.py`
- Modify: `local_dev/docs/superpowers/specs/2026-07-28-codex-reset-entry-choice-design.md`

**Interfaces:**
- Consumes: the picker controlling TTY file descriptor and plain prompt labels.
- Produces: one physical line per logical picker row and best-effort cursor restoration after an interrupted update.

- [x] **Step 1: Write and verify failing narrow-terminal test**

Use a 40-column terminal, long question, long ASCII rows, and a Korean wide-cell
row. Assert all five logical rows fit inside 39 cells, the Korean label fits
exactly 15 wide characters plus an ellipsis, and the collapsed row also fits.
The test must fail against the unbounded renderer.

- [x] **Step 2: Fit picker text to the controlling terminal**

Read `os.get_terminal_size(fd).columns` with an 80-column fallback, reserve the
last column, normalize non-printable text, and truncate by Unicode combining
and East Asian cell width. Keep the exact selected/total value visible ahead
of the question on narrow terminals.

- [x] **Step 3: Write and verify failing cursor-interruption test**

Use a stream that accepts cursor-hide then raises `KeyboardInterrupt` before
cursor-show. Assert a later cursor-show sequence is emitted and terminal
attributes are restored. The test must fail against the renderer without
exception recovery.

- [x] **Step 4: Restore the cursor on interrupted update output**

Keep the single write and flush on the normal path. On any update write or
flush exception, attempt a cursor-show write and flush, ignore only a failure
of that recovery attempt, and re-raise the original exception.

- [x] **Step 5: Run focused UI regression tests**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_ui_prompts.py -q
```

Expected: `26 passed`.

- [x] **Step 6: Preserve the original interrupt when final clear fails**

Add a stream that raises the original `KeyboardInterrupt` after cursor hide,
then raises `OSError` for both cursor recovery and block clear. Verify the
original interrupt still escapes and terminal attributes are restored. Make
the Ctrl+C block clear best-effort without replacing that exception.

- [x] **Step 7: Keep counts visible in an ultranarrow terminal**

Use a seven-column terminal and assert every logical row fits within the
six-cell no-wrap budget, the header retains `selected/total`, and the footer
retains `a N`. Shorten prompt and row prefixes and use progressively compact
footer variants when the normal forms do not fit.

- [x] **Step 8: Re-run focused UI regression tests**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_ui_prompts.py -q
```

Expected: `28 passed`.
