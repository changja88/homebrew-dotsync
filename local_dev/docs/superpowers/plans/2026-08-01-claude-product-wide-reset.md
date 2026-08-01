# Claude Code Product-Wide Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one confirmed Claude Code CLI reset that removes all known local conversations, sessions, auto-memory, and generated traces while preserving user-authored settings, including the unchanged `autoMemoryDirectory` value.

**Architecture:** Treat the real Claude CLI's `project purge --all --yes` as the authoritative project-state operation, surround it with capability detection and identity-pinned process quiescence, then delete only documented generated-data gaps from a strict allowlist. Verify filesystem, process state, and settings bytes independently before launching a new Claude process. Keep the existing Codex reset behavior intact.

**Tech Stack:** Python 3.12+, stdlib-only launcher code, pytest, real Claude CLI capability probe through an injectable subprocess seam.

**Design:** `../specs/2026-08-01-claude-product-wide-reset-design.md`

## Global Constraints

- Scope is the active local Claude Code CLI `CLAUDE_CONFIG_DIR`, not Claude
  Desktop, Claude.ai, VS Code, or remote/account history.
- The default keep branch performs no cleanup and injects no retention override.
- The destructive path requires a second default-no confirmation.
- Probe `project purge --help` for `--all` and `--yes` before stopping a process
  or deleting a file.
- Invoke the real binary selected by `find_real_binary("claude")`; never invoke
  the `claude` shim by name from the reset module.
- Preserve the original `CLAUDE_CONFIG_DIR` set/unset state. Pass the exact
  validated value when set; leave it unset for Claude's native default layout.
- Preserve user-scope settings/config/auth/plugins/skills/hooks/agents/MCP
  data. `settings.json` must be byte-for-byte unchanged after success.
- Accept removal of documented mixed-file project entries, including their
  project trust/history/MCP fields; preserve all non-project top-level values
  and repository `.claude/`/`.mcp.json` files.
- Delete the custom auto-memory store but never rewrite its
  `autoMemoryDirectory` setting.
- Preserve `backups/`, usage/statistics caches, policy caches, and repository
  `.claude/` directories.
- Never follow a symlink. Reject a symlinked root/intermediate component; unlink
  only a final allowlisted target symlink.
- Prevalidate all discoverable paths before mutation. Report a later partial
  failure honestly and abort launch; never claim rollback.
- Do not modify `codex_reset.py` or its reset behavior.
- Update only `local_dev/README.md`; do not edit the root README or Makefile.

---

### Task 1: Establish the reset result and non-mutating preflight

**Files:**
- Create: `local_dev/serena_mcp_management/claude_reset.py`
- Create: `local_dev/tests/test_claude_reset.py`

**Interfaces:**
- Produces: `ClaudeResetResult` and `reset_all_claude_data(...)`.
- Reuses: `lexical_claude_config_dir`, `scan_memory_inventory`, and
  `scan_claude_inventory`.
- Injects: `RunCommand` so unit tests never execute a real destructive command.

- [ ] **Step 1: Write failing capability tests**

Start the new test file with a command recorder:

```python
class CommandRecorder:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, command, **kwargs):
        key = tuple(command)
        self.calls.append((key, kwargs))
        code, stdout, stderr = self.responses.get(key, (0, "", ""))
        return subprocess.CompletedProcess(command, code, stdout, stderr)
```

Add `test_result_succeeds_only_without_error` and
`test_missing_official_purge_capability_fails_before_mutation`. The latter must
return help containing `--all` but not `--yes`, replace
`_terminate_claude_runtimes` with an assertion failure, and prove settings bytes
and generated fixture files are unchanged.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py -q
```

Expected: collection fails because the module does not exist.

- [ ] **Step 3: Add concrete types, constants, and capability probe**

Create these declarations:

```python
RunCommand = Callable[..., subprocess.CompletedProcess[str]]

_SUPPLEMENTAL_DIRECTORY_NAMES = (
    "agent-memory",
    "plans",
    "paste-cache",
    "image-cache",
    "session-env",
    "shell-snapshots",
    "sessions",
    "feedback-bundles",
    "todos",
    "logs",
)

