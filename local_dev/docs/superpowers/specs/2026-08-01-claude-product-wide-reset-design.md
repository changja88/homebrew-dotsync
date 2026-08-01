# Claude Code Product-Wide Reset Design

**Date:** 2026-08-01

## Goal

Give the interactive Claude launcher the same default-safe choice as Codex:
keep every local session and memory, or explicitly delete every discoverable
Claude Code conversation, session, auto-memory store, and generated
conversation trace in the active local application-data scope before starting
a new Claude process.

The reset preserves user-scope authored configuration. In particular, it never
edits or removes `settings.json`, and it leaves the `autoMemoryDirectory`
setting exactly as written while deleting the generated memory store at that
path.

This is a Claude Code CLI reset for the active `CLAUDE_CONFIG_DIR`. It is not a
Claude.ai account-history eraser and does not delete Claude Desktop, web, VS
Code, or remote-session history maintained outside that directory.

## Official Capability and Its Boundary

Claude Code provides an official project-state deletion command:

```text
claude project purge --all --yes
```

The official [Claude directory documentation](https://code.claude.com/docs/en/claude-directory)
states that an all-project purge removes project transcripts and auto-memory,
per-session tasks, debug logs, file history, prompt history, and project entries
in `~/.claude.json`. The same documentation explicitly says that
`shell-snapshots/` and `backups/` are not removed. The
[CLI reference](https://code.claude.com/docs/en/cli-usage) documents both
`project purge` and daemon/session stop commands. The
[memory documentation](https://code.claude.com/docs/en/memory) documents the
default project memory location and the configurable `autoMemoryDirectory`.

An isolated `--dry-run` probe against Claude Code 2.1.220 confirmed that the
official all-project purge planned these targets:

- project transcript and default memory directories;
- task, debug, and file-history state;
- `history.jsonl`; and
- project entries in the mixed global JSON file.

The same probe did not plan generated `plans/`, `paste-cache/`, `image-cache/`,
`session-env/`, `shell-snapshots/`, `sessions/`, `feedback-bundles/`, legacy
`todos/` or `logs/`, top-level `agent-memory/`, or a custom
`autoMemoryDirectory`. Therefore `project purge --all` is the authoritative
first operation, but it is not sufficient by itself for this launcher's strict
conversation-state reset contract.

## User Experience

The interactive Claude launcher shows one combined choice:

```text
? Reset Claude sessions and memories before launch?
  ▶ Keep all sessions and memories (default)
    Delete all sessions, memories, and conversation traces
```

Choosing Enter is an exact launcher-level no-op. It does not run a cleanup
command and it does not inject a five-day retention override into the child
Claude command. Claude then follows the user's own settings and native
defaults.

The destructive choice requires a second default-no confirmation:

```text
Permanently delete all known local Claude Code sessions, memories, history,
generated traces, and currently running CLI sessions? [y/N]
```

Cancelling either prompt changes nothing. A failed or incomplete reset aborts
the new Claude launch so it cannot immediately create state on top of a partial
reset.

Non-interactive invocations, help/version requests, print mode, and other
existing prompt-bypass paths remain non-destructive and launch Claude directly.

## Reset Scope

### Official purge targets

The real Claude binary, not the launcher shim, owns deletion of its documented
project state:

- `$CLAUDE_CONFIG_DIR/projects/` transcript, subagent, tool-result, and default
  memory state;
- `$CLAUDE_CONFIG_DIR/tasks/`;
- `$CLAUDE_CONFIG_DIR/debug/`;
- `$CLAUDE_CONFIG_DIR/file-history/`;
- `$CLAUDE_CONFIG_DIR/history.jsonl`; and
- matching generated project entries in the Claude global JSON file.

The launcher capability-probes the actual binary with `project purge --help`
and requires `--all` and `--yes` before it stops a process or deletes a file.
This detects command availability instead of coupling correctness to a hard
coded version number.

The subprocess environment preserves whether `CLAUDE_CONFIG_DIR` was originally
set. When it was set, every probe/stop/purge call receives that exact validated
value. When it was unset, the launcher leaves it unset so Claude continues to
use its native `~/.claude` plus `~/.claude.json` layout. Resolving the default
path and then newly exporting it is forbidden because Claude treats an explicit
custom config directory differently, including the location of its mixed
global JSON file.

### Supplemental generated-data targets

After the official purge succeeds, the launcher removes only this explicit
allowlist beneath the validated active `CLAUDE_CONFIG_DIR`:

- `agent-memory/`;
- `plans/`;
- `paste-cache/`;
- `image-cache/`;
- `session-env/`;
- `shell-snapshots/`;
- `sessions/`;
- `feedback-bundles/`;
- legacy `todos/`; and
- legacy `logs/`.

It also deletes the exact auto-memory store discovered from the user-scope
`$CLAUDE_CONFIG_DIR/settings.json` `autoMemoryDirectory` value. The settings
file and the setting value are never rewritten. The same existing strict
validation used by memory management applies: the path must be absolute or
`~/`-relative, must not contain parent traversal or a symlink component, must
not be `/`, the home directory, the Claude config directory, the projects
directory, or one of their ancestors, and a non-empty custom store must contain
the expected `MEMORY.md` marker.

Project- or local-scope `.claude/settings*.json` files are user-authored
repository configuration and are never modified by this product-wide
application-data reset. Current official documentation says
`autoMemoryDirectory` is not accepted from those scopes. Default project
auto-memory under `$CLAUDE_CONFIG_DIR/projects/*/memory` is covered by the
official purge.

Managed policy has higher precedence and can set `autoMemoryDirectory`. The
launcher checks file-based macOS managed settings, `managed-settings.d` JSON
drop-ins, the `com.anthropic.claudecode` managed-preferences domain, and the
recognized `$CLAUDE_CONFIG_DIR/remote-settings.json` server-policy cache. A
managed `autoMemoryDirectory` or dynamic `policyHelper` makes reset fail closed
because this user-scope tool cannot safely prove the effective external memory
root. The same check runs before mutation, immediately after the official
purge, and before final success, covering cache updates while the external
command and supplemental verification execute. Explicit `--settings`
invocations already bypass the interactive reset flow.

`plansDirectory` is different from `autoMemoryDirectory`: it is relative to a
project root, can be configured at project/local scope, and the destination can
legitimately contain versioned user files. Claude keeps no complete central
ledger of historical custom plan paths. Deleting such a directory wholesale or
searching every repository would cross the preservation boundary. Reset
therefore fails before mutation when `plansDirectory` is found in user or
managed settings, the current launch project, or project roots discoverable
from the pre-purge mixed global JSON and its recognized backups. For linked
worktrees, discovery expands each git path to both the current worktree root and
the git common/main checkout root because Claude can read settings from both.
The default application-data `plans/` directory remains part of the
supplemental allowlist.

### Explicitly preserved data

The reset does not remove or edit:

- `$CLAUDE_CONFIG_DIR/settings.json`;
- the `autoMemoryDirectory` configuration value;
- authentication and account state;
- user-scope plugin enablement/configuration, skills, commands, hooks, agents,
  and MCP configuration;
- persistent plugin data under `$CLAUDE_CONFIG_DIR/plugins/data/`;
- policy, managed, and remote settings or caches;
- `$CLAUDE_CONFIG_DIR/backups/` files and their non-project top-level values;
  only generated `projects` mappings are removed because they can retain
  `lastSessionId` and session statistics;
- usage/statistics caches such as `stats-cache.json`; or
- any repository `.claude/` directory.

Before mutation the launcher creates descriptor-anchored content manifests for
the named user-authored/high-value roots in this list: credentials,
`plugins/data/`, skills, commands, hooks, agents, rules, output styles, themes,
workflows, keybindings, `CLAUDE.md`, MCP files, and `stats-cache.json`. Their
existence and content must match after the official purge and at final
verification. User-scope plugin enablement and configuration are covered by the
byte-identical `settings.json`; Claude-managed plugin caches, marketplace
clones, and registry metadata may refresh while a Claude command runs.
`remote-settings.json` and `policy-limits.json` are included. The mixed global
JSON, user `settings.json`, and recognized backups retain their separate
semantic/byte-level invariants described above.

`~/.claude.json` is a mixed state file. The official purge may remove its
generated project entries as documented, but the launcher does not delete the
file or independently rewrite its other top-level preferences.

Those deleted project entries can include project trust, history, and
project-entry MCP data. That is accepted because the requested preservation
boundary is user scope. Repository-owned `.claude/settings*.json` and
`.mcp.json` files remain untouched, and pre-existing non-project top-level
values in the mixed global JSON must survive verification, except for the
observed Claude-managed experiment/feature-flag caches
(`cachedExperimentData`, `cachedExperimentFeatures`,
`cachedGrowthBookFeatures`, and `cachedGrowthBookFeaturesAt`), which may
refresh when the real CLI starts.

For a custom `CLAUDE_CONFIG_DIR`, the equivalent mixed file is
`$CLAUDE_CONFIG_DIR/.claude.json`. Before mutation the launcher snapshots all
pre-existing non-`projects` top-level values except those four volatile cache
keys. Verification requires the `projects` mapping to be absent or empty and
every other snapshotted non-project value to remain equal; Claude may add new
product-owned metadata keys.

Claude documents recognized `.claude.json.backup.*` files as a rotating set of
at most five migration snapshots. A pre-snapshotted recognized backup may
therefore disappear while the real CLI runs. Every surviving pre-existing
backup must retain its non-project values, and every recognized backup present
after purge, including a newly rotated-in file, has its generated `projects`
mapping removed and is verified clean. Unrecognized files under `backups/`
remain content-manifested and may not change.

## Runtime Quiescence

Conversation state can be recreated by a running foreground CLI, background
session, or transient daemon. Before running the purge, the launcher:

1. asks the real binary to run `claude daemon stop --any`, which terminates the
   supervisor and background sessions without uninstalling it;
2. scans for remaining Claude Code CLI processes while excluding the current
   launcher and its ancestors;
3. records each PID together with its immutable process start-time identity;
4. revalidates that identity immediately before sending termination signals;
5. repeats discovery and termination up to four times to catch respawns; and
6. fails if process inspection is unavailable, an identity changes, a process
   survives, or processes keep respawning.

The existing Claude process matcher intentionally excludes Claude Desktop.
This reset must retain that boundary: it stops local Claude Code CLI and daemon
workers only and never closes or reopens the desktop application.

The implementation reuses the shared `process_identity`, `terminate_pid`,
`pid_is_alive`, and `running_client_processes("claude")` primitives. It keeps
the small Claude orchestration local to the new module instead of refactoring
the already-verified Codex reset.

## Safe Deletion and Failure Semantics

All targets are discovered and validated before the first destructive step.
The active Claude config root must be absolute, must not be a broad directory,
must not be a shared/system/temp or shallow volume root, and must not contain a
symlink component. Supplemental targets are constructed
only by joining fixed allowlisted names to that root; arbitrary directory names
found on disk are never treated as reset targets.

For a recognized final target, a symlink is unlinked without following it. A
symlink in the config root or any intermediate component fails closed. Regular
files at a supplemental directory target are the wrong type and cause a failure
rather than a recursive guess.
Recursive directory deletion opens every ancestor and target with
`O_DIRECTORY|O_NOFOLLOW`, pins device/inode identity, operates through
directory-relative file descriptors, and revalidates the namespace before and
after mutation. A concurrent ancestor rename or symlink swap therefore fails
instead of redirecting deletion.

There is no honest filesystem transaction spanning the Claude CLI and several
directories. A failure after the official purge can therefore leave a partial
reset. The result reports all completed counts and the first actionable error,
and the launcher aborts instead of claiming success or starting Claude. It does
not attempt to restore deleted conversation data.

After deletion, an independent rescan must prove:

- no Claude Code CLI/background processes remain;
- the official purge target roots contain no session or conversation state;
- no supplemental allowlisted target remains;
- no discovered user-scope custom auto-memory store remains; and
- `settings.json` is byte-for-byte unchanged from its pre-reset snapshot; and
- the mixed global JSON has no project entries and preserves all pre-existing
  non-project top-level values except the four recognized volatile
  experiment/feature-flag cache keys.

Any unreadable path, unsafe target, non-zero purge exit, residual state,
settings change, or failed verification makes the result unsuccessful. Once
official target verification proves the purge completed, later preservation
or supplemental failures retain the completed official session count instead
of reporting `0/N`. A successful final empty-memory rescan reports every
preflight-discovered memory store, including default stores removed by the
official purge.

## Implementation Boundary

Add `local_dev/serena_mcp_management/claude_reset.py` with a small public API:

```python
@dataclass(frozen=True)
class ClaudeResetResult:
    discovered_sessions: int = 0
    deleted_sessions: int = 0
    deleted_memory_stores: int = 0
    deleted_residual_targets: int = 0
    terminated_processes: int = 0
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


reset_all_claude_data(
    *, home: Path, claude_config_dir: Path | None, real_claude_binary: str
) -> ClaudeResetResult
```

The module owns capability probing, prevalidation, CLI quiescence, invocation
of the official purge, supplemental deletion, and post-reset verification.
Command/process/filesystem callables remain injectable as private keyword-only
test seams; production callers use defaults.

Launcher changes are deliberately narrow:

- `_run_memory_choice_v2` returns `keep` silently for both products;
- `_run_session_choice_v2` returns `keep` or `reset_all` for Claude and Codex;
- `_main_v2` dispatches `reset_all` to a client-specific reset helper;
- `_run_claude_reset_v2` resolves the real binary, invokes the new module, and
  renders one combined result row;
- `build_child_command` and `_launch_bare_child` stop injecting
  `cleanupPeriodDays: 5`; and
- the existing Codex reset implementation remains unchanged.

The legacy Claude selective-cleanup and standalone memory helpers may remain as
internal code in this change, but the interactive launch path no longer calls
them. Removing those larger subsystems is a separate cleanup task after the new
flow has proven stable.

Only `local_dev/README.md` is updated. The root public README and root Makefile
remain untouched because `local_dev/` is not part of the Homebrew deliverable.

## Test Contract

Focused tests must prove:

- missing official purge capability fails before process or filesystem
  mutation;
- the exact real-binary command is `project purge --all --yes` and a non-zero
  exit aborts supplemental deletion;
- daemon stop is attempted, remaining CLI processes are identity-pinned,
  terminated, rescanned, and respawn is rejected;
- Claude Desktop is never selected by the process matcher;
- official targets and every supplemental allowlisted generated target are
  gone after success;
- user settings, `autoMemoryDirectory`, auth, plugin persistent data,
  statistics, unrelated files, and non-project values in surviving recognized
  backups survive unchanged;
- Claude-managed plugin cache/marketplace refresh, the volatile
  experiment/feature-flag caches, and recognized backup rotation do not block
  the reset;
- named preserved user-data roots are content-manifested before mutation and
  reverified after the purge and before success;
- the custom memory directory contents are removed without editing its setting;
- broad, relative, parent-traversing, symlinked, markerless, unreadable, and
  wrong-type targets fail safely;
- static, dynamic-helper, macOS-managed, and cached server-managed memory
  redirects fail closed and are checked after the official purge and before
  final success;
- a discoverable custom `plansDirectory` fails before mutation rather than
  risking repository-file deletion or claiming a complete plan reset;
- purge failure, partial filesystem failure, settings mutation, and residual
  state all return failure and prevent child launch;
- default and custom config layouts preserve the original environment-variable
  set/unset state and verify the correct mixed global JSON file;
- Enter keeps Claude data and does not call any cleanup helper;
- confirmed Claude reset calls only the Claude reset helper, while confirmed
  Codex reset continues to call only the Codex reset helper;
- Claude child commands no longer receive the forced five-day settings
  override; and
- non-interactive and prompt-bypass invocations remain non-destructive.

## Accepted Limitations

- The operation is not atomic; failure is reported and launch is aborted, but
  already deleted conversation data cannot be restored.
- Claude Code may add new generated paths in future releases. Capability and
  post-reset checks protect known state, while the explicit allowlist avoids
  deleting a new user-authored directory by guesswork. The allowlist must be
  reviewed when Claude's application-data documentation changes.
- A historical repository-relative custom plan path can outlive every central
  project entry after its setting is removed. Without a path ledger created at
  write time, the launcher cannot safely discover that orphan by scanning the
  machine. The success contract is therefore limited to the active config's
  default plan store; any currently discoverable custom `plansDirectory`
  blocks reset before mutation.
- Claude Desktop, VS Code, web, remote, and account-side conversation history
  are outside the active Claude Code CLI config-directory boundary.
