"""Coverage for the launcher's default node-runtime wrappers.

The phase tests in test_launcher_phases.py inject node_need/resolve/install
stubs (and an autouse fixture stubs `_client_node_need`), so the real default
wrappers — `_client_node_need`, `_homebrew_node_present`, `_node_runtime_install`
— are never exercised there. These tests pin them directly (L2 gap)."""
import json

from local_dev.serena_mcp_management import serena_agent_launcher as launcher
from local_dev.serena_mcp_management.node_preflight import NodeNeed


def _write_plugin_mcp(claude_dir, marketplace, plugin, command):
    d = claude_dir / "plugins" / "cache" / marketplace / plugin / "abc123"
    d.mkdir(parents=True)
    (d / ".mcp.json").write_text(json.dumps({plugin: {"command": command}}))


def test_client_node_need_claude_reads_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    cdir = tmp_path / ".claude"
    cdir.mkdir()
    (cdir / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"context7@mp": True}})
    )
    _write_plugin_mcp(cdir, "mp", "context7", "npx")

    need = launcher._client_node_need("claude")
    assert need.generic is True
    assert need.homebrew is False


def test_client_node_need_claude_respects_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    cfg = tmp_path / "custom-claude"
    cfg.mkdir()
    (cfg / "settings.json").write_text(
        json.dumps(
            {
                "statusLine": {
                    "type": "command",
                    "command": 'exec "/opt/homebrew/bin/node" hud.js',
                }
            }
        )
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))

    need = launcher._client_node_need("claude")
    assert need.homebrew is True
    assert need.generic is False


def test_client_node_need_codex_reads_codex_home(tmp_path, monkeypatch):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text('[mcp_servers.pw]\ncommand = "npx"\n')
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    need = launcher._client_node_need("codex")
    assert need.generic is True
    assert need.homebrew is False


def test_homebrew_node_present_reflects_resolver(monkeypatch):
    monkeypatch.setattr(
        launcher, "homebrew_node_command",
        lambda: ["/opt/homebrew/bin/node"], raising=False)
    assert launcher._homebrew_node_present() is True

    monkeypatch.setattr(
        launcher, "homebrew_node_command", lambda: None, raising=False)
    assert launcher._homebrew_node_present() is False


def test_node_runtime_install_returns_2_without_brew(monkeypatch):
    monkeypatch.setattr(launcher, "node_install_command", lambda: None, raising=False)
    assert launcher._node_runtime_install() == 2


def test_node_runtime_install_streams_brew_command(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(
        launcher, "node_install_command",
        lambda: ["/stub/brew", "install", "node"], raising=False)

    def fake_stream(cmd, *, label, stream=None):
        calls["cmd"] = cmd
        calls["label"] = label
        return 0

    monkeypatch.setattr(
        launcher, "_run_tool_install_streaming", fake_stream, raising=False)
    rc = launcher._node_runtime_install()
    assert rc == 0
    assert calls["cmd"] == ["/stub/brew", "install", "node"]
    assert calls["label"] == "node"
