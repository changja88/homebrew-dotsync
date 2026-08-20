# Serena Worktree-Shared Server Implementation Plan

> Source-repository mapping: paths beginning with `agent_launcher/` describe
> the installed runtime mirror. In `homebrew-dotsync`, the corresponding
> development source is under `local_dev/serena_mcp_management/` and the
> committed tests are under `local_dev/tests/`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run one launcher-managed Serena server per opted-in Git worktree, share it across Codex and Claude, and stop it when the final live launcher lease exits.

**Architecture:** Replace the client-specific Serena scope with a canonical worktree plus a bundled shared context profile. Keep mutable lifecycle state in an owner-private, SHA-256-keyed user runtime/cache root outside every worktree. Preserve the proxy, registry lock, heartbeat, watchdog, and process-identity defenses, while adding server-instance generation checks and direct-child ownership so old launchers cannot attach to replacement servers and failed startup cannot leak processes.

**Tech Stack:** Python 3.12 standard library, zsh, Serena CLI streamable HTTP MCP, `fcntl.flock`, `unittest`

**Spec:** `docs/superpowers/specs/2026-08-20-serena-worktree-shared-server-design.md`

## Global Constraints

- `.serena/project.yml` is the only Serena opt-in marker.
- Missing opt-in never starts or initializes Serena non-interactively.
- Codex and Claude share one endpoint only when their canonical worktree roots match.
- `client_type` is metadata and client injection logic, never server identity.
- Shared Serena context name is `oaicompat-agent`; profile ID is `dotsync-shared-cli-v1`.
- Final-lease shutdown is immediate; no idle grace period.
- Process termination requires PID plus process-start identity.
- Process-table root/context matching is diagnostics-only and never grants termination authority.
- Registry, locks, server/proxy logs, and host-port coordination state must remain outside repository control in verified owner-only runtime directories.
- `SERENA_AGENT_PROJECT_ROOT` is a matching hint only; Python recomputes the nearest boundary from the current working directory.
- Legacy `codex/` and `claude/` runtime records are not deleted or force-terminated.
- Graphify and user-scope agent configuration are unchanged.
- The active implementation is `agent_launcher/local_dev/serena_mcp_management/`; the legacy top-level copy is not edited.
- This workspace is not a Git repository. Do not run `git init`; omit commit steps unless execution is moved into a Git-backed source copy.

---

### Task 1: Add a test harness and make worktree resolution nearest-boundary-first

**Files:**
- Create: `agent_launcher/tests/__init__.py`
- Create: `agent_launcher/tests/test_paths.py`
- Create: `agent_launcher/tests/test_zsh_shim.py`
- Modify: `agent_launcher/local_dev/serena_mcp_management/serena_mcp/paths.py`
- Modify: `agent_launcher/local_dev/serena_mcp_management/serena_zsh_shim.py`
- Modify: `zsh/.zshrc`

**Interfaces:**
- Produces: `find_project_root(cwd: Path) -> Path`, where the nearest `.serena/project.yml` or `.git` boundary wins.
- Produces: `serena_opted_in(project_root: Path) -> bool`.
- Preserves: `_dotsync_agent_project_root` in the rendered zsh block with the same boundary semantics.

- [ ] **Step 1: Write Python boundary and opt-in tests**

Create `test_paths.py` using `tempfile.TemporaryDirectory` and `unittest`. Cover these concrete cases:

```python
class ProjectRootTests(unittest.TestCase):
    def test_nested_worktree_git_file_beats_ancestor_serena_marker(self):
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw) / "parent"
            nested = parent / "worktrees" / "feature"
            child = nested / "src"
            (parent / ".serena").mkdir(parents=True)
            (parent / ".serena" / "project.yml").write_text("project_name: parent\n")
            nested.mkdir(parents=True)
            (nested / ".git").write_text("gitdir: /tmp/fake\n")
            child.mkdir()
            self.assertEqual(find_project_root(child), nested.resolve())

    def test_marker_at_worktree_root_is_opted_in(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".git").mkdir()
            (root / ".serena").mkdir()
            (root / ".serena" / "project.yml").write_text("project_name: test\n")
            self.assertTrue(serena_opted_in(root))

    def test_ancestor_marker_does_not_opt_in_nested_worktree(self):
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw) / "parent"
            nested = parent / "feature"
            (parent / ".serena").mkdir(parents=True)
            (parent / ".serena" / "project.yml").write_text("project_name: parent\n")
            nested.mkdir()
            (nested / ".git").write_text("gitdir: /tmp/fake\n")
            self.assertFalse(serena_opted_in(find_project_root(nested)))
```

