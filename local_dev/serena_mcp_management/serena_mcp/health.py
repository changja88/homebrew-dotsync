"""Health checks for scoped Serena MCP servers."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def pid_is_alive(pid: int) -> bool:
    """Return true if a process id currently exists."""

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
    """Probe a streamable HTTP MCP endpoint with initialize."""

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
    request = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False


def dashboard_matches_project(
    dashboard_url: str,
    project_root: Path,
    *,
    timeout: float = 1.0,
) -> bool:
    """Return true when Serena dashboard reports this active project."""

    url = normalize_dashboard_url(dashboard_url) + "/get_config_overview"
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
        return expected in body and "Active Project: None" not in body
    active_project = data.get("active_project") if isinstance(data, dict) else None
    if not isinstance(active_project, dict):
        return False
    return active_project.get("path") == expected


def normalize_dashboard_url(url: str) -> str:
    """Normalize a Serena dashboard URL to scheme, host, and port."""

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"invalid dashboard URL: {url}")
    return f"{parsed.scheme}://{parsed.netloc}"
