"""Detect whether the active client needs a Node.js runtime.

context7/playwright MCP servers run via `npx`, and the claude-hud statusLine
runs the HUD via `node`. When node is absent these fail at startup with
`os error 2` (No such file or directory). This module scans the client's
configured MCP servers and statusLine for node/npx commands so the launcher
can offer to install node before launch, instead of letting them fail silently.

Pure functions over explicit paths — the launcher passes real config locations,
tests pass temp dirs. No process spawning here; resolution/install of node
itself lives in `external_cli`.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Match `node` or `npx` as a standalone command token: preceded by start,
# whitespace, quote, or path separator, and followed by end, whitespace, or
# quote. This catches a bare `npx`, an absolute `/opt/homebrew/bin/node`, and a
# node path embedded in a quoted bash blob, while skipping `node_modules`,
# `nodejs`, and `npx`-prefixed longer words.
_NODE_TOKEN_RE = re.compile(r"""(?:^|[\s'"/])(?:node|npx)(?=$|[\s'"])""")

# The claude-hud statusLine hardcodes this exact path, so a PATH node elsewhere
# (e.g. nvm) does NOT make the HUD work — only a node at this path does.
HOMEBREW_NODE_PATH = "/opt/homebrew/bin/node"


@dataclass(frozen=True)
class NodeNeed:
    """What kind of Node.js a client requires.

    ``generic`` — an `npx`/`node` command that any node on PATH satisfies
    (npx-based MCP servers). ``homebrew`` — a command that hardcodes
    ``/opt/homebrew/bin/node`` (claude-hud statusLine), which only a node at
    that exact path satisfies. The remedy for both is `brew install node`,
    but they resolve against different locations.
    """

    generic: bool
    homebrew: bool

    @property
    def any(self) -> bool:
        return self.generic or self.homebrew


def command_needs_node(command: object) -> bool:
    """True if a command string invokes node or npx (path-agnostic)."""
    if not isinstance(command, str) or not command.strip():
        return False
    return bool(_NODE_TOKEN_RE.search(command))


def _classify_command(command: object) -> tuple[bool, bool]:
    """Return (needs_generic, needs_homebrew) for one command string.

    A command that hardcodes the homebrew node path is a homebrew need; any
    other node/npx invocation is generic. A single command is at most one kind.
    """
    if isinstance(command, str) and HOMEBREW_NODE_PATH in command:
        return (False, True)
    return (command_needs_node(command), False)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _mcp_server_commands(servers: object) -> list[str]:
    """Extract each server's `command` from an MCP server map.

    Handles both a plugin `.mcp.json` (the whole doc is the server map) and a
    `.claude.json`/codex `mcp_servers` table (server name -> meta).
    """
    if not isinstance(servers, dict):
        return []
    out: list[str] = []
    for meta in servers.values():
        if isinstance(meta, dict) and isinstance(meta.get("command"), str):
            out.append(meta["command"])
    return out


def _plugin_mcp_commands(claude_dir: Path, plugin_id: str) -> list[str]:
    """Collect MCP commands from an enabled plugin's cached `.mcp.json`."""
    if not isinstance(plugin_id, str) or not plugin_id:
        return []
    if "@" in plugin_id:
        plugin, marketplace = plugin_id.split("@", 1)
    else:
        plugin, marketplace = plugin_id, None
    cache = claude_dir / "plugins" / "cache"
    roots = (
        [cache / marketplace / plugin]
        if marketplace
        else list(cache.glob(f"*/{plugin}"))
    )
    out: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for mcp in sorted(root.glob("*/.mcp.json")):
            out.extend(_mcp_server_commands(_read_json(mcp)))
    return out


def claude_node_commands(*, claude_dir: Path, claude_json: Path) -> list[str]:
    """Every command string the Claude client would spawn that could need node:
    the statusLine command, each enabled plugin's MCP servers, and the
    user-level `.claude.json` MCP servers.
    """
    commands: list[str] = []
    settings = _read_json(claude_dir / "settings.json")
    if isinstance(settings, dict):
        status_line = settings.get("statusLine")
        if isinstance(status_line, dict) and isinstance(
            status_line.get("command"), str
        ):
            commands.append(status_line["command"])
        enabled = settings.get("enabledPlugins")
        if isinstance(enabled, dict):
            for plugin_id, is_on in enabled.items():
                if is_on:
                    commands.extend(_plugin_mcp_commands(claude_dir, plugin_id))
    claude_doc = _read_json(claude_json)
    if isinstance(claude_doc, dict):
        commands.extend(_mcp_server_commands(claude_doc.get("mcpServers")))
    return commands


def codex_node_commands(*, codex_home: Path) -> list[str]:
    """Every command string the Codex client would spawn from its
    `config.toml` `[mcp_servers.*]` tables.
    """
    config = codex_home / "config.toml"
    if not config.is_file():
        return []
    try:
        data = tomllib.loads(config.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return []
    return _mcp_server_commands(data.get("mcp_servers"))


def node_need(
    client: str,
    *,
    claude_dir: Path | None = None,
    claude_json: Path | None = None,
    codex_home: Path | None = None,
) -> NodeNeed:
    """The Node.js need for the client's configured plugins/MCP/statusLine."""
    if client == "claude":
        cdir = claude_dir if claude_dir is not None else Path.home() / ".claude"
        cjson = (
            claude_json if claude_json is not None else Path.home() / ".claude.json"
        )
        commands = claude_node_commands(claude_dir=cdir, claude_json=cjson)
    else:
        chome = codex_home if codex_home is not None else Path.home() / ".codex"
        commands = codex_node_commands(codex_home=chome)
    generic = homebrew = False
    for command in commands:
        cmd_generic, cmd_homebrew = _classify_command(command)
        generic = generic or cmd_generic
        homebrew = homebrew or cmd_homebrew
    return NodeNeed(generic=generic, homebrew=homebrew)
