"""Install a managed hook that seeds future linked worktrees."""
from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import textwrap
from pathlib import Path


START_MARKER = "# worktree-setup-hook-start"
END_MARKER = "# worktree-setup-hook-end"
_ZERO_SHA = "0" * 40


class WorktreeSetupError(RuntimeError):
    """Raised when a managed worktree hook cannot be changed safely."""


def _run_git_path(project_root: Path, *args: str) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    stdout = getattr(result, "stdout", None)
    returncode = getattr(result, "returncode", 1)
    if returncode != 0 or not isinstance(stdout, str):
        return None
    value = stdout.strip()
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return Path(os.path.abspath(candidate))


def _git_directories(project_root: Path) -> tuple[Path, Path] | None:
    git_dir = _run_git_path(project_root, "--git-dir")
    common_dir = _run_git_path(project_root, "--git-common-dir")
    if git_dir is None or common_dir is None:
        return None
    try:
        return git_dir.resolve(), common_dir.resolve()
    except (OSError, RuntimeError):
        return None


def _post_checkout_hook(project_root: Path) -> Path | None:
    return _run_git_path(project_root, "--git-path", "hooks/post-checkout")


def _tool_is_opted_in(project_root: Path) -> bool:
    return (
        (project_root / ".serena" / "project.yml").is_file()
        or (project_root / "graphify-out" / "graph.json").is_file()
    )


def worktree_setup_available(project_root: Path) -> bool:
    """Return whether this primary checkout may own a setup hook."""
    root = project_root.resolve()
    if not _tool_is_opted_in(root):
        return False
    directories = _git_directories(root)
    return directories is not None and directories[0] == directories[1]


def _owned_block_bounds(text: str) -> tuple[int, int] | None:
    lines = text.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == START_MARKER]
    ends = [index for index, line in enumerate(lines) if line.rstrip("\r\n") == END_MARKER]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise WorktreeSetupError("worktree setup managed marker is malformed or duplicated")
    return starts[0], ends[0]


def render_worktree_setup_block() -> str:
    """Return the marker-owned POSIX shell block installed in post-checkout."""
    return textwrap.dedent(
        f"""\
        {START_MARKER}
        # Managed by the dotsync agent launcher. Seed only the first checkout
        # created by `git worktree add`; ordinary branch/file checkouts are inert.
        _dotsync_wt_copy_file() {{
          if [ ! -f "$1" ] || [ -e "$2" ] || [ -L "$2" ]; then
            return 0
          fi
          cp -c -p "$1" "$2" 2>/dev/null || cp -p "$1" "$2"
        }}

        _dotsync_wt_prepare_dir() {{
          if [ -L "$1" ]; then
            return 1
          fi
          if [ ! -e "$1" ]; then
            mkdir -p "$1" || return 1
          fi
          [ -d "$1" ] && [ ! -L "$1" ]
        }}

        if [ "$3" = "1" ] && [ "$1" = "{_ZERO_SHA}" ]; then
          _DOTSYNC_WT_GIT_DIR=$(git rev-parse --git-dir 2>/dev/null) || _DOTSYNC_WT_GIT_DIR=""
          case "$_DOTSYNC_WT_GIT_DIR" in
            */worktrees/*)
              _DOTSYNC_WT_TARGET=$(git rev-parse --show-toplevel 2>/dev/null) || _DOTSYNC_WT_TARGET=""
              _DOTSYNC_WT_COMMON_DIR=$(git rev-parse --git-common-dir 2>/dev/null) || _DOTSYNC_WT_COMMON_DIR=""
              if [ -n "$_DOTSYNC_WT_TARGET" ] && [ -n "$_DOTSYNC_WT_COMMON_DIR" ]; then
                case "$_DOTSYNC_WT_COMMON_DIR" in
                  /*) ;;
                  *) _DOTSYNC_WT_COMMON_DIR="$_DOTSYNC_WT_TARGET/$_DOTSYNC_WT_COMMON_DIR" ;;
                esac
                _DOTSYNC_WT_COMMON_DIR=$(cd "$_DOTSYNC_WT_COMMON_DIR" 2>/dev/null && pwd -P) || _DOTSYNC_WT_COMMON_DIR=""
                _DOTSYNC_WT_PRIMARY=$(dirname "$_DOTSYNC_WT_COMMON_DIR")
                if [ -n "$_DOTSYNC_WT_COMMON_DIR" ] && [ "$_DOTSYNC_WT_PRIMARY" != "$_DOTSYNC_WT_TARGET" ]; then
                  _dotsync_wt_copy_file "$_DOTSYNC_WT_PRIMARY/.env.local" "$_DOTSYNC_WT_TARGET/.env.local"

                  if [ -f "$_DOTSYNC_WT_PRIMARY/.serena/project.yml" ] && \
                     _dotsync_wt_prepare_dir "$_DOTSYNC_WT_TARGET/.serena"; then
                    _dotsync_wt_copy_file \
                      "$_DOTSYNC_WT_PRIMARY/.serena/project.yml" \
                      "$_DOTSYNC_WT_TARGET/.serena/project.yml"
                    _dotsync_wt_copy_file \
                      "$_DOTSYNC_WT_PRIMARY/.serena/project.local.yml" \
                      "$_DOTSYNC_WT_TARGET/.serena/project.local.yml"
                    if [ -d "$_DOTSYNC_WT_PRIMARY/.serena/memories" ] && \
                       [ ! -e "$_DOTSYNC_WT_TARGET/.serena/memories" ] && \
                       [ ! -L "$_DOTSYNC_WT_TARGET/.serena/memories" ]; then
                      ln -s "$_DOTSYNC_WT_PRIMARY/.serena/memories" \
                        "$_DOTSYNC_WT_TARGET/.serena/memories"
                    fi
                  fi

                  if [ -f "$_DOTSYNC_WT_PRIMARY/graphify-out/graph.json" ] && \
                     _dotsync_wt_prepare_dir "$_DOTSYNC_WT_TARGET/graphify-out"; then
                    _dotsync_wt_copy_file \
                      "$_DOTSYNC_WT_PRIMARY/graphify-out/graph.json" \
                      "$_DOTSYNC_WT_TARGET/graphify-out/graph.json"
                    _dotsync_wt_copy_file \
                      "$_DOTSYNC_WT_PRIMARY/graphify-out/GRAPH_REPORT.md" \
                      "$_DOTSYNC_WT_TARGET/graphify-out/GRAPH_REPORT.md"
                    _dotsync_wt_copy_file \
                      "$_DOTSYNC_WT_PRIMARY/graphify-out/.graphify_python" \
                      "$_DOTSYNC_WT_TARGET/graphify-out/.graphify_python"
                    if [ -f "$_DOTSYNC_WT_PRIMARY/graphify-out/reflections/LESSONS.md" ] && \
                       _dotsync_wt_prepare_dir \
                      "$_DOTSYNC_WT_TARGET/graphify-out/reflections"; then
                      _dotsync_wt_copy_file \
                        "$_DOTSYNC_WT_PRIMARY/graphify-out/reflections/LESSONS.md" \
                        "$_DOTSYNC_WT_TARGET/graphify-out/reflections/LESSONS.md"
                    fi
                    if [ -f "$_DOTSYNC_WT_PRIMARY/.codex/hooks.json" ] && \
                       _dotsync_wt_prepare_dir "$_DOTSYNC_WT_TARGET/.codex"; then
                      _dotsync_wt_copy_file \
                        "$_DOTSYNC_WT_PRIMARY/.codex/hooks.json" \
                        "$_DOTSYNC_WT_TARGET/.codex/hooks.json"
                    fi
                    if [ -f "$_DOTSYNC_WT_PRIMARY/.claude/settings.json" ] && \
                       _dotsync_wt_prepare_dir "$_DOTSYNC_WT_TARGET/.claude"; then
                      _dotsync_wt_copy_file \
                        "$_DOTSYNC_WT_PRIMARY/.claude/settings.json" \
                        "$_DOTSYNC_WT_TARGET/.claude/settings.json"
                    fi
                  fi
                fi
              fi
              ;;
          esac
        fi
        {END_MARKER}
        """
    )


