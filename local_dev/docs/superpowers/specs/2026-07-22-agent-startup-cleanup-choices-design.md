# Agent Startup Cleanup Choices Design

## Goal

Replace the launcher's single memory decision with two independent startup
decisions for both Codex and Claude:

1. whether to delete the selected product's auto-memory;
2. whether to delete all inactive sessions or keep the normal five-day session
   retention behavior.

The memory question defaults to no deletion. The session question defaults to
no full deletion while retaining the existing five-day cleanup. Session cleanup
is always limited to the product being launched, and every session cleanup
policy preserves sessions that are currently running.

This feature belongs only to the private `local_dev` launcher. It does not change
the public `dotsync` package, root README, root Makefile, or Homebrew formula.

## User-Visible Flow

After preflight and setup succeed, the interactive launcher asks both questions
before performing any deletion. The selected product name is substituted into
the labels:

```text
? Delete Codex auto-memory before launch?
  ▶ Keep all memory (default)
    Delete all Codex auto-memory

? Delete Codex sessions before launch?
  ▶ No full deletion — automatic cleanup after 5 days (default)
    Delete all inactive sessions — running sessions are preserved
```

Claude uses the same wording with `Claude` in place of `Codex`.

The two choices are independent, so all four combinations are valid:

| Memory choice | Session choice | Result before launch |
| --- | --- | --- |
| keep | five-day default | keep memory; run normal five-day session cleanup |
| delete | five-day default | delete product memory; run normal five-day session cleanup |
| keep | delete inactive | keep memory; delete every inactive product session |
| delete | delete inactive | delete product memory; delete every inactive product session |

An empty line-input answer or Enter on the arrow selector chooses the first
option. Ctrl+C at either question cancels with status 130 before any deletion,
session cleanup, or child launch. There is no separate `Cancel` menu item and no
third launch-confirmation prompt.

Non-interactive launcher calls keep their current prompt bypass and do not gain
an implicit full-memory or full-session deletion path.

## Execution Order

The interactive control flow is:

1. Build the existing preflight inventory and render setup status.
2. Ask the memory question and record the answer without mutating anything.
3. Ask the session question and record the answer without mutating anything.
4. If requested, rescan and safely delete the selected product's auto-memory.
5. Apply either the selected product's normal five-day session policy or its
   explicit all-inactive session policy.
6. Launch the selected child with the existing Serena integration.
7. Render the existing shutdown summary with cleanup counts and warnings.

Collecting both answers before step 4 guarantees that cancellation at the second
question cannot occur after memory was already deleted.

Memory and session actions are not a cross-product transaction. If memory
deletion succeeds and a later explicit session deletion encounters a filesystem
or safety failure, the launcher reports that memory was already deleted and
continues to launch. It never claims that either action was rolled back.

## Memory Policy

The existing `memory_management.py` discovery and deletion contract remains the
authority:

- Codex deletion targets only `memories/` under the known Codex homes.
- Claude deletion targets only official project auto-memory directories and a
  valid configured `autoMemoryDirectory`.
- Memory belonging to the other product is never considered.
- Sessions, settings, authentication, history, skills, plugins, and auxiliary
  runtime state are outside memory deletion.

`Keep all memory` performs no memory mutation. `Delete all <Product>
auto-memory` triggers a fresh scan, target validation, and the existing
same-product process-conflict check immediately before deletion.

Unlike session deletion, memory deletion cannot safely preserve only a running
process's portion of shared product memory. If another process of the selected
product is running, explicit memory deletion fails closed without mutating the
memory store, reports a warning, and the launcher continues with the selected
session policy and child launch.

## Session Policies

### Five-Day Default

`No full deletion — automatic cleanup after 5 days` preserves the current policy:

- The threshold is exactly `5 * 24 hours`.
- A session whose newest relevant timestamp equals the cutoff is kept.
- Codex cleans eligible logical session groups through the official
  `codex delete --force <UUID>` command after the existing path, fingerprint,
  graph, and open-file checks.
- Claude launches with the existing native
  `--settings '{"cleanupPeriodDays":5}'` retention setting when the caller did
  not already supply its own `--settings` argument.
- Currently running sessions remain preserved.

This is the default session answer. It is not a promise that no session data is
deleted; it explicitly means that only the normal five-day retention cleanup is
allowed.

### Delete All Inactive Sessions

`Delete all inactive sessions — running sessions are preserved` replaces the age
threshold with an explicit inactivity policy for this launch only:

