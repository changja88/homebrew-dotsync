# local_dev Test and Legacy Code Pruning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove unreachable and test-only `local_dev` runtime code, then reduce
the 614-test suite to the smallest contract-focused regression suite that still
protects current destructive operations and launcher behavior.

**Architecture:** Codex session handling has one supported path:
`scan_codex_session_catalog` for display and `reset_all_codex_data` for a
confirmed reset. Claude retains its existing inventory, native retention, and
inactive-bundle cleanup. Tests are kept only at destructive safety boundaries,
user-observable orchestration boundaries, and external process/file protocol
boundaries.

**Tech Stack:** Python 3.12+, pytest, stdlib-only runtime, argparse-free internal
launcher modules, macOS process/filesystem APIs.

## Global Constraints

- Preserve every reachable user-visible behavior documented in
  `local_dev/README.md`.
- Do not modify `lib/dotsync/` or `Formula/dotsync.rb`.
- Do not run `make -C local_dev install-shim`; do not modify the stable runtime
  mirror or `~/.zshrc`.
- Treat all pre-existing working-tree changes as user-owned and preserve them.
- Do not commit implementation files because several targets already contain
  user-owned uncommitted edits; use explicit diff review instead.
- Keep the runtime stdlib-only and macOS-only.
- Do not weaken fail-closed deletion, symlink, TOCTOU, process-identity, or
  partial-mutation handling.
- Use `apply_patch` for hand edits. A one-off syntax-aware test-pruning script is
  allowed only for the mechanical removal of explicitly listed test functions;
  inspect its complete diff immediately afterward.

---

### Task 1: Record and protect the working baseline

**Files:**
- Read: `local_dev/serena_mcp_management/**/*.py`
- Read: `local_dev/tests/**/*.py`
- Read: current Git index and worktree state

**Interfaces:**
- Consumes: current dirty working tree
- Produces: baseline counts and a list of pre-existing changed paths that later
  diff review must preserve

- [ ] **Step 1: Confirm branch/worktree state and pre-existing changes**

Run:

```bash
git branch --show-current
git status --short
git diff --stat -- local_dev
```

Expected: branch `main`; existing changes under `local_dev` remain unstaged.
The design-only commit `7ca0467` is already in history.

- [ ] **Step 2: Reconfirm the behavioral baseline**

Run:

```bash
.venv/bin/python3 -m pytest --collect-only -q local_dev/tests
.venv/bin/python3 -m pytest -q local_dev/tests
```

Expected: 614 collected and 614 passed.

- [ ] **Step 3: Record source/test size**

Run:

```bash
rg --files local_dev/tests -g 'test_*.py' | wc -l
wc -l local_dev/tests/*.py local_dev/serena_mcp_management/*.py \
  local_dev/serena_mcp_management/serena_mcp/*.py
```

Expected: 29 `test_*.py` files and the current per-file line counts for the
final before/after report.

### Task 2: Remove the unreachable Codex per-session cleanup subsystem

**Files:**
- Modify: `local_dev/serena_mcp_management/session_inventory.py`
- Modify: `local_dev/serena_mcp_management/session_cleanup.py`
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py`
- Delete: `local_dev/tests/test_session_cleanup.py`
- Modify: `local_dev/tests/test_session_inventory.py`
- Modify: `local_dev/tests/test_launcher_phases.py`

**Interfaces:**
- Consumes: `scan_codex_session_catalog`, `reset_all_codex_data`,
  `scan_inventory`, `cleanup_claude_inventory`
- Produces:
  - `scan_claude_inventory(*, home, claude_config_dir=None, now=None,
    policy="retention_5d", open_file_identities=None,
    active_claude_session_ids=None) -> AgentInventory`
  - `cleanup_claude_inventory(inventory, *,
    active_session_snapshot=..., open_file_snapshot=...) -> CleanupResult`
  - launcher Codex flow with no per-session deletion branch

- [ ] **Step 1: Recheck every production reference before deletion**

Run:

```bash
rg -n '\b(cleanup_codex_inventory|CodexCleanupTarget|OwnerDeletePlan|CodexSessionFile|_scan_codex_inventory)\b' \
  local_dev/serena_mcp_management
