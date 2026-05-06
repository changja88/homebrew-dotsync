"""Filter dynamic local Serena MCP entries from synced agent config."""
from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

SERENA_SERVER_NAME = "serena"


@dataclass(frozen=True)
class SanitizedText:
    """Sanitized text plus whether anything was removed."""

    text: str
    changed: bool


@dataclass(frozen=True)
class SanitizedMapping:
    """Sanitized mapping plus whether any entry was removed."""

    value: dict[str, object]
    changed: bool


def sanitize_codex_config_text(text: str) -> str:
    """Return Codex config text without dynamic local Serena MCP tables."""

    return sanitize_codex_config(text).text


def sanitize_codex_config(text: str) -> SanitizedText:
    """Remove dynamic local Serena MCP tables while preserving other text."""

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
        elif any(current_table.startswith(f"{prefix}.") for prefix in dynamic_prefixes):
            changed = True
        else:
            output.extend(current_block)
        current_block = []
        current_table = None

    for line in lines:
        table = _table_name(line)
        if table is not None:
            flush()
            current_table = table
        current_block.append(line)

    flush()
    result = "".join(output)
    if changed:
        while result.endswith("\n\n"):
            result = result[:-1]
    return SanitizedText(text=result, changed=changed or result != text)


def filter_claude_mcp_servers(servers: Mapping[str, object]) -> SanitizedMapping:
    """Return Claude MCP servers without dynamic local Serena HTTP entries."""

    result: dict[str, object] = {}
    changed = False
    for name, value in servers.items():
        if is_dynamic_serena_server(name, value):
            changed = True
            continue
        result[name] = copy.deepcopy(value)
    return SanitizedMapping(value=result, changed=changed)


def is_dynamic_serena_server(name: str, value: object) -> bool:
    """Return true when a server is the per-run local Serena HTTP endpoint."""

    if name.lower() != SERENA_SERVER_NAME:
        return False
    return isinstance(value, Mapping) and _is_local_url(value.get("url"))


def _is_local_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


_TABLE_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")
_LOCAL_URL_RE = re.compile(
    r"""url\s*=\s*["']https?://(?:127\.0\.0\.1|localhost|\[::1\])"""
)


def _table_name(line: str) -> str | None:
    match = _TABLE_HEADER_RE.match(line)
    if not match:
        return None
    return ".".join(_split_toml_dotted_key(match.group(1).strip()))


def _split_toml_dotted_key(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for char in value:
        if quote:
            if escaped:
                current.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            else:
                current.append(char)
        elif char in {"'", '"'}:
            quote = char
        elif char == ".":
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return parts


def _is_dynamic_serena_codex_table(table: str, block: list[str]) -> bool:
    if table != "mcp_servers.serena" and not table.startswith("mcp_servers.serena."):
        return False
    return bool(_LOCAL_URL_RE.search("".join(block)))