- Every safely identified inactive session is eligible regardless of age.
- Every currently running session is excluded, including its known descendants
  and session-specific auxiliary artifacts.
- The launcher rescans after the user chooses this action; the older preflight
  snapshot is informational only.
- A zero-target result is a successful no-op.
- The policy is product-scoped: a Codex launch cannot delete Claude sessions and
  a Claude launch cannot delete Codex sessions.

After explicit deletion, the normal five-day policy remains configured for the
new child so future sessions retain the existing launcher behavior.

## Codex Full-Session Cleanup

Codex full cleanup extends the existing inventory and cleanup pipeline rather
than introducing a second deletion implementation:

1. Scan all known Codex session homes with an explicit `all_inactive` policy.
2. Read each rollout's first metadata row and construct the existing logical
   root/descendant groups, including missing-parent synthetic roots.
3. Snapshot open rollout identities with `lsof` and keep a whole logical group
   when any member is open.
4. Treat malformed IDs, cycles, conflicting relationships, an unavailable active
   scan, or changed paths as unsafe rather than as deletion candidates.
5. Revalidate the complete path set and every stored fingerprint immediately
   before mutation.
6. Invoke only the official `codex delete --force <UUID>` command for each
   present group member, descendants before parents and source homes before the
   Orca runtime mirror.

`archived_sessions`, memory, SQLite databases, Orca orchestration state,
configuration, authentication, and logs remain out of scope.

## Claude Full-Session Cleanup

Claude has no narrow native command that expresses "delete every inactive
session while preserving active sessions." The launcher therefore uses a
bounded, session-bundle cleanup instead of `claude project purge --all`, which
would also remove memory and other project state.

The Claude configuration root is the absolute `$CLAUDE_CONFIG_DIR` when set, or
`~/.claude` otherwise. A `ClaudeSessionBundle` groups one valid session UUID's
known data across these documented roots:

- `projects/<encoded-project>/<session-id>.jsonl`;
- `projects/<encoded-project>/<session-id>/` for subagent and tool-result data;
- `file-history/<session-id>/`;
- `session-env/<session-id>/`;
- `tasks/<session-id>/`;
- `debug/<session-id>.txt`.

Discovery uses exact UUID components and exact supported roots. It never uses a
prefix wildcard as deletion authority. Settings, credentials, auto-memory,
`CLAUDE.md`, rules, plugins, commands, prompt history, and unrelated project
files remain untouched.

The active-session set is built immediately before deletion:

1. Read Claude's `sessions/*.json` running-session markers.
2. Validate marker shape, session UUID, PID, product process identity, and the
   recorded process-start identity to reject PID reuse.
3. Supplement markers with a fresh open-file snapshot of discovered transcript
   paths.
4. Preserve the complete bundle when either source proves that the session is
   active.

A marker whose PID is definitively dead or whose process-start identity no
longer matches is stale and does not classify its session as running. The
launcher does not delete marker files itself; Claude remains responsible for
their lifecycle. If marker parsing or process inspection cannot establish a
complete active set, explicit full cleanup fails closed before deleting the
first bundle.

The launcher snapshots every target path and fingerprint, rescans active state,
and revalidates targets immediately before mutation. It deletes only complete,
unchanged inactive bundles and never follows a symlink.