```

Expected: references are confined to `session_inventory.py`,
`session_cleanup.py`, and the two unreachable launcher branches documented in
the design.

- [ ] **Step 2: Make session inventory Claude-specific**

Delete Codex-only dataclasses and functions from `session_inventory.py`:

```python
CodexSessionFile
OwnerDeletePlan
CodexCleanupTarget
_read_codex_session_files
_group_codex_files
_depth
_root_ids_for_group
_owner_delete_plans
_scan_codex_inventory
```

Remove `canonical_codex_homes` and other imports used only by those functions.
Replace the public dispatcher with:

```python
def scan_claude_inventory(
    *,
    home: Path,
    claude_config_dir: Path | None = None,
    now: float | None = None,
    policy: SessionPolicy = "retention_5d",
    open_file_identities: frozenset[FileIdentity] | None = None,
    active_claude_session_ids: frozenset[str] | None = None,
) -> AgentInventory:
    if policy not in {"retention_5d", "all_inactive"}:
        raise ValueError(f"unsupported session policy: {policy}")
    return _scan_claude_inventory(
        home=home,
        claude_config_dir=claude_config_dir,
        now=time.time() if now is None else now,
        policy=policy,
        open_file_identities=open_file_identities,
        active_claude_session_ids=active_claude_session_ids,
    )
```

Remove `AgentInventory.codex_targets`. Keep `snapshot_open_rollouts` because
Claude cleanup uses it to preserve open transcripts. Update
`ActiveSessionScanError` so its docstring is not Codex-specific.

- [ ] **Step 3: Delete the Codex cleanup implementation**

From `session_cleanup.py`, delete:

```python
DELETE_TIMEOUT_SECONDS
RunCommand
_current_session_paths
_current_fingerprint
_target_unchanged
_codex_target_is_open
_codex_target_revalidation_error
_command_detail
_run_codex_command
cleanup_codex_inventory
```

Remove the `subprocess` and `CodexCleanupTarget` imports. Preserve shared result
formatting helpers, Claude quarantine helpers, `claude_retention_args`, and
`cleanup_claude_inventory`.

- [ ] **Step 4: Remove dead launcher branches and arguments**

In `serena_agent_launcher.py`:

```python
from ...session_cleanup import (
    CleanupResult,
    claude_retention_args,
    cleanup_claude_inventory,
)
from ...session_inventory import (
    AgentInventory,
    CountStats,
    RETENTION_DAYS,
    scan_claude_inventory,
)
```

Make `_run_launch_prep_v2` Claude-only by removing `real_binary` and the Codex
branch. Keep its current unavailable/mismatch handling and native-retention
summary.

Make `_run_explicit_session_cleanup_v2` call only:

```python
inventory = scan_claude_inventory(
    home=scan_kwargs["home"],
    claude_config_dir=scan_kwargs["claude_config_dir"],
    policy="all_inactive",
)
result = cleanup_claude_inventory(inventory)
```

Remove its `real_binary` argument. In `_main_v2`, do not resolve the agent
binary before Claude cleanup; resolve it later at the existing common point.

- [ ] **Step 5: Remove obsolete tests and retain Claude inventory contracts**

Delete `test_session_cleanup.py`.

In `test_session_inventory.py`, retain these contracts and update calls to
`scan_claude_inventory`:

```text
test_scan_claude_counts_all_projects_without_memory_or_subagents
test_scan_claude_uses_strict_five_day_cutoff
test_scan_claude_uses_absolute_custom_config_root
test_scan_claude_rejects_relative_config_root
test_scan_rejects_unknown_session_policy
test_snapshot_open_rollouts_parses_lsof_paths
test_snapshot_open_rollouts_fails_closed_on_lsof_error
```

Delete every Codex inventory test and `test_scan_rejects_unknown_client`.
Delete launcher tests whose sole contract is the unreachable Codex cleanup
branch:

```text
test_v2_launch_prep_codex_uses_snapshot_and_official_cleanup
test_explicit_session_cleanup_codex_uses_fresh_all_inactive_scan
```

Update retained launcher tests for the removed `real_binary` arguments.

- [ ] **Step 6: Verify the reduced session subsystem**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_session_inventory.py \
  local_dev/tests/test_claude_session_inventory.py \
  local_dev/tests/test_claude_session_cleanup.py \
  local_dev/tests/test_launcher_phases.py -q
```

Expected: all retained tests pass and no removed Codex name remains in
production:

```bash
rg -n '\b(cleanup_codex_inventory|CodexCleanupTarget|OwnerDeletePlan|CodexSessionFile|_scan_codex_inventory)\b' \
  local_dev/serena_mcp_management
```

