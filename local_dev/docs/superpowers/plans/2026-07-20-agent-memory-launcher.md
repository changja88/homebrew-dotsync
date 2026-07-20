# Agent Memory Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe three-way memory decision to every interactive Codex and Claude launcher run, with complete product-wide auto-memory discovery and explicit delete-all behavior before the existing five-day session cleanup.

**Architecture:** Put effective product-home derivation in one shared path module so session and memory scans cannot drift. Put memory discovery, validation, process conflict detection, and deletion in a standalone stdlib-only module; keep terminal selection/rendering in `ui.py`; let `serena_agent_launcher.py` orchestrate the approved order without moving existing Serena or session-retention responsibilities.

**Tech Stack:** macOS, Python 3.12+ standard library, pytest, ANSI terminal rendering, existing zsh shim and `make -C local_dev install-shim`

## Global Constraints

- The five-day rule remains a session-retention rule only; memory is never deleted by age.
- Memory is deleted only after the explicit `Delete all memory and run` choice.
- Codex scope is only `memories/` under the default, active, and Orca runtime Codex homes; exclude `memories_extensions/`, Chronicle, sessions, skills, config, auth, logs, and SQLite state.
- Claude scope is every `<config>/projects/<project>/memory/` plus the exact valid user-level `autoMemoryDirectory`; exclude `agent-memory/`, instruction files, transcripts, tasks, debug data, file history, and prompt history.
- Reject symlinks, wrong file types, broad roots, malformed settings, unreadable stores, and non-empty custom Claude stores without `MEMORY.md` before deletion.
- Refuse deletion and launch when another same-product process is running or a filesystem deletion is partial.
- Cancel and Ctrl+C perform no memory deletion, no session cleanup, and no child launch; return status 130 without a traceback.
- Preserve non-interactive bypass behavior and keep runtime dependencies stdlib-only.
- Keep all changes under `local_dev/`; do not modify public dotsync code, the root README, or the root Makefile.

---

## File Structure

- Create `local_dev/serena_mcp_management/agent_paths.py` — shared effective Codex/Claude home derivation.
- Create `local_dev/serena_mcp_management/memory_management.py` — inventory, safety validation, process checking, and delete-all operation.
- Modify `local_dev/serena_mcp_management/session_inventory.py` — consume the shared Codex-home helper without changing session behavior.
- Modify `local_dev/serena_mcp_management/ui.py` — memory-tree styling and generic three-option selector.
- Modify `local_dev/serena_mcp_management/serena_agent_launcher.py` — preflight memory snapshot, decision, deletion result, and launch ordering.
- Create `local_dev/tests/test_agent_paths.py` — effective-home contract tests.
- Create `local_dev/tests/test_memory_management.py` — discovery and destructive-safety tests using temporary directories.
- Modify `local_dev/tests/test_ui_prompts.py` — three-option line and raw-TTY prompt tests.
- Modify `local_dev/tests/test_ui_style.py` — memory tree color and wording tests.
- Modify `local_dev/tests/test_launcher_phases.py` — preflight row, decision outcomes, and end-to-end ordering tests.
- Modify `local_dev/README.md` — document the interactive memory choice and exact product scopes.

### Task 1: Shared Product Homes and Memory Inventory

**Files:**
- Create: `local_dev/serena_mcp_management/agent_paths.py`
- Create: `local_dev/serena_mcp_management/memory_management.py`
- Modify: `local_dev/serena_mcp_management/session_inventory.py`
- Create: `local_dev/tests/test_agent_paths.py`
- Create: `local_dev/tests/test_memory_management.py`
- Modify: `local_dev/tests/test_session_inventory.py`

**Interfaces:**
- Produces `canonical_codex_homes(*, home: Path, codex_home: Path, orca_codex_home: Path | None = None) -> tuple[tuple[Path, ...], Path, Path]`.
- Produces `effective_claude_config_dir(*, home: Path, claude_config_dir: Path | None = None) -> Path`.
- Produces immutable `MemoryStore` and `MemoryInventory` models.
- Produces `scan_memory_inventory(*, client: str, home: Path, codex_home: Path, claude_config_dir: Path | None = None, orca_codex_home: Path | None = None) -> MemoryInventory`.
- Preserves the existing `scan_inventory(...) -> AgentInventory` contract.

- [ ] **Step 1: Write failing shared-path tests**

