# Codex Reset Entry Choice Implementation Plan

> **Superseded:** The approved implementation now performs one confirmed full
> Codex conversation-state reset with no per-session picker. See
> `../specs/2026-07-28-codex-reset-entry-choice-design.md` and
> `local_dev/README.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-safe entry choice so the Codex session picker appears only after the user chooses to select sessions and reset all memories.

**Architecture:** Keep the new gate inside `_run_session_choice_v2`, immediately before its detailed Codex reset catalog scan. The keep branch returns an empty `CodexResetSelection`; the reset branch reuses the existing scan, multi-select, confirmation, and deletion orchestration unchanged.

**Tech Stack:** Python 3.12+, stdlib-only terminal UI, pytest.

## Global Constraints

- The default choice is `Keep all sessions and memories (default)`.
- The destructive choice is `Select sessions to delete and reset all memories`.
- Keeping is an exact no-op and must not enter the reset-specific catalog flow.
- The earlier preflight inventory may still scan session metadata for aggregate
  counts.
- Selecting sessions remains per-session; confirming any non-empty selection resets all Codex memories and related traces.
- Do not add an independent Codex memory prompt.
- Do not change Claude behavior or `delete_selected_codex_sessions`.
- Update only internal `local_dev` documentation; do not edit the public root README.
- Preserve unrelated notification-guard worktree changes and never stage them.

---

### Task 1: Add the Codex reset entry gate

**Files:**
- Modify: `local_dev/tests/test_launcher_phases.py`
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py`
- Modify: `local_dev/README.md`

**Interfaces:**
- Consumes: `select_option(...) -> str`, `CodexResetSelection`, and the existing `scan_codex_session_catalog`, `multi_select`, and `confirm` flow.
- Produces: `_run_session_choice_v2(...) -> Literal["retention_5d", "delete_inactive"] | CodexResetSelection` with a default-safe Codex entry choice.

- [ ] **Step 1: Write focused failing tests**

Replace the current direct-picker default test with a keep-path test that
proves it does not enter the reset scan:

```python
def test_codex_cleanup_defaults_to_keep_before_reset_scan(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")

    def unexpected_scan(**kwargs):
        raise AssertionError("keep choice must not enter the reset scan")

    monkeypatch.setattr(
        launcher,
        "scan_codex_session_catalog",
        unexpected_scan,
    )
    memory_out = io.StringIO()
    out = io.StringIO()

    memory = launcher._run_memory_choice_v2(
        stream=memory_out,
        input_fn=lambda: "",
    )
    selection = launcher._run_session_choice_v2(
        stream=out,
        input_fn=lambda: "",
    )

    assert memory == "keep"
    assert memory_out.getvalue() == ""
    assert isinstance(selection, launcher.CodexResetSelection)
    assert selection.root_ids == ()
    plain = _strip_ansi(out.getvalue())
    assert "Reset Codex sessions and memories before launch?" in plain
    assert "Keep all sessions and memories (default)" in plain
    assert "Select sessions to delete and reset all memories" in plain
    assert "Select Codex sessions to force-delete" not in plain
```

Add a reset-path test that proves the detailed scan occurs only after choosing
the second option:

```python
def test_codex_reset_choice_scans_before_showing_empty_catalog(
    monkeypatch,
):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setenv("SERENA_AGENT_INTERACTIVE", "1")
    calls = []
    monkeypatch.setattr(
        launcher,
        "scan_codex_session_catalog",
        lambda **kwargs: calls.append(kwargs)
        or launcher.CodexSessionCatalog(homes=(), sessions=()),
    )
    out = io.StringIO()

    selection = launcher._run_session_choice_v2(
        stream=out,
        input_fn=lambda: "2",
    )

    assert isinstance(selection, launcher.CodexResetSelection)
    assert selection.root_ids == ()
    assert len(calls) == 1
    assert "no persisted Codex sessions found" in _strip_ansi(out.getvalue())
```

