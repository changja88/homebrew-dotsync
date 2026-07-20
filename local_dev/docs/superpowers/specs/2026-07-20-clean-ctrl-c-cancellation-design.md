# Clean Ctrl+C Cancellation Design

## Goal

Make Ctrl+C cancel the launcher cleanly during every pre-launch prompt. The
launcher must restore terminal state, remove the active selector block, print
one concise cancellation row, return exit code `130`, and never emit a Python
traceback for the user-initiated interrupt.

## Root Cause

The raw yes/no selector converts Ctrl+C into `KeyboardInterrupt` and restores
the original `termios` attributes in a `finally` block. It does not remove the
three-line selector block when interrupted. The exception then reaches
`main()`, which currently delegates directly to `_main_v2()` without a
`KeyboardInterrupt` boundary, so Python prints the traceback shown by the
user.

The terminal mode is therefore already protected; the missing pieces are
visual prompt cleanup and CLI-level cancellation reconciliation.

## Selected Behavior

The user selected explicit cancellation output:

```text
  ! cancelled
```

The process returns `130`, the conventional shell status for an interrupt by
SIGINT. It does not launch Codex or Claude, continue to later setup questions,
or print a traceback.

## Exception Flow

Keep `KeyboardInterrupt` as the internal cancellation signal and handle it at
two responsibility boundaries:

1. `_read_yes_no_arrow()` catches `KeyboardInterrupt` only to erase its own
   three-line prompt block, flushes the output, and re-raises. Its existing
   `finally` block remains the sole owner of restoring `termios` attributes.
2. `main()` catches the re-raised `KeyboardInterrupt`, clears the current
   terminal line, writes the standard `! cancelled` row to `sys.stdout`,
   flushes it, and returns `130`.

The same `main()` boundary also covers Ctrl+C from line-input prompts and other
pre-launch phases. It catches only `KeyboardInterrupt`; normal exceptions,
`SystemExit`, and child-process exit codes retain their existing behavior.

Once the real Codex or Claude child is running, the launcher's existing signal
handlers continue to forward termination to that child. This design does not
change child lifecycle or post-child summary behavior.

## Why Not Return False From `confirm()`

`confirm()` is used for optional installation, project initialization, hook
setup, and the final Run/Abort gate. Treating Ctrl+C as `False` would mean
different things at different call sites; some optional prompts would skip an
action and continue launching. Re-raising to the CLI boundary preserves the
unambiguous meaning of Ctrl+C: cancel the entire launcher immediately.

## Output Details

The CLI boundary writes a carriage return plus erase-to-end escape sequence
before the cancellation row. This replaces a partially rendered line-input
prompt without adding an empty line. The raw selector first removes its full
three-line block, so the same final row is correct for both input modes.

The cancellation row uses the launcher's existing warning vocabulary. No
additional explanation or stack information is printed.

## Scope

Changes are limited to:

- `local_dev/serena_mcp_management/ui.py` for raw prompt cleanup;
- `local_dev/serena_mcp_management/serena_agent_launcher.py` for the CLI
  interrupt boundary;
- focused tests under `local_dev/tests/`;
- the existing internal `local_dev/README.md` behavior description;
- the installed runtime mirror through `make -C local_dev install-shim`.

Session retention, session cleanup, memory handling, Serena MCP lifecycle,
Graphify setup, child command construction, the public `dotsync` CLI, and root
documentation remain unchanged.

## Verification

Tests will prove:

- `_read_yes_no_arrow()` erases its three-line block when Ctrl+C interrupts
  `os.read()` with `KeyboardInterrupt`;
- raw-selector interruption still restores the original terminal attributes;
- `_read_yes_no_arrow()` re-raises `KeyboardInterrupt` instead of returning a
  yes/no value;
- `main()` converts `KeyboardInterrupt` from `_main_v2()` into exit code `130`;
- `main()` renders exactly one visible `! cancelled` row and no traceback;
- non-interrupt exceptions still propagate;
- ordinary Yes/No confirmation behavior remains unchanged;
- focused tests, all `local_dev` tests, and all public `dotsync` tests pass;
- the installed runtime source matches the development source and produces
  the same cancellation result.