- [ ] **Step 2: Run the path tests and verify the nested-worktree case fails**

Run from `agent_launcher/`:

```text
python3 -m unittest tests.test_paths -v
```

Expected: the nested worktree test resolves to the ancestor because the current implementation searches every Serena marker before any Git boundary.

- [ ] **Step 3: Implement one-pass boundary resolution and the explicit opt-in predicate**

Use this shape in `paths.py`:

```python
def find_project_root(cwd: Path) -> Path:
    current = cwd.resolve()
    candidates = (current, *current.parents)
    for candidate in candidates:
        if ((candidate / ".serena" / "project.yml").is_file()
                or (candidate / ".git").exists()):
            return candidate
    for candidate in candidates:
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate
    return current


def serena_opted_in(project_root: Path) -> bool:
    return (project_root.resolve() / ".serena" / "project.yml").is_file()
```

- [ ] **Step 4: Add a zsh parity test before editing the shim**

Render the shim into a temporary rc file, start `zsh -df`, source that file, `cd` into the nested worktree fixture, and print `_dotsync_agent_project_root "$PWD"`. Assert that stdout equals the nested worktree path. Skip only when `shutil.which("zsh")` is `None`.

- [ ] **Step 5: Change the generated zsh root search to the same one-pass boundary rule**

In `_dotsync_agent_project_root`, walk upward once and return immediately when either of these is true:

```zsh
[[ -f "$dir/.serena/project.yml" || -e "$dir/.git" ]]
```

Keep the general marker fallback as a second pass. Make the same managed-block change in `zsh/.zshrc` and assert in `test_zsh_shim.py` that the generated managed block equals the checked-in block.

- [ ] **Step 6: Run the boundary suite**

Run from `agent_launcher/`:

```text
python3 -m unittest tests.test_paths tests.test_zsh_shim -v
```

Expected: all tests pass for `.git` directory, `.git` file, local Serena marker, ancestor Serena marker, and marker fallback cases.

### Task 2: Bundle and validate a client-neutral Serena context

**Files:**
- Create: `agent_launcher/local_dev/serena_mcp_management/serena_mcp/contexts/oaicompat-agent.yml`
- Create: `agent_launcher/tests/test_shared_context.py`
- Modify: `agent_launcher/local_dev/serena_mcp_management/serena_mcp/paths.py`

**Interfaces:**
- Produces: `SHARED_CONTEXT_PROFILE: str = "dotsync-shared-cli-v1"`.
- Produces: `shared_context_path() -> Path` returning an existing absolute `oaicompat-agent.yml`.
- Produces: a Serena context whose resolved context name triggers OpenAI-compatible schema handling while remaining Claude-compatible.

- [ ] **Step 1: Write failing context contract tests**

Test that `shared_context_path()` is absolute, exists, has stem `oaicompat-agent`, and contains these exact YAML lines:

```text
name: oaicompat-agent
single_project: true
structured_tool_output: false
```

Also assert that the excluded tool entries are exactly:

```python
{
    "create_text_file",
    "read_file",
    "execute_shell_command",
    "replace_content",
    "find_file",
    "list_dir",
    "search_for_pattern",
}
```

Parse the small launcher-owned YAML contract with a purpose-built line parser in the test rather than adding PyYAML to the launcher runtime.

- [ ] **Step 2: Run the context test and verify it fails because the file and API do not exist**

Run from `agent_launcher/`:

```text
python3 -m unittest tests.test_shared_context -v
```

- [ ] **Step 3: Add the bundled context**

Use this complete YAML:

