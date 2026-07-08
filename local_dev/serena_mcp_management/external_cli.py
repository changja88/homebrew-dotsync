"""Resolve external CLI commands (serena, graphify) without assuming PATH.

serena/graphify는 셸 PATH 밖에 있을 수 있다: uv tool 설치는 ~/.local/bin에
바이너리를 두고, serena는 uvx로만 도는 머신도 있다. 각 resolver는 실행 가능한
argv prefix를 돌려주고, 못 찾으면 None을 돌려준다.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

SERENA_UVX_SPEC = "git+https://github.com/oraios/serena"

WhichFn = Callable[[str], "str | None"]


def _direct_binary(name: str, which: WhichFn, home: Path | None) -> str | None:
    found = which(name)
    if found:
        return found
    candidate = (home or Path.home()) / ".local" / "bin" / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def serena_server_command(
    *, which: WhichFn = shutil.which, home: Path | None = None
) -> list[str] | None:
    """Argv prefix for the long-running `serena start-mcp-server` process.

    직접 바이너리만 허용한다. uvx 래퍼는 실제 서버를 자식 프로세스로 두므로
    registry의 server_pid가 래퍼를 가리키게 되고, same-scope orphan cleanup이
    자기 서버의 자식을 죽인다.
    """
    binary = _direct_binary("serena", which, home)
    return [binary] if binary else None


def serena_oneshot_command(
    *, which: WhichFn = shutil.which, home: Path | None = None
) -> list[str] | None:
    """Argv prefix for run-and-wait serena commands (e.g. `project create`)."""
    binary = _direct_binary("serena", which, home)
    if binary:
        return [binary]
    uvx = which("uvx")
    if uvx:
        return [uvx, "--from", SERENA_UVX_SPEC, "serena"]
    return None


def graphify_command(
    *, which: WhichFn = shutil.which, home: Path | None = None
) -> list[str] | None:
    """Argv prefix for graphify commands.

    uvx fallback을 두지 않는다: graphify는 자기 절대 경로를 프로젝트 hook
    (.codex/hooks.json 등)에 기록하므로, 휘발성 uvx 캐시 경로가 박히면 캐시
    정리 후 hook이 죽는다. 없으면 `uv tool install graphifyy`로 설치한다.
    """
    binary = _direct_binary("graphify", which, home)
    return [binary] if binary else None


def dotsync_command(
    *, which: WhichFn = shutil.which, home: Path | None = None
) -> list[str] | None:
    """Argv prefix for the `dotsync` CLI, or None when it isn't installed.

    The launcher shells out to `dotsync claude account ...` for the optional
    account-select preflight. dotsync is a separate product (Homebrew-installed);
    a None here means "skip the account step" — never an error.
    """
    binary = _direct_binary("dotsync", which, home)
    return [binary] if binary else None


def serena_install_command(*, which: WhichFn = shutil.which) -> list[str] | None:
    """`uv tool install` argv that persistently installs the serena CLI."""
    uv = which("uv")
    if uv is None:
        return None
    return [uv, "tool", "install", "--from", SERENA_UVX_SPEC, "serena-agent"]


def graphify_install_command(*, which: WhichFn = shutil.which) -> list[str] | None:
    """`uv tool install` argv that persistently installs the graphify CLI."""
    uv = which("uv")
    if uv is None:
        return None
    return [uv, "tool", "install", "graphifyy"]


def homebrew_node_command(*, brew_node: Path | None = None) -> list[str] | None:
    """Resolve node at the homebrew path the claude-hud statusLine hardcodes.

    The HUD statusLine execs `/opt/homebrew/bin/node` literally, so a node
    elsewhere on PATH (e.g. nvm) does not make it work — only a node at this
    exact path does. Returns None when that path has no executable node.
    """
    candidate = brew_node if brew_node is not None else Path("/opt/homebrew/bin/node")
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return [str(candidate)]
    return None


def node_command(
    *, which: WhichFn = shutil.which, brew_node: Path | None = None
) -> list[str] | None:
    """Resolve any node binary for npx/node-based plugins and MCP servers.

    Prefer a PATH hit; otherwise fall back to the homebrew path. Returns None
    when node is unavailable anywhere. (For the statusLine's hardcoded path
    specifically, use `homebrew_node_command`.)
    """
    found = which("node")
    if found:
        return [found]
    return homebrew_node_command(brew_node=brew_node)


def node_install_command(*, which: WhichFn = shutil.which) -> list[str] | None:
    """`brew install node` argv. None when Homebrew is unavailable.

    Specifically Homebrew node: its binary lands at `/opt/homebrew/bin/node`,
    exactly where the claude-hud statusLine looks, so npx-based MCP servers and
    the HUD start working without rewriting the hardcoded path.
    """
    brew = which("brew")
    if brew is None:
        return None
    return [brew, "install", "node"]
