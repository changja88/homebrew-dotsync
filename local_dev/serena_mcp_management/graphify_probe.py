"""Probe what Graphify has installed, using Graphify's own identity markers.

The launcher turns these probes into setup prompts, so a false "missing" is
not a cosmetic bug: it re-asks "set it up?" on every launch, and answering Yes
re-runs an idempotent ``graphify ... install`` that changes nothing, so the
question never goes away. Earlier probes lived in the zsh shim and matched the
*text* of the hook commands Graphify writes (``graphify-out/graph.json``,
``_GFY_GITDIR`` ...). Graphify's hook payload is not a public interface — it
was reworded several times without a release note — and each rewording broke
one probe.

This module relies only on the markers Graphify itself uses to find and
replace its own files (``graphify/install.py`` and ``graphify/hooks.py``):

- the ``## graphify`` section header in ``CLAUDE.md`` / ``AGENTS.md``
- a ``hooks.PreToolUse`` entry mentioning ``graphify`` in
  ``.claude/settings.json`` / ``.codex/hooks.json``
- ``# graphify-hook-start`` / ``# graphify-checkout-hook-start`` blocks in the
  git ``post-commit`` / ``post-checkout`` hooks

Two runnability checks are kept on top of the markers because "registered" is
not "working": an absolute executable pinned into a hook must still exist
(uv tool reinstalls leave dangling paths), and an installed git hook must carry
Graphify's linked-worktree guard (``--git-common-dir`` inside the marker
block) because pre-0.9.14 hooks rebuilt a rogue graph inside linked worktrees.
Both are deliberately loose: a token, never the exact lines.

Graphify has no ``claude status`` command and ``hook status`` prints text with
exit code 0 either way, so there is nothing to delegate to yet.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from local_dev.serena_mcp_management.external_cli import graphify_command

STATUS_INSTALLED = "installed"
STATUS_MISSING = "missing"
GRAPH_BUILT = "built"

SECTION_MARKER = "## graphify"
POST_COMMIT_MARKER = "# graphify-hook-start"
POST_COMMIT_MARKER_END = "# graphify-hook-end"
POST_CHECKOUT_MARKER = "# graphify-checkout-hook-start"
POST_CHECKOUT_MARKER_END = "# graphify-checkout-hook-end"
WORKTREE_GUARD_TOKEN = "--git-common-dir"

ENVIRON_KEYS: dict[str, str] = {
    "cli": "SERENA_AGENT_PREFLIGHT_GRAPHIFY_CLI_STATUS",
    "global_skill": "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS",
    "graph": "SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS",
    "integration": "SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS",
    "hook": "SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS",
}

_INTEGRATION_FILES: dict[str, tuple[str, Path]] = {
    "claude": ("CLAUDE.md", Path(".claude") / "settings.json"),
    "codex": ("AGENTS.md", Path(".codex") / "hooks.json"),
}

# An absolute path mentioning graphify that starts a shell word: line start,
# whitespace, `=`, or a quote. A `/` continuing `${HOME}` or a relative path
# like graphify-out/.graphify_python is not a pin.
_PIN_RE = re.compile(r"""(?:^|[\s='"])(/[^\s"']*graphif[^\s"']*)""", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class GraphifyStatuses:
    """The five preflight rows the launcher renders and prompts from."""

    cli: str
    global_skill: str
    graph: str
    integration: str
    hook: str

    def as_environ(self) -> dict[str, str]:
        return {
            ENVIRON_KEYS["cli"]: self.cli,
            ENVIRON_KEYS["global_skill"]: self.global_skill,
            ENVIRON_KEYS["graph"]: self.graph,
            ENVIRON_KEYS["integration"]: self.integration,
            ENVIRON_KEYS["hook"]: self.hook,
        }


# ------------------------------------------------------------------- simple


def cli_status(*, command: Callable[[], list[str] | None] = graphify_command) -> str:
    return STATUS_INSTALLED if command() is not None else STATUS_MISSING


def global_skill_status(client: str, *, home: Path | None = None) -> str:
    """User-scope skill: ``<claude config dir>/skills/graphify`` or ``~/.codex/...``."""
    base = home if home is not None else Path.home()
    if client == "claude":
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        claude_dir = Path(config_dir).expanduser() if config_dir else base / ".claude"
        skill_dir = claude_dir / "skills" / "graphify"
    else:
        skill_dir = base / ".codex" / "skills" / "graphify"
    return STATUS_INSTALLED if skill_dir.is_dir() else STATUS_MISSING


def graph_status(project_root: Path) -> str:
    graph = project_root / "graphify-out" / "graph.json"
    return GRAPH_BUILT if graph.is_file() else STATUS_MISSING


# -------------------------------------------------------------- integration


def integration_files(project_root: Path, client: str) -> tuple[Path, Path]:
    """(instruction markdown, hook config) that ``graphify <client> install`` writes."""
    md_name, cfg_rel = _INTEGRATION_FILES.get(client, _INTEGRATION_FILES["codex"])
    return project_root / md_name, project_root / cfg_rel


def section_registered(md_path: Path) -> bool:
    """Graphify replaces or appends the block under this exact header."""
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(line.strip() == SECTION_MARKER for line in text.splitlines())


def graphify_hook_entries(cfg_path: Path) -> list[dict]:
    """``hooks.PreToolUse`` entries Graphify would recognise as its own.

    Mirrors the filter ``graphify/install.py`` applies before re-registering:
    the entry mentions ``graphify`` anywhere. The command wording is
    deliberately not inspected.
    """
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    hooks = data.get("hooks") if isinstance(data, dict) else None
    pre_tool = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    if not isinstance(pre_tool, list):
        return []
    return [
        entry for entry in pre_tool
        if isinstance(entry, dict) and "graphify" in json.dumps(entry)
    ]


def _hook_commands(entries: Iterable[dict]) -> list[str]:
    commands: list[str] = []
    for entry in entries:
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                commands.append(hook["command"])
    return commands


def command_executable_present(command: str) -> bool:
    """False only when the command starts with an absolute path that is gone.

    Bare commands (``graphify hook-guard ...`` from a ``--project`` install)
    resolve through PATH per machine, and inline shell has nothing to verify.
    """
    try:
        words = shlex.split(command)
    except ValueError:
        return True
    if not words or not words[0].startswith("/"):
        return True
    return Path(words[0]).exists()


def integration_status(project_root: Path, client: str) -> str:
    md_path, cfg_path = integration_files(project_root, client)
    if not section_registered(md_path):
        return STATUS_MISSING
    entries = graphify_hook_entries(cfg_path)
    if not entries:
        return STATUS_MISSING
    if not all(command_executable_present(c) for c in _hook_commands(entries)):
        return STATUS_MISSING
    return STATUS_INSTALLED


# ---------------------------------------------------------------- git hooks


def git_hooks_dir(project_root: Path) -> Path | None:
    """Where Graphify installs hooks: git's own answer, Husky-adjusted.

    ``git rev-parse --git-path hooks`` honours ``core.hooksPath`` and linked
    worktrees; Husky 9 points it at ``.husky/_`` while user hooks (and
    Graphify's) live in the parent — the same mapping ``graphify/hooks.py``
    applies on install.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "--git-path", "hooks"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    if not raw or any(ch in raw for ch in ("\n", "\r", "\x00")):
        return None
    hooks_dir = Path(raw)
    if not hooks_dir.is_absolute():
        hooks_dir = project_root / hooks_dir
    if hooks_dir.name == "_":
        hooks_dir = hooks_dir.parent
    return hooks_dir


def _marker_block(text: str, start: str, end: str) -> str | None:
    begin = text.find(start)
    if begin < 0:
        return None
    stop = text.find(end, begin)
    return text[begin:stop] if stop >= 0 else text[begin:]


def pins_runnable(text: str) -> bool:
    """Every absolute graphify path written into the file must still exist."""
    return all(Path(pin).exists() for pin in _PIN_RE.findall(text))


def _hook_file_installed(path: Path, start: str, end: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    block = _marker_block(text, start, end)
    if block is None or WORKTREE_GUARD_TOKEN not in block:
        return False
    return pins_runnable(text)


def hook_files(project_root: Path) -> tuple[Path, Path] | None:
    hooks_dir = git_hooks_dir(project_root)
    if hooks_dir is None:
        return None
    return hooks_dir / "post-commit", hooks_dir / "post-checkout"


def hook_status(project_root: Path) -> str:
    files = hook_files(project_root)
    if files is None:
        return STATUS_MISSING
    post_commit, post_checkout = files
    installed = (
        _hook_file_installed(post_commit, POST_COMMIT_MARKER, POST_COMMIT_MARKER_END)
        and _hook_file_installed(
            post_checkout, POST_CHECKOUT_MARKER, POST_CHECKOUT_MARKER_END
        )
    )
    return STATUS_INSTALLED if installed else STATUS_MISSING


# -------------------------------------------------------------- fingerprints


def fingerprint_files(paths: Iterable[Path]) -> str:
    """Stable digest of the named files' contents (absent files count too)."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<absent>")
        digest.update(b"\0")
    return digest.hexdigest()


def integration_fingerprint(project_root: Path, client: str) -> str:
    return fingerprint_files(integration_files(project_root, client))


def hook_fingerprint(project_root: Path) -> str:
    files = hook_files(project_root)
    return fingerprint_files(files if files is not None else ())


# --------------------------------------------------------------- aggregate


def probe(
    project_root: Path,
    client: str,
    *,
    home: Path | None = None,
    command: Callable[[], list[str] | None] = graphify_command,
) -> GraphifyStatuses:
    return GraphifyStatuses(
        cli=cli_status(command=command),
        global_skill=global_skill_status(client, home=home),
        graph=graph_status(project_root),
        integration=integration_status(project_root, client),
        hook=hook_status(project_root),
    )


def populate_environ(statuses: GraphifyStatuses, environ=os.environ) -> None:
    """Export the statuses the launcher reads, keeping any value already set.

    Preset values win so tests (and a shim that still exports them) can inject
    a scenario without the probe overriding it.
    """
    for key, value in statuses.as_environ().items():
        environ.setdefault(key, value)
