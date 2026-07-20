# Agent Memory Launcher Design

## Goal

Add an interactive memory decision to the private `local_dev` Codex/Claude
launcher. Native auto-memory stays enabled, but every interactive launch lets
the user keep the selected product's existing auto-memory, delete all of it
before launch, or cancel the launch.

The existing five-day rule remains a session-retention rule only. Memory is
never deleted by age and is never deleted without the explicit
`Delete all memory and run` selection.

## Selected Flow

The memory decision replaces the current final `Run <client>?` yes/no prompt.
It runs after Serena and graphify setup prompts and before session cleanup:

```text
Run with existing memory
  -> leave memory unchanged
  -> run existing five-day session cleanup
  -> launch the selected agent

Delete all memory and run
  -> rescan the selected product's memory paths
  -> validate every deletion target
  -> check for another running process of the selected product
  -> delete every discovered main auto-memory store
  -> print the deletion result
  -> run existing five-day session cleanup
  -> launch the selected agent

Cancel or Ctrl+C
  -> do not delete memory
  -> do not run session cleanup
  -> do not launch the agent
  -> exit with status 130 and no traceback
```

If deletion cannot be completed safely, the launcher reports the reason and
stops before session cleanup and agent launch. It must never silently fall back
to launching with memory that the user asked to delete.

Non-interactive commands keep their current bypass behavior and never show the
memory prompt or delete memory.

## Memory Scope

### Codex

The launcher derives the known Codex homes on every run using the same
machine-wide contract as session inventory:

- the default `~/.codex` home;
- the active absolute `$CODEX_HOME`, when set;
- Orca's managed runtime home at
  `~/Library/Application Support/orca/codex-runtime-home/home`.

Duplicate homes are collapsed after normalization. The only main auto-memory
target under each home is the exact `memories/` child directory.

The launcher must not include `memories_extensions/`, Chronicle data, sessions,
skills, configuration, authentication, logs, or SQLite state. The installed
Codex binary identifies the main memory workspace as `memories/` and separately
identifies `memories_extensions`; the official Codex manual confirms that
`CODEX_HOME` is the state root and that `[features].memories` plus the
`[memories]` table control generation and use.

### Claude

The effective Claude configuration root is the absolute
`$CLAUDE_CONFIG_DIR`, when set, or `~/.claude` otherwise. The launcher discovers
every direct project auto-memory directory matching:

```text
<claude-config-dir>/projects/<project>/memory/
```

It also parses the user-level `<claude-config-dir>/settings.json`. If
`autoMemoryDirectory` is present, valid, and different from a default project
memory directory, that exact configured directory is included. The supported
value must be absolute or start with `~/`, matching Claude's official setting
contract.

Default and configured stores are deduplicated. Claude subagent
`agent-memory/`, `CLAUDE.md`, `.claude/rules`, transcripts, tasks, debug data,
file history, and prompt history are outside this feature.

Official reference:
<https://code.claude.com/docs/en/memory#storage-location>

## Inventory and TUI

Memory inventory is captured with the session inventory before the preflight
box is drawn. The preflight box groups all memory facts into one row:

```text
· memory      codex
              ├─ stores  2 found
              ├─ files   17
              └─ scope   all known Codex homes
```

```text
· memory      claude
              ├─ stores  5 found
              ├─ files   54
              └─ scope   all Claude auto-memory stores
```

Missing expected directories are valid and count as zero stores. A malformed
settings file, unsafe target, wrong file type, or unreadable store makes the
memory row a warning and preserves the diagnostic for the decision step.

The final prompt is a three-option arrow selector:

```text
? Memory for codex?
  ▶ Run with existing memory
    Delete all Codex auto-memory and run
    Cancel
```

The selected option is collapsed to one confirmation line, following the
existing TUI behavior. Focused prompt text is purple, normal options are gray,
and successful deletion output uses the existing mint success vocabulary.
Line-input fallback accepts `1`, `2`, or `3`; an empty answer selects the first
option.

## Deletion Safety

