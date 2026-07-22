# Session Cleanup Spinner Design

## Goal

Keep the existing Codex and Claude session-cleanup behavior unchanged while
making its startup progress visibly active. The launcher currently renders one
static `spin` frame before running the blocking cleanup, so a long cleanup looks
like a frozen terminal.

This change belongs only to the private `local_dev` launcher. It does not change
the public `dotsync` package, cleanup eligibility, deletion order, command
timeouts, error handling, or whether the child agent launches.

## User-Visible Behavior

While an explicit all-inactive session cleanup is running, the existing
`sessions` row redraws in place every 0.1 seconds with the standard spinner
frames:

```text
  ⠋ sessions    deleting inactive codex sessions · running preserved
  ⠙ sessions    deleting inactive codex sessions · running preserved
  ⠹ sessions    deleting inactive codex sessions · running preserved
```

Only one terminal row remains visible because each frame uses a carriage return
and clears the remainder of the line. When cleanup finishes, the launcher stops
the ticker before replacing that row with the existing success or warning
summary.

## Implementation

`_run_explicit_session_cleanup_v2` reuses the existing `SpinnerTicker` and
`style_spinner` UI primitives already used for Serena startup, shutdown, and
tool installation.

The function starts the ticker immediately before the fresh inventory scan and
stops it in a `finally` block after cleanup returns or raises. The final result
row is written only after `SpinnerTicker.stop()` has joined the ticker thread,
so a late spinner frame cannot overwrite the result.

The cleanup remains synchronous on the calling thread. No worker is added for
session deletion, and no progress callback is added to the cleanup modules.
Consequently, session discovery, safety revalidation, official CLI calls,
timeouts, partial-mutation reporting, and launch continuation retain their
current contracts.

## Failure Handling

The ticker stops on success and on every exception already converted into a
`CleanupResult`. The existing final `done` or `warn` row remains authoritative.
The spinner must not introduce a new reason for cleanup or child launch to fail.

## Tests

Focused launcher tests verify that:

- more than one spinner frame can redraw while cleanup is pending;
- the ticker is stopped before the final result row is rendered;
- the ticker also stops when scanning or cleanup raises;
- existing success, failure, partial-mutation, Codex, and Claude cleanup tests
  remain unchanged and pass.

The full `local_dev` test suite is run before the development launcher is
mirrored to the stable runtime location with `make -C local_dev install-shim`.
