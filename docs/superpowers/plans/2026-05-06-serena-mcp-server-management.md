# Serena MCP Server Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Serena-aware launcher and dotsync safeguards so each `(project root, client type)` scope shares exactly one healthy Serena MCP server, shuts it down when no live sessions remain, and never syncs dead dynamic Serena MCP URLs.

**Architecture:** Runtime server coordination lives in local `tools/serena_mcp/` modules, not in the packaged `dotsync` CLI. The launcher owns session leases and child-process injection; a detached watchdog owns stale lease cleanup and server shutdown after terminal force quit. `dotsync` only gets sanitizer logic for Codex/Claude config files so dynamic local Serena MCP URLs are never copied into the sync folder or restored to user config.

**Tech Stack:** Python 3.12 stdlib only, macOS, `fcntl.flock`, `urllib.request`, `subprocess.Popen`, pytest, existing dotsync app plugin pattern.

---

## Current Constraints

- Do not add runtime dependencies.
- Do not create hidden global dotsync state.
- Store Serena runtime state under the project root, in `.serena/dotsync-mcp/`, which is already ignored.
- Do not rely on a persistent global daemon.
- The wrapper must separate `codex` and `claude` scopes even for the same project.
- The wrapper must not pass an MCP URL to Codex/Claude until both MCP and dashboard active project checks pass.
- Terminal force quit cannot run launcher cleanup code, so shutdown must be handled by heartbeat timeout plus an independent watchdog.
- Server publication and the first lease registration must be atomic. A newly started server must never be visible in the registry with zero leases.
- Normal launcher exit must not run stale-lease cleanup. It removes only its own lease, then shuts down the server only if no leases remain.
- Dashboard health must require `active_project.path == project_root`; a target path appearing only in `registered_projects` is not healthy.
- Claude uses Serena context `claude-code` even though the dotsync scope client type remains `claude`.
- Claude runtime MCP injection must use `--mcp-config=<path>`, not `--mcp-config <path>`, because Claude treats `--mcp-config` as variadic.

## File Structure

Create:

- `tools/serena_mcp/__init__.py`
  - Package marker for local Serena launcher modules.
- `tools/serena_mcp/paths.py`
  - Project root detection, scope key normalization, state directory paths.
- `tools/serena_mcp/registry.py`
  - JSON registry dataclasses, schema loading, lock handling, atomic writes, lease mutation.
- `tools/serena_mcp/health.py`
  - PID checks, port checks, MCP HTTP probe, dashboard active project probe.
- `tools/serena_mcp/server.py`
  - Port allocation, Serena process startup, live server discovery, record creation.
- `tools/serena_mcp/watchdog.py`
  - Detached scope watchdog that removes stale leases and kills zero-lease servers.
- `tools/serena_agent_launcher.py`
  - Executable Python entry point for `codex` and `claude` wrapper behavior.
- `tools/install_serena_agent_wrappers.py`
  - Opt-in installer that writes `codex` and `claude` wrapper scripts to a user-selected bin directory.
- `tests/tools/test_serena_paths.py`
- `tests/tools/test_serena_registry.py`
- `tests/tools/test_serena_health.py`
- `tests/tools/test_serena_server.py`
- `tests/tools/test_serena_watchdog.py`
- `tests/tools/test_serena_launcher.py`
- `lib/dotsync/apps/mcp_sanitizer.py`
- `tests/apps/test_mcp_sanitizer.py`

Modify:

- `lib/dotsync/apps/codex.py`
  - Sanitize Codex `config.toml` on `plan_from`, `sync_from`, `plan_to`, `sync_to`, and `status`.
- `lib/dotsync/apps/claude.py`
  - Sanitize Claude `mcpServers` on `plan_from`, `sync_from`, `plan_to`, `sync_to`, and `status`.
- `tests/apps/test_codex.py`
  - Add sanitizer integration tests.
- `tests/apps/test_claude.py`
  - Add sanitizer integration tests.
- `README.md`
  - English and Korean notes that dynamic local Serena MCP entries are intentionally excluded.

---

### Task 1: Confirm Launcher Interface Assumptions

**Files:**
- Create: `docs/superpowers/specs/2026-05-06-serena-cli-contract.md`

- [ ] **Step 1: Record the local CLI contracts**

Create the spec with the exact contracts the implementation will rely on:

```markdown
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

The response must contain the target project root. A dashboard response that
shows `Active Project: None` is unhealthy.

## Codex

Codex accepts per-run config overrides:

```bash
codex -c 'mcp_servers.serena.url="http://127.0.0.1:<port>/mcp"' <args...>
```

The wrapper must pass the override only to the child process. It must not write
this dynamic URL to `~/.codex/config.toml`.

## Claude

Claude accepts per-run MCP config:

```bash
claude --mcp-config=<json-file> <args...>
```

The wrapper writes a temporary JSON file:

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

The wrapper must not use `--strict-mcp-config` because user-configured MCP
servers should remain available.

Runtime verification must prove that a per-run `serena` entry overrides or
otherwise avoids a stale user-level `serena` entry. If Claude precedence is not
deterministic, the implementation must switch to an explicitly merged config
file or a collision-free runtime server name before enabling the wrapper.
```

- [ ] **Step 2: Verify the commands still expose the required options**

Run:

```bash
codex --help
codex mcp add --help
claude --help
serena start-mcp-server --help
serena context list
```

Expected:

- `codex --help` shows `-c, --config <key=value>`.
- `claude --help` shows `--mcp-config <configs...>`.
- `serena start-mcp-server --help` shows `--project`, `--transport`, `--port`, `--enable-web-dashboard`, and `--open-web-dashboard`.
- `serena context list` includes `codex` and `claude-code`.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-06-serena-cli-contract.md
git commit -m "docs: record serena launcher cli contract"
```

Note: `docs/superpowers/` is ignored in this repository today. If this plan stays local, skip the commit for this task.

---

### Task 2: Add Dotsync MCP Sanitizer

**Files:**
- Create: `lib/dotsync/apps/mcp_sanitizer.py`
- Test: `tests/apps/test_mcp_sanitizer.py`

- [ ] **Step 1: Write failing sanitizer tests**

```python
import json

