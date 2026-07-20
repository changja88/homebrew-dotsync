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

1. Keep one fully spelled-out `cleanup` row. This makes the box wider, but
   preserves the compact two-row layout and requires no legend. This is the
   user-selected layout.
2. Split condition, deletion, and preservation into separate rows. This fits a
   narrow box but adds vertical noise.
3. Keep one compact row with `g` and `r` abbreviations. This fits the existing
   width but is not understandable at a glance, so it is rejected.

## Output Contract

Codex displays logical groups and physical JSONL records:

```text
· sessions    codex 58 groups · 855 records
· cleanup     inactive longer than 5 days · delete 35 groups / 358 records · keep 23 groups / 497 records
```

Claude displays top-level session records because Claude owns parent and
subagent cleanup natively:

```text
· sessions    claude 108 records
· cleanup     inactive longer than 5 days · native delete 75 records · keep 33 records
```

The renderer may widen the preflight box to fit the cleanup row. It must not
abbreviate `groups`, `records`, or the five-day condition.

## Color Contract

Color communicates meaning in addition to the words; it never replaces them:

- session totals: pink (`PINK`);
- `inactive longer than 5 days`: purple (`PURPLE`);
- the complete delete segment, including counts and units: yellow (ANSI 33);
- the complete keep segment, including counts and units: mint (`MINT`);
- separators: neutral terminal text.

Claude's `native delete` segment follows the same yellow delete treatment.
`NO_COLOR=1` removes ANSI styling while preserving the exact readable text.

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

The cleanup text starts with `inactive longer than 5 days`, matching the strict
cutoff: a session exactly five days old is kept, while a session older than
five full 24-hour periods is eligible. Codex applies the cutoff to the newest
record in a logical group. Claude receives `cleanupPeriodDays: 5` and performs
native cleanup. No separate retention row is rendered.

## Verification

Tests will prove:

- Codex inventory reports logical-group and physical-record totals separately;
- eligible Codex groups expose matching physical delete/keep record counts;
- Codex preflight renders both rows with full units and the five-day condition
  first in `cleanup`;
- Claude preflight uses the `records` unit and the same explicit policy;
- policy, delete, keep, and total segments use their specified distinct colors;
- `NO_COLOR=1` keeps the full plain-text output;
- neither client renders a separate `retention` row;
- scan-unavailable rendering stays fail-closed;
- existing session cleanup behavior and full `local_dev` tests remain green.
