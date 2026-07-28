# Codex State-Aware Session Deletion Design

**Date:** 2026-07-28

## Goal

Make the Codex reset picker, deletion action, and success verification account
for persisted thread metadata in `state_<n>.sqlite`, including rows whose
rollout JSONL no longer exists. A selected logical session is successful only
when both its independently discovered rollout files and its state-database
thread rows are absent.

## Confirmed Failure

The current catalog scans only `sessions/**/*.jsonl` and
`archived_sessions/**/*.jsonl`. The current machine has 42 rows in
`state_5.sqlite`, but only 14 IDs belong to the one rollout-backed logical
session. The remaining 28 rows:

- are legacy `subAgentOther/guardian` threads;
- have no existing rollout file;
- have no `thread_spawn_edges` relationship;
- retain non-empty title, preview, first-user-message, and rollout-path
  metadata; and
- therefore cannot appear in the current picker or be reached by a root-only
  descendant cascade.

On an isolated database copy, `codex delete --force <guardian-id>` removed a
state-only row even though its rollout was missing. Deleting a linked root
removed the root and 13 recorded spawn descendants but left all 28 unlinked
guardian rows. The official per-ID command is therefore sufficient; the
launcher is failing to discover and invoke it for every persisted ID.

## Approaches

### 1. Official delete for every discovered ID — selected

Read Codex state databases without modifying them, merge those thread IDs with
rollout discovery, and invoke `codex delete --force` for every selected
owner-local ID in descendant-first order. Re-read the state databases and
verify that every selected ID is absent.

This uses Codex's supported deletion contract for metadata cleanup and works
for state-only rows with missing rollout files.

### 2. Delete the entire state database — rejected

Removing `state_<n>.sqlite` would also remove unselected sessions and unrelated
runtime state. It is incompatible with per-session selection and can race with
a running Codex process.

### 3. Direct SQL deletion — rejected

Deleting rows manually would couple the launcher to undocumented foreign-key,
migration, and auxiliary-table details. It could leave inconsistent state and
would be less reliable than the verified official command.

## Catalog Model

Add a state-thread record alongside the existing rollout-file record. Each
state record contains:

- normalized thread UUID;
- owner Codex home;
- source state-database path;
- parent ID from `thread_spawn_edges`, when present;
- cwd, preview/title, updated time, and archived state for display.

Discover every regular, non-symlink `state_<n>.sqlite` under each known Codex
home with a read-only SQLite connection. Query only the `threads` and
`thread_spawn_edges` tables. A missing database is valid. An existing database
with an unsafe file type, unreadable schema, invalid UUID, or conflicting
parent relationship raises a catalog error. The reset flow reports the catalog
as unavailable and performs no deletion; it must not fall back to a
rollout-only catalog that could claim complete success.

Build logical groups from the union of rollout IDs and state IDs:

- use recorded spawn edges and rollout parent IDs when they agree;
- keep an unlinked state-only thread as its own selectable logical session;
- never trust a state row's `rollout_path` as a deletion target;
- delete files only when independently enumerated beneath the known
  `sessions/` or `archived_sessions/` roots.

For each Codex-home owner, retain every local group ID as an official deletion
ID, ordered deepest descendant first and root last. Do not reduce the list to
roots.

## Deletion Flow

For every selected logical group:

1. Invoke `codex delete --force <UUID>` for every owner-local ID.
2. Continue attempting the remaining IDs after one official-delete failure;
   one failure must not suppress cleanup of unrelated IDs in the same home.
3. Directly remove each independently cataloged rollout path that remains,
   preserving the existing no-symlink and known-root checks.
4. Reset the existing global memory, history, log, and snapshot targets.
5. Re-enumerate state databases and query the selected IDs.
6. Report the logical group deleted only when every selected rollout path and
   every selected state row is absent.

An official-delete failure is a warning only when the selected ID is absent
from state after verification and no selected rollout remains. If the state row
remains, the reset result is a failure. The launcher never edits SQLite rows
directly.

## Concurrency

The launcher does not terminate Codex Desktop, CLI processes, or app-server.
If a running process recreates a selected row or trace before verification,
the residual is reported as a failure. Verification proves the state at that
point in time; it cannot prevent a still-running product from creating new
state afterward.

## User Experience

The already-approved entry gate remains unchanged:

```text
? Reset Codex sessions and memories before launch?
  ▶ Keep all sessions and memories (default)
    Select sessions to delete and reset all memories
```

Choosing reset displays rollout-backed groups and state-only groups. Existing
labels use state metadata when no rollout metadata is available. Empty
selection remains an exact no-op. Any confirmed non-empty selection still
resets all Codex memory and related traces.

The final action row must not report success merely because rollout files are
gone. State-database residual IDs make the row a failure.

## Tests

Use temporary SQLite databases created with Python's stdlib `sqlite3` module.
Cover:

- a state-only guardian row with a missing rollout appears in the catalog;
- rollout and state records for the same ID merge without duplicate rows;
- spawn-linked children group under their root;
- an unlinked guardian remains independently selectable;
- owner deletion IDs contain every local ID in descendant-first order;
- one official-delete failure does not skip later IDs;
- direct rollout removal without state-row removal is a failed reset;
- a successful runner that removes the temporary state row passes
  verification;
- unreadable, unsafe, or incompatible state databases cannot yield false
  success;
- empty selection changes neither files nor SQLite state;
- existing memory/trace deletion and symlink safety remain unchanged; and
- the full launcher and `local_dev` suites remain green.

Update `local_dev/README.md` to describe state-backed discovery, per-ID official
deletion, and dual rollout/state verification. Do not change the public root
README.