```python
def test_canonical_codex_homes_deduplicates_default_active_and_orca(tmp_path):
    home = tmp_path / "home"
    default = home / ".codex"
    orca = home / "Library/Application Support/orca/codex-runtime-home/home"

    homes, default_home, orca_home = canonical_codex_homes(
        home=home,
        codex_home=default,
        orca_codex_home=orca,
    )

    assert homes == (default.resolve(), orca.resolve())
    assert default_home == default.resolve()
    assert orca_home == orca.resolve()


def test_effective_claude_config_dir_requires_absolute_path(tmp_path):
    with pytest.raises(ValueError, match="claude_config_dir must be absolute"):
        effective_claude_config_dir(home=tmp_path, claude_config_dir=Path("relative"))
```

- [ ] **Step 2: Run the shared-path tests and verify RED**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_agent_paths.py -q`

Expected: collection fails because `agent_paths` does not exist.

- [ ] **Step 3: Add the shared path module and switch session inventory to it**

```python
ORCA_CODEX_HOME = Path("Library/Application Support/orca/codex-runtime-home/home")


def canonical_codex_homes(
    *, home: Path, codex_home: Path, orca_codex_home: Path | None = None
) -> tuple[tuple[Path, ...], Path, Path]:
    active_home = codex_home.expanduser()
    if not active_home.is_absolute():
        raise ValueError("codex_home must be absolute")
    default_home = (home / ".codex").resolve(strict=False)
    active_home = active_home.resolve(strict=False)
    orca_home = (orca_codex_home or home / ORCA_CODEX_HOME).expanduser()
    if not orca_home.is_absolute():
        raise ValueError("orca_codex_home must be absolute")
    orca_home = orca_home.resolve(strict=False)
    homes = tuple(dict.fromkeys((default_home, active_home, orca_home)))
    return homes, default_home, orca_home


def effective_claude_config_dir(
    *, home: Path, claude_config_dir: Path | None = None
) -> Path:
    candidate = (claude_config_dir or home / ".claude").expanduser()
    if not candidate.is_absolute():
        raise ValueError("claude_config_dir must be absolute")
    return candidate.resolve(strict=False)
```

Replace the private `_canonical_codex_homes` implementation in
`session_inventory.py` with an import and calls to `canonical_codex_homes`.

- [ ] **Step 4: Run shared-path and session-inventory tests and verify GREEN**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_agent_paths.py local_dev/tests/test_session_inventory.py -q`

Expected: all tests pass with no warnings.

- [ ] **Step 5: Write failing memory inventory tests**

```python
def test_codex_inventory_scans_only_memories_under_all_known_homes(tmp_path):
    home = tmp_path / "home"
    active = tmp_path / "active-codex"
    orca = tmp_path / "orca-codex"
    for root, text in ((home / ".codex", "default"), (active, "active"), (orca, "orca")):
        (root / "memories").mkdir(parents=True)
        (root / "memories/MEMORY.md").write_text(text)
        (root / "memories_extensions/chronicle").mkdir(parents=True)
        (root / "memories_extensions/chronicle/keep.md").write_text("keep")

    inventory = scan_memory_inventory(
        client="codex", home=home, codex_home=active, orca_codex_home=orca
    )

    assert {store.path for store in inventory.stores} == {
        home / ".codex/memories",
        active / "memories",
        orca / "memories",
    }
    assert inventory.file_count == 3
    assert all("memories_extensions" not in str(store.path) for store in inventory.stores)


def test_claude_inventory_finds_all_project_memory_and_custom_store(tmp_path):
    config = tmp_path / ".claude"
    first = config / "projects/repo-a/memory"
    second = config / "projects/repo-b/memory"
    custom = tmp_path / "custom-memory"
    for store in (first, second, custom):
        store.mkdir(parents=True)
        (store / "MEMORY.md").write_text("memory")
    (config / "agent-memory/reviewer").mkdir(parents=True)
    (config / "settings.json").write_text(
        json.dumps({"autoMemoryEnabled": True, "autoMemoryDirectory": str(custom)})
    )

    inventory = scan_memory_inventory(
        client="claude",
        home=tmp_path,
        codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )

    assert {store.path for store in inventory.stores} == {first, second, custom}
    assert inventory.file_count == 3
```

Add explicit safety cases in the same file:

