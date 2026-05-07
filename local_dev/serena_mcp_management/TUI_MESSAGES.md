# Agent TUI Message Drafts

This document is a screen-first draft for the managed `claude` and `codex`
launch flow. The current design has two rendering paths:

- `gum` path: used for interactive launches when `gum` is installed.
- fallback path: used when `gum` is missing, with an install hint.

`gum` is an external local tool. It is not bundled and is not a public
`dotsync` runtime dependency.

## 1. Gum Preflight

When `gum` is installed, the preflight uses `gum style`, `gum log`, and
`gum confirm`. This path is decision-first rather than board-first.

### Current

```text
  +--------------------------------------------------+
  |                                                  |
  |                       codex                      |
  |             ~/Desktop/homebrew-dotsync           |
  |                                                  |
  |                 Serena MCP managed               |
  |                                                  |
  | graphify: installed . run /graphify . when you   |
  |            want a project graph                  |
  |                 context: codex                   |
  |       cleanup: 0 to delete . 103 to keep         |
  |            memory: 0 files to reset              |
  |                                                  |
  +--------------------------------------------------+

? Run codex?  Run / Abort
```

### Included States

```text
WARN serena: project config missing
```

```text
WARN graphify: not installed . install graphify, then run /graphify .
```

```text
INFO cleanup: scan skipped (jq missing)
```

Sources:

- `_dotsync_agent_gum_preflight`
- `gum style`
- `gum log`
- `gum confirm`
- Official `gum style` pattern:
  `gum style --foreground 212 --border-foreground 212 --border double --align center --width 50 --margin "1 2" --padding "2 4" ...`

## 2. Fallback Preflight

When `gum` is not installed, the shim shows a one-line install hint and then
uses the existing board-style preflight.

### Current

```text
  ! gum       missing   . install: brew install gum

  codex . preflight                         pending
  ------------------------------------------------------------
  > workspace   ~/Desktop/homebrew-dotsync
  > serena      managed by scoped launcher
  o graphify    installed . run /graphify . when you want a project graph
  o context     codex
  o cleanup     0 to delete . 103 to keep
  o memory      0 files to reset
  ------------------------------------------------------------
  > Enter to run  .  Ctrl-C to abort 
```

Sources:

- `_dotsync_agent_preflight`
- `_dotsync_agent_gum_missing_hint`

## 3. Serena Setup

This screen appears only when `.serena/project.yml` is missing after preflight.

### Gum Path

```text
? Initialize Serena for this project?  Initialize / Skip
```

If the user skips:

```text
  ! serena    skipped   . launching codex without Serena project config
```

If the user initializes, the shim runs `serena project create <project-root>`
and feeds default answers with `yes ""`. Serena's own command output appears
raw.

```text
Project path /Users/hyun/Desktop/homebrew-dotsync does not exist or no associated project configuration file found, skipping.
Detected and enabled main language 'python' (83.33% of source files).
Additionally detected 1 other language(s).

Which additional languages do you want to enable?
Enable ruby (1.39% of source files)? [y/N] 
Generated project with languages {python} at /Users/hyun/Desktop/homebrew-dotsync/.serena/project.yml.
```

### Fallback Path

```text
  ! serena    missing   . initialize this project? [y/N] n
  ! serena    skipped   . launching codex without Serena project config
```

Sources:

- `_dotsync_agent_ensure_serena`
- `_dotsync_agent_create_serena_project`
- external `serena project create`

## 4. Launch Preparation

This screen appears after preflight and optional Serena setup, before the real
Claude or Codex TUI opens. This is still native Python/zsh output; the current
implementation does not run `gum` inside the Python launcher.

### Current

```text
  * cleanup    done      . sessions_deleted=0 memory_files_reset=0
  * serena     mcp       . preparing scoped server
  * serena     ready     . http://127.0.0.1:9000/mcp
```

Startup failure is not currently formatted as a friendly row. The user may see
the pending row followed by Python error output.

```text
  * serena     mcp       . preparing scoped server
Traceback (most recent call last):
  ...
RuntimeError: failed to start healthy Serena MCP server: <last error>
```

Sources:

- `_dotsync_agent_cleanup_codex`
- `_dotsync_agent_cleanup_claude`
- `format_mcp_progress_status`
- `ensure_server`

## 5. Agent TUI Transition

The real Claude or Codex screen is owned by the underlying tool, not by the
shim. The Python launcher clears preflight and preparation output before this
transition.

```text
<preflight / setup / launch preparation output>
ESC[3J ESC[H ESC[2J
<real agent TUI starts on a clean screen>
```

Sources:

- `clear_terminal_before_child`
- `SERENA_AGENT_CLEAR_BEFORE_CHILD=1`

## 6. Shutdown

This screen appears after the user exits Claude or Codex. It releases the
scoped Serena MCP lease and stops the server only when no other sessions remain.

### Current

```text
  * serena     shutdown  . stopping scoped MCP server
  * serena     done      . sessions_before=1 closed=1 remaining=0 server=stopped
```

Other sessions still active:

```text
  * serena     done      . sessions_before=3 closed=1 remaining=2 server=kept
```

Sources:

- `format_shutdown_progress_status`
- `format_shutdown_status`
- `release_lease_and_shutdown_if_empty`

## 7. Appendix: Shim Maintenance

These messages are not part of the `claude` or `codex` launch TUI, but they are
visible when installing or printing the managed zsh shim.

```text
installed Serena zsh shim into /Users/hyun/.zshrc
backup written to /Users/hyun/.zshrc.dotsync-serena.bak
```

Sources:

- `serena_zsh_shim.py --install-zshrc`
- `serena_zsh_shim.py`

## Open Design Questions

- Should launch preparation and shutdown also use `gum log`, or stay native so
  the Python launcher remains isolated from local UI tooling?
- Should `gum` preflight hide successful optional tools and show only blockers
  plus hints?
- Should raw `serena project create` output be wrapped with a `gum spin`
  message, or left visible because Serena owns that flow?