```yaml
name: oaicompat-agent
description: Shared single-worktree context for Codex and Claude CLI agents
prompt: |
  You are connected to a single project through a CLI coding agent that already
  provides basic file operations, text search, line-based edits, and shell commands.
  Use Serena for symbolic code understanding, reference analysis, and symbol-level
  edits when those capabilities materially improve correctness or efficiency.
excluded_tools:
  - create_text_file
  - read_file
  - execute_shell_command
  - replace_content
  - find_file
  - list_dir
  - search_for_pattern
included_optional_tools: []
fixed_tools: []
tool_description_overrides: {}
single_project: true
structured_tool_output: false
```

- [ ] **Step 4: Add the profile constant and path resolver**

Resolve the path relative to `paths.py`, not the current working directory or home directory:

```python
SHARED_CONTEXT_PROFILE = "dotsync-shared-cli-v1"


def shared_context_path() -> Path:
    path = Path(__file__).resolve().with_name("contexts") / "oaicompat-agent.yml"
    if not path.is_file():
        raise FileNotFoundError(f"bundled Serena context not found: {path}")
    return path
```

- [ ] **Step 5: Validate the context with the installed Serena CLI**

Run `serena print-system-prompt` against a temporary opted-in project using the absolute YAML path and `--only-instructions`. Expected: exit 0, no unknown-field error, and no `activate_project` tool in the single-project tool set. If no Serena CLI is installed, record this integration check as skipped while keeping the static unit test mandatory.

### Task 3: Replace client-specific scope and registry schema with worktree/profile scope

**Files:**
- Create: `agent_launcher/tests/test_registry.py`
- Modify: `agent_launcher/local_dev/serena_mcp_management/serena_mcp/paths.py`
- Modify: `agent_launcher/local_dev/serena_mcp_management/serena_mcp/registry.py`

**Interfaces:**
- Produces: `Scope(project_root: Path, context_profile: str = SHARED_CONTEXT_PROFILE)`.
- Produces: registry version 2 `Lease` including `client_type`.
- Produces: registry version 2 `ServerRecord` including `server_instance_id` and `context_profile`, with no `client_type`.
- Produces: `refresh_existing_lease(registry, *, lease: Lease, server_instance_id: str) -> bool`.

- [ ] **Step 1: Write scope-sharing and registry round-trip tests**

Cover:

```python
self.assertEqual(Scope(root).key, Scope(root).key)
self.assertEqual(state_dir_for(Scope(root)).parent.name, "dotsync-shared-cli-v1")
self.assertEqual(
    state_dir_for(Scope(root)).name,
    hashlib.sha256(Scope(root).key.encode("utf-8")).hexdigest(),
)
```

Create Codex and Claude leases in the same `ServerRecord`, persist through `locked_registry`, reload, and assert both remain and the record has one `server_instance_id` and no client-specific server field.

- [ ] **Step 2: Write a failing migration-isolation test**

Create old files under:

```text
.serena/dotsync-mcp/codex/registry.json
.serena/dotsync-mcp/claude/registry.json
```

Then open the new shared registry and assert those files are unchanged and the new record is stored only under `<private-runtime-root>/dotsync-shared-cli-v1/<sha256(Scope.key)>/registry.json`.

- [ ] **Step 3: Change `Scope` and state paths**

Remove `CLIENT_TYPES`, `serena_context_for`, and `client_type_for_serena_context` from scope identity. Keep client validation in the launcher/lease creation boundary instead.

- [ ] **Step 4: Implement registry version 2 models**

Use these field contracts:

```python
REGISTRY_VERSION = 2

@dataclass(slots=True)
class Lease:
    lease_id: str
    client_type: str
    launcher_pid: int
    heartbeat_at: float
    launcher_identity: str | None = None

@dataclass(slots=True)
class ServerRecord:
    server_instance_id: str
    server_pid: int
    mcp_url: str
    dashboard_url: str
    project_root: str
    context_profile: str
    started_at: float
    leases: dict[str, Lease]
    watchdog_pid: int | None = None
    upstream_mcp_url: str | None = None
    proxy_pid: int | None = None
    server_identity: str | None = None
    proxy_identity: str | None = None
    watchdog_identity: str | None = None
```