Claude's application-data locations and `cleanupPeriodDays` behavior are
documented in the official [`.claude` directory
reference](https://code.claude.com/docs/en/claude-directory) and [settings
reference](https://code.claude.com/docs/en/settings).

## UI and Color Semantics

Memory and session actions must be visually distinct for both Codex and Claude:

- memory question, focused memory option, collapsed memory answer, deletion
  start line, and deletion result line use the existing purple accent;
- session question, focused session option, collapsed session answer, cleanup
  start line, and cleanup result line use yellow;
- unfocused options remain gray;
- success and warning glyphs keep the existing glyph vocabulary, while the
  action label and value carry the semantic accent.

The reusable option selector accepts an accent parameter whose default remains
purple for backward compatibility. Both the raw-terminal arrow selector and the
numbered line-input fallback honor the selected accent.

Representative action output is:

```text
  ◌ memory      deleting all Codex auto-memory
  ✓ memory      2 stores · 17 files deleted
  ◌ sessions    deleting inactive Codex sessions · running preserved
  ✓ sessions    8 sessions deleted · 2 running preserved
```

The rendered memory rows are purple and the session rows are yellow. Exact
counts depend on the selected product and result.

## Components

### `serena_agent_launcher.py`

- Replace the three-option memory-and-run menu with two two-option policy
  selectors.
- Gather both choices before mutation.
- Orchestrate memory deletion, default retention, explicit session deletion,
  fail-closed cleanup with best-effort launch continuation, and the final
  summary.
- Keep non-interactive behavior and Serena lifecycle management unchanged.

### `ui.py`

- Add a backward-compatible selector accent parameter.
- Render memory selections in purple and session selections in yellow in both
  TTY and line-input modes.
- Provide or reuse a small status-row formatter so start and completion lines
  use the same semantic colors without duplicating raw ANSI sequences in the
  launcher.

### `session_inventory.py`

- Make the age criterion explicit rather than hard-coding one scan mode.
- Preserve the current five-day default.
- Add a Codex `all_inactive` inventory path that reuses logical grouping and
  active-file protection.
- Add bounded Claude session-bundle discovery and immutable fingerprints for
  explicit deletion.

### `session_cleanup.py`

- Keep the existing five-day Codex official-CLI cleanup.
- Add strict explicit-cleanup results that distinguish success, safe no-op,
  warning, and failure.
- Reuse Codex deletion safety gates for the all-inactive policy.
- Add Claude bundle revalidation and bounded inactive-bundle deletion.
- Keep Claude's native five-day child setting after either session choice.

### Documentation

Update `local_dev/README.md` to describe both questions, their defaults,
product scope, five-day semantics, active-session preservation, colors, and the
fact that explicit memory deletion still refuses concurrent same-product
processes. Do not mention `local_dev` behavior in the public root README.

## Error Behavior

- Ctrl+C at either choice: no deletion, no cleanup, no child; return 130 without
  a traceback.
- Memory keep: memory inventory warnings do not block launch.
- Explicit memory delete failure: report the reason, continue to the selected
  session policy, and launch the child.
- Default five-day cleanup warning: report the warning and continue launching,
  matching current best-effort behavior.
- Explicit all-inactive session scan or validation failure: delete nothing,
  report the reason, and continue to child launch.
- Explicit deletion runtime failure after mutation begins: report partial counts
  and the exact failure, continue launching, and do not claim rollback.
- Target became active: preserve its complete session bundle, include it in the
  running-preserved count, and continue with other inactive targets.
- Inactive target changed unexpectedly: stop before mutation when detected in
  prevalidation; if detected after another target was already removed, report
  partial counts. In either case, continue the child launch after reporting the
  cleanup failure.
- Zero memory stores or zero inactive sessions: successful no-op and continue.

Warnings and failures must identify whether they belong to `memory` or
`sessions`; a generic `cleanup` label must not make the selected action
ambiguous.

## Testing

Implementation follows test-first order and uses only temporary homes, fake
process tables, fake `lsof`, and fake client binaries. Tests must cover:

- both questions appear for Codex and Claude in the approved order;
- each selector has two options and defaults to its first option;
- exact wording includes the five-day default and running-session preservation;
- the four independent memory/session choice combinations;
- Ctrl+C at either question causes no mutation or child launch;
- purple memory and yellow session rendering in arrow, line-input, collapsed,
  start, success, warning, and zero-target output;
- the existing five-day boundary and best-effort behavior remain unchanged;
- full deletion is limited to the selected product;
- Codex full deletion keeps any logical group with an open member and invokes
  official deletion descendants-first for every other group;
- Claude live-marker validation, PID reuse, dead stale markers, open transcript
  protection, malformed markers, and unavailable process inspection;
- Claude exact bundle deletion and proof that memory, settings, history,
  credentials, plugins, and unrelated files remain;
- path-set, symlink, fingerprint, and active-state race revalidation;
- explicit failure and partial-failure paths report warnings and continue child
  launch;
- zero-target actions succeed;
- non-interactive behavior and existing Serena lifecycle tests remain green.

After focused tests pass, run the full `local_dev/tests` suite, refresh the
knowledge graph with `graphify update .`, deploy only through
`make -C local_dev install-shim`, compare every touched runtime file with its
source copy, and run non-destructive Codex and Claude smoke checks without
selecting explicit deletion against live user data.

## Out of Scope

- deleting or changing the other product's data;
- deleting currently running sessions;
- disabling the normal five-day retention policy;
- per-project, per-session, or age-customization menus;
- backup or restore for explicit destructive choices;
- deleting Claude auto-memory through session cleanup;
- using `claude project purge --all`;
- modifying public `dotsync` behavior or release artifacts.
