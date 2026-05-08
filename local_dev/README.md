# Local Dev Tooling

> **Scope.** This directory is internal-only development tooling that lives in
> this checkout for convenience. It is **unrelated to the `dotsync` CLI**: it
> ships nothing through the Homebrew formula, exposes no `dotsync` user-facing
> behavior, and shares no runtime code with `lib/dotsync/`. Treat it as a
> separate small project that just happens to be co-located.

## Two locations: dev (here) and runtime (stable)

Develop here, run from a stable directory. `make install-shim` mirrors the
launcher tree to a long-lived path before patching `~/.zshrc`, and the
`SERENA_AGENT_LAUNCHER` line in `~/.zshrc` always points at the mirrored copy
— never at this checkout. Moving or deleting this repo therefore does **not**
break the installed `claude` / `codex` shim functions.

| | Path | Purpose |
|---|---|---|
| Dev source | `local_dev/serena_mcp_management/` (this dir) | Edit, test, iterate. |
| Runtime mirror | `~/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/` | What `~/.zshrc` actually executes. |

The mirror copies the source tree exactly (same `local_dev/serena_mcp_management/`
layout) because `serena_agent_launcher.py` resolves its package root via
`Path(__file__).resolve().parents[2]` and imports `local_dev.serena_mcp_management.*`.
Preserving the depth keeps imports working without code changes.

Override the runtime location with `STABLE_DIR=...` if you want a different
stable home.

## Serena MCP Management

`serena_mcp_management/` contains the local Serena MCP launcher, zsh shim
generator, and scoped server lifecycle code used for Codex and Claude
development sessions.

The managed zsh flow is:

```text
~/.zshrc
  -> $STABLE_DIR/local_dev/serena_mcp_management/serena_agent_launcher.py
  -> $STABLE_DIR/local_dev/serena_mcp_management/serena_mcp/
  -> real codex or claude binary
```

Interactive no-argument `codex` / `claude` launches show a single ANSI
preflight box from the Python launcher: workspace, Serena project status,
Graphify status (4 rows: global / graph / integration / hook), context,
cleanup prediction, and memory reset count. After Run/Abort confirmation (and
an optional Initialize/Skip prompt when `.serena/project.yml` is absent), the
shim runs cleanup and starts the scoped Serena MCP server while updating the
same box in place. When the agent TUI exits, a summary box reports session
duration, cleanup result, MCP lifecycle, and any accumulated warnings.

## Workflow

```bash
cd local_dev
# Edit code under serena_mcp_management/ ...
make install-shim
exec zsh   # reload claude/codex shell functions
```

`install-shim` is the only command. It rsyncs the dev tree to `$STABLE_DIR`
and rewrites the managed block in `~/.zshrc` in one step.

If you ever need to remove the managed block (e.g. retiring this tool), the
prior `~/.zshrc` is auto-backed up to `~/.zshrc.dotsync-serena.bak` on every
install — restore that, or run the shim renderer with `--uninstall-zshrc`
directly:

```bash
python3 "$STABLE_DIR/local_dev/serena_mcp_management/serena_zsh_shim.py" \
  --uninstall-zshrc --rc-path ~/.zshrc
```

## Tests

Run the local dev tests with:

```bash
../.venv/bin/python3 -m pytest local_dev/tests -q
```

Run the public dotsync tests with:

```bash
../.venv/bin/python3 -m pytest tests -q
```

(Both invocations are run from the repo root; the relative paths above assume
you're inside `local_dev/`.)
