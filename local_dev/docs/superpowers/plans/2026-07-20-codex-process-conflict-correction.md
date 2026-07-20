# Codex Process Conflict Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an empty Codex memory deletion continue to launch and make non-empty deletion conflicts report only correctly parsed real client processes.

**Architecture:** `memory_management.py` will collect process identity and arguments from two independently parseable `/bin/ps` snapshots, join them by PID, and expose the full executable path on `ClientProcess`. `delete_all_memory()` will return a successful zero-count result before process inspection when a warning-free authoritative inventory has no stores, while retaining fail-closed behavior for every non-empty or unsafe deletion.

**Tech Stack:** Python 3.12+ stdlib, pytest, macOS BSD `/bin/ps`, existing launcher TUI.

## Global Constraints

- Runtime dependencies remain stdlib-only.
- Change only the private `local_dev/` launcher; do not modify public dotsync code, the root README, or the root Makefile.
- A failed or unsafe non-empty deletion must not clean sessions or launch an agent.
- A warning-free zero-store inventory is a successful no-op and continues through the existing launch path.
- ChatGPT GUI helpers are not Codex clients; an actual `codex` executable, including the ChatGPT app-server, remains a blocker when stores exist.
- Tests must not delete real user memory or launch Codex/Claude.
- Preserve the five-day session-only cleanup behavior.

---

### Task 1: Parse Process Identity Without Truncating macOS Paths

**Files:**
- Modify: `local_dev/serena_mcp_management/memory_management.py:40-120`
- Modify: `local_dev/tests/test_memory_management.py:1-820`

**Interfaces:**
- Consumes: injected `RunCommand = Callable[..., subprocess.CompletedProcess[str]]`.
- Produces: `ClientProcess(pid: int, ppid: int, executable: str, command: str)` and a two-snapshot `running_client_processes()`.

- [ ] **Step 1: Update the fake process boundary and add failing tests**

Make the test fake respond to these two exact commands:

```python
PS_IDENTITY_COMMAND = ["/bin/ps", "-axo", "pid=,ppid=,comm="]
PS_ARGUMENT_COMMAND = ["/bin/ps", "-axo", "pid=,args="]

def fake_ps(identity_output, *, args_output="", returncode=0):
    def run_command(command, **kwargs):
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "check": False,
        }
        if command == PS_IDENTITY_COMMAND:
            output = identity_output
        elif command == PS_ARGUMENT_COMMAND:
            output = args_output
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(
            command, returncode, stdout=output, stderr=""
        )
    return run_command
```

Add a regression whose identity rows contain full paths with spaces:

```python
def test_process_scan_preserves_spaced_comm_and_ignores_chatgpt_helpers():
    identities = (
        "2176 1 /Applications/ChatGPT.app/Contents/Frameworks/"
        "Codex Framework.framework/Helpers/browser_crashpad_handler\n"
        "2419 1 /Applications/ChatGPT.app/Contents/Frameworks/"
        "Codex Framework.framework/Helpers/Codex (Service).app/Contents/"
        "MacOS/Codex (Service)\n"
        "2502 1 /Applications/ChatGPT.app/Contents/Resources/codex\n"
        "2612 1 /Users/me/.codex/computer-use/"
        "Codex Computer Use.app/Contents/MacOS/SkyComputerUseService\n"
    )
    arguments = (
        "2176 browser_crashpad_handler --monitor-self\n"
        "2419 Codex (Service) --type=gpu-process\n"
        "2502 /Applications/ChatGPT.app/Contents/Resources/codex app-server\n"
        "2612 SkyComputerUseService\n"
    )
    result = running_client_processes(
        "codex",
        run_command=fake_ps(identities, args_output=arguments),
        current_pid=9999,
    )
    assert [(item.pid, Path(item.executable).name) for item in result] == [
        (2502, "codex")
    ]
```

Adapt ancestor, Claude Desktop, unrelated-command, native-client, and official Node-wrapper tests to the two snapshots.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_memory_management.py \
  -k 'process_scan or running_client_processes' -q
```

Expected: the new spaced-path test fails because the production scanner still requests one combined row and `ClientProcess` lacks `executable`.

- [ ] **Step 3: Implement the two-snapshot parser**

Add:

```python
PS_IDENTITY_COMMAND = ["/bin/ps", "-axo", "pid=,ppid=,comm="]
PS_ARGUMENT_COMMAND = ["/bin/ps", "-axo", "pid=,args="]

@dataclass(frozen=True)
class ClientProcess:
    pid: int
    ppid: int
    executable: str
    command: str

