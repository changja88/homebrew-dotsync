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
