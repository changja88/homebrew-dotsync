# Codex Full Reset Entry Choice Design

**Date:** 2026-07-28

## Goal

Provide one default-safe keep choice and one explicit hard reset. A confirmed
reset removes all known local Codex sessions, memories, and conversation traces.
It does not offer a per-session preserve list.

This replaces the earlier selective-session design. Codex memories are global
generated state and cannot be reliably partitioned and preserved per selected
session, so a partial session picker does not satisfy the approved reset
contract.

## User Experience

The interactive Codex launcher shows:

```text
? Reset Codex sessions and memories before launch?
  ▶ Keep all sessions and memories (default)
    Delete all sessions, memories, and conversation traces
```

Enter keeps everything. The destructive option requires a second, default-no
confirmation:

```text
Permanently delete ALL Codex sessions, memories, history, logs, snapshots,
and currently running sessions? The Codex app will be restarted if it is open.
[y/N]
```

There is no session catalog or multi-select step. Cancelling either prompt is
non-destructive.

## Reset Scope

The reset discovers the default Codex home, active `CODEX_HOME`, Orca managed
home, safe configured `sqlite_home` / `CODEX_SQLITE_HOME`, configured
`log_dir`, system/user/profile/trusted-project config layers, current CLI
overrides, detected running-process CLI overrides and working directories, and
the documented macOS Codex Desktop log root.

It then:

1. repeatedly discovers and identity-pins detected Codex CLI and app-server
   runtimes before and after mutation, temporarily restarting an open Desktop
   app so cached state cannot recreate deleted traces;
2. removes active and archived rollout directories;
3. removes state, goals, memory, and log SQLite stores including
   WAL/SHM/rollback-journal files;
4. removes memory directories, history, recognized logs, snapshots,
   visualizations, ambient suggestions, and chat-process state, and surgically
   clears conversation keys from mixed Desktop global-state files;
5. clears every Desktop runtime table except automation definitions and
   app-server feature enablement, then uses SQLite secure deletion, checkpoint,
   and `VACUUM`; and
6. rescans all known paths and session indexes before reporting success.

Config, authentication, plugins, skills, automation definitions, and unrelated
user files remain. Root/intermediate symlinks fail closed; a final recognized
target symlink is unlinked rather than followed.

## Implementation Boundary

- `_run_session_choice_v2` returns `keep` or `reset_all` for Codex.
- `_run_codex_reset_v2` invokes `reset_all_codex_data`; it does not require a
  catalog snapshot, session UUID, or Codex binary.
- `reset_all_codex_data` owns runtime termination, target discovery, deletion,
  and post-reset verification.
- Preflight cataloging remains read-only and is used only for aggregate counts.
- Claude memory and session choices remain unchanged.
- The root public README remains untouched because `local_dev/` is internal.

## Failure Contract

A process-inspection failure does not prevent deletion of known targets, but it
does make the result a failure because termination could not be proven. Any
surviving runtime, unsafe target, unreadable config, filesystem error, SQLite
cleanup error, unreadable post-reset catalog, residual target, or residual
session likewise prevents a success result. A failed reset aborts the new Codex
launch instead of creating another session on top of an incomplete reset.

## Tests

Tests must prove:

- Enter defaults to keeping everything and does not enter reset work.
- The destructive choice requires confirmation and never scans or renders a
  session picker.
- Every known home loses active, archived, state-only, memory, history, log,
  snapshot, goal, and desktop thread state.
- Config, auth, plugins, skills, unrelated SQLite files, and automation
  definitions survive.
- CLI and app-server runtimes are terminated; an open Desktop app is
  temporarily closed and automatically reopened after verification.
- System, user, profile, trusted-project, environment, current-CLI, and
  running-process-configured SQLite and log locations are reset without
  deleting unrelated files.
- Mixed Desktop global-state files preserve non-conversation app preferences.
- Symlinked roots, broad/overlapping log roots, unknown Desktop runtime tables,
  wrong-type file targets, uncheckpointed WAL state, and runtime respawn all
  fail safely.
- Post-reset residuals cause failure.
- Claude behavior and Ctrl+C handling remain unchanged.