_OFFICIAL_DIRECTORY_NAMES = (
    "projects",
    "tasks",
    "debug",
    "file-history",
)


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
```

Implement `_probe_purge_capability` by running
`[real_binary, "project", "purge", "--help"]` with captured text,
`check=False`, and the preserved environment. Search combined stdout/stderr and
return an error for OS/subprocess failure, non-zero exit, or absence of either
required flag.

- [ ] **Step 4: Implement read-only preflight**

Use this public signature:

```python
def reset_all_claude_data(
    *,
    home: Path,
    claude_config_dir: Path | None,
    real_claude_binary: str,
    run_command: RunCommand = subprocess.run,
) -> ClaudeResetResult:
```

Initially implement it only through preflight: resolve with
`lexical_claude_config_dir`, require an absolute non-broad non-symlinked root,
snapshot absent/present state plus bytes of `settings.json`, scan Claude memory
and reject warnings, build a fixed supplemental target list, preserve the
incoming `CLAUDE_CONFIG_DIR` set/unset state in a copied environment, and call
the capability probe. Derive the mixed global JSON path as `home/.claude.json`
for the unset/default layout or `config_dir/.claude.json` for an explicitly set
custom layout.
Return a zero-count success only after all checks pass; later tasks replace that
return with mutation orchestration.

- [ ] **Step 5: Add path-preflight coverage**

Add parametrized tests for a relative config root, `/`, home as config root,
parent traversal, root/intermediate symlink, symlinked settings, invalid settings
JSON, invalid or symlinked mixed global JSON, unsafe custom memory path, and a
non-empty custom store without `MEMORY.md`. Every case must prove that purge,
process stop, and filesystem deletion were not called.

- [ ] **Step 6: Verify GREEN and commit**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py -q
git add local_dev/serena_mcp_management/claude_reset.py local_dev/tests/test_claude_reset.py
git diff --cached --check
git commit -m "feat(local_dev): establish Claude reset preflight"
```

---

### Task 2: Quiesce Claude Code CLI and daemon runtimes safely

**Files:**
- Modify: `local_dev/serena_mcp_management/claude_reset.py`
- Modify: `local_dev/tests/test_claude_reset.py`

**Interfaces:**
- Adds private `_PinnedRuntime`, `_RuntimeTermination`, and
  `_terminate_claude_runtimes(...)`.
- Reuses `running_client_processes("claude")`, `process_identity`,
  `terminate_pid`, and `pid_is_alive` without refactoring Codex.

- [ ] **Step 1: Write failing runtime tests**

Cover these cases with injected fakes:

- `daemon stop --any` occurs before the first scan;
- an exact Claude CLI process is pinned, revalidated, terminated with its
  expected identity, rescanned, and counted;
- Claude Desktop is never returned by the existing matcher;
- missing/changed identity, surviving PID, process-scan failure, and four
  consecutive respawns each fail; and
- daemon-stop failure is a warning only if direct scans prove quiescence.

The primary test should record this observable order:

```python
assert events[0] == "daemon"
assert events.count("scan") >= 2
assert "terminate:4242:start-1" in events
assert termination.terminated == 1
assert termination.error is None
```

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py -k runtime -q
```

- [ ] **Step 3: Add records and bounded identity-pinned termination**

```python
@dataclass(frozen=True)
class _PinnedRuntime:
    process: ClientProcess
    identity: str


@dataclass(frozen=True)
class _RuntimeTermination:
    terminated: int = 0
    warnings: tuple[str, ...] = ()
    error: str | None = None