from dotsync.apps.mcp_sanitizer import (
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
    result = sanitize_codex_config("""[mcp_servers.serena]
url = "http://127.0.0.1:9123/mcp"
""")

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

    assert filter_claude_mcp_servers(servers) == {
        "playwright": {"command": "npx"}
    }


def test_filter_claude_mcp_servers_keeps_remote_serena_url():
    servers = {"serena": {"type": "http", "url": "https://example.com/mcp"}}

    assert filter_claude_mcp_servers(servers) == servers


def test_filter_claude_mcp_servers_returns_deep_copy():
    servers = {"playwright": {"command": "npx"}}

    result = filter_claude_mcp_servers(servers)
    result["playwright"]["command"] = "changed"

    assert servers["playwright"]["command"] == "npx"


def test_filter_claude_mcp_servers_keeps_non_serena_local_http_url():
    servers = {"context7": {"type": "http", "url": "http://127.0.0.1:7777/mcp"}}

    assert filter_claude_mcp_servers(servers) == servers


def test_filter_claude_mcp_servers_keeps_command_based_serena_entry():
    servers = {"serena": {"command": "serena", "args": ["start-mcp-server"]}}

    assert filter_claude_mcp_servers(servers) == servers
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
.venv/bin/python3 -m pytest tests/apps/test_mcp_sanitizer.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'dotsync.apps.mcp_sanitizer'`.

- [ ] **Step 3: Implement the sanitizer**

```python
"""Filter dynamic local Serena MCP entries from synced agent config."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from collections.abc import Mapping
from urllib.parse import urlparse

SERENA_SERVER_NAME = "serena"


@dataclass(frozen=True)
class SanitizedText:
    text: str
    changed: bool


def _is_local_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def is_dynamic_serena_server(name: str, value: object) -> bool:
    if name.lower() != SERENA_SERVER_NAME:
        return False
    if isinstance(value, Mapping):
        return _is_local_url(value.get("url"))
    return False


def filter_claude_mcp_servers(servers: Mapping[str, object]) -> dict[str, object]:
    return {
        name: copy.deepcopy(value)
        for name, value in servers.items()
        if not is_dynamic_serena_server(name, value)
    }


_TABLE_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")


def sanitize_codex_config(text: str) -> SanitizedText:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    current_block: list[str] = []
    current_table: str | None = None
    dynamic_prefixes: set[str] = set()
    changed = False

    def flush() -> None:
        nonlocal current_block, current_table, changed
        if current_table is None:
            output.extend(current_block)
        elif _is_dynamic_serena_codex_table(current_table, current_block):
            dynamic_prefixes.add(current_table)
            changed = True
        elif any(current_table.startswith(prefix + ".") for prefix in dynamic_prefixes):
            changed = True
        else:
            output.extend(current_block)
        current_block = []
        current_table = None

    for line in lines:
        match = _TABLE_HEADER_RE.match(line)
        if match:
            flush()
            current_table = match.group(1).strip().strip('"')
            current_block.append(line)
        else:
            current_block.append(line)
    flush()
    result = "".join(output)
    return SanitizedText(text=result, changed=changed or result != text)


def sanitize_codex_config_text(text: str) -> str:
    return sanitize_codex_config(text).text


def _is_dynamic_serena_codex_table(table: str, block: list[str]) -> bool:
    if table != "mcp_servers.serena" and not table.startswith("mcp_servers.serena."):
        return False
    joined = "".join(block)
    return bool(re.search(r'url\s*=\s*["\']https?://(?:127\.0\.0\.1|localhost|\[::1\])', joined))
```

- [ ] **Step 4: Run sanitizer tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/apps/test_mcp_sanitizer.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/dotsync/apps/mcp_sanitizer.py tests/apps/test_mcp_sanitizer.py
git commit -m "fix: filter dynamic serena mcp entries"
```

---

### Task 3: Apply Sanitizer to Codex Sync

**Files:**
- Modify: `lib/dotsync/apps/codex.py`
- Test: `tests/apps/test_codex.py`

- [ ] **Step 1: Add failing Codex integration tests**

Append tests:

```python
def test_sync_from_excludes_dynamic_serena_mcp_from_config(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text(
        'model = "gpt-5.2"\n\n'
        '[mcp_servers.serena]\n'
        'url = "http://127.0.0.1:9123/mcp"\n\n'
        '[mcp_servers.playwright]\n'
        'command = "npx"\n'
    )
    target = tmp_path / "configs"
    target.mkdir()

    _codex_app().sync_from(target)

    stored = (target / "codex" / "config.toml").read_text()
    assert "mcp_servers.serena" not in stored
    assert "mcp_servers.playwright" in stored


def test_sync_to_excludes_dynamic_serena_mcp_from_local_config(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = "old"\n')
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text(
        'model = "gpt-5.2"\n\n'
        '[mcp_servers.serena]\n'
        'url = "http://127.0.0.1:9123/mcp"\n'
    )
    backup = tmp_path / "backup"
    backup.mkdir()

    _codex_app().sync_to(target, backup)

    assert "mcp_servers.serena" not in (cdir / "config.toml").read_text()


def test_codex_status_ignores_dynamic_serena_mcp_difference(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text(
        'model = "gpt-5.2"\n\n'
        '[mcp_servers.serena]\n'
        'url = "http://127.0.0.1:9123/mcp"\n'
    )
    target = tmp_path / "configs"
    stored = target / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text('model = "gpt-5.2"\n')

    assert _codex_app().status(target).state == "clean"


def test_plan_from_marks_update_when_stored_has_only_stale_serena_url(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text('model = "gpt-5.2"\n')
    stored = tmp_path / "configs" / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text(
        'model = "gpt-5.2"\n\n'
        '[mcp_servers.serena]\n'
        'url = "http://127.0.0.1:9123/mcp"\n'
    )

    plan = _codex_app().plan_from(tmp_path / "configs")

    assert {c.label: c.kind for c in plan.changes}["config.toml"] == "update"


def test_plan_to_marks_update_when_local_has_only_stale_serena_url(fake_home, tmp_path):
    cdir = _codex_dir(fake_home)
    cdir.mkdir()
    (cdir / "config.toml").write_text(
        'model = "gpt-5.2"\n\n'
        '[mcp_servers.serena]\n'
        'url = "http://127.0.0.1:9123/mcp"\n'
    )
    stored = tmp_path / "configs" / "codex"
    stored.mkdir(parents=True)
    (stored / "config.toml").write_text('model = "gpt-5.2"\n')

    plan = _codex_app().plan_to(tmp_path / "configs")

    assert {c.label: c.kind for c in plan.changes}["config.toml"] == "update"
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
.venv/bin/python3 -m pytest tests/apps/test_codex.py -v
```

Expected: the new tests fail because the app copies and compares raw config text.

- [ ] **Step 3: Modify CodexApp**

Add imports:

```python
from dotsync.apps.mcp_sanitizer import sanitize_codex_config, sanitize_codex_config_text
```

Add helpers:

```python
def _read_sanitized_config(self, path: Path) -> str:
    return sanitize_codex_config_text(path.read_text())

def _write_sanitized_config(self, source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(self._read_sanitized_config(source))
```

Use `_write_sanitized_config()` instead of `shutil.copy2()` for `config.toml` in `sync_from()` and `sync_to()`.

In `plan_from()` and `plan_to()`, replace the `plan_file_copy("config.toml", ...)` call with a local helper that compares sanitized text:

```python
def _plan_sanitized_config_copy(self, source: Path, dest: Path) -> Change:
    if not source.exists():
        return Change("config.toml", "missing-source", source, dest)
    planned = self._read_sanitized_config(source)
    if not dest.exists():
        return Change("config.toml", "create", source, dest)
    current_result = sanitize_codex_config(dest.read_text())
    current = current_result.text
    if current_result.changed:
        return Change("config.toml", "update", source, dest, "remove dynamic Serena MCP URL")
    return Change(
        "config.toml",
        "unchanged" if planned == current else "update",
        source,
        dest,
    )
```

In `status()`, compare sanitized local and stored config text before optional files:

```python
def _config_status(self, stored: Path) -> AppStatus:
    local = self._config_path()
    dest = stored / "config.toml"
    if not local.exists() or not dest.exists():
        return AppStatus(state="missing", details="config.toml")
    if self._read_sanitized_config(local) != sanitize_codex_config_text(dest.read_text()):
        return AppStatus(state="dirty", details="config.toml")
    return AppStatus(state="clean")
```

- [ ] **Step 4: Run Codex tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/apps/test_codex.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/dotsync/apps/codex.py tests/apps/test_codex.py
git commit -m "fix: exclude dynamic serena config from codex sync"
```

---

### Task 4: Apply Sanitizer to Claude Sync

**Files:**
- Modify: `lib/dotsync/apps/claude.py`
- Test: `tests/apps/test_claude.py`

- [ ] **Step 1: Add failing Claude integration tests**

Append tests:

```python
def test_sync_from_excludes_dynamic_serena_from_claude_mcp(fake_home, tmp_path):
    _make_local(
        fake_home,
        mcp={
            "serena": {"type": "http", "url": "http://127.0.0.1:9123/mcp"},
            "playwright": {"command": "npx"},
        },
        settings={"theme": "dark"},
    )
    target = tmp_path / "configs"
    target.mkdir()

    ClaudeApp().sync_from(target)

    stored = json.loads((target / "claude" / "mcp-servers.json").read_text())
    assert stored == {"playwright": {"command": "npx"}}


def test_sync_to_excludes_dynamic_serena_from_claude_json(fake_home, tmp_path):
    _make_local(
        fake_home,
        mcp={"local": {"command": "old"}},
        settings={"theme": "old"},
    )
    cdir = tmp_path / "configs" / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text("{}")
    (cdir / "plugins" / "installed_plugins.json").write_text('{"plugins": {}}')
    (cdir / "plugins" / "known_marketplaces.json").write_text("{}")
    (cdir / "mcp-servers.json").write_text(json.dumps({
        "serena": {"type": "http", "url": "http://127.0.0.1:9123/mcp"},
        "playwright": {"command": "npx"},
    }))

    with patch("dotsync.apps.claude.subprocess.run"):
        ClaudeApp().sync_to(tmp_path / "configs", tmp_path / "backup")

    local = json.loads((fake_home / ".claude.json").read_text())["mcpServers"]
    assert local == {"playwright": {"command": "npx"}}


def test_claude_status_ignores_dynamic_serena_mcp_difference(fake_home, tmp_path):
    _make_local(
        fake_home,
        mcp={"serena": {"type": "http", "url": "http://127.0.0.1:9123/mcp"}},
        settings={"theme": "dark"},
    )
    cdir = tmp_path / "configs" / "claude"
    (cdir / "plugins").mkdir(parents=True)
    (cdir / "settings.json").write_text('{"theme": "dark"}')
    (cdir / "plugins" / "installed_plugins.json").write_text('{"plugins": {}}')
    (cdir / "plugins" / "known_marketplaces.json").write_text("{}")
    (cdir / "mcp-servers.json").write_text("{}")

    assert ClaudeApp().status(tmp_path / "configs").state == "clean"


def test_plan_from_marks_update_when_stored_has_only_stale_serena_mcp(fake_home, tmp_path):
    _make_local(
        fake_home,
        mcp={},
        settings={"theme": "dark"},
    )
    cdir = tmp_path / "configs" / "claude"
    cdir.mkdir(parents=True)
    (cdir / "mcp-servers.json").write_text(json.dumps({
        "serena": {"type": "http", "url": "http://127.0.0.1:9123/mcp"}
    }))

    plan = ClaudeApp().plan_from(tmp_path / "configs")

    assert {c.label: c.kind for c in plan.changes}["mcp-servers.json"] == "update"


def test_plan_to_marks_update_when_local_has_only_stale_serena_mcp(fake_home, tmp_path):
    _make_local(
        fake_home,
        mcp={"serena": {"type": "http", "url": "http://127.0.0.1:9123/mcp"}},
        settings={"theme": "dark"},
    )
    cdir = tmp_path / "configs" / "claude"
    cdir.mkdir(parents=True)
    (cdir / "mcp-servers.json").write_text("{}")

    plan = ClaudeApp().plan_to(tmp_path / "configs")

    assert {c.label: c.kind for c in plan.changes}["mcp-servers.json"] == "update"
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
.venv/bin/python3 -m pytest tests/apps/test_claude.py -v
```

Expected: the new tests fail because raw `mcpServers` are copied and compared.

- [ ] **Step 3: Modify ClaudeApp**

Add import:

```python
from dotsync.apps.mcp_sanitizer import filter_claude_mcp_servers
```

Use the sanitizer in `_plan_mcp_from()`, `_plan_mcp_to()`, `sync_from()`, `_validate_sync_to_sources()`, and `status()`:

```python
def _sanitized_mcp_servers(self, servers: dict) -> dict:
    return filter_claude_mcp_servers(servers)
```

For `sync_from()`:

```python
cj = json.loads(self._claude_json().read_text())
servers = self._sanitized_mcp_servers(cj.get("mcpServers", {}))
(stored / "mcp-servers.json").write_text(
    json.dumps(servers, indent=2, ensure_ascii=False)
)
```

For `sync_to()`:

```python
cj["mcpServers"] = self._sanitized_mcp_servers(stored_mcp)
```

For `status()` compare:

```python
local_mcp = self._sanitized_mcp_servers(json.loads(local_cj.read_text()).get("mcpServers", {}))
stored_mcp_data = self._sanitized_mcp_servers(json.loads(stored_mcp.read_text()))
```

For `_plan_mcp_from()` and `_plan_mcp_to()`, the destination side must force
`kind="update"` when it contains a filtered dynamic Serena entry, even when the
sanitized source and sanitized destination are equal. This is required because
the CLI skips apps whose plan has no changes.

- [ ] **Step 4: Run Claude tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/apps/test_claude.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lib/dotsync/apps/claude.py tests/apps/test_claude.py
git commit -m "fix: exclude dynamic serena config from claude sync"
```

---

### Task 5: Implement Scope Paths

**Files:**
- Create: `tools/serena_mcp/paths.py`
- Test: `tests/tools/test_serena_paths.py`

- [ ] **Step 1: Write failing tests**

```python
from pathlib import Path

from tools.serena_mcp.paths import Scope, find_project_root, state_dir_for


def test_find_project_root_uses_git_root(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()

    assert find_project_root(nested) == repo.resolve()


def test_find_project_root_falls_back_to_cwd(tmp_path):
    assert find_project_root(tmp_path) == tmp_path.resolve()


def test_scope_key_separates_client_type(tmp_path):
    root = tmp_path.resolve()

    assert Scope(root, "codex").key != Scope(root, "claude").key


def test_state_dir_lives_under_project_serena_dir(tmp_path):
    scope = Scope(tmp_path.resolve(), "codex")

    assert state_dir_for(scope) == tmp_path.resolve() / ".serena" / "dotsync-mcp" / "codex"
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_paths.py -v
```

Expected: FAIL with missing module.

- [ ] **Step 3: Implement paths**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CLIENT_TYPES = {"codex", "claude"}


@dataclass(frozen=True)
class Scope:
    project_root: Path
    client_type: str

    def __post_init__(self) -> None:
        root = self.project_root.resolve()
        object.__setattr__(self, "project_root", root)
        if self.client_type not in CLIENT_TYPES:
            raise ValueError(f"unsupported client type: {self.client_type}")

    @property
    def key(self) -> str:
        return f"{self.project_root}::{self.client_type}"


def find_project_root(cwd: Path) -> Path:
    current = cwd.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def state_dir_for(scope: Scope) -> Path:
    return scope.project_root / ".serena" / "dotsync-mcp" / scope.client_type
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_paths.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/serena_mcp/__init__.py tools/serena_mcp/paths.py tests/tools/test_serena_paths.py
git commit -m "feat: add serena mcp scope paths"
```

---

### Task 6: Implement Registry and Lease Mutations

**Files:**
- Create: `tools/serena_mcp/registry.py`
- Test: `tests/tools/test_serena_registry.py`

- [ ] **Step 1: Write failing registry tests**

```python
import os
import time

from tools.serena_mcp.paths import Scope
from tools.serena_mcp.registry import (
    Lease,
    ServerRecord,
    locked_registry,
    remove_lease,
    touch_lease,
)


def test_registry_adds_and_removes_lease(tmp_path):
    scope = Scope(tmp_path, "codex")
    record = ServerRecord(
        server_pid=123,
        mcp_url="http://127.0.0.1:9000/mcp",
        dashboard_url="http://127.0.0.1:24000",
        project_root=str(tmp_path),
        client_type="codex",
        started_at=1.0,
        leases={},
    )

    with locked_registry(scope) as registry:
        registry.record = record
        touch_lease(registry, Lease("lease-a", os.getpid(), time.time()))

    with locked_registry(scope) as registry:
        assert "lease-a" in registry.record.leases
        remove_lease(registry, "lease-a")

    with locked_registry(scope) as registry:
        assert registry.record.leases == {}


def test_registry_preserves_unknown_missing_file(tmp_path):
    scope = Scope(tmp_path, "claude")

    with locked_registry(scope) as registry:
        assert registry.record is None


def test_registry_treats_corrupt_json_as_no_record(tmp_path):
    scope = Scope(tmp_path, "codex")
    path = tmp_path / ".serena" / "dotsync-mcp" / "codex" / "registry.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    with locked_registry(scope) as registry:
        assert registry.record is None
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_registry.py -v
```

Expected: FAIL with missing module.

- [ ] **Step 3: Implement registry**

Implement:

```python
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator
import fcntl

from tools.serena_mcp.paths import Scope, state_dir_for

REGISTRY_VERSION = 1


@dataclass
class Lease:
    lease_id: str
    launcher_pid: int
    heartbeat_at: float


@dataclass
class ServerRecord:
    server_pid: int
    mcp_url: str
    dashboard_url: str
    project_root: str
    client_type: str
    started_at: float
    leases: dict[str, Lease]
    watchdog_pid: int | None = None


@dataclass
class Registry:
    path: Path
    record: ServerRecord | None


def registry_path(scope: Scope) -> Path:
    return state_dir_for(scope) / "registry.json"


def lock_path(scope: Scope) -> Path:
    return state_dir_for(scope) / "registry.lock"


@contextmanager
def locked_registry(scope: Scope) -> Iterator[Registry]:
    state_dir_for(scope).mkdir(parents=True, exist_ok=True)
    lock_file = lock_path(scope)
    with lock_file.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        path = registry_path(scope)
        registry = Registry(path=path, record=_load_record(path))
        try:
            yield registry
            _write_record(path, registry.record)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_record(path: Path) -> ServerRecord | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if data.get("version") != REGISTRY_VERSION:
            return None
        record = data.get("record")
        if not record:
            return None
        leases = {
            lease_id: Lease(**lease)
            for lease_id, lease in record.get("leases", {}).items()
        }
        record["leases"] = leases
        return ServerRecord(**record)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def _write_record(path: Path, record: ServerRecord | None) -> None:
    if record is None:
        if path.exists():
            path.unlink()
        return
    payload = {"version": REGISTRY_VERSION, "record": asdict(record)}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    os.replace(tmp, path)


def touch_lease(registry: Registry, lease: Lease) -> None:
    if registry.record is None:
        raise RuntimeError("cannot add lease without server record")
    registry.record.leases[lease.lease_id] = lease


def remove_lease(registry: Registry, lease_id: str) -> None:
    if registry.record is not None:
        registry.record.leases.pop(lease_id, None)


def stale_lease_ids(registry: Registry, *, now: float, timeout_seconds: float) -> list[str]:
    if registry.record is None:
        return []
    return [
        lease_id
        for lease_id, lease in registry.record.leases.items()
        if now - lease.heartbeat_at > timeout_seconds
    ]
```

- [ ] **Step 4: Run registry tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_registry.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/serena_mcp/registry.py tests/tools/test_serena_registry.py
git commit -m "feat: add serena mcp registry"
```

---

### Task 7: Implement Health Checks

**Files:**
- Create: `tools/serena_mcp/health.py`
- Test: `tests/tools/test_serena_health.py`

- [ ] **Step 1: Write failing tests with fake HTTP servers**

```python
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from tools.serena_mcp.health import (
    dashboard_matches_project,
    http_endpoint_alive,
    pid_is_alive,
)


class Handler(BaseHTTPRequestHandler):
    body = b"ok"
    status = 200

    def do_GET(self):
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(type(self).body)

    def do_POST(self):
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, format, *args):
        pass


def serve(body: bytes, status: int = 200):
    class CustomHandler(Handler):
        pass
    CustomHandler.body = body
    CustomHandler.status = status
    server = HTTPServer(("127.0.0.1", 0), CustomHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_pid_is_alive_for_current_process():
    import os

    assert pid_is_alive(os.getpid()) is True


def test_http_endpoint_alive_posts_json():
    server = serve(b'{"jsonrpc":"2.0","id":1,"result":{}}')

    assert http_endpoint_alive(f"http://127.0.0.1:{server.server_port}/mcp")

    server.shutdown()


def test_dashboard_matches_project_by_path(tmp_path):
    body = json.dumps({"active_project": {"path": str(tmp_path)}}).encode()
    server = serve(body)

    assert dashboard_matches_project(
        f"http://127.0.0.1:{server.server_port}",
        tmp_path,
    )

    server.shutdown()


def test_dashboard_rejects_active_project_none(tmp_path):
    server = serve(b"Active Project: None")

    assert not dashboard_matches_project(
        f"http://127.0.0.1:{server.server_port}",
        tmp_path,
    )

    server.shutdown()


def test_dashboard_rejects_registered_project_without_active_project(tmp_path):
    body = json.dumps({
        "active_project": {"path": None},
        "registered_projects": [{"path": str(tmp_path)}],
    }).encode()
    server = serve(body)

    assert not dashboard_matches_project(
        f"http://127.0.0.1:{server.server_port}",
        tmp_path,
    )

    server.shutdown()
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_health.py -v
```

Expected: FAIL with missing module.

- [ ] **Step 3: Implement health checks**

Implement:

```python
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def http_endpoint_alive(url: str, *, timeout: float = 1.0) -> bool:
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "dotsync-serena-launcher", "version": "1"},
        },
    }).encode()
    req = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False


def dashboard_matches_project(dashboard_url: str, project_root: Path, *, timeout: float = 1.0) -> bool:
    url = dashboard_url.rstrip("/") + "/get_config_overview"
    try:
        with urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (OSError, URLError):
        return False
    if "Active Project: None" in body:
        return False
    expected = str(project_root.resolve())
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return expected in body
    active_project = data.get("active_project") if isinstance(data, dict) else None
    if not isinstance(active_project, dict):
        return False
    return active_project.get("path") == expected
```

- [ ] **Step 4: Run health tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_health.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/serena_mcp/health.py tests/tools/test_serena_health.py
git commit -m "feat: add serena mcp health checks"
```

---

### Task 8: Start or Reuse One Server Per Scope

**Files:**
- Create: `tools/serena_mcp/server.py`
- Test: `tests/tools/test_serena_server.py`

- [ ] **Step 1: Write failing tests**

```python
import os
import subprocess

from tools.serena_mcp.paths import Scope
from tools.serena_mcp.registry import Lease, ServerRecord, locked_registry
from tools.serena_mcp.server import ensure_server, serena_context_for


def test_ensure_server_reuses_healthy_record(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    record = ServerRecord(
        server_pid=os.getpid(),
        mcp_url="http://127.0.0.1:9000/mcp",
        dashboard_url="http://127.0.0.1:24000",
        project_root=str(tmp_path),
        client_type="codex",
        started_at=1.0,
        leases={},
    )
    with locked_registry(scope) as registry:
        registry.record = record

    monkeypatch.setattr("tools.serena_mcp.server.server_is_healthy", lambda r, s: True)
    popen_calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popen_calls.append(a) or None)

    lease = Lease("lease-a", os.getpid(), 10.0)

    assert ensure_server(scope, lease).mcp_url == record.mcp_url
    assert popen_calls == []
    with locked_registry(scope) as registry:
        assert "lease-a" in registry.record.leases


def test_ensure_server_replaces_unhealthy_record(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "claude")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=999999,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path),
            client_type="claude",
            started_at=1.0,
            leases={},
        )

    class Proc:
        pid = os.getpid()

    monkeypatch.setattr("tools.serena_mcp.server.server_is_healthy", lambda r, s: False)
    lease = Lease("lease-a", os.getpid(), 10.0)
    monkeypatch.setattr("tools.serena_mcp.server._find_free_port_with_host_lock", lambda: 9001)
    monkeypatch.setattr("tools.serena_mcp.server._start_serena_process", lambda scope, port: Proc())
    monkeypatch.setattr("tools.serena_mcp.server._discover_dashboard_url", lambda proc: "http://127.0.0.1:24001")
    monkeypatch.setattr("tools.serena_mcp.server._wait_until_healthy", lambda record, scope: None)
    monkeypatch.setattr("tools.serena_mcp.server.ensure_watchdog", lambda scope: None)

    record = ensure_server(scope, lease)

    assert record.mcp_url == "http://127.0.0.1:9001/mcp"
    with locked_registry(scope) as registry:
        assert registry.record.leases == {"lease-a": lease}


def test_serena_context_maps_claude_client_to_claude_code():
    assert serena_context_for("codex") == "codex"
    assert serena_context_for("claude") == "claude-code"
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_server.py -v
```

Expected: FAIL with missing module.

- [ ] **Step 3: Implement server startup**

Key implementation:

```python
from __future__ import annotations

import fcntl
import socket
import subprocess
import time
from pathlib import Path

from tools.serena_mcp.health import (
    dashboard_matches_project,
    http_endpoint_alive,
    pid_is_alive,
)
from tools.serena_mcp.paths import Scope
from tools.serena_mcp.registry import Lease, ServerRecord, locked_registry, touch_lease
from tools.serena_mcp.watchdog import ensure_watchdog


def ensure_server(scope: Scope, initial_lease: Lease) -> ServerRecord:
    with locked_registry(scope) as registry:
        if registry.record and server_is_healthy(registry.record, scope):
            touch_lease(registry, initial_lease)
            record = registry.record
            needs_watchdog = True
        else:
            if registry.record:
                _terminate_pid(registry.record.server_pid)
                registry.record = None

            record = _start_healthy_server(scope, initial_lease)
            registry.record = record
            needs_watchdog = True
    if needs_watchdog:
        ensure_watchdog(scope)
    return record


def server_is_healthy(record: ServerRecord, scope: Scope) -> bool:
    if record.project_root != str(scope.project_root):
        return False
    if record.client_type != scope.client_type:
        return False
    return (
        pid_is_alive(record.server_pid)
        and http_endpoint_alive(record.mcp_url)
        and dashboard_matches_project(record.dashboard_url, scope.project_root)
    )


def serena_context_for(client_type: str) -> str:
    if client_type == "codex":
        return "codex"
    if client_type == "claude":
        return "claude-code"
    raise ValueError(f"unsupported client type: {client_type}")


def _find_free_port_with_host_lock() -> int:
    lock_path = Path("/tmp/dotsync-serena-mcp-ports.lock")
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            return _find_free_port()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_healthy_server(scope: Scope, initial_lease: Lease) -> ServerRecord:
    last_error: Exception | None = None
    for _attempt in range(3):
        port = _find_free_port_with_host_lock()
        proc = _start_serena_process(scope, port)
        try:
            dashboard_url = _discover_dashboard_url(proc)
            record = ServerRecord(
                server_pid=proc.pid,
                mcp_url=f"http://127.0.0.1:{port}/mcp",
                dashboard_url=dashboard_url,
                project_root=str(scope.project_root),
                client_type=scope.client_type,
                started_at=time.time(),
                leases={initial_lease.lease_id: initial_lease},
            )
            _wait_until_healthy(record, scope)
            return record
        except RuntimeError as exc:
            last_error = exc
            _terminate_pid(proc.pid)
    raise RuntimeError(f"failed to start healthy Serena MCP server: {last_error}")


def _start_serena_process(scope: Scope, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            "serena",
            "start-mcp-server",
            "--project",
            str(scope.project_root),
            "--context",
            serena_context_for(scope.client_type),
            "--mode",
            "editing",
            "--mode",
            "interactive",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--enable-web-dashboard",
            "true",
            "--open-web-dashboard",
            "false",
        ],
        cwd=str(scope.project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
```

Implementation detail:

- `_discover_dashboard_url(proc)` reads process output for a dashboard URL. If Serena does not print one within the timeout, raise `RuntimeError`.
- `_discover_dashboard_url(proc)` stores only the URL origin, such as `http://127.0.0.1:<dashboard-port>`, even if Serena prints `/dashboard/index.html`.
- `_wait_until_healthy(record, scope)` polls `server_is_healthy()` for up to 20 seconds. If it fails, terminate the process and raise `RuntimeError`.
- Do not write the registry record until `_wait_until_healthy()` succeeds.
- Do not start the watchdog until the registry record includes `initial_lease`.
- If startup fails because the port was taken by a different scope after allocation, retry with a new port before failing the launcher.

- [ ] **Step 4: Run server tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_server.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/serena_mcp/server.py tests/tools/test_serena_server.py
git commit -m "feat: start shared serena mcp server per scope"
```

---

### Task 9: Implement Watchdog Shutdown

**Files:**
- Create: `tools/serena_mcp/watchdog.py`
- Test: `tests/tools/test_serena_watchdog.py`

- [ ] **Step 1: Write failing watchdog tests**

```python
import os
import time

from tools.serena_mcp.paths import Scope
from tools.serena_mcp.registry import Lease, ServerRecord, locked_registry
from tools.serena_mcp.watchdog import cleanup_once, ensure_watchdog, shutdown_if_no_leases


def test_cleanup_once_removes_stale_leases(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "codex")
    terminated = []
    monkeypatch.setattr("tools.serena_mcp.watchdog._terminate_pid", terminated.append)
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=12345,
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path),
            client_type="codex",
            started_at=time.time(),
            leases={"old": Lease("old", 999999, time.time() - 999)},
        )

    cleanup_once(scope, now=time.time(), lease_timeout_seconds=1)

    with locked_registry(scope) as registry:
        assert registry.record is None
    assert terminated == [12345]


def test_cleanup_once_keeps_active_lease(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "claude")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=os.getpid(),
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path),
            client_type="claude",
            started_at=time.time(),
            leases={"live": Lease("live", os.getpid(), time.time())},
        )

    cleanup_once(scope, now=time.time(), lease_timeout_seconds=60)

    with locked_registry(scope) as registry:
        assert "live" in registry.record.leases


def test_shutdown_if_no_leases_keeps_server_when_sibling_lease_exists(tmp_path):
    scope = Scope(tmp_path, "codex")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=os.getpid(),
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path),
            client_type="codex",
            started_at=time.time(),
            leases={"sibling": Lease("sibling", os.getpid(), time.time())},
        )

    assert shutdown_if_no_leases(scope) is True

    with locked_registry(scope) as registry:
        assert "sibling" in registry.record.leases


def test_ensure_watchdog_does_not_spawn_duplicate_when_pid_alive(monkeypatch, tmp_path):
    scope = Scope(tmp_path, "claude")
    with locked_registry(scope) as registry:
        registry.record = ServerRecord(
            server_pid=os.getpid(),
            mcp_url="http://127.0.0.1:9000/mcp",
            dashboard_url="http://127.0.0.1:24000",
            project_root=str(tmp_path),
            client_type="claude",
            started_at=time.time(),
            leases={"live": Lease("live", os.getpid(), time.time())},
            watchdog_pid=777,
        )
    monkeypatch.setattr("tools.serena_mcp.watchdog.pid_is_alive", lambda pid: True)
    calls = []
    monkeypatch.setattr("tools.serena_mcp.watchdog.subprocess.Popen", lambda *a, **k: calls.append(a))

    ensure_watchdog(scope)

    assert calls == []
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_watchdog.py -v
```

Expected: FAIL with missing module.

- [ ] **Step 3: Implement watchdog**

Implement:

```python
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from tools.serena_mcp.health import pid_is_alive
from tools.serena_mcp.paths import Scope
from tools.serena_mcp.registry import locked_registry, stale_lease_ids

HEARTBEAT_INTERVAL_SECONDS = 5.0
LEASE_TIMEOUT_SECONDS = 30.0


def cleanup_once(scope: Scope, *, now: float, lease_timeout_seconds: float) -> bool:
    with locked_registry(scope) as registry:
        if registry.record is None:
            return False
        for lease_id in stale_lease_ids(
            registry,
            now=now,
            timeout_seconds=lease_timeout_seconds,
        ):
            registry.record.leases.pop(lease_id, None)
        if registry.record.leases:
            return True
        _terminate_pid(registry.record.server_pid)
        registry.record = None
        return False


def shutdown_if_no_leases(scope: Scope) -> bool:
    with locked_registry(scope) as registry:
        if registry.record is None:
            return False
        if registry.record.leases:
            return True
        _terminate_pid(registry.record.server_pid)
        registry.record = None
        return False


def run_watchdog(scope: Scope) -> int:
    while True:
        keep_running = cleanup_once(
            scope,
            now=time.time(),
            lease_timeout_seconds=LEASE_TIMEOUT_SECONDS,
        )
        if not keep_running:
            return 0
        time.sleep(HEARTBEAT_INTERVAL_SECONDS)


def ensure_watchdog(scope: Scope) -> None:
    with locked_registry(scope) as registry:
        if registry.record is None:
            return
        if registry.record.watchdog_pid and pid_is_alive(registry.record.watchdog_pid):
            return
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.serena_mcp.watchdog",
                str(scope.project_root),
                scope.client_type,
            ],
            cwd=str(scope.project_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        registry.record.watchdog_pid = proc.pid


def _terminate_pid(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
```

Add module CLI:

```python
if __name__ == "__main__":
    from pathlib import Path

    raise SystemExit(run_watchdog(Scope(Path(sys.argv[1]), sys.argv[2])))
```

- [ ] **Step 4: Run watchdog tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_watchdog.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/serena_mcp/watchdog.py tests/tools/test_serena_watchdog.py
git commit -m "feat: stop serena server when leases expire"
```

---

### Task 10: Implement Agent Launcher

**Files:**
- Create: `tools/serena_agent_launcher.py`
- Test: `tests/tools/test_serena_launcher.py`

- [ ] **Step 1: Write failing launcher tests**

```python
import os
import subprocess
import pytest

from tools.serena_agent_launcher import build_child_command, find_real_binary, infer_client_type


def test_infer_client_type_from_program_name():
    assert infer_client_type("codex") == "codex"
    assert infer_client_type("/tmp/claude") == "claude"


def test_build_codex_command_injects_runtime_mcp_url():
    cmd, cleanup = build_child_command(
        client_type="codex",
        real_binary="/opt/homebrew/bin/codex",
        mcp_url="http://127.0.0.1:9000/mcp",
        child_args=["--help"],
    )

    assert cmd == [
        "/opt/homebrew/bin/codex",
        "-c",
        'mcp_servers.serena.url="http://127.0.0.1:9000/mcp"',
        "--help",
    ]
    cleanup()


def test_build_claude_command_uses_temp_mcp_config():
    cmd, cleanup = build_child_command(
        client_type="claude",
        real_binary="/opt/homebrew/bin/claude",
        mcp_url="http://127.0.0.1:9000/mcp",
        child_args=["--help"],
    )

    assert cmd[0] == "/opt/homebrew/bin/claude"
    assert cmd[1].startswith("--mcp-config=")
    config_path = cmd[1].split("=", 1)[1]
    assert os.path.exists(config_path)
    assert cmd[2:] == ["--help"]
    cleanup()
    assert not os.path.exists(config_path)


def test_build_claude_command_does_not_swallow_positional_args():
    cmd, cleanup = build_child_command(
        client_type="claude",
        real_binary="/opt/homebrew/bin/claude",
        mcp_url="http://127.0.0.1:9000/mcp",
        child_args=["mcp", "list"],
    )

    assert cmd[1].startswith("--mcp-config=")
    assert cmd[2:] == ["mcp", "list"]
    cleanup()


def test_find_real_binary_uses_env_override(monkeypatch, tmp_path):
    real = tmp_path / "codex-real"
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    monkeypatch.setenv("SERENA_REAL_CODEX", str(real))

    assert find_real_binary("codex") == str(real)


def test_find_real_binary_rejects_missing_env_override(monkeypatch):
    monkeypatch.setenv("SERENA_REAL_CODEX", "/missing/codex")

    with pytest.raises(RuntimeError, match="SERENA_REAL_CODEX"):
        find_real_binary("codex")
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_launcher.py -v
```

Expected: FAIL with missing module.

- [ ] **Step 3: Implement launcher**

Implement:

```python
from __future__ import annotations

import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from tools.serena_mcp.paths import Scope, find_project_root
from tools.serena_mcp.registry import Lease, locked_registry, remove_lease, touch_lease
from tools.serena_mcp.server import ensure_server
from tools.serena_mcp.watchdog import HEARTBEAT_INTERVAL_SECONDS, shutdown_if_no_leases


def infer_client_type(program_name: str) -> str:
    name = Path(program_name).name
    if name in {"codex", "claude"}:
        return name
    raise RuntimeError(f"unsupported wrapper name: {program_name}")


def find_real_binary(client_type: str) -> str:
    env_name = f"SERENA_REAL_{client_type.upper()}"
    override = os.environ.get(env_name)
    if override:
        path = Path(override)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise RuntimeError(f"{env_name} points to a non-executable path: {override}")
    current = Path(sys.argv[0]).resolve()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / client_type
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        if candidate.resolve() != current:
            return str(candidate)
    fallback = Path("/opt/homebrew/bin") / client_type
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    raise RuntimeError(f"could not find real {client_type} binary outside the wrapper")


def build_child_command(
    *,
    client_type: str,
    real_binary: str,
    mcp_url: str,
    child_args: list[str],
) -> tuple[list[str], Callable[[], None]]:
    if client_type == "codex":
        return [
            real_binary,
            "-c",
            f'mcp_servers.serena.url="{mcp_url}"',
            *child_args,
        ], lambda: None
    if client_type == "claude":
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        with handle:
            json.dump(
                {
                    "mcpServers": {
                        "serena": {
                            "type": "http",
                            "url": mcp_url,
                        }
                    }
                },
                handle,
            )
        path = handle.name

        def cleanup() -> None:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

        return [real_binary, f"--mcp-config={path}", *child_args], cleanup
    raise RuntimeError(f"unsupported client type: {client_type}")
```

Main flow:

```python
def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    client_type = infer_client_type(os.environ.get("SERENA_AGENT_CLIENT", sys.argv[0]))
    project_root = find_project_root(Path.cwd())
    scope = Scope(project_root, client_type)
    lease_id = str(uuid.uuid4())
    lease = Lease(lease_id, os.getpid(), time.time())
    record = ensure_server(scope, lease)
    stop = threading.Event()
    child = None
    cleanup = lambda: None
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(scope, lease_id, stop),
        daemon=True,
    )
    heartbeat.start()
    real_binary = find_real_binary(client_type)
    cmd, cleanup = build_child_command(
        client_type=client_type,
        real_binary=real_binary,
        mcp_url=record.mcp_url,
        child_args=args,
    )
    try:
        child = subprocess.Popen(cmd, cwd=str(project_root))

        def shutdown(signum=None, frame=None):
            stop.set()
            if child is not None and child.poll() is None:
                child.terminate()
            _remove_lease_and_shutdown_if_empty(scope, lease_id)

        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, shutdown)
        atexit.register(lambda: _remove_lease_and_shutdown_if_empty(scope, lease_id))
        return child.wait()
    finally:
        stop.set()
        cleanup()
        _remove_lease_and_shutdown_if_empty(scope, lease_id)
```

Helper behavior:

- `_find_real_binary("codex")` must prefer `SERENA_REAL_CODEX` when set, skip the wrapper path, then resolve `/opt/homebrew/bin/codex` or the first executable on PATH that is not this launcher wrapper.
- `_heartbeat_loop()` updates heartbeat every `HEARTBEAT_INTERVAL_SECONDS`.
- `_remove_lease_and_shutdown_if_empty()` removes only the current lease and calls `shutdown_if_no_leases(scope)`. It must not run stale-lease cleanup.
- Every step after `ensure_server(scope, lease)` must be inside `try/finally`, so `_find_real_binary()`, command construction, and `subprocess.Popen()` failures remove the lease immediately.

- [ ] **Step 4: Run launcher tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_launcher.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/serena_agent_launcher.py tests/tools/test_serena_launcher.py
git commit -m "feat: launch codex and claude with scoped serena mcp"
```

---

### Task 11: Add Wrapper Installer

**Files:**
- Create: `tools/install_serena_agent_wrappers.py`
- Test: `tests/tools/test_serena_wrapper_install.py`

- [ ] **Step 1: Write failing installer tests**

```python
from pathlib import Path

from tools.install_serena_agent_wrappers import install_wrappers


def test_install_wrappers_writes_codex_and_claude_scripts(tmp_path):
    launcher = tmp_path / "repo" / "tools" / "serena_agent_launcher.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("# launcher\n")
    bin_dir = tmp_path / "bin"

    install_wrappers(
        bin_dir=bin_dir,
        launcher_path=launcher,
        real_binaries={"codex": "/opt/homebrew/bin/codex", "claude": "/opt/homebrew/bin/claude"},
    )

    codex = bin_dir / "codex"
    claude = bin_dir / "claude"
    assert codex.exists()
    assert claude.exists()
    assert "SERENA_AGENT_CLIENT=codex" in codex.read_text()
    assert "SERENA_AGENT_CLIENT=claude" in claude.read_text()
    assert "SERENA_REAL_CODEX=/opt/homebrew/bin/codex" in codex.read_text()
    assert "SERENA_REAL_CLAUDE=/opt/homebrew/bin/claude" in claude.read_text()
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_wrapper_install.py -v
```

Expected: FAIL with missing module.

- [ ] **Step 3: Implement installer**

Implement:

```python
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def wrapper_text(client_type: str, launcher_path: Path, real_binary: str) -> str:
    env_name = f"SERENA_REAL_{client_type.upper()}"
    return (
        "#!/bin/sh\n"
        f"export SERENA_AGENT_CLIENT={client_type}\n"
        f"export {env_name}={real_binary}\n"
        f'exec python3 "{launcher_path}" "$@"\n'
    )


def install_wrappers(
    *,
    bin_dir: Path,
    launcher_path: Path,
    real_binaries: dict[str, str],
) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    for client_type in ("codex", "claude"):
        path = bin_dir / client_type
        path.write_text(wrapper_text(client_type, launcher_path.resolve(), real_binaries[client_type]))
        path.chmod(path.stat().st_mode | 0o755)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bin-dir", default="~/.local/bin")
    args = parser.parse_args()
    launcher = Path(__file__).resolve().parent / "serena_agent_launcher.py"
    real_binaries = {
        name: shutil.which(name) or f"/opt/homebrew/bin/{name}"
        for name in ("codex", "claude")
    }
    install_wrappers(
        bin_dir=Path(args.bin_dir).expanduser(),
        launcher_path=launcher,
        real_binaries=real_binaries,
    )
    print(f"installed codex/claude wrappers into {Path(args.bin_dir).expanduser()}")
    print("ensure this directory appears before /opt/homebrew/bin in PATH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run installer tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_wrapper_install.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/install_serena_agent_wrappers.py tests/tools/test_serena_wrapper_install.py
git commit -m "feat: add serena agent wrapper installer"
```

---

### Task 12: Add End-to-End Launcher Tests With Fake Serena

**Files:**
- Modify: `tests/tools/test_serena_launcher.py`

- [ ] **Step 1: Add fake child process tests**

Append tests that mock:

- `ensure_server(scope, lease)` returns `mcp_url="http://127.0.0.1:9000/mcp"`.
- `subprocess.Popen()` records child commands and returns a fake process with `wait() == 0`.
- Registry lease functions are allowed to write to a temp project root.

Expected assertions:

```python
def test_launcher_registers_and_removes_codex_lease(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()

    class Record:
        mcp_url = "http://127.0.0.1:9000/mcp"

    class Proc:
        def poll(self):
            return 0
        def wait(self):
            return 0
        def terminate(self):
            pass

    commands = []
    monkeypatch.setenv("SERENA_AGENT_CLIENT", "codex")
    monkeypatch.setattr("tools.serena_agent_launcher.ensure_server", lambda scope, lease: Record())
    monkeypatch.setattr("tools.serena_agent_launcher.find_real_binary", lambda client: "/opt/homebrew/bin/codex")
    monkeypatch.setattr("tools.serena_agent_launcher.subprocess.Popen", lambda cmd, cwd=None: commands.append(cmd) or Proc())

    from tools.serena_agent_launcher import main

    assert main(["--help"]) == 0
    assert commands[0][0] == "/opt/homebrew/bin/codex"
    assert commands[0][1] == "-c"
```

- [ ] **Step 2: Run launcher tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/tools/test_serena_launcher.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/tools/test_serena_launcher.py
git commit -m "test: cover serena launcher child execution"
```

---

### Task 13: Manual Runtime Verification

**Files:**
- No code changes unless verification finds a defect.

- [ ] **Step 1: Install wrappers into a temporary bin dir**

Run:

```bash
python3 tools/install_serena_agent_wrappers.py --bin-dir /tmp/dotsync-serena-bin
```

Expected:

```text
installed codex/claude wrappers into /tmp/dotsync-serena-bin
ensure this directory appears before /opt/homebrew/bin in PATH
```

- [ ] **Step 2: Run one Codex help command through the wrapper**

Run:

```bash
PATH="/tmp/dotsync-serena-bin:$PATH" SERENA_AGENT_CLIENT=codex codex --help
```

Expected:

- Command exits 0.
- `.serena/dotsync-mcp/codex/registry.json` is removed after command exit, because the only lease ended.
- No persistent `mcp_servers.serena.url` is written to `~/.codex/config.toml`.

- [ ] **Step 3: Run parallel fake long-lived sessions**

Use two terminal windows in the same project:

```bash
PATH="/tmp/dotsync-serena-bin:$PATH" codex
PATH="/tmp/dotsync-serena-bin:$PATH" codex
```

Expected while both are running:

- `.serena/dotsync-mcp/codex/registry.json` exists.
- It contains exactly one `server_pid`.
- It contains two leases.
- Serena dashboard active project displays the current project root.

- [ ] **Step 4: Verify Codex/Claude separation**

Run in four terminal windows in the same project:

```bash
PATH="/tmp/dotsync-serena-bin:$PATH" codex
PATH="/tmp/dotsync-serena-bin:$PATH" codex
PATH="/tmp/dotsync-serena-bin:$PATH" claude
PATH="/tmp/dotsync-serena-bin:$PATH" claude
```

Expected:

- `.serena/dotsync-mcp/codex/registry.json` has one codex server and two leases.
- `.serena/dotsync-mcp/claude/registry.json` has one claude server and two leases.
- Codex and Claude server PIDs are different.
- Claude server was started with Serena context `claude-code`.

- [ ] **Step 5: Verify normal shutdown**

Exit both Codex sessions.

Expected:

- Codex registry record is removed.
- Codex Serena server process exits.
- Claude registry and server remain alive if Claude sessions are still running.

- [ ] **Step 6: Verify force-quit shutdown**

Start one wrapped Codex session, then kill its launcher process with `kill -9`.

Expected:

- Lease remains briefly.
- Watchdog removes the stale lease within `LEASE_TIMEOUT_SECONDS`.
- Watchdog terminates the scope server.
- Registry record is removed.

- [ ] **Step 7: Verify Claude runtime MCP precedence**

Create a temporary Claude user config containing a dead user-level `serena`
MCP URL, then run a wrapped Claude command with the live per-run MCP config.

Expected:

- Claude uses the wrapper-provided live Serena MCP URL.
- The stale user-level `serena` entry does not win over the per-run config.
- If precedence is ambiguous, do not install the wrapper globally. Change the
  launcher to pass an explicitly merged MCP config or use a collision-free
  runtime MCP server name, then repeat this verification.

---

### Task 14: Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update English README**

Add to the Codex sync section:

```markdown
dotsync intentionally excludes dynamic local Serena MCP URLs from Codex config
sync. Serena ports are per-project runtime state and are injected by the local
launcher when Codex starts, so a copied `127.0.0.1:<port>` URL is treated as
machine-local state rather than user-authored config.
```

Add to the Claude sync section:

```markdown
dotsync also excludes dynamic local Serena MCP entries from Claude's
`mcpServers` sync. Other MCP servers are still synced normally.
```

- [ ] **Step 2: Update Korean README**

Add to the Korean Codex sync section:

```markdown
dotsync 는 Codex 설정을 sync 할 때 동적 로컬 Serena MCP URL 을 의도적으로
제외한다. Serena 포트는 프로젝트별 runtime state 이고 Codex 시작 시 로컬
launcher 가 주입하므로, 복사된 `127.0.0.1:<port>` URL 은 사용자가 작성한
설정이 아니라 머신 로컬 상태로 취급한다.
```

Add to the Korean Claude sync section:

```markdown
dotsync 는 Claude 의 `mcpServers` sync 에서도 동적 로컬 Serena MCP 항목을
제외한다. 다른 MCP 서버 설정은 계속 정상적으로 sync 된다.
```

- [ ] **Step 3: Run README-related tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/apps/test_codex.py tests/apps/test_claude.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document serena mcp sync exclusion"
```

---

### Task 15: Full Verification

**Files:**
- No code changes unless verification finds a defect.

- [ ] **Step 1: Run targeted tests**

```bash
.venv/bin/python3 -m pytest \
  tests/apps/test_mcp_sanitizer.py \
  tests/apps/test_codex.py \
  tests/apps/test_claude.py \
  tests/tools -v
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

```bash
.venv/bin/python3 -m pytest
```

Expected: PASS.

- [ ] **Step 3: Run CLI smoke tests**

```bash
PYTHONPATH=lib python3 -m dotsync --help
PYTHONPATH=lib python3 bin/dotsync --help
```

Expected: both commands exit 0 and print help.

- [ ] **Step 4: Inspect final diff**

```bash
git diff --stat
git diff -- lib/dotsync/apps/codex.py lib/dotsync/apps/claude.py lib/dotsync/apps/mcp_sanitizer.py
git diff -- tools tests README.md
```

Expected:

- `dotsync` changes are limited to sanitizer behavior and docs.
- Serena runtime coordination is isolated under `tools/`.
- No generated `.serena/dotsync-mcp/` state is tracked.

---

## Feasibility Review

This plan is feasible with the current repository because:

- Python 3.12 stdlib has the required primitives: `fcntl.flock`, `os.replace`, `urllib.request`, `subprocess.Popen`, `signal`, and `threading`.
- Codex exposes per-run config override with `-c`, so dynamic MCP URL injection does not need to write `~/.codex/config.toml`.
- Claude exposes `--mcp-config`, so dynamic MCP URL injection can be per-run and temporary.
- Serena exposes `start-mcp-server --project --transport streamable-http --port`, so the launcher can start a project-specific HTTP MCP server.
- The project already has preview planning abstractions and focused app tests, so sanitizer behavior can be covered without touching unrelated sync logic.

Agent review changes incorporated:

- Normal-exit cleanup is now separate from stale-lease cleanup, so one exiting session cannot delete sibling leases.
- Server creation now accepts an initial lease and publishes the server record only after health checks pass, with that lease already present.
- Dashboard health now requires `active_project.path` to match the project root exactly.
- Claude runtime injection now uses `--mcp-config=<path>` to avoid variadic option parsing.
- Serena context is mapped from client type, with `claude -> claude-code`.
- The sanitizer plan now forces `update` when the destination contains a filtered stale Serena entry, even if sanitized contents match.
- Codex sanitizer tests now cover child tables, non-Serena local HTTP MCP URLs, and exact preservation of unrelated config text.

Main risks:

- Dashboard port discovery depends on Serena startup output. `_discover_dashboard_url()` must normalize a logged `/dashboard/index.html` URL down to its origin before health checks. If output is unstable, replace discovery with a deterministic Serena config setting or a dashboard-port CLI option if Serena adds one.
- Streamable HTTP MCP initialization may require headers or protocol details different from the test probe. Keep `http_endpoint_alive()` small and adjust it against a real Serena server before merging.
- `build_child_command()` assumes Codex config key `mcp_servers.serena.url`. Verify once with a real wrapped Codex session before installing wrappers globally.
- Claude per-run `serena` precedence over stale user config must be manually verified. If it is not deterministic, the launcher must pass a merged config or a collision-free server name.
- Host-global port races are reduced with a port allocation lock and must still be protected by startup retry on bind failure.
- Force-quit cleanup is bounded by heartbeat timeout, not instantaneous. The expected behavior is server termination within `LEASE_TIMEOUT_SECONDS`.

Out of scope:

- Changing Serena itself.
- Syncing wrapper installation through dotsync automatically.
- Supporting Linux or Windows.
- Adding a global daemon shared across all projects.
