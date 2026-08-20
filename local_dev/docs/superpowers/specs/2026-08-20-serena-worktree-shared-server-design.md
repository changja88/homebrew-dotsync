# Serena Worktree-Shared Server Design

> Source-repository mapping: paths beginning with `agent_launcher/` describe
> the installed runtime mirror. In `homebrew-dotsync`, the corresponding
> development source is under `local_dev/serena_mcp_management/` and the
> committed tests are under `local_dev/tests/`.

## Status

Approved design baseline from the 2026-08-20 discussion, amended after final security review. The amendment moves all mutable launcher runtime state out of repository control and tightens direct-child ownership; these requirements supersede the original in-worktree state-path examples.

## Goal

Run at most one launcher-managed Serena MCP server for each opted-in Git worktree, let Codex and Claude sessions in that worktree share it, and stop it immediately after the last live launcher session releases its lease.

## Decisions

- A Serena project boundary is the nearest ancestor containing either `.serena/project.yml` or `.git`; the nearest boundary wins in one upward pass.
- `.git` may be a directory or a worktree pointer file.
- `.serena/project.yml` remains the project opt-in marker. Missing markers are never treated as implicit opt-in.
- An interactive launch may ask whether to initialize Serena. Declining launches the agent without Serena and may be asked again next time.
- A non-interactive launch with no opt-in marker launches without Serena and does not initialize, install, or start it.
- The sharing scope is the canonical worktree root plus one launcher-owned context profile. `client_type` is not part of server identity.
- Codex and Claude keep their client-specific MCP injection mechanisms, but both receive the same HTTP endpoint.
- Each launcher process owns one lease. The lease records the client type only for diagnostics.
- The first lease starts the server. Intermediate lease releases keep it alive. The final release stops the proxy and Serena immediately while holding the scope lock.
- Heartbeats refresh an existing lease only for the exact server instance originally acquired. They never reattach an old client to a replacement server.
- PID plus process-start identity remains mandatory before terminating a process.
- Process-table discovery is diagnostic only. It never authorizes termination, even when project root and bundled context argv match exactly.
- Mutable registry, lock, and log state lives only in a user-private runtime/cache root, never beneath a worktree.
- Python always recomputes the nearest project boundary from the current working directory. `SERENA_AGENT_PROJECT_ROOT` is only a matching canonical hint.
- Graphify behavior, agent session cleanup, memory reset, and user-scope Codex/Claude settings are outside this change.

## Current Source of Truth

The active zsh configuration points to `agent_launcher/local_dev/serena_mcp_management/serena_agent_launcher.py`. The older top-level `serena_mcp_management/` copy is not referenced by the active shim and is not modified by this change. Removing that legacy copy is a separate, explicitly destructive cleanup decision.

## Approaches Considered

### Recommended: bundled common context plus worktree scope

Both clients use one launcher-owned `oaicompat-agent` context and one server keyed by worktree/profile. This removes duplicate language servers without making startup order choose the context, and keeps the integration testable as a single contract.

### Rejected: keep one server per client

This preserves Serena's built-in `codex` and `claude-code` tuning, but it repeats indexing and language-server memory for the same files. It also retains the launcher behavior that prompted this redesign.

### Rejected: rewrite context and tool schemas per HTTP connection

A client-aware proxy could theoretically expose different prompts and schemas while sharing one internal agent. Serena creates one agent/context per server, however, so this would require the launcher to emulate or rewrite MCP server behavior. The complexity and compatibility risk exceed the value of small client-specific prompt differences.

## Architecture

### Worktree and opt-in resolution

Python and the generated zsh shim use the same precedence:

1. Resolve the starting path.
2. Walk upward once.
3. Return the first directory containing `.serena/project.yml` or `.git`.
4. If neither exists, perform the existing fallback search for project markers such as `pyproject.toml` and `package.json`.
5. If no marker exists, use the starting directory.

This prevents a `.serena/project.yml` in an ancestor checkout from overriding a nearer nested worktree `.git` file.

Root detection and opt-in are separate decisions. A nearest `.git` boundary identifies the worktree even when that worktree has not opted into Serena. Only `<worktree>/.serena/project.yml` authorizes server startup.

The zsh-provided `SERENA_AGENT_PROJECT_ROOT` cannot select a root. Python resolves the boundary again from `Path.cwd()` and accepts the environment value only when its canonical path equals that result. A stale ancestor hint therefore cannot opt a nested worktree into Serena.

### Shared context

The launcher bundles a custom context at:

`agent_launcher/local_dev/serena_mcp_management/serena_mcp/contexts/oaicompat-agent.yml`

The basename and explicit context name are `oaicompat-agent` because Serena enables its OpenAI-compatible tool-schema normalization only for recognized names including `oaicompat-agent`. The profile is client-neutral and has these properties:

- `single_project: true`
- `structured_tool_output: false`
- a generic CLI coding-agent prompt
- no basic file read/write, shell, directory listing, filename search, pattern search, or line-based replacement tools
- symbolic overview, symbol lookup, reference lookup, and symbol-level editing remain available

