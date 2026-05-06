from dotsync.apps.mcp_sanitizer import (
    SanitizedMapping,
    SanitizedText,
    filter_claude_mcp_servers,
    sanitize_codex_config,
    sanitize_codex_config_text,
)


def test_sanitize_codex_config_removes_dynamic_serena_table():
    src = """model = "gpt-5.2"

[mcp_servers.serena]
url = "http://127.0.0.1:9123/mcp"

[mcp_servers.playwright]
command = "npx"
args = ["@playwright/mcp"]
"""

    assert sanitize_codex_config_text(src) == """model = "gpt-5.2"

[mcp_servers.playwright]
command = "npx"
args = ["@playwright/mcp"]
"""


def test_sanitize_codex_config_removes_dynamic_serena_child_tables():
    src = """[mcp_servers.serena]
url = "http://127.0.0.1:9123/mcp"

[mcp_servers.serena.env]
TOKEN = "secret"

[mcp_servers.serena.headers]
X_TOKEN = "secret"

[mcp_servers.context7]
url = "http://127.0.0.1:7777/mcp"
"""

    assert sanitize_codex_config_text(src) == """[mcp_servers.context7]
url = "http://127.0.0.1:7777/mcp"
"""


def test_sanitize_codex_config_preserves_unaffected_text_exactly():
    src = """# user comment
model = "gpt-5.2"

[mcp_servers.context7] # local but not Serena
url = "http://127.0.0.1:7777/mcp"

[mcp_servers.serena]
url = "http://127.0.0.1:9123/mcp"

[profiles.work]
model = "gpt-5.4"
"""

    assert sanitize_codex_config_text(src) == """# user comment
model = "gpt-5.2"

[mcp_servers.context7] # local but not Serena
url = "http://127.0.0.1:7777/mcp"

[profiles.work]
model = "gpt-5.4"
"""


def test_sanitize_codex_config_reports_when_text_changed():
    result = sanitize_codex_config(
        """[mcp_servers.serena]
url = "http://127.0.0.1:9123/mcp"
"""
    )

    assert result == SanitizedText(text="", changed=True)


def test_sanitize_codex_config_keeps_non_local_serena_table():
    src = """[mcp_servers.serena]
url = "https://example.com/mcp"
"""

    assert sanitize_codex_config_text(src) == src


def test_filter_claude_mcp_servers_removes_local_serena_http_url():
    servers = {
        "serena": {"type": "http", "url": "http://127.0.0.1:9123/mcp"},
        "playwright": {"command": "npx"},
    }

    assert filter_claude_mcp_servers(servers).value == {
        "playwright": {"command": "npx"}
    }


def test_filter_claude_mcp_servers_reports_when_changed():
    servers = {"serena": {"type": "http", "url": "http://127.0.0.1:9123/mcp"}}

    assert filter_claude_mcp_servers(servers) == SanitizedMapping(value={}, changed=True)


def test_filter_claude_mcp_servers_keeps_remote_serena_url():
    servers = {"serena": {"type": "http", "url": "https://example.com/mcp"}}

    assert filter_claude_mcp_servers(servers).value == servers
    assert filter_claude_mcp_servers(servers).changed is False


def test_filter_claude_mcp_servers_returns_deep_copy():
    servers = {"playwright": {"command": "npx"}}

    result = filter_claude_mcp_servers(servers).value
    result["playwright"]["command"] = "changed"

    assert servers["playwright"]["command"] == "npx"


def test_filter_claude_mcp_servers_keeps_non_serena_local_http_url():
    servers = {"context7": {"type": "http", "url": "http://127.0.0.1:7777/mcp"}}

    assert filter_claude_mcp_servers(servers).value == servers


def test_filter_claude_mcp_servers_keeps_command_based_serena_entry():
    servers = {"serena": {"command": "serena", "args": ["start-mcp-server"]}}

    assert filter_claude_mcp_servers(servers).value == servers