```python
@pytest.mark.parametrize(
    ("settings", "warning"),
    [
        ("{not-json", "invalid Claude settings"),
        (json.dumps({"autoMemoryDirectory": "relative"}), "must be absolute"),
        (json.dumps({"autoMemoryDirectory": "/"}), "unsafe broad path"),
    ],
)
def test_claude_inventory_reports_unsafe_settings(tmp_path, settings, warning):
    config = tmp_path / ".claude"
    config.mkdir()
    (config / "settings.json").write_text(settings)
    inventory = scan_memory_inventory(
        client="claude", home=tmp_path, codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )
    assert any(warning in item for item in inventory.warnings)


def test_inventory_rejects_symlink_store(tmp_path):
    config = tmp_path / ".claude"
    project = config / "projects/repo"
    project.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "memory").symlink_to(outside, target_is_directory=True)
    inventory = scan_memory_inventory(
        client="claude", home=tmp_path, codex_home=tmp_path / ".codex",
        claude_config_dir=config,
    )
    assert inventory.stores == ()
    assert any("symlink" in item for item in inventory.warnings)
```

- [ ] **Step 6: Run memory inventory tests and verify RED**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_memory_management.py -q`

Expected: collection fails because `memory_management` and its models do not exist.

- [ ] **Step 7: Implement immutable inventory models and discovery**

```python
@dataclass(frozen=True)
class MemoryStore:
    path: Path
    source: str
    file_count: int


@dataclass(frozen=True)
class MemoryInventory:
    client: str
    stores: tuple[MemoryStore, ...]
    file_count: int
    scope: str
    warnings: tuple[str, ...] = ()


def scan_memory_inventory(
    *,
    client: str,
    home: Path,
    codex_home: Path,
    claude_config_dir: Path | None = None,
    orca_codex_home: Path | None = None,
) -> MemoryInventory:
    if client == "codex":
        return _scan_codex_memory(
            home=home,
            codex_home=codex_home,
            orca_codex_home=orca_codex_home,
        )
    if client == "claude":
        return _scan_claude_memory(
            home=home,
            claude_config_dir=claude_config_dir,
        )
    raise ValueError(f"unsupported client: {client}")
```

Use `lstat`/`os.scandir` and `os.walk(..., followlinks=False)` so discovery
never follows symlinks. Treat missing directories as zero stores; collect
safety problems in `warnings` so keeping memory remains possible but deletion
can be refused later.

- [ ] **Step 8: Run inventory tests and verify GREEN**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_memory_management.py local_dev/tests/test_session_inventory.py -q`

Expected: all tests pass with no warnings.

- [ ] **Step 9: Commit Task 1**

```bash
git add local_dev/serena_mcp_management/agent_paths.py \
  local_dev/serena_mcp_management/memory_management.py \
  local_dev/serena_mcp_management/session_inventory.py \
  local_dev/tests/test_agent_paths.py \
  local_dev/tests/test_memory_management.py \
  local_dev/tests/test_session_inventory.py
git commit -m "feat(local_dev): inventory agent auto-memory"
```

### Task 2: Safe Delete-All and Process Conflict Detection

**Files:**
- Modify: `local_dev/serena_mcp_management/memory_management.py`
- Modify: `local_dev/tests/test_memory_management.py`

**Interfaces:**
- Produces immutable `ClientProcess` and `MemoryDeleteResult` models.
- Produces `running_client_processes(client: str, *, run_command: RunCommand = subprocess.run, current_pid: int | None = None) -> tuple[ClientProcess, ...]`.
- Produces `delete_all_memory(..., run_command: RunCommand = subprocess.run, remove_tree: RemoveTree = shutil.rmtree) -> MemoryDeleteResult`.
- `MemoryDeleteResult.succeeded` is true only when no error occurred.

- [ ] **Step 1: Write failing process and deletion tests**

```python
def test_running_client_processes_excludes_launcher_ancestors():
    ps = """10 1 zsh zsh\n20 10 python3 python3 serena_agent_launcher.py\n30 1 codex codex\n"""
    result = running_client_processes(
        "codex", run_command=fake_ps(ps), current_pid=20
    )
    assert [process.pid for process in result] == [30]


def test_delete_all_memory_removes_only_validated_stores(tmp_path):
    home, active, orca = build_codex_memory_fixture(tmp_path)
    sibling = active / "sessions/keep.jsonl"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("keep")

    result = delete_all_memory(
        client="codex",
        home=home,
        codex_home=active,
        orca_codex_home=orca,
        run_command=fake_ps(""),
    )

    assert result.succeeded
    assert result.deleted_stores == 3
    assert result.deleted_files == 3
    assert sibling.read_text() == "keep"


def test_delete_all_memory_refuses_running_same_product(tmp_path):
    home, active, orca = build_codex_memory_fixture(tmp_path)
    result = delete_all_memory(
        client="codex",
        home=home,
        codex_home=active,
        orca_codex_home=orca,
        run_command=fake_ps("40 1 codex codex\n"),
    )
    assert not result.succeeded
    assert "1 running Codex process" in result.error
    assert (active / "memories/MEMORY.md").exists()
```