The launcher passes the absolute YAML path to `serena start-mcp-server --context`. Bundling the file avoids mutable dependence on `~/.serena/contexts` and leaves `codex/config.toml`, `claude/settings.json`, and `claude/mcp-servers.json` unchanged.

### Scope and registry

`Scope` becomes:

```python
@dataclass(frozen=True, slots=True)
class Scope:
    project_root: Path
    context_profile: str = "dotsync-shared-cli-v1"
```

Mutable state is keyed by the SHA-256 digest of canonical `Scope.key` and stored at:

`<user-private-runtime-root>/dotsync-shared-cli-v1/<sha256(Scope.key)>/`

The runtime root is selected in this order:

1. `SERENA_AGENT_RUNTIME_ROOT`, for explicit isolation in tests and managed deployments;
2. `$XDG_RUNTIME_DIR/dotsync-serena-mcp`;
3. `$XDG_CACHE_HOME/dotsync/serena-mcp`;
4. `~/.cache/dotsync/serena-mcp`.

An explicit override must be absolute and must remain outside the selected worktree. The launcher creates and verifies the runtime root, profile directory, and scope directory as owner-owned, non-symlink directories with mode `0700`. Registry, lock, server log, proxy log, and host-port lock files are owner-owned regular files with mode `0600`, opened without following a final-component symlink. Registry commits use a random same-directory `0600` temporary file, `fsync`, and atomic replace, with cleanup on every write or replace failure.

Because this registry coordinates ephemeral local processes rather than durable user data, successful `os.replace` is the record/lease/watchdog ownership commit point. Clearing a record commits when the existing target is successfully unlinked. Payload serialization, temporary-file flush/fsync, replace, and target-unlink failures occur before commit: they propagate, retain the previous visible record, and clean temporary state best-effort without allowing cleanup errors to mask the primary failure. Directory fsync and registry-lock unlock/close happen after the visibility commit and are best-effort; their I/O failures do not report acquisition failure or revoke ownership of the now-visible generation.

Read-only lookup first validates the already-existing runtime root, profile, and hashed scope directories without creating them. Every launcher-owned component must be absolute, outside the worktree, owner-owned, non-symlink, and mode `0700`; otherwise the read fails closed before opening a registry or lock file.

Registry version 2 stores:

```text
ServerRecord
  server_instance_id
  server_pid / server_identity
  proxy_pid / proxy_identity
  watchdog_pid / watchdog_identity
  mcp_url / upstream_mcp_url / dashboard_url
  project_root
  context_profile
  started_at
  leases

Lease
  lease_id
  client_type
  launcher_pid
  launcher_identity
  heartbeat_at
```

The private profile directory deliberately does not inspect or reuse old `codex/`, `claude/`, or prior in-project shared-profile registries. Existing sessions drain under their existing launchers and watchdogs; the new launcher does not migrate, delete, or terminate those legacy processes merely because their context or state location differs. A prior in-project v2 process has the same root and bundled-context argv as a private-runtime generation, so argv matching is never kill authority. Diagnostics may display it as unowned, but only a PID plus identity from the current private registry or a directly retained `Popen` may authorize cleanup.

### Server lifecycle

Acquire and release are serialized by the existing per-scope `flock`.

Acquire:

1. Lock the worktree/profile registry.
2. Validate the existing private runtime directory chain, then load only a version-2 record that matches the canonical worktree and profile.
3. If the recorded server and proxy are healthy, add the new lease.
4. Otherwise terminate only identity-matched recorded processes and start a shared-context server and proxy, retaining both direct `Popen` handles until bounded identity capture succeeds.
5. Assign a new `server_instance_id`, register the initial lease, and transfer ownership when atomic replace exposes the record. Directory fsync and lock cleanup remain best-effort after that point.
6. If that durable commit fails, directly stop and reap only the newly owned server/proxy generation, clean any temporary registry file, and re-raise the persistence error. A reused healthy generation is never stopped because a joining lease failed to persist.
7. Ensure one watchdog exists through an inherited-pipe readiness handshake performed after CLI argument parsing and `Scope` construction. Retain and reap the watchdog handle on readiness, identity, or persistence failure and roll back the generation-bound lease acquisition.

Release:

1. Lock the same registry.
2. Require the expected `server_instance_id`.
3. Remove only the caller's lease.
4. If leases remain, persist and return.
5. If none remain, terminate the identity-matched proxy and Serena process, clear the record, persist, and unlock.

Holding the lock through final termination makes the last-release/new-acquire race deterministic: a new launcher waits and then starts a fresh server.

The launcher likewise owns its client `Popen`: any `BaseException` before or during `wait()` stops its process group and performs a bounded reap before exactly one lease release. Child-cleanup and release failures do not replace the original exception.

### Heartbeat and crash recovery

Every launcher refreshes its lease every five seconds. Refresh succeeds only when:

