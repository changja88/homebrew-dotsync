# Preflight Information Hierarchy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render fully named Serena MCP process counts and one hierarchical sessions tree using the user-selected data-type comparison layout.

**Architecture:** Extend the existing flat `Item` renderer so newline-separated values align under the first value column and participate in border-width calculation line by line. Replace the independent preflight `sessions` and `cleanup` values with one styled multiline sessions value; keep inventory and cleanup behavior unchanged.

**Tech Stack:** Python 3.12+, stdlib-only runtime, pytest, ANSI terminal rendering, zsh runtime shim.

## Global Constraints

- Limit source, test, and documentation changes to `local_dev/`.
- Do not change session discovery, retention eligibility, deletion order, memory handling, Serena MCP lifecycle, or public `dotsync` behavior.
- Preserve the existing `BoxModel.items` and `Item` top-level contract.
- Render Codex `groups`, `records`, and `cleanup` beneath one `sessions` item.
- Render Claude `records` and `cleanup` beneath one `sessions` item and name native Claude cleanup.
- Replace user-facing `ps` with `server processes`; spell out server categories.
- Preserve distinct total, delete, keep, cleanup-policy, normal, and risk colors.
- Keep the runtime stdlib-only and macOS-only.
- Preserve the user-owned `AGENTS.md` modification.

---

### Task 1: Multiline Box Rows and Full MCP Terminology

**Files:**
- Modify: `local_dev/tests/test_ui_renderer.py`
- Modify: `local_dev/serena_mcp_management/ui.py`

**Interfaces:**
- Consumes: existing `Item.value: str`, `BoxModel`, `_visible_len()`, and `style_mcp_inventory(...) -> str`.
- Produces: `_render_item_lines(item: Item, *, spin_frame: int) -> list[str]`; generic newline-aligned item rendering; fully named MCP inventory text.

- [ ] **Step 1: Write failing renderer tests**

Add tests that require continuation lines to begin at the same visible column as the first value and require the border to cover the longest continuation line:

```python
def test_render_box_aligns_multiline_values_under_value_column():
    model = BoxModel(
        phase="preflight",
        title="codex",
        items=[
            Item(
                id="sessions",
                label="sessions",
                value="codex\n├─ groups   58 total\n└─ cleanup  inactive longer than 5 days",
                status="info",
            )
        ],
    )

    lines = _strip_ansi(render_box(model)).splitlines()
    parent = next(line for line in lines if "sessions" in line)
    groups = next(line for line in lines if "├─ groups" in line)
    cleanup = next(line for line in lines if "└─ cleanup" in line)

    value_column = parent.index("codex")
    assert groups.index("├─") == value_column
    assert cleanup.index("└─") == value_column


def test_render_box_sizes_border_by_longest_multiline_value():
    model = BoxModel(
        phase="preflight",
        title="codex",
        items=[
            Item(
                id="sessions",
                label="sessions",
                value=(
                    "codex\n"
                    "└─ records  855 total · 358 to delete · 497 to keep"
                ),
            )
        ],
    )

    plain_lines = _strip_ansi(render_box(model)).splitlines()
    border_width = max(
        len(line.strip()) for line in plain_lines if set(line.strip()) == {"─"}
    )
    record_line = next(line for line in plain_lines if "└─ records" in line)
    assert border_width == max(60, len(record_line) - 2)
```

Update the MCP plain-text expectation to:

```python
assert _strip_ansi(text) == (
    "server processes[3] → managed servers[2] · "
    "orphaned servers[1] · leases[3] · stale leases[1]"
)
assert "ps[" not in _strip_ansi(text)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_ui_renderer.py::test_render_box_aligns_multiline_values_under_value_column \
  local_dev/tests/test_ui_renderer.py::test_render_box_sizes_border_by_longest_multiline_value \
  local_dev/tests/test_ui_renderer.py::test_style_mcp_inventory_renders_single_line_plain_text -v
```

Expected: multiline alignment/width fail because `render_box()` emits embedded newlines without continuation indentation, and the MCP assertion fails on `ps[3 servers]`.

- [ ] **Step 3: Implement minimal multiline rendering**

Add one private renderer helper and reuse it in width measurement and output:

```python
def _render_item_lines(item: Item, *, spin_frame: int) -> list[str]:
    value_lines = item.value.splitlines() or [""]
    marker = _marker_for(item.status, spin_frame=spin_frame)
    label = _ansi(MINT, f"{item.label:<10}")
    lines = [f"  {marker} {label}  {value_lines[0]}"]
    value_indent = " " * _visible_len(f"  {marker} {item.label:<10}  ")
    lines.extend(f"{value_indent}{line}" for line in value_lines[1:])
    return lines


def _box_width_for(model: BoxModel) -> int:
    width = _BOX_WIDTH
    for item in model.items:
        for row in _render_item_lines(item, spin_frame=0):
            width = max(width, _visible_len(row) - 2)
    return width
```