Add exact conflict and prevalidation cases:

```python
def test_process_scan_ignores_claude_desktop_but_finds_claude_code():
    ps = (
        "50 1 /Applications/Claude.app/Contents/MacOS/Claude "
        "/Applications/Claude.app/Contents/MacOS/Claude\n"
        "60 1 claude /Users/me/.local/bin/claude\n"
    )
    result = running_client_processes(
        "claude", run_command=fake_ps(ps), current_pid=20
    )
    assert [process.pid for process in result] == [60]


def test_delete_prevalidates_every_store_before_mutation(tmp_path):
    home, active, orca = build_codex_memory_fixture(tmp_path)
    (orca / "memories").rename(orca / "memories-real")
    (orca / "memories").symlink_to(orca / "memories-real", target_is_directory=True)
    calls = []
    result = delete_all_memory(
        client="codex", home=home, codex_home=active, orca_codex_home=orca,
        run_command=fake_ps(""), remove_tree=lambda path: calls.append(path),
    )
    assert not result.succeeded
    assert calls == []


def test_delete_reports_partial_counts_and_stops(tmp_path):
    home, active, orca = build_codex_memory_fixture(tmp_path)
    calls = []

    def fail_second(path):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk busy")
        shutil.rmtree(path)

    result = delete_all_memory(
        client="codex", home=home, codex_home=active, orca_codex_home=orca,
        run_command=fake_ps(""), remove_tree=fail_second,
    )
    assert not result.succeeded
    assert result.deleted_stores == 1
    assert "disk busy" in result.error
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_memory_management.py -q`

Expected: failures report missing `running_client_processes`,
`delete_all_memory`, and result models.

- [ ] **Step 3: Implement process parsing and deletion result**

```python
@dataclass(frozen=True)
class ClientProcess:
    pid: int
    ppid: int
    command: str


@dataclass(frozen=True)
class MemoryDeleteResult:
    deleted_stores: int = 0
    deleted_files: int = 0
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None
```

Run `/bin/ps -axo pid=,ppid=,comm=,args=` through the injectable runner,
remove the current PID and ancestor chain, and match only real Codex or Claude
Code commands. Exclude the macOS Claude Desktop application executable because
Claude Desktop does not own Claude Code's project auto-memory.

- [ ] **Step 4: Implement rescan, prevalidation, and delete-all**

```python
def delete_all_memory(
    *,
    client: str,
    home: Path,
    codex_home: Path,
    claude_config_dir: Path | None = None,
    orca_codex_home: Path | None = None,
    run_command: RunCommand = subprocess.run,
    remove_tree: RemoveTree = shutil.rmtree,
) -> MemoryDeleteResult:
    inventory = scan_memory_inventory(
        client=client,
        home=home,
        codex_home=codex_home,
        claude_config_dir=claude_config_dir,
        orca_codex_home=orca_codex_home,
    )
    if inventory.warnings:
        return MemoryDeleteResult(error="memory scan unsafe: " + "; ".join(inventory.warnings))
    conflicts = running_client_processes(
        client, run_command=run_command, current_pid=os.getpid()
    )
    if conflicts:
        return MemoryDeleteResult(
            error=f"{len(conflicts)} running {client.title()} process(es)"
        )
    for store in inventory.stores:
        _validate_store_again(
            store=store,
            client=client,
            home=home,
            claude_config_dir=claude_config_dir,
        )
    deleted_stores = deleted_files = 0
    for store in inventory.stores:
        try:
            remove_tree(store.path)
        except OSError as exc:
            return MemoryDeleteResult(
                deleted_stores=deleted_stores,
                deleted_files=deleted_files,
                error=f"{store.path}: {exc}",
            )
        deleted_stores += 1
        deleted_files += store.file_count
    return MemoryDeleteResult(deleted_stores, deleted_files)
```

