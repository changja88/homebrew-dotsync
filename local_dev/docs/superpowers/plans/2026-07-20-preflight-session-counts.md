# Preflight Session Counts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render Codex logical groups and physical JSONL records together, move the five-day condition to a fully spelled-out cleanup row, and give totals, policy, deletion, and preservation distinct semantic colors for both Codex and Claude.

**Architecture:** `session_inventory.py` will keep the existing logical `sessions` statistics and add an optional physical `records` statistic to the immutable snapshot. `serena_agent_launcher.py` will format separate `sessions` and `cleanup` values, while `ui.py` will color complete semantic segments instead of regex-coloring fragments of the old sentence. The existing discovery and cleanup algorithms remain unchanged.

**Tech Stack:** Python 3.12+, stdlib dataclasses/regex, pytest, ANSI terminal styling.

## Global Constraints

- Change only `local_dev/`; do not modify public `dotsync` runtime, root README, root Makefile, or Homebrew formula.
- Preserve Codex discovery, five-day eligibility, active-session safeguards, official `codex delete`, and source-before-Orca ordering.
- Preserve Claude `cleanupPeriodDays: 5` native cleanup and user `--settings` bypass.
- Do not add dependencies.
- Do not touch memory cleanup.
- Preserve the user's existing `AGENTS.md` modification.
- Use no `retention` row and no `g`/`r` abbreviations.

---

### Task 1: Add Physical Record Statistics to the Inventory

**Files:**
- Modify: `local_dev/serena_mcp_management/session_inventory.py:67-75,352-431,435-462`
- Test: `local_dev/tests/test_session_inventory.py:138-195`

**Interfaces:**
- Consumes: existing `CountStats(total: int, to_delete: int, to_keep: int)` and `CodexCleanupTarget.files`.
- Produces: `AgentInventory.records: CountStats | None`; real Codex and Claude scans populate it.

- [ ] **Step 1: Write failing record-count tests**

Update the two existing Codex grouping tests and the Claude all-project test with assertions equivalent to:

```python
assert inventory.sessions == CountStats(total=1, to_delete=0, to_keep=1)
assert inventory.records == CountStats(total=3, to_delete=0, to_keep=3)
```

```python
assert inventory.sessions == CountStats(total=1, to_delete=1, to_keep=0)
assert inventory.records == CountStats(total=3, to_delete=3, to_keep=0)
```

```python
assert inventory.sessions == CountStats(total=2, to_delete=1, to_keep=1)
assert inventory.records == CountStats(total=2, to_delete=1, to_keep=1)
```

The first Codex case proves that a root hard-linked into Orca plus a child is one logical group but three physical records. The second proves that every physical member of an eligible group is counted for deletion.

- [ ] **Step 2: Run the targeted tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_session_inventory.py::test_scan_claude_counts_all_projects_without_memory_or_subagents \
  local_dev/tests/test_session_inventory.py::test_scan_codex_groups_all_homes_and_uses_descendant_activity \
  local_dev/tests/test_session_inventory.py::test_scan_codex_builds_source_before_orca_delete_plan -q
```

Expected: FAIL because `AgentInventory` has no `records` attribute.

- [ ] **Step 3: Implement physical record statistics**

Add the snapshot field after `criteria`:

```python
@dataclass(frozen=True)
class AgentInventory:
    client: str
    sessions: CountStats
    criteria: str
    records: CountStats | None = None
    codex_targets: tuple[CodexCleanupTarget, ...] = ()
    scanned_paths: tuple[Path, ...] = ()
    session_dirs: tuple[Path, ...] = ()
    warnings: tuple[str, ...] = ()
```

Before returning the Codex inventory, compute physical files from the immutable target snapshot:

```python
record_total = len(scanned_paths)
records_to_delete = sum(len(target.files) for target in targets)
record_stats = CountStats(
    total=record_total,
    to_delete=records_to_delete,
    to_keep=record_total - records_to_delete,
)
```

Pass `records=record_stats` to the Codex `AgentInventory`. In the Claude scanner, create one `CountStats` local and pass it as both `sessions` and `records`, because Claude's launcher inventory already counts top-level records.

- [ ] **Step 4: Run the targeted inventory tests and verify GREEN**

Run the command from Step 2.

Expected: `3 passed`.

- [ ] **Step 5: Run all session inventory and cleanup tests**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_session_inventory.py \
  local_dev/tests/test_session_cleanup.py -q
```

