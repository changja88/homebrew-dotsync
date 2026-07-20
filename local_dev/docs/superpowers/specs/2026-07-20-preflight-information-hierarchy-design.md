# Preflight Information Hierarchy Design

## Goal

Improve the launcher preflight hierarchy without changing cleanup behavior.
Spell out the Serena MCP process inventory and render session totals, deletion
candidates, preserved sessions, and the five-day cleanup rule as one visual
group.

This design supersedes the flat `sessions` plus `cleanup` output contract in
`2026-07-20-preflight-session-counts-design.md`. The inventory and cleanup
semantics from that design remain unchanged.

## Scope

This change is limited to `local_dev/`. It changes the generic box renderer,
preflight value formatting, focused tests, and the internal README. It does not
change session discovery, eligibility, deletion order, Codex's official delete
command, Claude's native cleanup, memory handling, Serena MCP lifecycle, or the
public `dotsync` CLI.

## Selected Session Layout

The user selected the data-type comparison layout. Codex renders one parent
`sessions` item with `groups`, `records`, and `cleanup` children:

```text
· sessions    codex
              ├─ groups   58 total · 35 to delete · 23 to keep
              ├─ records  855 total · 358 to delete · 497 to keep
              └─ cleanup  inactive longer than 5 days
```

Claude has no launcher-defined logical groups, so it renders the applicable
`records` child and identifies the native cleanup authority:

```text
· sessions    claude
              ├─ records  108 total · 74 to delete · 34 to keep
              └─ cleanup  inactive longer than 5 days · native Claude cleanup
```

This knowingly replaces the earlier cleanup-first sentence layout. Cleanup is
still named explicitly and remains inside the `sessions` tree; the selection
prioritizes direct comparison of total, delete, and keep counts by data type.
There is no separate top-level `cleanup`, `criteria`, or `retention` item.

## Serena MCP Terminology

`ps` is an implementation abbreviation for the operating-system process scan.
It is not user-facing language. The preflight spells out every server category:

```text
✓ serena mcp  server processes[3] → managed servers[3] · orphaned servers[0] · leases[4] · stale leases[0]
```

The counts retain their current meanings:

- `server processes`: every discovered running Serena MCP server process;
- `managed servers`: discovered processes that match a valid launcher registry
  record and process identity;
- `orphaned servers`: discovered server processes not proven to be managed;
- `leases`: all launcher leases on managed server records;
- `stale leases`: leases older than the configured lease timeout.

## Rendering Model

Keep `BoxModel.items` and `Item` as the stable top-level renderer contract.
Support newline-separated `Item.value` content generically:

- the first value line stays beside the marker and top-level label;
- continuation lines align beneath the first value column;
- continuation text owns its tree glyphs and child-label styling;
- box width is calculated independently for every visible line after removing
  ANSI escape sequences;
- single-line items retain their exact existing layout.

Preflight creates one `sessions` item whose multiline value contains the
selected tree. It no longer creates an independent `cleanup` item. This is the
smallest renderer extension that expresses the hierarchy and remains reusable
for future grouped values without introducing a second nested model API.

## Color Contract

Words and tree structure carry the meaning; color only reinforces it:

- top-level and child labels: mint;
- total counts and units: pink;
- delete counts and units: yellow;
- keep counts and units: mint;
- cleanup condition and native-cleanup note: purple;
- tree glyphs and separators: neutral gray.

The Serena MCP row keeps the same risk behavior: positive orphaned-server and
stale-lease counts are yellow, while zero-risk segments are gray. Normal
category labels are purple, managed servers are mint, and normal counts are
pink.

## Failure Behavior

Inventory scanning remains fail-closed. When the inventory is unavailable,
the single `sessions` item is warning-colored and reports `scan unavailable`
without rendering invented counts. No cleanup is attempted from a failed
snapshot.

## Documentation

Update only `local_dev/README.md`, preserving its English and Korean parity.
Do not mention the internal launcher in the public root README or root
Makefile.

## Verification

Tests will prove:

- Serena MCP output contains `server processes` and no `ps[` abbreviation;
- all Serena MCP categories use their full user-facing names and existing risk
  colors;
- generic multiline values align continuation rows below the value column;
- border width covers the longest visible continuation row;
- Codex renders one `sessions` parent with `groups`, `records`, and `cleanup`
  children using the selected count order;
- Claude renders `records` and `cleanup` children and identifies native cleanup;
- there is no independent top-level `cleanup`, `criteria`, or `retention` row;
- total, delete, keep, and cleanup segments use their specified distinct colors;
- scan-unavailable behavior remains fail-closed;
- focused renderer and launcher tests, the complete `local_dev` test suite, and
  the public `dotsync` test suite remain green;
- the installed runtime copy produces the same plain-text hierarchy.