Unsupported or malformed records load as `None`. Do not inspect or terminate PIDs obtained from a record that failed validation.

- [ ] **Step 5: Add refresh-only heartbeat mutation**

`refresh_existing_lease` returns `False` unless the current record instance matches and the lease ID already exists. On success it replaces only that lease with the new timestamp/identity metadata. It must never add a missing lease.

- [ ] **Step 6: Run registry tests**

Run from `agent_launcher/`:

```text
python3 -m unittest tests.test_registry -v
```

Expected: version round-trip, corrupt-record failure, legacy isolation, two-client lease storage, and refresh-only behavior pass.

### Final Review Security Amendment: private runtime and owned startup

The final senior review identified repository-controlled mutable paths as a deployment blocker. These steps are a required security-driven deviation from Task 3's original in-worktree profile directory and extend Tasks 4–7's lifecycle ownership requirements.

- [x] Add adversarial RED tests for former in-project state/log symlinks and predictable registry-temp symlinks, including outside sentinels.
- [x] Select a stable user-private runtime/cache root, allow an absolute test override, hash canonical `Scope.key`, verify non-symlink owner-owned `0700` directories, and open `0600` regular runtime files without following symlinks.
- [x] Replace predictable atomic-write temp paths with random same-directory `0600` files, clean them on write/replace failure, and `fsync` the committed file and directory.
- [x] Add persistence RED tests proving a newly owned server/proxy generation is stopped and reaped when the registry context exit fails, while a reused durable generation survives a failed joining-lease write.
- [x] Retain Serena/proxy/watchdog `Popen` handles through bounded identity/readiness/persistence confirmation; directly stop and reap owned children on failure.
- [x] Require a real inherited-pipe watchdog readiness handshake after CLI parsing and scope construction, followed by generation-bound acquisition rollback on failure.
- [x] Add stale-root end-to-end coverage and make Python recompute the nearest boundary from `cwd` regardless of the environment hint.
- [x] Add client-child ownership RED tests for signal-handler and `wait()` `BaseException`, with process-group termination, bounded reap, original-error precedence, and exactly one release.
- [x] Make generic missing-identity termination fail closed, use macOS libproc second-plus-microsecond start identity, retain Linux kernel start ticks, and render non-final shutdown as `kept (N sessions)`.
- [x] Run the final focused suites, watchdog race three times, real/fake integration, full discovery, compileall, forbidden-manifest verification, and exact managed-process audit.

### Final Re-review Amendment: commit boundary, rolling migration, and read validation

- [x] Define successful registry `os.replace` or target unlink as the ephemeral ownership/visibility commit point.
- [x] Cover payload write, file fsync, replace, target unlink, temporary cleanup, post-replace directory fsync, and post-commit lock unlock/close faults without ambiguous caller-visible ownership.
- [x] Preserve pre-commit errors and previous bytes; make post-commit directory durability and lock cleanup best-effort so new servers, reused leases, watchdogs, and cleared records remain coherently committed.
- [x] Remove automatic process-table orphan termination. Keep process discovery diagnostic-only and terminate only current-private-registry identities or directly owned children.
- [x] Validate existing runtime directory components without creation before read-only registry access; fail closed on missing, symlinked, non-owned, or non-`0700` components.
- [x] Re-run focused fault/lifecycle tests, watchdog race three times, real integration, full discovery, compileall, forbidden manifest, and exact process audit.

### Task 4: Start, discover, and diagnose one shared-context server per worktree

**Files:**
- Create: `agent_launcher/tests/test_server.py`
- Create: `agent_launcher/tests/test_processes.py`
- Modify: `agent_launcher/local_dev/serena_mcp_management/serena_mcp/server.py`
- Modify: `agent_launcher/local_dev/serena_mcp_management/serena_mcp/processes.py`
- Modify: `agent_launcher/local_dev/serena_mcp_management/serena_mcp/diagnostics.py`
- Modify: `agent_launcher/local_dev/serena_mcp_management/serena_mcp/watchdog.py`

