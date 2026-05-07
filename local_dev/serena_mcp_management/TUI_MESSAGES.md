# Agent TUI Messages

The Python launcher renders a single ANSI box that updates in place across phases (preflight → serena-init → launch-prep → agent → summary). When the agent TUI exits, a summary box reports session duration, cleanup result, MCP lifecycle, and any accumulated warnings.

## Preflight box

```
  ────────────────────────────────────────────────────────────
  codex  ·  preflight
  ────────────────────────────────────────────────────────────
  ✓ workspace   ~/Desktop/homebrew-dotsync
  ✓ serena      managed by scoped launcher
  ✓ graphify    installed
  ✓ context     codex
  ✓ cleanup     0 to delete . 103 to keep
  ✓ memory      0 files to reset
  ────────────────────────────────────────────────────────────

  > Run codex? [Y/n]
```

When `.serena/project.yml` is absent, the preflight marks Serena as `warn` and a follow-up prompt appears:

```
  > Initialize Serena for this project? [y/N]
```

## Launch-prep

After the preflight confirmation (and optional Serena initialization), the box updates:

```
  ✓ cleanup     0 deleted . 0 memory files reset
  ✓ serena      ready      . http://127.0.0.1:9000/mcp
```

The terminal is cleared just before the real agent TUI launches.

## Summary (after agent exits)

A new box is drawn to scroll-back:

```
  ────────────────────────────────────────────────────────────
  codex  ·  summary
  ────────────────────────────────────────────────────────────
  ✓ duration    2m 5s
  ✓ cleanup     0 deleted . 0 memory files reset
  ✓ serena      server stopped
  ────────────────────────────────────────────────────────────
```

Sources: `local_dev/serena_mcp_management/ui.py` (BoxModel, BoxRenderer, SpinnerTicker, confirm), `local_dev/serena_mcp_management/serena_agent_launcher.py` (`_main_v2`, `_run_preflight_v2`, `_run_serena_init_v2`, `_run_launch_prep_v2`, `_start_mcp_with_spinner`, `_render_summary_v2`).