Expected: all tests pass; cleanup command planning is unchanged.

- [ ] **Step 6: Commit the inventory slice**

```bash
git add \
  local_dev/serena_mcp_management/session_inventory.py \
  local_dev/tests/test_session_inventory.py
git commit -m "feat(local_dev): expose physical session record counts"
```

---

### Task 2: Render Readable Session and Cleanup Rows with Semantic Colors

**Files:**
- Modify: `local_dev/serena_mcp_management/ui.py:96-146`
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py:55-70,114-124,612-728`
- Test: `local_dev/tests/test_ui_style.py:1-55`
- Test: `local_dev/tests/test_launcher_phases.py:89-115,161-244,1430-1452`

**Interfaces:**
- Consumes: `AgentInventory.sessions`, `AgentInventory.records`, and `RETENTION_DAYS`.
- Produces: `_sessions_value(inventory) -> str`, `_cleanup_value(inventory) -> str`, `style_session_counts(phrase) -> str`, and `style_cleanup_segments(condition, delete, keep) -> str`.

- [ ] **Step 1: Write failing ANSI styling tests**

Replace the old `style_inventory_counts` tests with real semantic-segment assertions:

```python
def test_style_session_counts_colors_complete_totals_pink():
    result = style_session_counts("codex 58 groups · 855 records")
    assert f"\x1b[{PINK}m58 groups\x1b[0m" in result
    assert f"\x1b[{PINK}m855 records\x1b[0m" in result


def test_style_cleanup_segments_colors_each_meaning():
    result = style_cleanup_segments(
        "inactive longer than 5 days",
        "delete 35 groups / 358 records",
        "keep 23 groups / 497 records",
    )
    assert f"\x1b[{PURPLE}minactive longer than 5 days\x1b[0m" in result
    assert "\x1b[33mdelete 35 groups / 358 records\x1b[0m" in result
    assert f"\x1b[{MINT}mkeep 23 groups / 497 records\x1b[0m" in result
```

- [ ] **Step 2: Write failing preflight output tests**

Extend `_stub_preflight_inventory` to accept `records_total`, `records_to_delete`, and `records_to_keep`, then populate `AgentInventory.records`. Assert the plain ANSI-stripped Codex output contains:

```python
assert "codex 58 groups · 855 records" in plain
assert (
    "inactive longer than 5 days · "
    "delete 35 groups / 358 records · keep 23 groups / 497 records"
) in plain
assert "criteria" not in plain
assert "retention" not in plain
```

Assert Claude output contains:

```python
assert "claude 108 records" in plain
assert (
    "inactive longer than 5 days · native delete 75 records · keep 33 records"
) in plain
```

Update scan-unavailable coverage to expect `sessions` and `cleanup` rows instead of `sessions` and `criteria`.

- [ ] **Step 3: Run style and preflight tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_ui_style.py \
  local_dev/tests/test_launcher_phases.py -q
```

Expected: FAIL because the new style functions and cleanup row do not exist and the old criteria row is still rendered.

- [ ] **Step 4: Implement semantic style helpers**

Replace `style_inventory_counts` with:

```python
def style_session_counts(phrase: str) -> str:
    if not phrase:
        return phrase
    return re.sub(
        r"\d+ (?:groups|records)",
        lambda match: _ansi(PINK, match.group(0)),
        phrase,
    )


def style_cleanup_segments(condition: str, delete: str, keep: str) -> str:
    return " · ".join(
        (
            _ansi(PURPLE, condition),
            _ansi("33", delete),
            _ansi(MINT, keep),
        )
    )
```

- [ ] **Step 5: Implement the two preflight values**

Import `RETENTION_DAYS`, `style_session_counts`, and `style_cleanup_segments`. Format Codex and Claude without abbreviations:

```python
def _sessions_value(inventory: AgentInventory) -> str:
    records = inventory.records or inventory.sessions
    if inventory.client == "codex":
        phrase = (
            f"codex {inventory.sessions.total} groups · "
            f"{records.total} records"
        )
    else:
        phrase = f"claude {records.total} records"
    return style_session_counts(phrase)


def _cleanup_value(inventory: AgentInventory) -> str:
    records = inventory.records or inventory.sessions
    condition = f"inactive longer than {RETENTION_DAYS} days"
    if inventory.client == "codex":
        delete = (
            f"delete {inventory.sessions.to_delete} groups / "
            f"{records.to_delete} records"
        )
        keep = (
            f"keep {inventory.sessions.to_keep} groups / "
            f"{records.to_keep} records"
        )
    else:
        delete = f"native delete {records.to_delete} records"
        keep = f"keep {records.to_keep} records"
    return style_cleanup_segments(condition, delete, keep)
```

In `_preflight_box`, render `Item(id="cleanup", label="cleanup", ...)` immediately after `sessions` and remove the `criteria` item. On scan failure, use `cleanup_value = style_criteria("scan unavailable")` and preserve the warning sessions row.

- [ ] **Step 6: Run style and preflight tests and verify GREEN**

Run the command from Step 3.

Expected: all tests pass.

- [ ] **Step 7: Run launcher-focused regression tests**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_serena_launcher.py \
  local_dev/tests/test_ui_renderer.py \
  local_dev/tests/test_ui_state.py \
  local_dev/tests/test_ui_style.py \
  local_dev/tests/test_launcher_phases.py -q
```

Expected: all tests pass with no old `criteria` output assertions remaining.

- [ ] **Step 8: Commit the TUI slice**

```bash
git add \
  local_dev/serena_mcp_management/ui.py \
  local_dev/serena_mcp_management/serena_agent_launcher.py \
  local_dev/tests/test_ui_style.py \
  local_dev/tests/test_launcher_phases.py
git commit -m "feat(local_dev): clarify session cleanup preflight"
```

---

### Task 3: Document, Verify, Graph, and Install the Runtime Copy

**Files:**
- Modify: `local_dev/README.md:145-155`
- Verify: `local_dev/serena_mcp_management/`
- Install to: `~/Desktop/dotsync_config/agent_launcher/` and managed `~/.zshrc` block via the existing Make target.

**Interfaces:**
- Consumes: final preflight text and existing `make -C local_dev install-shim` workflow.
- Produces: documented and installed launcher behavior; no public dotsync changes.

- [ ] **Step 1: Update the internal README**

Replace the old `N total . D to delete . K to keep` wording with the exact two-row Codex and Claude examples from the design spec. State that policy is purple, delete is yellow, keep is mint, and totals are pink.

- [ ] **Step 2: Run focused and full verification**

Run:

```bash
.venv/bin/python3 -m py_compile \
  local_dev/serena_mcp_management/session_inventory.py \
  local_dev/serena_mcp_management/ui.py \
  local_dev/serena_mcp_management/serena_agent_launcher.py
.venv/bin/python3 -m pytest local_dev/tests -q
.venv/bin/python3 -m pytest tests -q
```

Expected: compilation succeeds; all local launcher and public dotsync tests pass.

- [ ] **Step 3: Render a real read-only Codex preflight model**

Capture the ANSI-stripped `_preflight_box` output without confirming launch or invoking cleanup. Verify it contains full `groups`, `records`, and `inactive longer than 5 days` text and contains neither `criteria` nor `retention` rows.

- [ ] **Step 4: Refresh graphify**

Run:

```bash
graphify update .
```

Expected: AST graph rebuild completes.

- [ ] **Step 5: Commit documentation**

```bash
git add local_dev/README.md
git commit -m "docs(local_dev): explain session preflight counts"
```

- [ ] **Step 6: Install and compare the runtime copy**

Run:

```bash
make -C local_dev install-shim
diff -qr --exclude='__pycache__' --exclude='*.pyc' \
  local_dev/serena_mcp_management \
  /Users/hyun/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management
```

Expected: installation reports the managed `~/.zshrc` block update and `diff` prints nothing.

- [ ] **Step 7: Final repository check**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` is clean and only the user's pre-existing `M AGENTS.md` remains. Stop and remove the generated visual-companion session before the final status check so `.superpowers/` is not left untracked.