Expected result: no matches.

### Task 3: Remove production symbols that exist only for tests

**Files:**
- Modify: `local_dev/serena_mcp_management/ui.py`
- Modify: `local_dev/serena_mcp_management/agent_paths.py`
- Modify: `local_dev/serena_mcp_management/codex_reset.py`
- Modify: `local_dev/serena_mcp_management/serena_mcp/diagnostics.py`
- Modify: `local_dev/serena_mcp_management/serena_mcp/registry.py`
- Modify: `local_dev/serena_mcp_management/serena_mcp/watchdog.py`
- Modify/Delete: corresponding test files

**Interfaces:**
- Consumes: production-wide reference search
- Produces: no top-level runtime symbol whose only caller is a test

- [ ] **Step 1: Recheck candidate references**

Run:

```bash
rg -n '\b(style_count|effective_claude_config_dir|LifecycleSnapshot|snapshot_lifecycle|shutdown_if_no_leases|remove_lease|stale_lease_ids|_parse_codex_process_environment|_process_codex_environment)\b' \
  local_dev/serena_mcp_management
```

Expected: each candidate has only its definition, type-local references, or the
companion dead function listed in the design.

- [ ] **Step 2: Delete the candidate definitions**

Remove all nine candidates and imports made unused by them. Do not change:

```text
style_session_tree
lexical_claude_config_dir
GlobalLifecycleSnapshot
snapshot_global_lifecycle
release_lease_and_shutdown_if_empty
locked_registry
_parse_codex_process_context
_process_codex_context
```

- [ ] **Step 3: Delete only tests for the removed contracts**

Apply these test changes:

- `test_agent_paths.py`: delete
  `test_effective_claude_config_dir_requires_absolute_path`.
- `test_serena_diagnostics.py`: delete the two `snapshot_lifecycle` tests;
  retain global lifecycle tests.
- `test_serena_watchdog.py`: delete the direct `shutdown_if_no_leases` tests;
  retain release/cleanup/ensure-watchdog contracts.
- `test_serena_registry.py`: stop importing/calling `remove_lease`; retain the
  registry persistence test by mutating `registry.record.leases` inside
  `locked_registry`.
- `test_ui_style.py`: the whole file is deleted in Task 4, so no replacement
  `style_count` test is needed.
- No tests exist for the two unused Codex environment wrappers or
  `stale_lease_ids`.

- [ ] **Step 4: Verify no references or import failures remain**

Run:

```bash
.venv/bin/python3 -m compileall -q local_dev/serena_mcp_management
.venv/bin/python3 -m pytest \
  local_dev/tests/test_agent_paths.py \
  local_dev/tests/test_serena_diagnostics.py \
  local_dev/tests/test_serena_registry.py \
  local_dev/tests/test_serena_watchdog.py -q
```

Expected: compile and selected tests pass; the candidate reference search
returns no matches.

### Task 4: Delete whole test files that duplicate stronger contracts

**Files:**
- Delete: `local_dev/tests/test_ui_state.py`
- Delete: `local_dev/tests/test_ui_style.py`
- Delete: `local_dev/tests/test_ui_progress.py`
- Delete: `local_dev/tests/test_launcher_node_runtime.py`

**Interfaces:**
- Consumes: retained launcher, renderer, prompt, node-preflight, and external CLI
  tests
- Produces: no standalone tests of passive dataclass/enum shape, exact visual
  constants, spinner internals, or launcher adapter one-liners

- [ ] **Step 1: Reconfirm overlapping contracts**

Run:

```bash
rg -n 'SpinnerTicker|_client_node_need|_homebrew_node_present|_node_runtime_install|BoxModel|ItemStatus|PhaseKind' \
  local_dev/tests
```

Expected: launcher phase tests cover spinner serialization and node prompt
outcomes; renderer tests cover `BoxModel`; node/external CLI tests cover
detection and command resolution.

- [ ] **Step 2: Delete the four files**

Use `apply_patch` file deletions. Do not delete `ui.py`, `node_preflight.py`, or
the runtime `SpinnerTicker`; only their redundant direct tests are removed.

- [ ] **Step 3: Verify the overlapping suites**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_launcher_phases.py \
  local_dev/tests/test_node_preflight.py \
  local_dev/tests/test_external_cli.py \
  local_dev/tests/test_ui_renderer.py \
  local_dev/tests/test_ui_prompts.py -q