```

Implement the helper in this exact order:

1. Run `[real_binary, "daemon", "stop", "--any"]` once.
2. Record a non-zero/OS failure as a warning and continue.
3. For at most four passes, scan with `current_pid=os.getpid()`.
4. Return success when a pass is empty.
5. Pin every PID with its start-time identity before terminating the pass.
6. Rescan, require the same PID and identity, then call
   `terminate_pid(pid, expected_identity=identity)`.
7. Require `pid_is_alive(pid)` to be false.
8. After four non-empty passes, return a respawn error.

Any scan/pin/revalidation/termination error is fatal. Never signal a process
whose identity is missing or changed.

- [ ] **Step 4: Keep the public preflight non-mutating for this commit**

Do not call the termination helper from `reset_all_claude_data` yet. Task 4
composes quiescence and purge atomically at the orchestration level; an
intermediate commit must not stop Claude processes without performing the reset.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py -q
git add local_dev/serena_mcp_management/claude_reset.py local_dev/tests/test_claude_reset.py
git diff --cached --check
git commit -m "feat(local_dev): quiesce Claude runtimes before reset"
```

---

### Task 3: Delete supplemental generated state and custom memory

**Files:**
- Modify: `local_dev/serena_mcp_management/claude_reset.py`
- Modify: `local_dev/tests/test_claude_reset.py`

**Interfaces:**
- Adds `_SupplementalTarget`, `_discover_supplemental_targets`,
  `_delete_supplemental_targets`, and `_supplemental_residuals`.
- Reuses `delete_all_memory(client="claude", ...)` for the user-scope custom
  memory path and existing broad-path/marker validation.

- [ ] **Step 1: Seed exhaustive generated and preserved fixtures**

Add a helper that creates every `_SUPPLEMENTAL_DIRECTORY_NAMES` entry with a
file and creates a custom store containing `MEMORY.md`. It must also create and
retain sentinels under `backups/`, `plugins/`, `skills/`, auth, statistics, an
unrelated config directory, and a repository `.claude/settings.local.json`.

Use settings bytes built exactly once and assert them unchanged after reset:

```python
settings_bytes = json.dumps({
    "theme": "dark",
    "autoMemoryDirectory": str(custom_memory),
}, separators=(",", ":")).encode()
(config_dir / "settings.json").write_bytes(settings_bytes)
```

- [ ] **Step 2: Write failing deletion/safety tests**

Add helper-level tests for successful exhaustive deletion/preservation, final
allowlisted symlink unlink without target traversal, wrong-type allowlisted
directory, injected recursive-delete failure, unsafe custom path, and
markerless custom directory. Wrong type and unsafe custom path must fail before
official purge.

- [ ] **Step 3: Verify RED**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py -k "supplemental or custom_memory or preserved or symlink or wrong_type" -q
```

- [ ] **Step 4: Implement fixed-target deletion**

```python
@dataclass(frozen=True)
class _SupplementalTarget:
    path: Path
    allowed_root: Path


def _discover_supplemental_targets(config_dir: Path):
    return tuple(
        _SupplementalTarget(config_dir / name, config_dir)
        for name in _SUPPLEMENTAL_DIRECTORY_NAMES
    )
```

Validate all targets with `lstat` before mutation: missing is valid, a final
symlink is unlink-only, a real directory is recursive, and every other type is
fatal. Revalidate immediately before deleting. Use `os.unlink` for a final
symlink and `shutil.rmtree` for a directory; never resolve the final link.

Keep these as deletion helpers in this commit. Task 4 calls
`delete_all_memory` with the same home and resolved Claude config root after
the official purge, propagates partial counts on failure, and then calls the
supplemental helper in fixed tuple order. Do not expose a partially composed
reset path between Tasks 3 and 4.

- [ ] **Step 5: Verify shared memory behavior and commit**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py local_dev/tests/test_memory_management.py -q
git add local_dev/serena_mcp_management/claude_reset.py local_dev/tests/test_claude_reset.py
git diff --cached --check
git commit -m "feat(local_dev): remove residual Claude conversation state"
```

---

### Task 4: Compose official purge and independent post-verification

**Files:**
- Modify: `local_dev/serena_mcp_management/claude_reset.py`
- Modify: `local_dev/tests/test_claude_reset.py`

**Interfaces:**
- Completes `reset_all_claude_data`.
- Adds `_official_residuals(...)` and `_settings_unchanged(...)`.

- [ ] **Step 1: Write failing orchestration tests**

Prove:

- exact command order is capability probe, daemon stop, official purge;
- every Claude subprocess preserves the same config-variable set/unset state;
- the default layout verifies `home/.claude.json`, while a custom layout
  verifies `config_dir/.claude.json`;
- non-zero purge prevents memory/supplement deletion and reports stderr;
- zero purge exit with an official residual still fails;
- final process respawn fails;
- changed settings bytes fail even when all generated targets are gone; and
- success returns session, memory, supplemental, and terminated counts.

A successful fake purge must mutate only its temporary fixture:

```python
def fake_successful_claude(command, **kwargs):
    if command[-4:] == ["project", "purge", "--all", "--yes"]:
        config_dir = Path(kwargs["env"].get("CLAUDE_CONFIG_DIR", default_config_dir))
        for name in claude_reset._OFFICIAL_DIRECTORY_NAMES:
            shutil.rmtree(config_dir / name, ignore_errors=True)
        (config_dir / "history.jsonl").unlink(missing_ok=True)
    stdout = (
        "Options: --all --yes"
        if command[-3:] == ["project", "purge", "--help"]
        else ""
    )
    return subprocess.CompletedProcess(command, 0, stdout, "")
```

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py -k "purge or residual or settings_unchanged or final_process" -q
```

- [ ] **Step 3: Complete orchestration in a fixed order**

Implement these stages:

1. Resolve/validate root and snapshot settings plus non-project global values.
2. Count sessions with `scan_claude_inventory`.
3. Validate memory and supplemental targets.
4. Probe purge capability.
5. Stop daemon and identity-pinned CLI processes.
6. Run `[real_binary, "project", "purge", "--all", "--yes"]` with captured
   output, `check=False`, and the preserved environment.
7. Delete custom memory and supplemental targets.
8. Rescan processes, official roots, supplemental targets, and memory.
9. Require the mixed global JSON `projects` mapping to be absent/empty and
   every pre-existing non-project top-level value to remain equal.
10. Compare settings absent/present state and bytes.
11. Return success only if every check passes.

Treat an official directory as clean only when absent or an empty real
directory. `history.jsonl` must be absent. Any unreadable path, wrong type,
symlink, non-empty official directory, supplemental target, memory store, live
process, or settings change is a final residual error. Set
`deleted_sessions = discovered_sessions` only after official target verification.

- [ ] **Step 4: Verify no live CLI invocation and commit**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py -q
.venv/bin/python3 -m pytest local_dev/tests -q
git add local_dev/serena_mcp_management/claude_reset.py local_dev/tests/test_claude_reset.py
git diff --cached --check
git commit -m "feat(local_dev): verify complete Claude Code reset"
```

The suite's subprocess guard remains enabled. Every reset test supplies a fake
runner; no test invokes the installed Claude binary.

---

### Task 5: Integrate the combined Claude reset and make Keep truthful

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py`
- Modify: `local_dev/serena_mcp_management/session_cleanup.py`
- Modify: `local_dev/tests/test_launcher_phases.py`
- Modify: `local_dev/tests/test_serena_launcher.py`

**Interfaces:**
- Imports `ClaudeResetResult` and `reset_all_claude_data`.
- Adds `_run_claude_reset_v2(...) -> ClaudeResetResult`.
- Removes `CLAUDE_RETENTION_JSON` and `claude_retention_args`.
- Leaves the legacy selective cleanup engine present but unreachable from the
  interactive launch path.

- [ ] **Step 1: Write failing prompt tests**

Replace the two old Claude prompts with this contract:

```python
assert launcher._run_memory_choice_v2(
    stream=memory_out,
    input_fn=lambda: "",
) == "keep"
assert memory_out.getvalue() == ""
assert launcher._run_session_choice_v2(
    stream=session_out,
    input_fn=lambda: "",
) == "keep"
plain = _strip_ansi(session_out.getvalue())
assert "Reset Claude sessions and memories before launch?" in plain
assert "Keep all sessions and memories (default)" in plain
assert "Delete all sessions, memories, and conversation traces" in plain
assert "automatic cleanup after 5 days" not in plain
```

Add confirmation tests for second option plus empty/no returning `keep`, and
second option plus yes returning `reset_all`. Claude confirmation must describe
local Claude Code CLI data and running CLI sessions, not Desktop/web or Codex.

- [ ] **Step 2: Write failing dispatch and command tests**

Prove Claude keep calls no cleanup helper; Claude reset calls only
`_run_claude_reset_v2`; reset failure returns `1` before child launch; Codex
reset still calls only `_run_codex_reset_v2`; non-interactive Claude never calls
reset; scoped and bare Claude commands contain no injected `--settings` or
`cleanupPeriodDays`; and a user-supplied `--settings` is passed through once.

- [ ] **Step 3: Verify RED**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py local_dev/tests/test_serena_launcher.py -k "claude or codex_reset or retention" -q
```

