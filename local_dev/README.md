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

Interactive no-argument `codex` / `claude` launches show the existing
preflight first, including cleanup and memory reset counts. If the project root
does not have `.serena/project.yml`, the preflight marks Serena as
`project config missing`; after the user confirms the preflight, the shim asks
whether to run `serena project create <project-root>`. Declining that prompt
launches the real agent binary without Serena project config. When project
creation runs, additional Serena language prompts receive the default answer so
optional language servers are not enabled accidentally.

The preflight also reports whether `graphify` is installed. This is guidance
only: the shim does not install Graphify, run Graphify, or create
`graphify-out/`. When you want a project graph, run `/graphify .` from inside
Codex or Claude. Large corpora should be handled by Graphify's own detection
flow rather than by the launcher.

After project setup, the shim runs the agent cleanup step and then delegates to
the Python launcher. The launcher prints Serena MCP progress rows while it
starts or reuses the scoped server, then clears the preflight output immediately
before starting the real Codex or Claude TUI.

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
