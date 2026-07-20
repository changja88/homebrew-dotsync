# Preflight Session Counts Design

## Goal

Make the launcher preflight explain both how many user-resumable Codex session
groups exist and how many physical JSONL records those groups contain. State
the five-day deletion threshold explicitly for both Codex and Claude.

## Scope

This change is limited to `local_dev/`. It changes preflight inventory data,
rendering, focused tests, and the internal README. It does not change session
discovery, eligibility, deletion order, the official Codex delete command,
Claude native cleanup, memory handling, or public `dotsync` behavior.

## Considered Layouts

1. Put group counts, record counts, cleanup counts, and retention policy on one
   row. This is compact vertically but exceeds the normal preflight width.
2. Use `sessions` and `cleanup` rows, with the compact retention condition at
   the start of `cleanup`. This keeps each value scannable and fits the
   existing box. This is the selected layout.
3. Give groups and records separate sections. This is explicit but adds more
   vertical noise than the small inventory needs.

## Output Contract

Codex displays logical groups and physical JSONL records:

```text
· sessions    codex 58 groups / 855 records
· cleanup     inactive >5d . delete 35g / 358r . keep 23g / 497r
```

The `g` and `r` abbreviations mean `groups` and `records`, established by the
`sessions` row immediately above. They keep the cleanup row within the normal
TUI width.

Claude displays top-level session records because Claude owns parent and
subagent cleanup natively:

```text
· sessions    claude 108 records
· cleanup     inactive >5d . native delete 75 records . keep 33 records
```

When inventory scanning fails, the existing fail-closed behavior remains. The
`sessions` and `cleanup` rows show that the scan is unavailable without
inventing counts.

## Inventory Data

Codex keeps its existing logical group counts and additionally records physical
JSONL counts in the immutable inventory snapshot:

- total records: every scanned `sessions/**/*.jsonl`, including malformed
  records and copies in different known homes;
- records to delete: the physical files contained by eligible logical groups;
- records to keep: total records minus records to delete.

Claude already counts top-level `projects/*/*.jsonl` records, so its existing
counts are rendered with the `records` unit. The native cleanup remains the
authority for what is actually removed.

## Boundary Semantics

The cleanup text starts with `inactive >5d`, matching the strict cutoff: a
session exactly five days old is kept, while a session older than five full
24-hour periods is eligible. Codex applies the cutoff to the newest record in
a logical group. Claude receives `cleanupPeriodDays: 5` and performs native
cleanup. No separate retention row is rendered.

## Verification

Tests will prove:

- Codex inventory reports logical-group and physical-record totals separately;
- eligible Codex groups expose matching physical delete/keep record counts;
- Codex preflight renders both rows with the five-day condition first in
  `cleanup`;
- Claude preflight uses the `records` unit and the same explicit policy;
- neither client renders a separate `retention` row;
- scan-unavailable rendering stays fail-closed;
- existing session cleanup behavior and full `local_dev` tests remain green.