- [ ] **Step 4: Implement the combined choice**

Make `_run_memory_choice_v2` return `keep` silently for both products. Build the
session options from the client product name:

```python
product = "Codex" if client == "codex" else "Claude"
reset_choice = select_option(
    f"Reset {product} sessions and memories before launch?",
    options=(
        SelectOption("keep", "Keep all sessions and memories (default)"),
        SelectOption(
            "reset",
            "Delete all sessions, memories, and conversation traces",
        ),
    ),
    default_index=0,
    accent=AMBER,
    stream=out,
    input_fn=input_fn,
)
```

Keep Codex confirmation text unchanged. Add separate truthful Claude
confirmation. Return only `keep` or `reset_all` for either client.

- [ ] **Step 5: Add Claude dispatch and generic reset summary**

Add `_run_claude_reset_v2` beside the Codex helper. Resolve the real binary
once, pass home/config/binary to the new backend, catch `OSError`,
`RuntimeError`, and `ValueError`, and render session, memory, residual target,
runtime, warning, and error counts in one row.

In `_main_v2`, dispatch `reset_all` by `client_type` and abort on either result
failure. Rename the launcher-local `LaunchPrepSummary.codex_reset` and
`_render_summary_v2` keyword to `conversation_reset`; do not rename or change
`CodexResetResult`.

- [ ] **Step 6: Remove forced five-day retention**

In `build_child_command`, return:

```python
return [real_binary, f"--mcp-config={path}", *child_args], cleanup
```

In `_launch_bare_child`, always use `child_args = list(args)`. Remove the
launcher import, `CLAUDE_RETENTION_JSON`, and `claude_retention_args`. Do not
delete the selective-cleanup implementation in this feature.

Normalize Claude preflight display to zero-to-delete/all-to-keep and
`sessions: all projects + full reset only`. Render
`full reset on confirmation · no automatic deletion` instead of native
five-day retention.

- [ ] **Step 7: Verify Claude and Codex regression, then commit**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py local_dev/tests/test_serena_launcher.py -q
.venv/bin/python3 -m pytest local_dev/tests/test_codex_reset.py local_dev/tests/test_claude_session_inventory.py local_dev/tests/test_claude_session_cleanup.py -q
git add local_dev/serena_mcp_management/serena_agent_launcher.py local_dev/serena_mcp_management/session_cleanup.py local_dev/tests/test_launcher_phases.py local_dev/tests/test_serena_launcher.py
git diff --cached --check
git commit -m "feat(local_dev): add combined Claude reset choice"
```

---

### Task 6: Document, adversarially verify, and promote the runtime copy

**Files:**
- Modify: `local_dev/README.md`
- Verify: all `local_dev/serena_mcp_management/` and `local_dev/tests/`

- [ ] **Step 1: Update internal documentation**

Document the combined prompt, local Claude Code CLI boundary, official and
supplemental targets, preserved settings/auth/plugins/backups/statistics,
unchanged `autoMemoryDirectory` setting with deleted store, process shutdown,
abort-on-failure behavior, removed forced retention override, project/local
custom-memory limitation, and Desktop/web/remote exclusion. State that Codex
continues using its existing hard-reset backend.

- [ ] **Step 2: Run the adversarial test matrix**

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py -k "capability or broad or traversal or symlink or wrong_type or marker" -q
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py -k "identity or respawn or surviving or final_process or daemon" -q
.venv/bin/python3 -m pytest local_dev/tests/test_claude_reset.py -k "purge or residual or settings or preserved or partial" -q
.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py -k "claude or codex_reset" -q
```