The final validation must use `lstat` again and must run for every store before
the first `remove_tree` call.

- [ ] **Step 5: Run destructive-safety tests and verify GREEN**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_memory_management.py -q`

Expected: all tests pass; all mutations are confined to pytest temporary directories.

- [ ] **Step 6: Commit Task 2**

```bash
git add local_dev/serena_mcp_management/memory_management.py \
  local_dev/tests/test_memory_management.py
git commit -m "feat(local_dev): safely delete agent auto-memory"
```

### Task 3: Three-Option Prompt and Memory Tree Styling

**Files:**
- Modify: `local_dev/serena_mcp_management/ui.py`
- Modify: `local_dev/tests/test_ui_prompts.py`
- Modify: `local_dev/tests/test_ui_style.py`

**Interfaces:**
- Produces immutable `SelectOption(value: str, label: str)`.
- Produces `select_option(question: str, *, options: tuple[SelectOption, ...], default_index: int = 0, stream: TextIO | None = None, input_fn: Callable[[], str] | None = None) -> str`.
- Produces `style_memory_tree(*, client: str, stores: int, files: int, scope: str) -> str`.
- Keeps `confirm(...) -> bool` backward compatible.

- [ ] **Step 1: Write failing line-mode and styling tests**

```python
def test_select_option_line_mode_accepts_number_and_defaults_to_first():
    options = (
        SelectOption("keep", "Run with existing memory"),
        SelectOption("delete", "Delete all Codex auto-memory and run"),
        SelectOption("cancel", "Cancel"),
    )
    assert select_option("Memory for codex?", options=options, input_fn=lambda: "2") == "delete"
    assert select_option("Memory for codex?", options=options, input_fn=lambda: "") == "keep"


def test_style_memory_tree_assigns_distinct_color_roles():
    value = style_memory_tree(
        client="codex", stores=2, files=17, scope="all known Codex homes"
    )
    plain = strip_ansi(value)
    assert plain.splitlines() == [
        "codex",
        "├─ stores   2 found",
        "├─ files    17",
        "└─ scope    all known Codex homes",
    ]
    assert PINK in value
    assert MINT in value
    assert PURPLE in value
```

- [ ] **Step 2: Write the failing raw-TTY Ctrl+C test**

Use the existing pseudo-terminal helper with this assertion:

```python
def test_select_option_ctrl_c_erases_four_line_block(monkeypatch):
    options = (
        SelectOption("keep", "Run with existing memory"),
        SelectOption("delete", "Delete all Codex auto-memory and run"),
        SelectOption("cancel", "Cancel"),
    )
    stream = io.StringIO()
    old_attrs = ["old-terminal-state"]
    restored = []
    monkeypatch.setattr(ui.termios, "tcgetattr", lambda fd: old_attrs)
    monkeypatch.setattr(ui.tty, "setcbreak", lambda fd: None)
    monkeypatch.setattr(
        ui.os, "read", lambda fd, size: (_ for _ in ()).throw(KeyboardInterrupt)
    )
    monkeypatch.setattr(
        ui.termios, "tcsetattr", lambda *args: restored.append(args)
    )

    with pytest.raises(KeyboardInterrupt):
        ui._read_select_arrow(
            "Memory for codex?", options=options, cursor=0, stream=stream, fd=7
        )

    assert stream.getvalue().endswith("\x1b[4A\x1b[J")
    assert restored == [(7, ui.termios.TCSADRAIN, old_attrs)]
```

- [ ] **Step 3: Run prompt/style tests and verify RED**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_ui_prompts.py local_dev/tests/test_ui_style.py -q`

Expected: failures report missing `SelectOption`, `select_option`, and
`style_memory_tree`.

- [ ] **Step 4: Implement generic selection and memory styling**

```python
@dataclass(frozen=True)
class SelectOption:
    value: str
    label: str


def select_option(
    question: str,
    *,
    options: tuple[SelectOption, ...],
    default_index: int = 0,
    stream: TextIO | None = None,
    input_fn: Callable[[], str] | None = None,
) -> str:
    if not options:
        raise ValueError("options must not be empty")
    if not 0 <= default_index < len(options):
        raise ValueError("default_index out of range")
    out = stream if stream is not None else sys.stdout
    fd = _tty_fd() if input_fn is None else None
    if fd is not None:
        return _read_select_arrow(
            question,
            options=options,
            cursor=default_index,
            stream=out,
            fd=fd,
        )
    return _read_select_line(
        question,
        options=options,
        default_index=default_index,
        stream=out,
        input_fn=input_fn or input,
    )
```