In `render_box()`, replace the per-item line construction with:

```python
for item in model.items:
    lines.extend(_render_item_lines(item, spin_frame=spin_frame))
```

Update `style_mcp_inventory()` to render:

```python
return (
    f"{normal('server processes', ps_servers)} "
    f"{_ansi('90', '→')} "
    f"{_ansi(MINT, 'managed servers')}[{_ansi(PINK, str(managed_servers))}] · "
    f"{risk('orphaned servers', orphan_servers)} · "
    f"{normal('leases', leases)} · "
    f"{risk('stale leases', stale_leases)}"
)
```

- [ ] **Step 4: Run focused renderer tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_ui_renderer.py -v
```

Expected: all renderer tests pass and no warning is emitted.

- [ ] **Step 5: Commit the renderer slice**

```bash
git add local_dev/tests/test_ui_renderer.py local_dev/serena_mcp_management/ui.py
git commit -m "feat(local_dev): support hierarchical preflight rows"
```

### Task 2: One Styled Sessions Tree

**Files:**
- Modify: `local_dev/tests/test_ui_style.py`
- Modify: `local_dev/tests/test_launcher_phases.py`
- Modify: `local_dev/serena_mcp_management/ui.py`
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py`

**Interfaces:**
- Consumes: `AgentInventory.sessions`, `AgentInventory.records`, `RETENTION_DAYS`, and Task 1 multiline rendering.
- Produces: `style_session_tree(...) -> str`; `_sessions_value(inventory: AgentInventory) -> str` containing the complete hierarchy; a single `Item(id="sessions")` in `_preflight_box()`.

- [ ] **Step 1: Write failing style and preflight tests**

Add a style test for exact uncolored structure and semantic colors:

```python
import re


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def test_style_session_tree_colors_counts_and_policy_by_meaning():
    result = style_session_tree(
        client="codex",
        groups=(58, 35, 23),
        records=(855, 358, 497),
        condition="inactive longer than 5 days",
    )

    assert _strip_ansi(result) == (
        "codex\n"
        "├─ groups   58 total · 35 to delete · 23 to keep\n"
        "├─ records  855 total · 358 to delete · 497 to keep\n"
        "└─ cleanup  inactive longer than 5 days"
    )
    assert f"\x1b[{PINK}m58 total\x1b[0m" in result
    assert "\x1b[33m35 to delete\x1b[0m" in result
    assert f"\x1b[{MINT}m23 to keep\x1b[0m" in result
    assert f"\x1b[{PURPLE}minactive longer than 5 days\x1b[0m" in result
```

Update launcher tests to assert the exact Codex and Claude trees, assert that
`_preflight_box().items` contains exactly one `sessions` item and no `cleanup`
item, and update scan-failure expectations to one warning `sessions` item.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_ui_style.py::test_style_session_tree_colors_counts_and_policy_by_meaning \
  local_dev/tests/test_launcher_phases.py::test_v2_preflight_renders_box_with_session_records_and_cleanup \
  local_dev/tests/test_launcher_phases.py::test_v2_preflight_labels_claude_candidates_as_native_cleanup \
  local_dev/tests/test_launcher_phases.py::test_v2_preflight_inventory_scan_failure_renders_warning_row -v
```

Expected: style import/function is missing and launcher output still contains
independent flat `sessions` and `cleanup` rows.

- [ ] **Step 3: Implement session-tree styling**

Add this public UI helper, keeping retention data outside the renderer:

```python
def style_session_tree(
    *,
    client: str,
    groups: tuple[int, int, int] | None,
    records: tuple[int, int, int],
    condition: str,
    cleanup_note: str = "",
) -> str:
    def stats_line(branch: str, label: str, stats: tuple[int, int, int]) -> str:
        total, delete, keep = stats
        return (
            f"{_ansi('90', branch)} {_ansi(MINT, f'{label:<9}')}"
            f"{_ansi(PINK, f'{total} total')} · "
            f"{_ansi('33', f'{delete} to delete')} · "
            f"{_ansi(MINT, f'{keep} to keep')}"
        )

    lines = [client]
    if groups is not None:
        lines.append(stats_line("├─", "groups", groups))
    lines.append(stats_line("├─", "records", records))
    cleanup = condition if not cleanup_note else f"{condition} · {cleanup_note}"
    cleanup_label = f"{'cleanup':<9}"
    lines.append(
        f"{_ansi('90', '└─')} {_ansi(MINT, cleanup_label)}"
        f"{_ansi(PURPLE, cleanup)}"
    )
    return "\n".join(lines)