Deletion is deliberately split into discovery, validation, process checking,
and mutation:

1. Rescan paths and configuration after the user chooses deletion. The latest
   valid inventory, not the preflight snapshot, is the deletion authority.
2. Validate all targets before deleting the first one.
3. Reject a target that is a symlink, is not a directory, cannot be inspected,
   resolves to `/`, the user's home, a product configuration root, a projects
   root, or another broad ancestor.
4. A configured Claude directory must contain its official `MEMORY.md` marker
   when it exists and is non-empty. This prevents an accidentally broad custom
   path from becoming a recursive deletion target.
5. Inspect the macOS process table immediately before deletion. Ignore the
   launcher and its ancestor chain. If another process for the selected product
   is running, refuse deletion and stop the launch because that process could
   rewrite memory concurrently.
6. Delete only the validated store roots without following symlinks. Count
   removed stores and regular files and print a result such as:

```text
  ✓ memory      5 stores · 54 files deleted
```

All targets are prevalidated, but filesystem errors can still cause a partial
deletion. In that case the launcher reports the partial counts and error,
skips session cleanup, and does not launch the agent. Memory deletion is an
explicit destructive choice and is not backed up automatically.

## Components

### `memory_management.py`

Owns product-independent data models plus product-specific discovery and safe
deletion:

- `MemoryStore`: one validated candidate store and its source;
- `MemoryInventory`: client, stores, file count, scope label, and warnings;
- `MemoryDeleteResult`: removed counts and warnings;
- `scan_memory_inventory(...)`: derive and inspect current product stores;
- `running_client_processes(...)`: identify conflicting live processes;
- `delete_all_memory(...)`: rescan authority validation and deletion.

The module stays Python-standard-library-only and exposes injectable process
and filesystem boundaries for deterministic tests.

### `ui.py`

Adds a generic three-option selector while preserving the current yes/no
`confirm()` API. Ctrl+C erases the complete prompt block and restores terminal
attributes before propagating `KeyboardInterrupt` to the launcher's existing
clean cancellation handler.

### `serena_agent_launcher.py`

Combines session and memory snapshots for the preflight display, renders the
new memory tree row, obtains the memory action, performs deletion when chosen,
and only then invokes the existing session cleanup and child launch paths.

## Error Behavior

- Preflight scan failure: show a warning row. Keeping memory remains available;
  deletion is refused because its complete scope cannot be established.
- Malformed Claude settings: keep launch is available; deletion is refused.
- Running same-product process: report the matching process count and stop
  before deletion.
- Unsafe or changed target: report the path reason and stop before deletion.
- Partial filesystem failure: report removed counts plus the failure and stop
  before session cleanup and launch.
- Cancel or Ctrl+C: print the existing `! cancelled` row and return 130.

## Testing

Tests must cover:

- Codex discovery across default, active, and Orca homes with deduplication;
- Claude default project memories and custom `autoMemoryDirectory`;
- exclusion of Chronicle, subagent memory, instructions, and sessions;
- missing directories, malformed settings, symlinks, broad custom paths, and
  non-directory targets;
- deletion counts and proof that unrelated sibling files remain;
- refusal when a same-product process is active;
- no session cleanup or child launch after cancel or deletion failure;
- exact order: overview, setup, memory decision/deletion, session cleanup,
  child launch;
- three-option line and TTY prompt behavior including Ctrl+C cleanup;
- preflight memory row wording and ANSI color roles;
- existing session retention, Serena lifecycle, and non-interactive behavior.

After targeted tests pass, run the full `local_dev/tests` suite, install the
runtime copy with `make -C local_dev install-shim`, and smoke-test both launcher
commands without selecting destructive deletion against live user memory.

## Out of Scope

- age-based or automatic memory retention;
- per-project deletion choices;
- memory editing, preview, backup, or restore;
- Claude `project purge` because it also deletes transcripts and other state;
- Codex session or Claude transcript policy changes;
- public `dotsync` code, root README, or root Makefile changes.