Implement `_read_select_arrow` once and make `_read_yes_no_arrow` delegate to
it, preserving `y`/`n` shortcuts for the existing two-option confirmation.
Use `len(options) + 1` for redraw and Ctrl+C erasure so three-option and future
selectors restore the screen correctly.

- [ ] **Step 5: Run UI tests and verify GREEN**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_ui_prompts.py local_dev/tests/test_ui_style.py local_dev/tests/test_ui_renderer.py -q`

Expected: all tests pass and existing yes/no rendering remains unchanged.

- [ ] **Step 6: Commit Task 3**

```bash
git add local_dev/serena_mcp_management/ui.py \
  local_dev/tests/test_ui_prompts.py \
  local_dev/tests/test_ui_style.py
git commit -m "feat(local_dev): add memory launch selector"
```

### Task 4: Launcher Integration and Ordering

**Files:**
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py`
- Modify: `local_dev/tests/test_launcher_phases.py`

**Interfaces:**
- Extends `InventorySnapshot` with `memory_inventory: MemoryInventory | None` and `memory_error: str | None` while preserving `.inventory` and `.error` for session cleanup.
- Produces `_memory_inventory_for_preflight(client: str) -> MemoryInventory`.
- Produces `_run_memory_choice_v2(...) -> Literal["keep", "delete", "cancel"]`.
- Removes `_run_final_confirm_v2`; the memory choice becomes the final gate.
- `_main_v2` maps cancel to 130, unsafe/partial deletion to 1, and successful keep/delete choices to the existing session cleanup and launch paths.

- [ ] **Step 1: Write failing preflight memory-row tests**

```python
def test_v2_preflight_groups_memory_inventory_in_one_row(monkeypatch):
    stub_memory_inventory(monkeypatch, client="codex", stores=2, files=17)
    out = io.StringIO()
    launcher._render_preflight_overview_v2(stream=out)
    plain = _strip_ansi(out.getvalue())
    assert "· memory      codex" in plain
    assert "├─ stores   2 found" in plain
    assert "├─ files    17" in plain
    assert "└─ scope    all known Codex homes" in plain
```

Add a scan-failure case asserting a warning row while the session row still
renders.

- [ ] **Step 2: Write failing memory choice tests**

```python
def test_memory_choice_offers_keep_delete_cancel(monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    out = io.StringIO()
    choice = launcher._run_memory_choice_v2(
        stream=out, input_fn=lambda: "2"
    )
    assert choice == "delete"
    assert "Run with existing memory" in out.getvalue()
    assert "Delete all Codex auto-memory and run" in out.getvalue()
    assert "Cancel" in out.getvalue()
```

- [ ] **Step 3: Write failing launch-order and stop-condition tests**

Cover these complete call logs:

```python
assert keep_log == [
    "overview", "serena-init", "setup", "memory-keep", "session-cleanup", "launch"
]
assert delete_log == [
    "overview", "serena-init", "setup", "memory-delete", "session-cleanup", "launch"
]
assert cancel_log == ["overview", "serena-init", "setup", "memory-cancel"]
assert delete_failure_log == [
    "overview", "serena-init", "setup", "memory-delete-failed"
]
```

Assert cancel returns 130, deletion failure returns 1, and neither path calls
`_run_launch_prep_v2`, Serena server startup, bare launch, or child launch.

- [ ] **Step 4: Run launcher tests and verify RED**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py -q`

Expected: failures show the old final confirmation and missing memory row/
decision functions.

- [ ] **Step 5: Capture memory and session inventory independently**

```python
@dataclass(frozen=True)
class InventorySnapshot:
    inventory: AgentInventory | None
    error: str | None = None
    memory_inventory: MemoryInventory | None = None
    memory_error: str | None = None
```

Refactor `_capture_inventory_snapshot` so a session scan failure does not skip
the memory scan and a memory scan failure does not discard the usable session
snapshot. `_preflight_box` must add exactly one `memory` item immediately before
the existing `sessions` item.

- [ ] **Step 6: Replace the final confirmation with the memory decision**

```python
choice = _run_memory_choice_v2()
if choice == "cancel":
    return 130
