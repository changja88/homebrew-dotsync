import json

from local_dev.serena_mcp_management import node_preflight as np

# The claude-hud statusLine wraps node in a bash blob; reproduced shape here.
_STATUSLINE_BLOB = (
    "bash -c 'plugin_dir=$(ls -d \"$HOME/.claude\"/plugins/cache/claude-hud/"
    "claude-hud/*/); COLUMNS=200 exec \"/opt/homebrew/bin/node\" "
    "\"${plugin_dir}dist/index.js\"'"
)


def test_command_needs_node_matches_npx_and_node():
    assert np.command_needs_node("npx")
    assert np.command_needs_node("node")
    assert np.command_needs_node("/opt/homebrew/bin/node")
    assert np.command_needs_node(_STATUSLINE_BLOB)


def test_command_needs_node_ignores_non_node():
    assert not np.command_needs_node("uvx")
    assert not np.command_needs_node("/usr/lib/node_modules/foo/bin/tool")
    assert not np.command_needs_node("nodejs-tool")
    assert not np.command_needs_node("")
    assert not np.command_needs_node(None)


def _write_plugin_mcp(claude_dir, marketplace, plugin, server_command):
    d = claude_dir / "plugins" / "cache" / marketplace / plugin / "abc123"
    d.mkdir(parents=True)
    (d / ".mcp.json").write_text(
        json.dumps({plugin: {"command": server_command, "args": []}})
    )


def test_claude_node_commands_collects_statusline_plugin_and_claude_json(tmp_path):
    cdir = tmp_path / ".claude"
    cdir.mkdir()
    (cdir / "settings.json").write_text(
        json.dumps(
            {
                "statusLine": {"type": "command", "command": _STATUSLINE_BLOB},
                "enabledPlugins": {
                    "context7@claude-plugins-official": True,
                    "disabled@mp": False,
                },
            }
        )
    )
    _write_plugin_mcp(cdir, "claude-plugins-official", "context7", "npx")
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(
        json.dumps({"mcpServers": {"stitch": {"command": "node", "args": ["s.js"]}}})
    )

    cmds = np.claude_node_commands(claude_dir=cdir, claude_json=claude_json)
    assert "npx" in cmds  # from the enabled plugin's .mcp.json
    assert "node" in cmds  # from .claude.json mcpServers
    assert any(np.command_needs_node(c) for c in cmds)


def test_claude_node_commands_ignores_disabled_plugins(tmp_path):
    cdir = tmp_path / ".claude"
    cdir.mkdir()
    (cdir / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"context7@mp": False}})
    )
    _write_plugin_mcp(cdir, "mp", "context7", "npx")
    cmds = np.claude_node_commands(claude_dir=cdir, claude_json=tmp_path / "none.json")
    assert "npx" not in cmds


def test_codex_node_commands_reads_config_toml(tmp_path):
    ch = tmp_path / ".codex"
    ch.mkdir()
    (ch / "config.toml").write_text(
        '[mcp_servers.context7]\ncommand = "npx"\nargs = ["-y", "@upstash/context7-mcp"]\n'
    )
    cmds = np.codex_node_commands(codex_home=ch)
    assert "npx" in cmds


def test_codex_node_commands_missing_config_is_empty(tmp_path):
    assert np.codex_node_commands(codex_home=tmp_path / "nope") == []


def test_node_need_generic_for_claude_with_npx_plugin(tmp_path):
    """An npx-based MCP is a generic node need — any node on PATH satisfies it."""
    cdir = tmp_path / ".claude"
    cdir.mkdir()
    (cdir / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"context7@mp": True}})
    )
    _write_plugin_mcp(cdir, "mp", "context7", "npx")
    need = np.node_need("claude", claude_dir=cdir, claude_json=tmp_path / "x.json")
    assert need.any
    assert need.generic
    assert not need.homebrew


def test_node_need_homebrew_for_claude_hud_statusline(tmp_path):
    """The claude-hud statusLine hardcodes /opt/homebrew/bin/node — a homebrew
    need that a PATH node elsewhere does NOT satisfy."""
    cdir = tmp_path / ".claude"
    cdir.mkdir()
    (cdir / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": _STATUSLINE_BLOB}})
    )
    need = np.node_need("claude", claude_dir=cdir, claude_json=tmp_path / "x.json")
    assert need.any
    assert need.homebrew
    assert not need.generic


def test_node_need_none_for_claude_with_only_uvx(tmp_path):
    cdir = tmp_path / ".claude"
    cdir.mkdir()
    (cdir / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"serena@mp": True}})
    )
    _write_plugin_mcp(cdir, "mp", "serena", "uvx")
    need = np.node_need("claude", claude_dir=cdir, claude_json=tmp_path / "x.json")
    assert not need.any


def test_node_need_generic_for_codex_with_npx(tmp_path):
    ch = tmp_path / ".codex"
    ch.mkdir()
    (ch / "config.toml").write_text('[mcp_servers.pw]\ncommand = "npx"\n')
    need = np.node_need("codex", codex_home=ch)
    assert need.generic
    assert not need.homebrew
