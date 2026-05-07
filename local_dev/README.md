# Local Dev Tooling

This directory contains local-only development tooling for this checkout. It is
not part of the public dotsync Homebrew package.

## Serena MCP Management

`local_dev/serena_mcp_management/` contains the local Serena MCP launcher,
zsh shim generator, and scoped server lifecycle code used for Codex and Claude
development sessions.

The managed zsh flow is:

```text
~/.zshrc
  -> local_dev/serena_mcp_management/serena_agent_launcher.py
  -> local_dev/serena_mcp_management/serena_mcp/
  -> real codex or claude binary
```

Generate the local zsh snippet from this checkout:

```bash
python3 local_dev/serena_mcp_management/serena_zsh_shim.py
```

Apply the generated snippet to the managed block in `~/.zshrc`:

```bash
make local-serena-shim
```

Interactive no-argument `codex` / `claude` launches show a single ANSI
preflight box from the Python launcher: workspace, Serena project status,
Graphify availability, context, cleanup prediction, and memory reset count.
After Run/Abort confirmation (and an optional Initialize/Skip prompt when
`.serena/project.yml` is absent), the shim runs cleanup and starts the scoped
Serena MCP server while updating the same box in place. When the agent TUI
exits, a summary box reports session duration, cleanup result, MCP lifecycle,
and any accumulated warnings.

After moving this repository or this directory, update the managed block in
`~/.zshrc` or regenerate it.

## Tests

Run the local dev tests with:

```bash
.venv/bin/python3 -m pytest local_dev/tests -q
```

Run the public dotsync tests with:

```bash
.venv/bin/python3 -m pytest tests -q
```