if choice == "delete":
    result = delete_all_memory(**_memory_scan_kwargs(client_type))
    if not result.succeeded:
        out.write(f"  ! memory      delete failed · {result.error}\n")
        out.flush()
        return 1
    out.write(
        f"  ✓ memory      {result.deleted_stores} stores · "
        f"{result.deleted_files} files deleted\n"
    )
    out.flush()
```

This block must run before `find_real_binary`, `_run_launch_prep_v2`, bare
launch fallback, and Serena MCP startup. Keeping memory performs no memory
rescan or mutation. Non-interactive mode returns `"keep"` without prompting.

- [ ] **Step 7: Run launcher and cleanup regression tests and verify GREEN**

Run: `.venv/bin/python3 -m pytest local_dev/tests/test_launcher_phases.py local_dev/tests/test_session_cleanup.py local_dev/tests/test_session_inventory.py -q`

Expected: all tests pass; session retention behavior and Claude native cleanup
arguments remain unchanged.

- [ ] **Step 8: Commit Task 4**

```bash
git add local_dev/serena_mcp_management/serena_agent_launcher.py \
  local_dev/tests/test_launcher_phases.py
git commit -m "feat(local_dev): choose memory policy before launch"
```

### Task 5: Documentation, Full Verification, and Runtime Promotion

**Files:**
- Modify: `local_dev/README.md`
- Runtime mirror: `~/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/`
- Managed shell block: `~/.zshrc` through `make -C local_dev install-shim`

**Interfaces:**
- Documents the exact three choices, memory scopes, five-day session-only rule,
  conflict behavior, and exit statuses.
- Promotes the verified dev tree with the repository's only supported runtime
  installation command.

- [ ] **Step 1: Update the private launcher README**

Add an `Agent memory` subsection showing:

```text
Run with existing memory
Delete all <product> auto-memory and run
Cancel
```

State explicitly that Codex includes all known Codex homes, Claude includes all
project auto-memory plus a configured custom directory, five days applies only
to sessions, and process/safety failures stop the launch.

- [ ] **Step 2: Run formatting and targeted diagnostics**

Run:

```bash
git diff --check
.venv/bin/python3 -m compileall -q local_dev/serena_mcp_management
.venv/bin/python3 -m pytest \
  local_dev/tests/test_agent_paths.py \
  local_dev/tests/test_memory_management.py \
  local_dev/tests/test_ui_prompts.py \
  local_dev/tests/test_ui_style.py \
  local_dev/tests/test_launcher_phases.py -q
```

Expected: all commands exit 0 with no warnings or failures.

- [ ] **Step 3: Run the complete private launcher suite**

Run: `.venv/bin/python3 -m pytest local_dev/tests -q`

Expected: exit 0 and zero failed tests.

- [ ] **Step 4: Verify public dotsync isolation**

Run: `.venv/bin/python3 -m pytest tests -q`

Expected: exit 0 and zero failed tests. Confirm `git diff --name-only` contains
only `local_dev/` paths, excluding pre-existing user changes.

- [ ] **Step 5: Refresh the graph**

Run: `graphify update .`

Expected: exit 0. Review graph health output and report any integrity warning.

- [ ] **Step 6: Promote through the supported shim installer**

Run: `make -C local_dev install-shim`

Expected: exit 0; the dev launcher tree is mirrored to the stable runtime path
and the managed zsh block still points there. Do not invoke either destructive
memory choice during promotion.

- [ ] **Step 7: Smoke-test the stable runtime without deleting live memory**

Run a Python import from the stable runtime and call the line-mode selector with
choice `1`; assert it returns `keep`. Then run `zsh -n ~/.zshrc` and compare the
stable `memory_management.py`, `ui.py`, and `serena_agent_launcher.py` with the
dev copies using `cmp`.

Expected: selector prints the three choices and returns `keep`; zsh syntax and
all three byte comparisons exit 0. Do not launch Codex or Claude from inside
the verification process.

- [ ] **Step 8: Commit documentation and any verification-only adjustments**

```bash
git add local_dev/README.md
git commit -m "docs(local_dev): document launcher memory control"
```

- [ ] **Step 9: Fresh final verification**

Run after the final commit:

```bash
git diff --check HEAD^..HEAD
.venv/bin/python3 -m pytest local_dev/tests -q
.venv/bin/python3 -m pytest tests -q
```

Expected: all commands exit 0 with zero failed tests. Review `git status --short`
and preserve the pre-existing `AGENTS.md` and `.superpowers/` changes.