```

Expected: all pass.

### Task 5: Prune destructive-operation tests to independent safety contracts

**Files:**
- Modify: `local_dev/tests/test_codex_reset.py`
- Modify: `local_dev/tests/test_memory_management.py`
- Modify: `local_dev/tests/test_claude_session_cleanup.py`
- Modify: `local_dev/tests/test_claude_session_inventory.py`

**Interfaces:**
- Consumes: full-reset, memory deletion, Claude bundle deletion public results
- Produces: representative happy path plus one test per independent safety or
  external-state failure channel

- [ ] **Step 1: Retain the Codex reset contract set**

Keep the following tests and delete the other test functions from
`test_codex_reset.py`:

```text
test_process_environment_reads_only_codex_state_variables
test_catalog_lists_active_and_archived_roots_and_groups_descendants
test_catalog_merges_state_threads_and_lists_state_only_guardian
test_catalog_rejects_incompatible_state_database
test_catalog_rejects_symlinked_state_database
test_catalog_excludes_an_unsafe_broad_active_codex_home
test_full_reset_removes_every_codex_session_and_trace_but_keeps_config
test_full_reset_terminates_codex_runtimes_including_desktop_app
test_full_reset_fails_if_desktop_cannot_reopen
test_full_reset_does_not_signal_a_reused_pid
test_full_reset_fails_if_codex_runtime_respawns_during_mutation
test_full_reset_clears_unknown_desktop_thread_tables
test_full_reset_fails_when_desktop_sqlite_wal_is_still_open
test_full_reset_fails_when_a_codex_runtime_survives_termination
test_full_reset_deletes_known_data_but_fails_when_process_scan_is_unknown
test_full_reset_fails_when_a_codex_config_cannot_be_parsed
test_full_reset_rejects_symlinked_codex_home_without_following_it
test_full_reset_preserves_wrong_type_targets_and_fails
test_full_reset_rejects_log_dir_that_overlaps_codex_home
test_full_reset_rejects_log_dir_inside_preserved_plugin_tree
test_full_reset_clears_configured_sqlite_and_log_locations
test_full_reset_clears_cli_override_state_locations
test_full_reset_clears_system_config_state_locations
test_full_reset_clears_locations_from_every_codex_profile
test_full_reset_clears_trusted_project_config_state_locations
test_full_reset_ignores_untrusted_project_config_state_locations
```

This keeps each documented config source and independent failure channel while
removing repeated quiescence/root variants.

- [ ] **Step 2: Retain the memory safety contract set**

Keep these tests from `test_memory_management.py`:

```text
test_codex_inventory_scans_only_memories_under_all_known_homes
test_codex_inventory_rejects_symlinked_active_home
test_codex_inventory_rejects_parent_traversal_before_symlink_inspection
test_claude_inventory_finds_all_project_memory_and_custom_store
test_claude_inventory_deduplicates_project_and_custom_case_aliases
test_claude_inventory_retains_lexical_guard_if_identity_comparison_misses
test_claude_inventory_fails_closed_if_identity_inspection_fails
test_claude_inventory_rejects_symlinked_config_root
test_claude_inventory_rejects_nonempty_custom_store_without_marker
test_claude_inventory_rejects_custom_store_with_parent_traversal
test_claude_inventory_rejects_tilde_store_below_symlinked_home
test_inventory_rejects_symlink_store
test_inventory_rejects_store_below_symlinked_parent
test_inventory_does_not_follow_symlinks_inside_store
test_inventory_reports_memory_path_with_wrong_file_type
test_running_client_processes_excludes_launcher_ancestors
test_process_scan_ignores_claude_desktop_but_finds_claude_code
test_process_scan_finds_official_node_client_wrappers
test_delete_all_memory_removes_only_validated_stores
test_delete_all_claude_memory_removes_project_and_custom_stores_only
test_delete_all_memory_refuses_running_same_product
test_delete_empty_inventory_succeeds_without_process_scan
test_delete_prevalidates_every_store_before_mutation
test_delete_revalidates_store_immediately_before_each_removal
test_delete_reports_partial_counts_and_stops
test_delete_refuses_when_process_scan_fails
```

- [ ] **Step 3: Retain the Claude session safety contract set**

Keep all seven tests in `test_claude_session_inventory.py`; each covers a
different bundle, marker, process identity, or symlink boundary.

Keep these functions from `test_claude_session_cleanup.py`:

```text
test_cleanup_claude_removes_exact_inactive_bundle_only
test_cleanup_claude_preserves_bundle_that_becomes_active
test_cleanup_claude_refreshes_open_files_before_bundle_mutation
test_cleanup_claude_prevalidates_all_target_root_sets_before_delete
test_cleanup_claude_fails_before_delete_when_manifest_changes
test_cleanup_claude_does_not_follow_swapped_ancestor
test_cleanup_claude_does_not_delete_final_name_replacement
test_cleanup_claude_handles_quarantine_validation_failure_after_open
test_cleanup_claude_reports_quarantine_cleanup_failure
test_cleanup_claude_preserves_freshly_open_transcript
test_cleanup_claude_zero_targets_is_success
test_cleanup_claude_zero_targets_with_warning_fails_closed
test_cleanup_claude_inventory_warning_fails_before_delete
test_cleanup_claude_reports_partial_unlink_failure
test_cleanup_claude_reports_intra_bundle_partial_root_mutation
test_cleanup_claude_leaves_stale_marker_files
```

- [ ] **Step 4: Remove unused test helpers/imports**

Use an AST reference report to identify top-level helper definitions with no
remaining `Name` load. Remove them and unused imports without changing retained
test bodies.

- [ ] **Step 5: Run destructive-operation tests**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_codex_reset.py \
  local_dev/tests/test_memory_management.py \
  local_dev/tests/test_claude_session_inventory.py \
  local_dev/tests/test_claude_session_cleanup.py -q
```

