# Claude Reset Runtime Mutations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Claude full reset complete when Claude Code performs documented product-managed cache and backup maintenance, while still preserving user-scope settings and reporting completed deletion counts truthfully.

**Architecture:** Keep `claude project purge --all --yes` as the authoritative project-state deletion. Split official-state verification from preservation verification, treat recognized backup rotation and plugin cache refresh as allowed product behavior, and continue strict byte/content verification for `settings.json`, credentials, authored extensions, and `plugins/data/` persistent state.

**Tech Stack:** Python 3.12, stdlib only, pytest, existing descriptor-anchored safe filesystem helpers.

## Global Constraints

- Work only under `local_dev/`; do not change or document the public `dotsync` package.
- Do not edit or remove `settings.json`; preserve `autoMemoryDirectory` exactly as written.
- Do not delete auth, personal MCP configuration, authored skills/commands/hooks/agents, or plugin persistent data.
- Do not weaken symlink, ownership, path-boundary, runtime-quiescence, or post-delete residual checks.
- Keep Codex reset behavior unchanged.

---

### Task 1: Reproduce Claude-managed runtime mutations

**Files:**
- Modify: `local_dev/tests/test_claude_reset.py`

**Interfaces:**
- Consumes: `reset_all_claude_data(...) -> ClaudeResetResult`
- Produces: regression coverage for volatile global metadata, backup rotation, plugin cache refresh, persistent plugin data, and partial-result counters.

- [x] **Step 1: Write a failing end-to-end unit test**

Create a realistic fake Claude run in which `project purge --help` refreshes `plugins/cache/`, and `project purge --all --yes` changes `cachedGrowthBookFeaturesAt`, rotates one recognized backup, creates a replacement backup containing `projects`, and removes official targets. Assert reset success, unchanged settings and `plugins/data/`, sanitized current backups, deleted supplemental targets, and truthful counts.

- [x] **Step 2: Write focused preservation tests**

Assert that changing a surviving backup's non-project value or `plugins/data/` still fails. Assert that deleting a recognized backup as part of rotation does not fail. Assert a true post-purge authored-data failure still reports the already completed official session deletion count.

- [x] **Step 3: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py -k "runtime_mutation or backup_values or plugin_persistent or completed_session_count" -q
```

Expected: failures caused by the current global-key, whole-plugin-tree, backup-disappearance, and counter-order behavior.

### Task 2: Narrow preservation to user-owned semantics

**Files:**
- Modify: `local_dev/serena_mcp_management/claude_reset.py`
- Test: `local_dev/tests/test_claude_reset.py`

**Interfaces:**
- Consumes: `_GlobalConfigSnapshot`, `_BackupSnapshot`, `_PreservedPathSnapshot`
- Produces: semantic global config verification, rotation-tolerant backup verification, and selective plugin persistent-data protection.

- [x] **Step 1: Exclude the observed volatile global cache key**

Do not include `cachedGrowthBookFeaturesAt` in the preserved non-project snapshot. Continue comparing every other pre-existing non-project value and continue rejecting residual `projects` entries.

- [x] **Step 2: Preserve plugin persistent data instead of mutable cache trees**

Replace the whole `plugins/` content manifest with a manifest of `plugins/data/`. User plugin enablement/configuration remains protected by the byte-identical `settings.json`; Claude-managed `cache/`, `marketplaces/`, and registry metadata may refresh.

- [x] **Step 3: Permit recognized backup rotation**

Ignore a pre-snapshotted recognized backup that no longer exists, but continue rejecting changed non-project values in every surviving pre-existing backup. Sanitize `projects` from all recognized backups present after purge, including newly created replacements.

- [x] **Step 4: Separate official completion from later preservation errors**

After official directories and current global project mappings verify clean, set the official session deletion count before checking preserved data. A later safety failure must abort launch but retain completed counts in `ClaudeResetResult`.

- [x] **Step 5: Run the focused tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py -k "runtime_mutation or backup_values or plugin_persistent or completed_session_count" -q
```

Expected: all selected tests pass.

### Task 3: Make memory and launcher reporting truthful

**Files:**
- Modify: `local_dev/serena_mcp_management/claude_reset.py`
- Modify: `local_dev/tests/test_claude_reset.py`
- Verify: `local_dev/tests/test_launcher_phases.py`

**Interfaces:**
- Consumes: the pre-reset `MemoryInventory` and final memory rescan.
- Produces: `ClaudeResetResult.deleted_memory_stores` covering both official default stores and a configured custom store once final verification proves none remain.

- [x] **Step 1: Add a failing memory-count regression test**

Provide a pre-reset inventory with default project memory stores and a final empty inventory. Assert that the successful result reports every discovered store deleted even when the supplemental custom-memory deleter itself deleted zero stores.

- [x] **Step 2: Run the test and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py -k "reports_all_deleted_memory_stores" -q
```

Expected: the result reports zero instead of the pre-reset discovered count.

- [x] **Step 3: Implement final-state-based successful reporting**

Keep partial failure counts conservative. When final verification proves no memory store remains, report the number of unique stores from the immutable pre-reset inventory.

- [x] **Step 4: Run Claude reset and launcher tests**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py local_dev/tests/test_launcher_phases.py -q
```

Expected: all tests pass.

### Task 4: Align design documentation and verify the runtime copy

**Files:**
- Modify: `local_dev/docs/superpowers/specs/2026-08-01-claude-product-wide-reset-design.md`
- Modify if behavior wording requires it: `local_dev/README.md`

**Interfaces:**
- Consumes: verified behavior from Tasks 1-3.
- Produces: documentation that distinguishes user-authored plugin data from Claude-managed plugin caches and recognizes native backup rotation.

- [x] **Step 1: Update the design invariants**

Document the volatile global key exception, `plugins/data/` preservation boundary, surviving-backup semantic checks, current-backup sanitization, and truthful partial counters.

- [x] **Step 2: Run formatting/static checks and the full suite**

Run:

```bash
.venv/bin/python3 -m compileall -q local_dev/serena_mcp_management
make test
```

Expected: both commands exit 0 with no failures.

- [x] **Step 3: Review the diff and promote the local runtime**

Run:

```bash
git diff --check
git diff -- local_dev
make -C local_dev install-shim
```

Verify that only `local_dev/`, its runtime mirror, and the managed zsh launcher block are affected; the public `dotsync` package remains untouched.
