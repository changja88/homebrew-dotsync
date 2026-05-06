# Serena CLI Contract

작성일: 2026-05-06

## Serena

Codex scope uses:

```bash
serena start-mcp-server \
  --project <project-root> \
  --context codex \
  --mode editing \
  --mode interactive \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port <mcp-port> \
  --enable-web-dashboard true \
  --open-web-dashboard false
```

Claude scope uses the Claude Code Serena context while keeping the registry
client type as `claude`:

```bash
serena start-mcp-server \
  --project <project-root> \
  --context claude-code \
  --mode editing \
  --mode interactive \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port <mcp-port> \
  --enable-web-dashboard true \
  --open-web-dashboard false
```

Dashboard active project is verified through:

```text
GET http://127.0.0.1:<dashboard-port>/get_config_overview
```

The response must contain the target project root as the active project path.
`Active Project: None`, or a matching path only under `registered_projects`, is
unhealthy.

## Codex

Codex accepts per-run config overrides:

```bash
codex -c 'mcp_servers.serena.url="http://127.0.0.1:<port>/mcp"' <args...>
```

The Serena launcher passes this override only to the child process after the zsh
agent shim has finished preflight cleanup. It must not write the dynamic URL to
`~/.codex/config.toml`.

## Claude

Claude accepts per-run MCP config:

```bash
claude --mcp-config=<json-file> <args...>
```

Use the `--mcp-config=<path>` form because `--mcp-config <path>` is variadic and
can consume positional child args.

The Serena launcher writes a temporary JSON file:

```json
{
  "mcpServers": {
    "serena": {
      "type": "http",
      "url": "http://127.0.0.1:<port>/mcp"
    }
  }
}
```

The Serena launcher must not use `--strict-mcp-config` unless runtime
verification shows that a user-level stale `serena` entry wins over the per-run
config. In that case, the implementation must switch to an explicitly merged
config file or a collision-free runtime server name.