Expected: all retained tests pass.

### Task 6: Prune orchestration, UI, notification, shim, and MCP tests

**Files:**
- Modify: `local_dev/tests/test_launcher_phases.py`
- Modify: `local_dev/tests/test_notification_guard.py`
- Modify: `local_dev/tests/test_ui_renderer.py`
- Modify: `local_dev/tests/test_ui_prompts.py`
- Modify: `local_dev/tests/test_external_cli.py`
- Modify: `local_dev/tests/test_node_preflight.py`
- Modify: `local_dev/tests/test_serena_*.py`

**Interfaces:**
- Consumes: user-visible launcher outcomes and external boundary behavior
- Produces: one representative test per choice, failure channel, protocol, and
  lifecycle state

- [ ] **Step 1: Prune launcher tests by observable contract**

Keep tests for these exact outcome groups:

```text
main: clean Ctrl+C, non-interrupt propagation, child exit code
preflight: representative success, bounded inventory failure, node-before-graphify
graphify: CLI decline gate, integration default No, hook default Yes
serena init/install: create success, captured failure, decline, no-uv failure
Codex choices: default keep, confirmed reset, confirmation cancel, backend call
Codex main: keep no-op, reset success, reset failure abort
Claude choices: product scope, non-interactive bypass, action ordering
cleanup: success spinner, scan failure, in-flight tick serialization,
         Claude-only cleanup, bounded partial mutation
memory: keep no-op, delete result, scan failure, partial result
summary: reset counts and warning rendering
notification guard: before-launch ordering, clean, repair, crash, non-interactive
MCP: start success/failure and shutdown success/failure
bare launch: skipped Serena and unavailable Serena CLI
```

Delete tests that only assert exact install-progress strings, each installer
wrapper independently, every equivalent preflight status permutation, or the
removed Codex per-session path. Retain the current working-tree Codex reset and
notification guard tests even when they were not in `HEAD`.

- [ ] **Step 2: Prune notification guard tests**

Retain:

```text
user/managed target discovery
missing-file no-op
notify repair with previous arguments
permission and subagent key derivation
hooks-state existing-line replacement and missing-block creation
atomic no-op, concurrent retry, replace failure cleanup, mode preservation
Claude channel repair
Orca master/task/focus warnings and bell exclusion
user-home full hooks repair
corrupt hooks warning while other repairs continue
run guard clean, repaired, and internal-error outcomes
```

Delete quoting/header-layout permutations that have the same transform result
and obsolete reviewer-specific variants superseded by the current auto-review
policy.

- [ ] **Step 3: Prune UI and command-resolution tests**

For `test_ui_renderer.py`, retain title/phase, multiline alignment and sizing,
done/warn/info markers, spinner frame, first draw, redraw, clear, newline, and
one known-client banner test. Delete exact RGB, gradient, texture, and
pixel-by-pixel assertions.

