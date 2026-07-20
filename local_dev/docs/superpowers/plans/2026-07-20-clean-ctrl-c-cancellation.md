# Clean Ctrl+C Cancellation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert Ctrl+C during launcher preflight into one clean `! cancelled` row and exit code `130` without a traceback.

**Architecture:** The raw selector erases only the prompt block it owns and re-raises `KeyboardInterrupt`; its existing `finally` block continues to restore terminal attributes. The public `main()` function is the single CLI cancellation boundary that clears the current line, renders the cancellation row, flushes stdout, and returns `130`.

**Tech Stack:** Python 3.12+, stdlib-only runtime, pytest, POSIX `termios`/`tty`, ANSI terminal control, zsh runtime shim.

## Global Constraints

- Limit implementation, tests, and documentation to `local_dev/`.
- Catch only `KeyboardInterrupt`; preserve normal exceptions, `SystemExit`, and child exit codes.
- Ctrl+C must cancel the entire launcher, never act like a `False` answer to an optional prompt.
- Preserve the existing `termios` restoration owner and child signal-forwarding behavior.
- Render exactly one visible `  ! cancelled` row and return `130`.
- Do not change session cleanup, memory, Serena MCP lifecycle, Graphify behavior, or public `dotsync` behavior.
- Promote through `make -C local_dev install-shim` only.
- Preserve the user-owned `AGENTS.md` modification.

---

### Task 1: Raw Selector Cleanup on Ctrl+C

**Files:**
- Modify: `local_dev/tests/test_ui_prompts.py`
- Modify: `local_dev/serena_mcp_management/ui.py`

**Interfaces:**
- Consumes: `_read_yes_no_arrow(question: str, *, default: bool, stream: TextIO, fd: int) -> bool` and its existing `termios` `finally` block.
- Produces: the same function contract for ordinary input; on `KeyboardInterrupt`, the function erases its three rendered lines, restores terminal attributes, and re-raises.

- [ ] **Step 1: Write the failing raw-selector test**

Add `pytest` and the `ui` module import, then add:

```python
def test_arrow_prompt_ctrl_c_erases_block_and_restores_terminal(monkeypatch):
    stream = io.StringIO()
    old_attrs = ["old-terminal-state"]
    restored: list[tuple[object, ...]] = []

    monkeypatch.setattr(ui.termios, "tcgetattr", lambda fd: old_attrs)
    monkeypatch.setattr(ui.tty, "setcbreak", lambda fd: None)

    def interrupt_read(fd, size):
        raise KeyboardInterrupt

    monkeypatch.setattr(ui.os, "read", interrupt_read)
    monkeypatch.setattr(
        ui.termios,
        "tcsetattr",
        lambda *args: restored.append(args),
    )

    with pytest.raises(KeyboardInterrupt):
        ui._read_yes_no_arrow(
            "Run codex?",
            default=True,
            stream=stream,
            fd=7,
        )

    assert stream.getvalue().endswith("\x1b[3A\x1b[J")
    assert restored == [(7, ui.termios.TCSADRAIN, old_attrs)]
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_ui_prompts.py::test_arrow_prompt_ctrl_c_erases_block_and_restores_terminal -v
```

Expected: FAIL because terminal restoration already occurs but the rendered
stream does not end with the prompt-block erase sequence.

- [ ] **Step 3: Implement prompt-owned cleanup**

Add an `except` between the input loop and the existing `finally`:

```python
    except KeyboardInterrupt:
        stream.write("\x1b[3A\x1b[J")
        stream.flush()
        raise
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
```

Do not render the final cancellation message here; this layer owns only its
three-line selector block.

- [ ] **Step 4: Run prompt tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests/test_ui_prompts.py -v
```

Expected: all prompt tests pass.

- [ ] **Step 5: Commit the raw-selector slice**

```bash
git add \
  local_dev/tests/test_ui_prompts.py \
  local_dev/serena_mcp_management/ui.py