**Interfaces:**
- `ensure_server(scope: Scope, initial_lease: Lease) -> ServerRecord` returns a record with a stable `server_instance_id`.
- `process_matches_scope(process: SerenaMcpProcess, scope: Scope) -> bool` matches canonical project root plus bundled context path.
- The watchdog command accepts project root and context profile, never client type.

- [ ] **Step 1: Write a failing server-command test**

Mock `serena_server_command` and `subprocess.Popen`, call `_start_serena_process`, and assert the argv contains:

```text
--project <canonical-worktree-root>
--context <absolute-path-ending-in-contexts/oaicompat-agent.yml>
--transport streamable-http
```

Assert neither `codex` nor `claude-code` appears as a context argument.

- [ ] **Step 2: Write reuse tests with mixed client leases**

Seed a healthy record with a Codex lease, acquire with a Claude lease under the same `Scope`, and assert `ensure_server` does not call `_start_healthy_server`; it returns the same `server_instance_id`, URL, server PID, and proxy PID with two leases.

- [ ] **Step 3: Update server creation and health checks**

Generate `server_instance_id` with `uuid.uuid4()` each time `_start_healthy_server` creates a server. Validate `project_root` and `context_profile`, and start Serena with `shared_context_path()`.

- [x] **Step 4: Keep process matching diagnostic-only**

Diagnostics may classify a launcher process as same-scope only when:

```python
process.project_root == scope.project_root
and Path(process.context).resolve() == shared_context_path()
```

Do not use this classification for orphan termination. A prior in-project v2 shared server is argv-indistinguishable from a new private-runtime server and must drain. Termination authority is limited to the current private registry's PID plus identity or a directly owned `Popen`. Legacy contexts remain visible but unmanaged.

- [ ] **Step 5: Update watchdog construction and diagnostics**

Pass `scope.context_profile` as the watchdog's second argument. Reconstruct `Scope(Path(argv[1]), argv[2])` in its CLI. Diagnostics recognize managed processes by shared context path and version-2 registry identity; legacy client-context servers remain non-managed and are never terminated by diagnostics.

- [ ] **Step 6: Run server/process tests**

Run from `agent_launcher/`:

```text
python3 -m unittest tests.test_server tests.test_processes -v
```

### Task 5: Make lease acquisition, refresh, and final shutdown generation-safe

**Files:**
- Create: `agent_launcher/tests/test_watchdog.py`
- Modify: `agent_launcher/local_dev/serena_mcp_management/serena_mcp/watchdog.py`
- Modify: `agent_launcher/local_dev/serena_mcp_management/serena_agent_launcher.py`

**Interfaces:**
- `make_launcher_lease(lease_id: str, client_type: str, *, now: float | None = None) -> Lease`.
- `release_lease_and_shutdown_if_empty(scope: Scope, lease_id: str, server_instance_id: str) -> ShutdownStats`.
- `_heartbeat_loop` and `_touch_lease_if_record_exists` require the acquired server instance ID.

- [ ] **Step 1: Write the three-session final-release test**

Seed one record with `claude-1`, `claude-2`, and `codex-1`. Mock `_terminate_record`. Release in this order and assert:

```text
claude-1 → remaining=2, server_stopped=False
codex-1  → remaining=1, server_stopped=False
claude-2 → remaining=0, server_stopped=True, terminate called once
```

- [ ] **Step 2: Write stale lease and PID reuse tests**

Mock `process_identity` so a stale identity-matched launcher survives with a refreshed timestamp, while a stale mismatched identity is removed. Assert termination receives both proxy/server expected identities and never runs for a mismatched record.

- [ ] **Step 3: Write a replacement-server heartbeat test**

Acquire instance A, replace the registry fixture with instance B, then call the instance-A heartbeat helper. Assert it returns `False` and does not add A's lease to B.

- [ ] **Step 4: Write a final-release/new-acquire lock test**

Use two threads and a barrier around a mocked slow `_terminate_record`. Assert acquire cannot enter the locked registry while final release is terminating; after release clears the record, acquire creates instance B and persists its initial lease.

- [ ] **Step 5: Implement client-labeled leases and instance-aware release**