```

- [ ] **Step 4: Build one sessions value and remove the flat cleanup item**

Replace `_sessions_value()` with:

```python
def _sessions_value(inventory: AgentInventory) -> str:
    records = inventory.records or inventory.sessions
    groups = None
    cleanup_note = ""
    if inventory.client == "codex":
        groups = (
            inventory.sessions.total,
            inventory.sessions.to_delete,
            inventory.sessions.to_keep,
        )
    else:
        cleanup_note = "native Claude cleanup"
    return style_session_tree(
        client=inventory.client,
        groups=groups,
        records=(records.total, records.to_delete, records.to_keep),
        condition=f"inactive longer than {RETENTION_DAYS} days",
        cleanup_note=cleanup_note,
    )
```

Delete `_cleanup_value()` and `_counted()`; Serena reference inspection shows
that both are limited to the replaced formatting functions. Delete
`style_session_counts()`, `style_cleanup_segments()`, and `style_criteria()`;
their only runtime references are the removed flat preflight formatting paths.
In
`_preflight_box()`, remove `cleanup_value`, `cleanup_item_status`, and the
top-level `Item(id="cleanup", ...)`. On scan failure, keep the single sessions
warning value `scan unavailable: <detail>`.

Replace the obsolete launcher imports with `style_session_tree`. Update
`local_dev/tests/test_ui_style.py` to remove direct tests and imports for the
three deleted style helpers.

- [ ] **Step 5: Run focused style and launcher tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_ui_style.py \
  local_dev/tests/test_launcher_phases.py -v
```

Expected: all style and launcher-phase tests pass.

- [ ] **Step 6: Commit the session-tree slice**

```bash
git add \
  local_dev/tests/test_ui_style.py \
  local_dev/tests/test_launcher_phases.py \
  local_dev/serena_mcp_management/ui.py \
  local_dev/serena_mcp_management/serena_agent_launcher.py
git commit -m "feat(local_dev): group session cleanup in preflight"
```

### Task 3: Internal Documentation, Runtime Promotion, and Verification

**Files:**
- Modify: `local_dev/README.md`
- Modify: `local_dev/docs/superpowers/specs/2026-07-20-preflight-information-hierarchy-design.md`
- Create: `local_dev/docs/superpowers/plans/2026-07-20-preflight-information-hierarchy.md`
- Generated/updated: `graphify-out/`
- Runtime mirror: `~/Desktop/dotsync_config/agent_launcher/` through `make -C local_dev install-shim`

**Interfaces:**
- Consumes: Tasks 1 and 2 output contracts.
- Produces: accurate internal documentation, installed runtime parity, refreshed graph, and final verification evidence.

- [ ] **Step 1: Update internal documentation**

Replace the two flat preflight examples in `local_dev/README.md` with the exact
Codex and Claude trees from the design. Explain that totals are compared by
data type inside one sessions tree and document `server processes` as the full
name for the machine-wide process count. Do not edit the public root README or
root Makefile.

- [ ] **Step 2: Run source checks and complete test suites**

Run:

```bash
python3 -m py_compile \
  local_dev/serena_mcp_management/ui.py \
  local_dev/serena_mcp_management/serena_agent_launcher.py
.venv/bin/python3 -m pytest local_dev/tests -q
.venv/bin/python3 -m pytest -q
```

Expected: compilation exits 0; local-dev and public suites report zero failures.

- [ ] **Step 3: Refresh graphify and review repository scope**

Run:

```bash
graphify update .
git diff --check
git status --short
git diff -- local_dev
```

Expected: graphify refresh exits 0, no whitespace errors, no public dotsync
source changes, and the user-owned `AGENTS.md` remains untouched.

- [ ] **Step 4: Promote through the supported shim target**

Run:

```bash
make -C local_dev install-shim
```

Expected: the development launcher tree is mirrored to
`~/Desktop/dotsync_config/agent_launcher/` and the managed zsh block is updated.

- [ ] **Step 5: Verify the installed runtime output**

Invoke the installed launcher preflight renderer with a deterministic inventory
snapshot, strip ANSI, and assert that output includes `server processes`, the
Codex `groups / records / cleanup` tree, and no top-level cleanup row or `ps[`.
Also compare the installed `ui.py` and `serena_agent_launcher.py` with the dev
copies using `cmp`.

Expected: assertions and both comparisons exit 0.

- [ ] **Step 6: Commit documentation and graph metadata**

```bash
git add \
  local_dev/README.md \
  local_dev/docs/superpowers/specs/2026-07-20-preflight-information-hierarchy-design.md \
  local_dev/docs/superpowers/plans/2026-07-20-preflight-information-hierarchy.md \
  graphify-out
git commit -m "docs(local_dev): explain grouped preflight sessions"
```

- [ ] **Step 7: Run final verification after the last commit**

Run again:

```bash
.venv/bin/python3 -m pytest local_dev/tests -q
.venv/bin/python3 -m pytest -q
git status --short
```

Expected: both suites report zero failures; only the pre-existing user-owned
`AGENTS.md` modification and temporary ignored/untracked brainstorming state,
if still present, remain outside the implementation commits.