For `test_ui_prompts.py`, retain confirm yes/no/default, line-mode selection and
retry, nonzero default, validation, Ctrl+C restoration, raw navigation, and
legacy y/n shortcuts. Delete color-only and immutable-dataclass assertions.

For `test_external_cli.py`, retain one direct/fallback/no-fallback test for each
distinct resolution policy and install precondition. For
`test_node_preflight.py`, retain command classification plus one Claude generic,
one Claude Homebrew, and one Codex generic scan.

- [ ] **Step 4: Prune Serena MCP tests**

Retain these boundary categories:

```text
paths: project-root precedence/fallback, scope separation, context mapping
processes: spaced/quoted parse, bad input fail-closed, scope match, ps failure
health: live/dead identity, HTTP probe, project match/reject, URL normalization
registry: lease persistence, legacy record, corrupt record, scope rejection
diagnostics: global counts, identity mismatch, scan failure, malformed registry
proxy: POST/GET forwarding, hop headers, streaming, DELETE suppression
server: reuse/replace, identity and scope isolation, startup record,
        health-failure cleanup, orphan cleanup, CLI-missing error
termination: TERM/KILL, process-group fallback, reused identity
watchdog: stale/active identity, last/sibling release, duplicate/mismatched pid,
          repo-root import path
launcher: command injection, Claude temp config/retention, lease reattach,
          project root, binary override, signal/finally cleanup
zsh shim: managed command matcher/bypass, graphify checks, project root,
          stable Python, install/uninstall, managed env packing, uv tool PATH
packaging: Homebrew excludes local_dev
```

Delete parser spelling permutations, private one-line delegation tests, passive
field-by-field persistence tests, and exact generated-shell fragments already
exercised by a subprocess round trip.

- [ ] **Step 5: Remove unused helpers/imports and compile tests**

Run an AST unused-definition/import report, remove confirmed dead helpers, then:

```bash
.venv/bin/python3 -m compileall -q local_dev/tests
```

Expected: no syntax/import errors.

- [ ] **Step 6: Run the complete retained local_dev suite**

Run:

```bash
.venv/bin/python3 -m pytest --collect-only -q local_dev/tests
.venv/bin/python3 -m pytest -q local_dev/tests
```

Expected: substantially fewer than 614 tests, all passing. Do not restore a
deleted test merely to reach a count; restore only if it protects an independent
contract from the approved design.

### Task 7: Final reference, behavior, and repository verification

**Files:**
- Inspect: every changed path
- Modify: `local_dev/README.md` only if a removed internal path is still
  described as current behavior

**Interfaces:**
- Consumes: completed source and test pruning
- Produces: fresh evidence that current local and public behavior remains green

- [ ] **Step 1: Search for removed APIs and obsolete Codex behavior**

Run:

```bash
rg -n '\b(cleanup_codex_inventory|CodexCleanupTarget|OwnerDeletePlan|CodexSessionFile|_scan_codex_inventory|style_count|effective_claude_config_dir|LifecycleSnapshot|snapshot_lifecycle|shutdown_if_no_leases|remove_lease|stale_lease_ids|_parse_codex_process_environment|_process_codex_environment)\b' \
  local_dev
```

Expected: no runtime/test references. Historical design/plan references are
allowed only when clearly describing removed history.

- [ ] **Step 2: Compile runtime and tests**

Run:

```bash
.venv/bin/python3 -m compileall -q \
  local_dev/serena_mcp_management \
  local_dev/tests
```

Expected: exit 0.

- [ ] **Step 3: Run both complete test suites**

Run:

```bash
.venv/bin/python3 -m pytest -q local_dev/tests
.venv/bin/python3 -m pytest -q tests
```

Expected: both exit 0 with no failures.

- [ ] **Step 4: Check whitespace and inspect the full diff**

Run:

```bash
git diff --check
git diff --stat
git status --short
```

Then inspect each changed production file and each retained test file. Confirm
that existing user changes in README, notification guard, launcher, UI, and
Codex reset remain present.

- [ ] **Step 5: Report measurable reduction and scope**

Run:

```bash
rg --files local_dev/tests -g 'test_*.py' | wc -l
.venv/bin/python3 -m pytest --collect-only -q local_dev/tests
wc -l local_dev/tests/*.py
```

Report the literal before/after values emitted by these commands for test-file
count, collected-test count, and summed test LOC. Also report removed runtime
subsystems/symbols, fresh test results, untouched external runtime locations,
and the Serena/Graphify usage note required by `AGENTS.md`.