Validate `client_type` against `{"codex", "claude"}` in `make_launcher_lease`. On server-instance mismatch, release reports no action and does not mutate the current record. Preserve the existing sleep/wake rule requiring both stale heartbeat and dead/mismatched process identity before eviction.

- [ ] **Step 6: Update launcher heartbeat and release calls**

After `ensure_server`, pass `record.server_instance_id` to the heartbeat thread and final release. Replace `touch_lease` reattachment with `refresh_existing_lease`. Keep `finally` as the only normal release path.

- [ ] **Step 7: Run lifecycle tests repeatedly**

Run from `agent_launcher/`:

```text
python3 -m unittest tests.test_watchdog -v
python3 -m unittest tests.test_watchdog -v
python3 -m unittest tests.test_watchdog -v
```

Expected: all three runs pass without race-dependent failures.

### Task 6: Enforce opt-in before installation/startup and preserve bare-launch fallback

**Files:**
- Create: `agent_launcher/tests/test_launcher.py`
- Modify: `agent_launcher/local_dev/serena_mcp_management/serena_agent_launcher.py`
- Modify: `agent_launcher/local_dev/serena_mcp_management/serena_zsh_shim.py`
- Modify: `zsh/.zshrc`

**Interfaces:**
- `_run_serena_init_v2` accepts an already-resolved worktree root.
- `_main_v2` never calls `ensure_server` unless the exact worktree has an opt-in marker after initialization.
- Both client command builders continue to consume the same `record.mcp_url`.

- [ ] **Step 1: Write a non-interactive opt-out test**

Set a temporary worktree root with `.git` but no Serena marker, set `SERENA_AGENT_INTERACTIVE=0`, mock install/init/server functions, and call `_main_v2`. Assert the bare child is launched and none of `_run_serena_cli_install_v2`, `_serena_project_create`, or `ensure_server` is called.

- [ ] **Step 2: Write interactive decline and accept tests**

Decline: assert no CLI installation or server startup follows and the agent launches bare.

Accept: create the marker through the mocked project-create function, then assert CLI resolution and one shared-scope server acquisition occur.

- [ ] **Step 3: Reorder `_main_v2` around the explicit opt-in gate**

Resolve `client_type` and `project_root` first. If already opted in, continue. If missing and interactive, ask initialization before offering persistent CLI installation; if declined, launch bare. After consent, require `serena_server_command()` to resolve: offer persistent CLI installation when it does not, and launch bare without creating the marker if installation is declined, fails, or remains unresolved. Once the persistent CLI resolves, create the project and re-check the marker. If missing and non-interactive, launch bare immediately.

- [ ] **Step 4: Add graceful shared-server failure fallback**

Wrap `ensure_server`/spinner startup. On failure, terminate only partial processes already handled by `server.py`, emit one warning, and run `_launch_bare_child`. Do not start a heartbeat or call release when no lease was successfully registered.

- [ ] **Step 5: Update UI labels without changing client behavior**

Display context as `shared-cli (oaicompat-agent)` for both clients and server lifecycle as shared worktree state. Keep client-specific reset, memory, session, Codex `-c`, and Claude temporary MCP config behavior unchanged.

- [ ] **Step 6: Verify the zsh checked-in block matches the generator**

Run the shim parity test from Task 1 after updating both files. Do not install into live `~/.zshrc` yet.

- [ ] **Step 7: Run launcher tests**

Run from `agent_launcher/`:

```text
python3 -m unittest tests.test_launcher tests.test_zsh_shim -v
```

### Task 7: Prove two-client sharing and safe rollout

**Files:**
- Create: `agent_launcher/tests/test_shared_server_integration.py`

**Interfaces:**
- End-to-end contract: one worktree, two client types, one URL/server/proxy/watchdog, independent leases, final-exit shutdown.

- [ ] **Step 1: Add a fake-client integration fixture**

Create executable temporary Codex and Claude stand-ins that record argv and block on a control pipe. Launch two launcher subprocesses against one temporary opted-in Git worktree. Use a fake Serena process only for deterministic lifecycle tests; assert both child argv files contain the same MCP URL and the registry has two leases labeled with different clients.

- [ ] **Step 2: Exercise independent exit order**

