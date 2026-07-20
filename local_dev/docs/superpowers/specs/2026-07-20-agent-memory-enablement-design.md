# Agent Memory Enablement Design

## Goal

Enable the native main auto-memory features in both Codex and Claude before
adding launcher-owned memory discovery and deletion behavior. This phase only
changes product configuration and verifies the effective settings. It does not
delete memory, change session retention, or add a launcher prompt.

The follow-up launcher phase will independently resolve and validate memory
paths on every launch. Enabling memory does not itself register those paths
with the launcher.

## Selected Approach

Use each product's documented persistent configuration instead of adding
launch-time flags or environment overrides.

For Codex, explicitly enable memory generation and use:

```toml
[features]
memories = true

[memories]
generate_memories = true
use_memories = true
```

Apply the same targeted keys to both effective host homes:

- `~/.codex/config.toml`, the canonical Codex configuration;
- `~/Library/Application Support/orca/codex-runtime-home/home/config.toml`,
  the effective `CODEX_HOME` inside the current Orca terminal.

Do not replace either file wholesale. Preserve all unrelated settings, comments,
ordering, and Orca hook trust state. Do not directly edit Orca's
`codex-accounts/*/home/config.toml` copies; Orca owns and refreshes those copies
from the canonical configuration.

For Claude, explicitly enable auto-memory in `~/.claude/settings.json`:

```json
{
  "autoMemoryEnabled": true
}
```

Preserve all existing keys. Do not set `autoMemoryDirectory`, so Claude keeps
using its default project-scoped location under
`${CLAUDE_CONFIG_DIR:-~/.claude}/projects/<project>/memory/`. Do not edit Orca's
per-account `claude-accounts/*/auth/settings.json` copies; on the current macOS
host, Orca changes Claude account credentials through Keychain while normal
Claude state remains under `~/.claude`.

## Alternatives Considered

### Rely on product defaults

Claude auto-memory is currently on by default, but leaving the key implicit
would make the intended policy invisible and vulnerable to a future default
change. Codex memory is off by default, so this approach cannot satisfy the
goal for both products.

### Inject flags or environment variables in the launcher

This would couple memory enablement to the launcher and make direct Codex or
Claude runs behave differently. Product configuration is the correct durable
control surface.

### Edit only the canonical Codex configuration

Orca normally mirrors the canonical Codex configuration into its runtime home,
but the already-materialized runtime home is the effective home in the current
terminal. Applying the same targeted keys to both homes makes the result
immediate and independently verifiable without replacing Orca-owned state.

## Path Contract for the Follow-up Phase

After this phase, the launcher must still compute paths itself on every run.
It will distinguish:

- an expected memory path derived from effective configuration;
- a discovered memory store that currently exists and passed safety checks.

The relevant current expected paths are:

- normal Codex: `~/.codex/memories/`;
- Codex in an Orca terminal:
  `~/Library/Application Support/orca/codex-runtime-home/home/memories/`;
- Claude without a custom directory:
  `~/.claude/projects/<project>/memory/`;
- Claude with a future `autoMemoryDirectory`: the effective configured path.

A missing directory immediately after enablement is valid. Both products may
create memory only after an eligible session or background pass. The launcher
must not create an empty product memory directory merely to prove the setting
is enabled.

Chronicle memory under
`$CODEX_HOME/memories_extensions/chronicle/`, Claude subagent memory, durable
instruction files, and session transcripts remain outside the main
auto-memory scope.

## Safety and Failure Behavior

- Back up each configuration file before changing it so the targeted settings
  can be restored if validation fails.
- Parse the existing TOML and JSON before editing; abort that product's change
  if its configuration is malformed.
- Apply only the selected keys and preserve file permissions.
- If one product succeeds and the other fails, report the partial result
  explicitly and restore the successful product from its backup, leaving the
  two-product policy atomic from the user's perspective.
- Do not launch either agent, generate memory, delete existing memory, or touch
  session files as part of this phase.

## Verification

Verification must prove:

- `codex features list` reports `memories ... true` with
  `CODEX_HOME=~/.codex`;
- the same command reports `memories ... true` with Orca's runtime
  `CODEX_HOME`;
- both Codex configurations resolve `generate_memories = true` and
  `use_memories = true`;
- `~/.claude/settings.json` resolves `autoMemoryEnabled` to `true`;
- `autoMemoryDirectory` remains unset;
- existing Claude memory file paths and SHA-256 content hashes are unchanged;
- the configuration procedure does not directly write Codex or Claude session
  files; concurrent active-agent changes are retained as audit deltas rather
  than treated as configuration mutations;
- unrelated configuration content remains unchanged;
- no file under the public `dotsync` implementation or documentation changes.

## Follow-up Boundary

Launcher memory inventory, path registry, TUI choices, deletion safety,
process rechecks, and deletion result output belong to a separate design and
implementation cycle after this configuration-only phase is verified.