Update the existing confirmed-selection answers from `("1", "yes")` to
`("2", "1", "yes")`. Update the scan-failure test to supply
`input_fn=lambda: "2"` so it explicitly enters the reset branch.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_launcher_phases.py::test_codex_cleanup_defaults_to_keep_before_reset_scan \
  local_dev/tests/test_launcher_phases.py::test_codex_reset_choice_scans_before_showing_empty_catalog \
  local_dev/tests/test_launcher_phases.py::test_codex_session_picker_confirms_combined_reset \
  local_dev/tests/test_launcher_phases.py::test_codex_session_picker_scan_failure_skips_reset_and_keeps_launchable \
  -q
```

Expected: the new keep-path test fails because `_run_session_choice_v2`
immediately calls `scan_codex_session_catalog`; the reset-path inputs also do
not match the missing top-level prompt.

- [ ] **Step 3: Implement the minimal entry choice**

At the start of the interactive Codex branch in `_run_session_choice_v2`, add:

```python
reset_choice = select_option(
    "Reset Codex sessions and memories before launch?",
    options=(
        SelectOption(
            "keep",
            "Keep all sessions and memories (default)",
        ),
        SelectOption(
            "reset",
            "Select sessions to delete and reset all memories",
        ),
    ),
    default_index=0,
    accent=AMBER,
    stream=out,
    input_fn=input_fn,
)
if reset_choice == "keep":
    return CodexResetSelection(
        catalog=CodexSessionCatalog(homes=(), sessions=()),
        root_ids=(),
    )
if reset_choice != "reset":
    raise RuntimeError(f"unsupported Codex reset choice: {reset_choice}")
```

Leave the existing scan, empty-catalog handling, multi-select, and final
confirmation immediately after this gate.

- [ ] **Step 4: Update the internal launcher documentation**

Change `local_dev/README.md` so the Codex section first shows:

```text
? Reset Codex sessions and memories before launch?
  ▶ Keep all sessions and memories (default)
    Select sessions to delete and reset all memories
```

Then show the existing force-delete picker as the second stage. State that the
default keep choice does not open the reset catalog or delete data, while the
second choice preserves the existing empty-selection and confirmation rules.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_launcher_phases.py::test_codex_cleanup_defaults_to_keep_before_reset_scan \
  local_dev/tests/test_launcher_phases.py::test_codex_reset_choice_scans_before_showing_empty_catalog \
  local_dev/tests/test_launcher_phases.py::test_codex_session_picker_confirms_combined_reset \
  local_dev/tests/test_launcher_phases.py::test_codex_session_picker_scan_failure_skips_reset_and_keeps_launchable \
  -q
```

Expected: `4 passed`.

- [ ] **Step 6: Run regression and static verification**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py -q
.venv/bin/python3 -m pytest local_dev/tests -q
.venv/bin/python3 -m compileall -q local_dev/serena_mcp_management
git diff --check
```

Expected: every command exits `0` with no failures or whitespace errors.

- [ ] **Step 7: Promote the verified launcher and check parity**

Run:

```bash
make -C local_dev install-shim
shasum -a 256 \
  local_dev/serena_mcp_management/serena_agent_launcher.py \
  "$HOME/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/serena_agent_launcher.py"
```

Expected: `install-shim` succeeds and both launcher files have the same SHA-256
digest. The managed zsh block continues to point at the stable runtime copy.

- [ ] **Step 8: Commit only the reset-related implementation**

Before staging, inspect `git status --short` and exclude these unrelated files:

```text
local_dev/docs/notification-guard-spec.md
local_dev/serena_mcp_management/notification_guard.py
local_dev/tests/test_notification_guard.py
local_dev/docs/notification-rebuild-evidence.md
```

Stage only the three files changed by this task:

```bash
git add \
  local_dev/README.md \
  local_dev/serena_mcp_management/serena_agent_launcher.py \
  local_dev/tests/test_launcher_phases.py
git diff --cached --check
git commit -m "feat(local_dev): gate Codex session and memory reset"
```

If the staged diff contains pre-existing reset implementation required by
these files, report that fact before committing rather than silently claiming
the commit is isolated.
