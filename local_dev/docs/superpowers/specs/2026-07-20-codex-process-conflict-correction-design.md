# Codex Memory Process-Conflict Correction Design

## Problem

The interactive launcher currently refuses `Delete all Codex auto-memory and
run` even when the memory inventory contains zero stores. Its process scanner
also parses the whitespace-delimited `comm` and `args` columns from one
`/bin/ps` row. macOS executable paths may contain spaces, so ChatGPT helpers
under `.../Codex Framework.framework/...` are truncated to a false executable
named `Codex` and counted as Codex clients.

The observed result was an empty inventory followed by a false-looking
`10 running Codex process(es)` failure and no agent launch.

## Selected Approach

1. Keep fail-closed deletion semantics. A failed or unsafe non-empty deletion
   must not silently launch with memory the user asked to remove.
2. After a warning-free authoritative scan, treat an empty inventory as a
   successful no-op (`0 stores`, `0 files`) before inspecting processes. There
   is no mutation that can race in this case.
3. Read process identity and arguments in two unambiguous `/bin/ps` snapshots:
   - `pid`, `ppid`, and `comm`, parsing only the first two whitespace fields;
   - `pid` and `args`, parsing only the PID before preserving the remainder.
   Join rows by PID. This retains executable paths containing spaces and keeps
   official Node-wrapper detection from the full arguments.
4. Match native clients from the full `comm` basename. ChatGPT crash handlers,
   renderers, services, and Computer Use helpers are therefore excluded. A
   genuine executable whose basename is `codex`, including the ChatGPT Codex
   app-server, remains a conflict when stores actually exist.
5. Return concise conflict details containing PID and executable name. Limit
   display length and summarize any remaining processes.

## Rejected Alternatives

- Excluding every process under `ChatGPT.app`: rejected because its real
  `Resources/codex ... app-server` process may use the same Codex home and can
  race with non-empty memory deletion.
- Launching after any deletion failure: rejected because it changes the
  selected action into “run with existing memory” without user consent.
- Lowercasing or substring matching process paths: rejected because it keeps
  the current false-positive class and makes identity less precise.

## Flow

```text
authoritative memory scan
  -> warnings? fail and do not launch
  -> zero stores? deletion succeeds as no-op
  -> otherwise scan real client processes
       -> conflicts? report PID/name, fail, do not launch
       -> no conflicts? validate targets, delete, then launch
```

## Error and UI Behavior

- Empty inventory: print the existing successful
  `0 stores · 0 files deleted` row, then run session cleanup and the agent.
- Real process conflict: print a failed deletion row with the number of
  processes plus representative PID/executable pairs; do not clean sessions or
  launch.
- Process-scan failure: remain fail-closed.
- Partial filesystem deletion: retain deleted counts plus the error and stop.

## Testing

- A zero-store inventory must not call `/bin/ps` and must return success.
- A launcher delete choice over zero stores must continue through session
  cleanup and launch.
- `comm` paths containing `Codex Framework.framework`, `Codex (Service)`,
  `Codex (Renderer)`, and `Codex Computer Use.app` must not match.
- A real native `codex`, another Codex CLI, and the official Node entrypoint
  must still match.
- Process parsing must preserve paths and arguments containing spaces.
- Conflict output must contain bounded PID/executable details.
- Existing Claude Desktop exclusion, ancestor exclusion, deletion safety, and
  five-day session cleanup regressions must remain green.

## Scope

Only the private launcher under `local_dev/` changes. Public `dotsync` code,
the root README, and Homebrew packaging remain untouched. Runtime promotion
uses `make -C local_dev install-shim` after all tests pass.