def worktree_setup_installed(project_root: Path) -> bool:
    """Return whether the exact current managed block is executable."""
    hook = _post_checkout_hook(project_root.resolve())
    if hook is None or hook.is_symlink() or not hook.is_file():
        return False
    try:
        text = hook.read_text()
        bounds = _owned_block_bounds(text)
        mode = stat.S_IMODE(hook.stat().st_mode)
    except (OSError, UnicodeError, WorktreeSetupError):
        return False
    if bounds is None or not mode & stat.S_IXUSR:
        return False
    lines = text.splitlines(keepends=True)
    start, end = bounds
    return "".join(lines[start : end + 1]) == render_worktree_setup_block()


def _updated_hook_text(existing: str, block: str) -> str:
    bounds = _owned_block_bounds(existing)
    if bounds is not None:
        lines = existing.splitlines(keepends=True)
        start, end = bounds
        return "".join([*lines[:start], block, *lines[end + 1 :]])

    if not existing:
        return f"#!/bin/sh\n\n{block}"
    if existing.startswith("#!"):
        newline = existing.find("\n")
        if newline == -1:
            return f"{existing}\n\n{block}"
        return f"{existing[:newline + 1]}\n{block}{existing[newline + 1:]}"
    return f"#!/bin/sh\n\n{block}{existing}"


def _atomic_write_hook(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw_fd = -1
    temporary: Path | None = None
    try:
        raw_fd, raw_name = tempfile.mkstemp(
            prefix=f".{path.name}.dotsync-",
            dir=path.parent,
        )
        temporary = Path(raw_name)
        os.fchmod(raw_fd, mode)
        stream = os.fdopen(raw_fd, "w", encoding="utf-8", newline="")
        raw_fd = -1
        with stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise WorktreeSetupError(f"could not install worktree setup hook: {exc}") from exc
    finally:
        if raw_fd >= 0:
            try:
                os.close(raw_fd)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def install_worktree_setup_hook(project_root: Path) -> Path:
    """Install or refresh the marker-owned post-checkout block."""
    root = project_root.resolve()
    directories = _git_directories(root)
    if directories is None or directories[0] != directories[1]:
        raise WorktreeSetupError("worktree setup requires a primary Git checkout")
    hook = _post_checkout_hook(root)
    if hook is None:
        raise WorktreeSetupError("could not resolve the post-checkout hook path")
    if hook.is_symlink():
        raise WorktreeSetupError("refusing to replace a symlink post-checkout hook")
    if hook.exists() and not hook.is_file():
        raise WorktreeSetupError("post-checkout hook is not a regular file")

    try:
        existing = hook.read_text() if hook.exists() else ""
        existing_mode = stat.S_IMODE(hook.stat().st_mode) if hook.exists() else 0o755
        updated = _updated_hook_text(existing, render_worktree_setup_block())
    except (OSError, UnicodeError) as exc:
        raise WorktreeSetupError(f"could not read post-checkout hook: {exc}") from exc

    mode = existing_mode | stat.S_IXUSR
    if updated == existing and stat.S_IMODE(hook.stat().st_mode) == mode:
        return hook
    _atomic_write_hook(hook, updated, mode)
    return hook