- the registry still contains the acquired `server_instance_id`;
- the lease ID already exists; and
- the launcher has not begun shutdown.

The watchdog retains the current 30-second stale threshold. A stale timestamp is not enough to evict a lease: if PID and process-start identity still match, the lease is refreshed. This preserves sessions across macOS sleep/wake. A dead or identity-mismatched launcher loses its lease; the watchdog stops the server if that was the final lease.

### Client boundary

`client_type` remains necessary only to:

- locate the real `codex` or `claude` binary;
- inject the common endpoint using Codex `-c` or Claude `--mcp-config`;
- label the lease and UI diagnostics;
- perform existing client-specific conversation and memory cleanup.

It never selects a Serena context, registry, state directory, proxy, watchdog, or server.

### Proxy boundary

The existing proxy remains part of the lifecycle and continues to expose one stable URL to all leases. This change does not alter its DELETE behavior. A two-client integration test must prove that closing one client-side MCP session does not break the other before rollout.

## Failure behavior

- Missing opt-in marker: launch without Serena.
- Declined initialization: launch without Serena.
- Serena CLI unavailable after opt-in: warn and launch without Serena.
- Shared context rejected or server health check fails: clean up identity-matched partial processes, warn, and launch without Serena.
- Unsafe runtime-root override, symlinked/public runtime directory, or insecure runtime file: fail closed without consulting prior in-project state.
- Registry write or replace failure after new server startup: stop and reap the exact directly owned server/proxy handles, expose no record or lease, remove the random temporary file, and preserve the persistence exception.
- Registry directory-fsync or lock-cleanup failure after replace/unlink: retain the visible committed state and return success; never reap the generation, abandon a reused lease, or stop a persisted watchdog because of a post-commit durability/cleanup error.
- Watchdog readiness, identity, or registry persistence failure: stop and reap the directly owned watchdog, roll back the just-acquired generation-bound lease, and leave no duplicate or untracked watchdog.
- Corrupt or unsupported new registry: treat it as no record; never trust its PIDs for termination.
- Legacy registry or process: leave it to its legacy launcher/watchdog to drain.
- Launcher `SIGINT`, `SIGTERM`, or `SIGHUP`: terminate the child, release the lease in `finally`, then apply final-lease shutdown.
- Launcher `SIGKILL` or crash: watchdog removes the stale dead lease after the timeout.

## Acceptance criteria

1. A nested worktree `.git` file wins over an ancestor `.serena/project.yml` in both Python and zsh root resolution.
2. A missing `<worktree>/.serena/project.yml` cannot start Serena non-interactively.
3. Codex and Claude launched from the same worktree receive the same MCP URL and create one Serena process, one proxy, and one watchdog.
4. The shared server command uses the bundled `oaicompat-agent.yml`, `--project <worktree>`, and never a client-specific context.
5. Codex and Claude launched from different worktrees receive different MCP URLs and separate processes.
6. With three leases, releasing two keeps the server; releasing the third stops proxy and Serena.
7. A new acquire racing with final release cannot reuse a stopping process or lose its lease.
8. A stale but identity-matched lease survives; a stale dead lease is removed.
9. A heartbeat from server instance A cannot attach to replacement instance B.
10. PID reuse cannot cause termination of an unrelated process.
11. Existing `codex/` and `claude/` state is not deleted or force-terminated during migration.
12. User-scope Codex/Claude configuration and Graphify files are unchanged.
13. Repository-controlled symlinks at former registry/temp/server-log/proxy-log paths cannot read, write, truncate, migrate, or delete outside targets; new state appears only below the owner-private hashed runtime root.
14. Registry temp-write and replace failures expose no new record or lease and leave no owned server/proxy process; the same failure while joining a reused generation does not stop that generation.
15. Serena, proxy, watchdog, and client direct-child startup failures leave their `Popen` handles reaped; watchdog registration requires the real readiness handshake.
16. A stale ancestor `SERENA_AGENT_PROJECT_ROOT` never overrides the nearest boundary recomputed from the current directory.
17. macOS process identity uses immutable sub-second libproc start data and fails closed when unavailable; missing identity never authorizes generic termination.
18. Pre-commit registry faults preserve previous bytes and caller-visible ownership, while post-replace/unlink directory-fsync and lock-cleanup faults preserve the new committed server, reused lease, watchdog, or cleared record.
19. A missing or symlink-swapped/nonprivate runtime directory returns no record without creating state or exposing bytes through the swapped path.
20. With no private record, a discovered same-root exact-context prior process is not terminated; a healthy private record likewise does not sweep a second unowned exact-context process.

## Rollout

Run unit tests first, then a temporary-directory integration test with fake client executables, then a real smoke test in one opted-in disposable Git worktree. Start Codex and Claude together, verify one shared URL and three-process runtime set, exit one client and verify the other still calls Serena, then exit the final client and verify all managed runtime processes stop. Only after that smoke test should the generated zsh block be installed into the live `~/.zshrc`.