git commit -m "fix(local_dev): clear interrupted prompt block"
```

### Task 2: CLI KeyboardInterrupt Boundary

**Files:**
- Modify: `local_dev/tests/test_launcher_phases.py`
- Modify: `local_dev/serena_mcp_management/serena_agent_launcher.py`

**Interfaces:**
- Consumes: `_main_v2(args: list[str]) -> int`, `sys.stdout`, and Task 1's re-raised `KeyboardInterrupt`.
- Produces: `main(argv: list[str] | None = None) -> int` returning `130` with one visible cancellation row for Ctrl+C.

- [ ] **Step 1: Write failing CLI-boundary tests**

Add:

```python
def test_main_turns_keyboard_interrupt_into_clean_cancel(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(launcher.sys, "stdout", out)

    def interrupt(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(launcher, "_main_v2", interrupt)

    try:
        rc = launcher.main([])
    except KeyboardInterrupt:
        rc = None

    visible = (
        _strip_ansi(out.getvalue())
        .replace("\r", "")
        .replace("\x1b[J", "")
    )
    assert rc == 130
    assert visible == "  ! cancelled\n"
    assert "Traceback" not in visible


def test_main_does_not_swallow_non_interrupt_exceptions(monkeypatch):
    def fail(args):
        raise RuntimeError("boom")

    monkeypatch.setattr(launcher, "_main_v2", fail)

    with pytest.raises(RuntimeError, match="boom"):
        launcher.main([])
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_launcher_phases.py::test_main_turns_keyboard_interrupt_into_clean_cancel \
  local_dev/tests/test_launcher_phases.py::test_main_does_not_swallow_non_interrupt_exceptions -v
```

Expected: the interrupt test fails with `rc is None` and empty output; the
normal-exception regression test passes.

- [ ] **Step 3: Implement the CLI boundary**

Wrap only `_main_v2(args)` in `main()`:

```python
def main(argv: list[str] | None = None) -> int:
    """Run the scoped Serena launcher."""

    args = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main_v2(args)
    except KeyboardInterrupt:
        sys.stdout.write("\r\x1b[J  ! cancelled\n")
        sys.stdout.flush()
        return 130
```

- [ ] **Step 4: Run launcher tests and verify GREEN**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_launcher_phases.py::test_main_turns_keyboard_interrupt_into_clean_cancel \
  local_dev/tests/test_launcher_phases.py::test_main_does_not_swallow_non_interrupt_exceptions -v
```

Expected: both tests pass.

- [ ] **Step 5: Run all prompt and launcher-phase tests**

Run:

```bash
.venv/bin/python3 -m pytest \
  local_dev/tests/test_ui_prompts.py \
  local_dev/tests/test_launcher_phases.py -q
```

Expected: zero failures.

- [ ] **Step 6: Commit the CLI-boundary slice**

```bash
git add \
  local_dev/tests/test_launcher_phases.py \
  local_dev/serena_mcp_management/serena_agent_launcher.py
git commit -m "fix(local_dev): exit cleanly on ctrl-c"
```

### Task 3: Documentation, Runtime Promotion, and Final Verification

**Files:**
- Modify: `local_dev/README.md`
- Create: `local_dev/docs/superpowers/plans/2026-07-20-clean-ctrl-c-cancellation.md`
- Generated/updated: `graphify-out/`
- Runtime mirror: `~/Desktop/dotsync_config/agent_launcher/` through the supported Make target.

**Interfaces:**
- Consumes: Tasks 1 and 2 cancellation contract.
- Produces: accurate internal documentation, installed runtime parity, refreshed graph, and final verification evidence.

- [ ] **Step 1: Update internal documentation**

Document in `local_dev/README.md` that Ctrl+C at any pre-launch prompt removes
the active prompt, prints `! cancelled`, and exits `130` without launching the
child or showing a traceback. Do not edit the public root README or Makefile.

- [ ] **Step 2: Run source and complete test verification**

Run:

```bash
python3 -m py_compile \
  local_dev/serena_mcp_management/ui.py \
  local_dev/serena_mcp_management/serena_agent_launcher.py
.venv/bin/python3 -m pytest local_dev/tests -q
.venv/bin/python3 -m pytest -q
git diff --check
```

Expected: compilation exits `0`; both suites report zero failures; no
whitespace errors appear. Run the `local_dev` suite outside the filesystem
sandbox if its loopback proxy tests are denied socket bind permission.

- [ ] **Step 3: Refresh graphify**

Run:

```bash
graphify update .
```

Expected: AST update exits `0` and refreshes the graph for modified code.

- [ ] **Step 4: Promote the verified runtime**

Run:

```bash
make -C local_dev install-shim
```

Expected: the stable launcher mirror and managed zsh block are updated.

- [ ] **Step 5: Verify the installed cancellation boundary**

From `~/Desktop/dotsync_config/agent_launcher`, import the installed launcher,
replace `_main_v2` with a function that raises `KeyboardInterrupt`, capture
`sys.stdout`, and assert:

```python
assert launcher.main([]) == 130
assert strip_ansi(output).replace("\r", "") == "  ! cancelled\n"
assert "Traceback" not in output
```

Compare installed and development copies of `ui.py` and
`serena_agent_launcher.py` using `cmp`.

- [ ] **Step 6: Commit documentation**

```bash
git add \
  local_dev/README.md \
  local_dev/docs/superpowers/plans/2026-07-20-clean-ctrl-c-cancellation.md
git commit -m "docs(local_dev): document ctrl-c cancellation"
```

- [ ] **Step 7: Run final verification after the last commit**

Run:

```bash
.venv/bin/python3 -m pytest local_dev/tests -q
.venv/bin/python3 -m pytest -q
git status --short
```

Expected: both suites report zero failures; only the pre-existing user-owned
`AGENTS.md` modification remains.