def _run_ps(command: list[str], run_command: RunCommand) -> str:
    result = run_command(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or f"exit {result.returncode}"
        raise RuntimeError(f"cannot inspect running processes: {detail}")
    return result.stdout
```

Parse identity rows with `split(maxsplit=2)`, argument rows with
`split(maxsplit=1)`, join by PID, and then apply the existing ancestor and
`_matches_client_process` filters with the complete executable and command.

- [ ] **Step 4: Verify GREEN and commit**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_memory_management.py \
  -k 'process_scan or running_client_processes or running_same_product or ignores_other_product' -q
```

Expected: all selected tests pass.

Commit:

```bash
git add local_dev/serena_mcp_management/memory_management.py \
  local_dev/tests/test_memory_management.py
git commit -m "fix(local_dev): parse agent process paths safely"
```

---

### Task 2: Treat Empty Memory Deletion as a Successful No-op

**Files:**
- Modify: `local_dev/serena_mcp_management/memory_management.py:122-175`
- Modify: `local_dev/tests/test_memory_management.py`
- Modify: `local_dev/tests/test_launcher_phases.py:1500-1685`

**Interfaces:**
- Consumes: Task 1 `ClientProcess.executable` and `running_client_processes()`.
- Produces: zero-store success without `/bin/ps`; bounded process-conflict details.

- [ ] **Step 1: Add failing zero-store and conflict-detail tests**

```python
def test_delete_empty_inventory_succeeds_without_process_scan(tmp_path):
    def forbidden_ps(*args, **kwargs):
        pytest.fail("empty memory inventory must not inspect processes")
    result = delete_all_memory(
        client="codex",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        run_command=forbidden_ps,
    )
    assert result.succeeded
    assert result.deleted_stores == 0
    assert result.deleted_files == 0
```

Strengthen the real-conflict assertion with `PID 40` and `codex`. Extend the
launcher test helper to inject a successful result with zero counts, then
assert the call log reaches `session-cleanup` and `launch` and output contains
`0 stores · 0 files deleted`.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_memory_management.py::test_delete_empty_inventory_succeeds_without_process_scan \
  local_dev/tests/test_memory_management.py::test_delete_all_memory_refuses_running_same_product \
  local_dev/tests/test_launcher_phases.py -k 'zero_store' -q
```

Expected: the empty inventory calls the forbidden process boundary and the old conflict error lacks PID/name details.

- [ ] **Step 3: Implement no-op success and bounded conflict formatting**

Immediately after rejecting inventory warnings:

```python
if not inventory.stores:
    return MemoryDeleteResult()
```

Add:

```python
def _process_conflict_detail(conflicts: tuple[ClientProcess, ...]) -> str:
    shown = ", ".join(
        f"PID {item.pid} {Path(item.executable).name}"
        for item in conflicts[:3]
    )
    remaining = len(conflicts) - 3
    suffix = f", +{remaining} more" if remaining > 0 else ""
    return shown + suffix
```

Use it in the existing failure result:

```python
return MemoryDeleteResult(
    error=(
        f"{len(conflicts)} running {client.title()} process(es): "
        f"{_process_conflict_detail(conflicts)}"
    )
)
```

- [ ] **Step 4: Verify GREEN and commit**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_memory_management.py \
  local_dev/tests/test_launcher_phases.py \
  local_dev/tests/test_session_cleanup.py \
  local_dev/tests/test_session_inventory.py -q
```

Expected: all tests pass.

Commit:

```bash
git add local_dev/serena_mcp_management/memory_management.py \
  local_dev/tests/test_memory_management.py \
  local_dev/tests/test_launcher_phases.py
git commit -m "fix(local_dev): run after empty memory cleanup"
```

---

### Task 3: Document, Verify, and Promote

**Files:**
- Modify: `local_dev/README.md:104-150`
- Runtime mirror: `~/Desktop/dotsync_config/agent_launcher/`
- Managed shell block: `~/.zshrc` through `make -C local_dev install-shim`

**Interfaces:**
- Consumes: Tasks 1-2 behavior.
- Produces: accurate private documentation and byte-identical stable runtime.

- [ ] **Step 1: Update private documentation**

Document this exact behavior:

```text
A warning-free inventory with zero stores is a successful no-op and continues
to session cleanup and launch. For a non-empty inventory, only real native or
official Node client processes block deletion; conflict output identifies
representative PID/executable pairs. GUI helper processes are not clients.
```

- [ ] **Step 2: Run complete verification**

```bash
git diff --check
.venv/bin/python3 -m compileall -q local_dev/serena_mcp_management
.venv/bin/python3 -m pytest local_dev/tests -q
.venv/bin/python3 -m pytest tests -q
```

Expected: all commands exit 0. The private proxy tests may require scoped
loopback permission.

- [ ] **Step 3: Refresh graph and commit docs**

```bash
graphify update .
git add local_dev/README.md
git commit -m "docs(local_dev): clarify memory process conflicts"
```

- [ ] **Step 4: Promote and smoke-test without mutation**

With scoped approval run:

```bash
make -C local_dev install-shim
zsh -n ~/.zshrc
cmp local_dev/serena_mcp_management/memory_management.py \
  ~/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/memory_management.py
cmp local_dev/serena_mcp_management/serena_agent_launcher.py \
  ~/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/serena_agent_launcher.py
```

Also import the stable runtime with injected fake process snapshots to verify
GUI helpers are excluded and an empty inventory succeeds without invoking the
process boundary. Do not launch a real agent or delete real memory.

- [ ] **Step 5: Review final status**

```bash
git status --short
git log --oneline -5
```

Expected: only the pre-existing `AGENTS.md` modification and untracked
`.superpowers/` records remain. Do not push or create a PR.