Every command must collect at least one test. Add/rename a test if pytest
reports zero selected tests.

- [ ] **Step 3: Run complete verification**

```bash
.venv/bin/python3 -m pytest local_dev/tests -q
.venv/bin/python3 -m compileall -q local_dev/serena_mcp_management
git diff --check
```

- [ ] **Step 4: Reject boundary violations in final diff**

Inspect `git diff --stat` and the seven changed implementation/test/doc files.
Reject any recursive deletion rooted at home, unbounded glob, settings edit,
shell invocation of bare `claude`, swallowed reset failure, change under
`lib/dotsync/`, root README change, or root Makefile change.

- [ ] **Step 5: Promote only after verification**

```bash
make -C local_dev install-shim
shasum -a 256 local_dev/serena_mcp_management/claude_reset.py "$HOME/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/claude_reset.py"
shasum -a 256 local_dev/serena_mcp_management/serena_agent_launcher.py "$HOME/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/serena_agent_launcher.py"
```

Each development/runtime pair must match. Do not copy files manually.

- [ ] **Step 6: Commit documentation only**

```bash
git add local_dev/README.md
git diff --cached --check
git commit -m "docs(local_dev): document Claude product-wide reset"
```

## Adversarial Review Outcome

| Attack / failure | Outcome | Required defense |
|---|---|---|
| Assume official `purge --all` covers every artifact | **Rejected** | Supplemental allowlist plus independent rescan. |
| Delete the entire `~/.claude` directory | **Rejected** | It would erase settings/auth/plugins; use official purge plus fixed targets only. |
| Clear `autoMemoryDirectory` to reset memory | **Rejected** | Delete the validated store and byte-compare settings. |
| Let Keep inject five-day retention | **Rejected** | Remove `cleanupPeriodDays: 5` from scoped and bare launches. |
| Purge while old CLI/daemon workers can rewrite state | **Rejected** | Daemon stop, identity-pinned termination, respawn loop, final scan. |
| Kill by PID without identity revalidation | **Rejected** | PID plus process start time is required before signaling. |
| Follow a symlink to external data | **Rejected** | Intermediate links fail; final allowlisted link is unlinked only. |
| Search every repository for project settings | **Rejected** | It is unbounded and can delete repo-authored data; document the limitation. |
| Trust zero purge exit as success | **Rejected** | Verify official, supplemental, memory, process, and settings state independently. |
| Export default `~/.claude` as a new custom config value | **Rejected** | Preserve the environment variable's original set/unset state and verify the corresponding global JSON path. |
| Hide a partial reset and continue launch | **Rejected** | Return partial counts plus error and abort child launch. |
| Hard-code a minimum Claude version | **Rejected** | Probe flags on the actual binary used. |
| Claim Desktop/web/account erasure | **Rejected** | UI/docs explicitly say local Claude Code CLI only. |
| Refactor mature Codex reset for reuse | **Rejected for this change** | Reuse lifecycle primitives only; retain Codex implementation/tests. |
| Delete backups/statistics because they are in config root | **Rejected** | They are outside conversation/session/memory scope and are preserved. |

### Residual risks accepted after review

1. The operation cannot be atomic across an external CLI and filesystem
   deletions. Prevalidation minimizes partial mutation; later failure remains
   visible and prevents launch.
2. Future Claude versions can add generated locations. The strict allowlist is
   intentionally conservative and must track official application-data docs.
3. A custom memory path set only in project/local settings is not discovered
   product-wide because safe exhaustive repository discovery would expand the
   deletion scope.
4. Account, Desktop, VS Code, web, and remote histories are independent stores
   outside this launcher feature.

No unresolved contradiction remains inside the stated local Claude Code CLI
scope. These four residual risks must stay visible in implementation docs.
