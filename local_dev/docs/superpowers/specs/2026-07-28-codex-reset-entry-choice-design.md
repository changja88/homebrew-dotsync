# Codex Reset Entry Choice Design

**Date:** 2026-07-28

## Goal

Do not open the destructive Codex session picker immediately during every
interactive launcher run. First ask whether the user wants to keep existing
Codex sessions and memories or enter the reset flow.

## User Experience

The interactive Codex launcher shows this amber single-choice prompt before
running the detailed reset catalog scan or listing persisted sessions:

```text
? Reset Codex sessions and memories before launch?
  ▶ Keep all sessions and memories (default)
    Select sessions to delete and reset all memories
```

Choosing `Keep all sessions and memories (default)`, including by pressing
Enter, is an exact no-op. It does not scan the session catalog, show the
multi-select picker, delete data, or run the former five-day Codex cleanup.

Choosing `Select sessions to delete and reset all memories` enters the
existing combined reset flow:

1. Scan every known Codex home for active and archived logical sessions.
2. If no persisted sessions exist, report that fact and continue without
   deleting anything.
3. Otherwise, show `Select Codex sessions to force-delete`.
4. Treat an empty selection as an exact no-op.
5. For a non-empty selection, retain the existing final confirmation.
6. After confirmation, delete only the selected logical session groups and
   reset all known Codex memory, history, log, and snapshot targets.

The option wording deliberately distinguishes the two scopes: sessions are
selected individually, while memories and related traces are reset globally
whenever at least one selected session is confirmed.

## Implementation Boundary

Keep orchestration in `_run_session_choice_v2`:

- Add one `select_option` call for interactive Codex launches.
- Return an empty `CodexResetSelection` immediately for the keep choice.
- Perform the existing catalog scan, multi-select, and confirmation only for
  the reset choice.
- Do not add another memory prompt or change Claude behavior.
- Do not change `delete_selected_codex_sessions` or its deletion contract.

Update only the focused launcher tests and the Codex startup-choice section in
`local_dev/README.md`. The public root README remains untouched because
`local_dev/` is an internal-only launcher.

## Errors and Cancellation

- Ctrl+C at the new prompt keeps the existing launcher-wide cancellation
  behavior: no reset and no child launch.
- Catalog scan failures remain non-destructive and launchable after the user
  explicitly enters the reset flow.
- The keep path cannot surface catalog errors because it performs no scan.

## Tests

Add or update focused tests proving:

- Enter defaults to keeping sessions and memories.
- The keep choice does not scan or render the session picker.
- The reset choice scans and then renders the existing picker.
- The no-session and scan-failure messages occur only after entering the reset
  flow.
- A selected and confirmed session still produces the same
  `CodexResetSelection`.
- Existing Claude choices and launcher cancellation behavior remain unchanged.

Run the focused launcher tests first, then the complete `local_dev` suite.
