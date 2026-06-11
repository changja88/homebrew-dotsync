# Local Dev Tooling

> **Scope.** This directory is internal-only development tooling that lives in
> this checkout for convenience. It is **unrelated to the `dotsync` CLI**: it
> ships nothing through the Homebrew formula, exposes no `dotsync` user-facing
> behavior, and shares no runtime code with `lib/dotsync/`. Treat it as a
> separate small project that just happens to be co-located.

## Two locations: dev (here) and runtime (stable)

Develop here, run from a stable directory. `make install-shim` mirrors the
launcher tree to a long-lived path **and provisions a self-contained Python
venv there** before patching `~/.zshrc`. Both the `SERENA_AGENT_LAUNCHER` and
`SERENA_AGENT_PYTHON` lines in `~/.zshrc` point inside `$STABLE_DIR` — never at
this checkout (neither its code nor its `.venv`). Moving or deleting this repo
therefore does **not** break the installed `claude` / `codex` shim functions.

| | Path | Purpose |
|---|---|---|
| Dev source | `local_dev/serena_mcp_management/` (this dir) | Edit, test, iterate. |
| Runtime mirror | `~/Desktop/dotsync_config/agent_launcher/local_dev/serena_mcp_management/` | What `~/.zshrc` actually executes. |
| Runtime interpreter | `~/Desktop/dotsync_config/agent_launcher/.venv/` | The stdlib-only Python that runs the launcher (`SERENA_AGENT_PYTHON`). |

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
machine-wide Serena MCP inventory, Graphify status (4 rows: global / graph /
integration / hook), context, session inventory, memory inventory, and the
cleanup criteria. After Run/Abort confirmation (and an optional
Initialize/Skip prompt when `.serena/project.yml` is absent), the launcher
runs cleanup and starts the scoped Serena MCP server with inline progress rows
below the preflight box. When the agent TUI exits, a summary box reports
session duration, cleanup result, MCP lifecycle, and any accumulated warnings.

The session and memory rows are computed in Python from the current launcher
context, not predicted by the zsh shim:

| Context | Sessions | Memory | Cleanup criteria |
|---|---|---|---|
| `codex` | `$CODEX_HOME/sessions` (`~/.codex/sessions` by default), recursive `*.jsonl` files whose `session_meta.payload.cwd` matches the current working directory | `$CODEX_HOME/memories` (`~/.codex/memories` by default) | Delete matching sessions older than 3 days; reset all Codex memory files. |
| `claude` | `~/.claude/projects/<encoded cwd>/*.jsonl` | `~/.claude/projects/<encoded project root>/memory` | Delete project sessions older than 3 days; reset all Claude memory files for the project. |

Preflight displays each row as `client total . to delete/reset . to keep`, and
the final summary uses `N sessions deleted . M memory files reset`.

## Workflow

```bash
cd local_dev
# Edit code under serena_mcp_management/ ...
make install-shim
exec zsh   # reload claude/codex shell functions
```

`install-shim` is the only command. It rsyncs the dev tree to `$STABLE_DIR`,
creates the runtime venv at `$STABLE_DIR/.venv` if missing with `uv venv
--managed-python` (a uv-managed standalone interpreter, independent of brew or
system Python; version pinned by `PYTHON_VERSION`, default `3.13`), and
rewrites the managed block in `~/.zshrc` — pointing `SERENA_AGENT_PYTHON` at
that venv — in one step. The launcher is stdlib-only, so the venv needs no
packages and any Python 3.12+ base works.

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
