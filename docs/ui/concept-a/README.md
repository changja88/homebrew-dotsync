# Concept A visual baseline

**Approved:** 2026-08-21
**Status:** immutable design reference; not production frontend code

This directory preserves the two HTML artifacts that define the approved
DotSync menu-bar experience:

- `original-concepts.html` is the recovered three-direction exploration. Its
  first option, **A · Menu Bar Companion**, is the selected original concept.
- `menu-bar-plus-management.html` is the approved extension that combines the
  original menu-bar popover with a full management window for Overview,
  Accounts, Config Sync, and Settings.

The extension is authoritative when the original popover and the management
window need to be considered together. The original artifact remains here to
prevent later work from silently replacing Concept A with a sidebar-only web
dashboard.

Both files contain fixture-only example data. They must never invoke provider
CLIs, access real accounts, or modify local/sync files. Production UI assets may
be implemented separately, but visual review must compare them against these
references.

Claude account controls shown in the original exploration are historical design
content. The approved extension and production application keep public Claude
subscription account management policy-disabled until Anthropic explicitly
permits that third-party flow.