Release the fake Claude client and assert the registry still exists with one lease and all managed processes remain. Release fake Codex and assert the registry record disappears and the identity-matched fake server/proxy terminate.

- [ ] **Step 3: Add a real Serena smoke test guarded by availability**

When `serena_server_command()` resolves, start a real shared-context server in a disposable opted-in worktree, send two MCP `initialize` requests with distinct client names and session IDs through the proxy, close one client-side session, and invoke `tools/list` from the other. Assert the second remains healthy. Finally release both launcher leases and assert the server and proxy PIDs are dead.

- [ ] **Step 4: Run the complete automated suite**

Run from `agent_launcher/`:

```text
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: all unit tests pass; the real Serena test either passes or reports an explicit availability skip.

- [ ] **Step 5: Review the diff for forbidden scope changes**

Verify no changes appear under:

```text
codex/config.toml
claude/settings.json
claude/mcp-servers.json
codex/skills/graphify/
claude/skills/graphify/
```

Also verify the legacy top-level `serena_mcp_management/` copy is untouched.

- [ ] **Step 6: Perform the disposable-worktree manual acceptance test**

In one opted-in disposable worktree, start two Claude sessions and one Codex session. Confirm the preflight/registry reports three leases and one MCP URL. Exit in two steps and confirm `kept (2 sessions)`, then `kept (1 sessions)`. Exit the final session and confirm `stopped`. Repeat with two different worktrees and confirm different URLs and separate server PIDs.

- [ ] **Step 7: Install the validated generated block into live user scope**

Only after Steps 1–6 pass, run the existing shim installer against `~/.zshrc`, retain its generated backup, open a fresh login shell, and repeat one Codex/one Claude smoke test. This is the only user-scope mutation in the rollout and requires explicit execution-time approval because `~/.zshrc` is outside the workspace.

## Adversarial Review Applied

The first draft was challenged against these failure modes, and the plan was changed as follows:

1. **Ancestor checkout hijacks a nested worktree.** The existing two-pass root search is unsafe. Task 1 changes it to a nearest-boundary single pass and tests `.git` pointer files.
2. **A generic custom context silently loses Codex schema compatibility.** Serena keys compatibility on the context name. Task 2 fixes the name to `oaicompat-agent` and validates the exact contract.
3. **Removing `client_type` from the key lets the first client choose the context.** The launcher no longer derives context from any client; it always passes one bundled context path.
4. **Old heartbeats can pin a replacement server they never connected to.** Tasks 3 and 5 add `server_instance_id` and refresh-only heartbeats.
5. **A final exit can kill a server while a new session acquires it.** Final termination remains inside the same `flock`; Task 5 adds an explicit concurrency test.
6. **Wall-clock lease expiry kills live sessions after sleep/wake.** PID plus start identity remains a second condition; Task 5 locks this in with tests.
7. **PID reuse kills an unrelated process.** Existing identity-aware termination remains mandatory and receives regression coverage.
8. **HTTP connection count is mistaken for session ownership.** Leases remain tied to launcher processes, not transient MCP connections.
9. **A project that declined Serena still triggers installation or startup.** Task 6 moves explicit opt-in ahead of installation and prevents non-interactive implicit management.
10. **Rolling migration kills old Codex/Claude or prior shared-v2 sessions.** The private runtime never uses process argv as kill authority. Prior client-context and exact bundled-context processes remain diagnostic-only and drain under their original owners.
11. **Two source trees drift.** The plan names the active `local_dev` tree and deliberately leaves destructive legacy removal out of scope.
12. **One client's MCP shutdown breaks another.** Task 7 requires a real two-session proxy test before live rollout.
13. **Serena failure prevents Codex/Claude from opening at all.** Task 6 adds a bare-launch fallback after cleaned-up shared-server startup failure.

## Review Verdict

The design is implementable with the current architecture; the existing lease, watchdog, lock, proxy, and process-identity mechanisms are reusable. The highest-risk areas are context compatibility, nested-worktree root resolution, and stale launcher interaction with replacement servers. The tasks above put those risks before UI and live rollout. No unresolved blocker remains in the plan.
