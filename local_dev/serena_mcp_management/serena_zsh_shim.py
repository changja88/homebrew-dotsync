"""Render zsh functions that preserve agent cleanup and delegate Serena lifecycle."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

START_MARKER = "# >>> dotsync serena agent launcher >>>"
END_MARKER = "# <<< dotsync serena agent launcher <<<"

PYTHON_CANDIDATES = (
    Path("/opt/homebrew/bin/python3.12"),
    Path("/opt/homebrew/bin/python3.13"),
    Path("/usr/local/bin/python3.12"),
    Path("/usr/local/bin/python3.13"),
    Path("/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"),
    Path("/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13"),
)


def render_zsh_shim(
    *,
    launcher_path: Path,
    python_executable: Path,
    codex_binary: Path,
    claude_binary: Path,
) -> str:
    """Return the managed zsh snippet for Serena-aware agent launches."""

    template = r'''# >>> dotsync serena agent launcher >>>
SERENA_AGENT_LAUNCHER="__LAUNCHER_PATH__"
SERENA_AGENT_PYTHON="__PYTHON_EXECUTABLE__"

# serena/graphify CLI는 uv tool bin($HOME/.local/bin)에 설치된다 — launcher와
# 그 아래 agent 세션이 같은 CLI를 보도록 PATH를 보강한다.
export PATH="$HOME/.local/bin:$PATH"

_dotsync_agent_marker_present() {
  local dir="$1"
  local marker=""

  for marker in AGENTS.md CLAUDE.md pyproject.toml package.json Cargo.toml go.mod Gemfile Makefile; do
    [[ -e "$dir/$marker" ]] && return 0
  done
  [[ -e "$dir/.git" ]]
}

_dotsync_agent_project_root() {
  local start="${1:-$PWD}"
  local dir="${start:a}"
  local marker_root=""

  [[ -f "$dir" ]] && dir="${dir:h}"
  while true; do
    if [[ -f "$dir/.serena/project.yml" ]]; then
      print -r -- "$dir"
      return 0
    fi
    [[ "$dir" == "/" ]] && break
    dir="${dir:h}"
  done

  dir="${start:a}"
  [[ -f "$dir" ]] && dir="${dir:h}"
  while true; do
    if _dotsync_agent_marker_present "$dir"; then
      marker_root="$dir"
      break
    fi
    [[ "$dir" == "/" ]] && break
    dir="${dir:h}"
  done

  if [[ -n "$marker_root" ]]; then
    print -r -- "$marker_root"
  else
    print -r -- "${start:a}"
  fi
}

_dotsync_agent_should_manage_launch() {
  local interactive="$1"
  local arg_count="$2"

  [[ "$interactive" == "1" && "$arg_count" == "0" ]]
}

_dotsync_agent_serena_project_available() {
  local project_root="$1"

  [[ -f "$project_root/.serena/project.yml" ]]
}

_dotsync_agent_graphify_available() {
  command -v graphify >/dev/null 2>&1
}

_dotsync_agent_graphify_global_installed() {
  local client="$1"
  case "$client" in
    claude) [[ -d "$HOME/.claude/skills/graphify" ]] ;;
    *)      [[ -d "$HOME/.codex/skills/graphify" ]] ;;
  esac
}

_dotsync_agent_graphify_graph_built() {
  local project_root="$1"
  [[ -f "$project_root/graphify-out/graph.json" ]]
}

_dotsync_agent_graphify_integration_installed() {
  local project_root="$1"
  local client="$2"
  local md_file=""
  local cfg_file=""
  case "$client" in
    claude)
      md_file="$project_root/CLAUDE.md"
      cfg_file="$project_root/.claude/settings.json"
      ;;
    *)
      md_file="$project_root/AGENTS.md"
      cfg_file="$project_root/.codex/hooks.json"
      ;;
  esac
  [[ -f "$md_file" && -f "$cfg_file" ]] || return 1
  grep -q "graphify-out" "$md_file" 2>/dev/null || return 1
  case "$client" in
    claude)
      grep -q "graphify-out" "$cfg_file" 2>/dev/null || return 1
      ;;
    *)
      grep -q "graphify" "$cfg_file" 2>/dev/null || return 1
      grep -q "hook-check" "$cfg_file" 2>/dev/null || return 1
      ;;
  esac
  return 0
}

_dotsync_agent_graphify_hooks_installed() {
  local project_root="$1"
  local hooks_path=""
  local hooks_dir=""
  local pc=""
  local pco=""

  hooks_path="$(git -C "$project_root" config core.hooksPath 2>/dev/null || true)"
  if [[ -n "$hooks_path" ]]; then
    case "$hooks_path" in
      "~"|"~/"*) hooks_dir="${hooks_path/#\~/$HOME}" ;;
      /*)        hooks_dir="$hooks_path" ;;
      *)         hooks_dir="$project_root/$hooks_path" ;;
    esac
  else
    hooks_dir="$project_root/.git/hooks"
  fi

  pc="$hooks_dir/post-commit"
  pco="$hooks_dir/post-checkout"

  [[ -f "$pc" && -f "$pco" ]] || return 1
  grep -q "graphify-hook-start" "$pc" 2>/dev/null || return 1
  grep -q "graphify-checkout-hook-start" "$pco" 2>/dev/null || return 1
  return 0
}

claude() {
  local interactive=0
  [[ -t 0 && -t 1 ]] && interactive=1
  local real_binary="__CLAUDE_BINARY__"

  if ! _dotsync_agent_should_manage_launch "$interactive" "$#"; then
    "$real_binary" "$@"
    return $?
  fi

  local project_root="$(_dotsync_agent_project_root "$PWD")"
  local serena_status="managed"
  _dotsync_agent_serena_project_available "$project_root" || serena_status="missing"
  local graphify_global_status="installed"
  _dotsync_agent_graphify_global_installed claude || graphify_global_status="missing"
  local graphify_graph_status="built"
  _dotsync_agent_graphify_graph_built "$project_root" || graphify_graph_status="missing"
  local graphify_integration_status="installed"
  _dotsync_agent_graphify_integration_installed "$project_root" claude || graphify_integration_status="missing"
  local graphify_hook_status="installed"
  _dotsync_agent_graphify_hooks_installed "$project_root" || graphify_hook_status="missing"

  SERENA_AGENT_PREFLIGHT_SERENA_STATUS="$serena_status" \
  SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS="$graphify_global_status" \
  SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS="$graphify_graph_status" \
  SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS="$graphify_integration_status" \
  SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS="$graphify_hook_status" \
  SERENA_AGENT_CLIENT=claude \
  SERENA_AGENT_QUIET=1 \
  SERENA_AGENT_INTERACTIVE="$interactive" \
  SERENA_AGENT_CLEAR_BEFORE_CHILD="$interactive" \
  SERENA_AGENT_PROJECT_ROOT="$project_root" \
  SERENA_REAL_CLAUDE=__CLAUDE_BINARY__ \
  "$SERENA_AGENT_PYTHON" "$SERENA_AGENT_LAUNCHER" "$@"
}

codex() {
  local interactive=0
  [[ -t 0 && -t 1 ]] && interactive=1
  local real_binary="__CODEX_BINARY__"

  if ! _dotsync_agent_should_manage_launch "$interactive" "$#"; then
    "$real_binary" "$@"
    return $?
  fi

  local project_root="$(_dotsync_agent_project_root "$PWD")"
  local serena_status="managed"
  _dotsync_agent_serena_project_available "$project_root" || serena_status="missing"
  local graphify_global_status="installed"
  _dotsync_agent_graphify_global_installed codex || graphify_global_status="missing"
  local graphify_graph_status="built"
  _dotsync_agent_graphify_graph_built "$project_root" || graphify_graph_status="missing"
  local graphify_integration_status="installed"
  _dotsync_agent_graphify_integration_installed "$project_root" codex || graphify_integration_status="missing"
  local graphify_hook_status="installed"
  _dotsync_agent_graphify_hooks_installed "$project_root" || graphify_hook_status="missing"

  SERENA_AGENT_PREFLIGHT_SERENA_STATUS="$serena_status" \
  SERENA_AGENT_PREFLIGHT_GRAPHIFY_GLOBAL_STATUS="$graphify_global_status" \
  SERENA_AGENT_PREFLIGHT_GRAPHIFY_GRAPH_STATUS="$graphify_graph_status" \
  SERENA_AGENT_PREFLIGHT_GRAPHIFY_INTEGRATION_STATUS="$graphify_integration_status" \
  SERENA_AGENT_PREFLIGHT_GRAPHIFY_HOOK_STATUS="$graphify_hook_status" \
  SERENA_AGENT_CLIENT=codex \
  SERENA_AGENT_QUIET=1 \
  SERENA_AGENT_INTERACTIVE="$interactive" \
  SERENA_AGENT_CLEAR_BEFORE_CHILD="$interactive" \
  SERENA_AGENT_PROJECT_ROOT="$project_root" \
  SERENA_REAL_CODEX=__CODEX_BINARY__ \
  "$SERENA_AGENT_PYTHON" "$SERENA_AGENT_LAUNCHER" "$@"
}
# <<< dotsync serena agent launcher <<<
'''
    return (
        template.replace("__LAUNCHER_PATH__", str(launcher_path))
        .replace("__PYTHON_EXECUTABLE__", str(python_executable))
        .replace("__CODEX_BINARY__", str(codex_binary))
        .replace("__CLAUDE_BINARY__", str(claude_binary))
    )


def default_binary_path(name: str) -> Path:
    """Return the default real agent binary path for a generated shim."""

    found = shutil.which(name)
    if found:
        return Path(found)
    return Path("/opt/homebrew/bin") / name


def default_python_executable() -> Path:
    """Return a Python executable that can run the launcher modules."""

    if sys.version_info >= (3, 12):
        return Path(sys.executable)
    for path in PYTHON_CANDIDATES:
        if path.is_file():
            return path
    return Path(sys.executable)


def install_zshrc_shim(
    *,
    rc_path: Path,
    launcher_path: Path,
    python_executable: Path,
    codex_binary: Path,
    claude_binary: Path,
) -> Path:
    """Install the generated Serena zsh shim into a shell rc file."""

    snippet = render_zsh_shim(
        launcher_path=launcher_path,
        python_executable=python_executable,
        codex_binary=codex_binary,
        claude_binary=claude_binary,
    )
    original = rc_path.read_text() if rc_path.exists() else ""
    backup_path = rc_path.with_name(f"{rc_path.name}.dotsync-serena.bak")
    backup_path.write_text(original)
    rc_path.write_text(_replace_managed_block(original, snippet))
    return backup_path


def uninstall_zshrc_shim(*, rc_path: Path) -> Path | None:
    """Remove the managed Serena agent block from a zsh rc file.

    Idempotent: returns ``None`` and does nothing if the rc file does not
    exist. Otherwise always writes a ``.dotsync-serena.bak`` snapshot of the
    original file (even when the block is absent) so the operation always
    leaves a recoverable backup, mirroring ``install_zshrc_shim``.
    """

    if not rc_path.exists():
        return None
    original = rc_path.read_text()
    backup_path = rc_path.with_name(f"{rc_path.name}.dotsync-serena.bak")
    backup_path.write_text(original)
    rc_path.write_text(_strip_managed_block(original))
    return backup_path


def _strip_managed_block(text: str) -> str:
    start = text.find(START_MARKER)
    end = text.find(END_MARKER)
    if start == -1 or end == -1 or start >= end:
        return text
    end += len(END_MARKER)
    # Swallow a trailing newline from the block so we don't leave a blank
    # line where the block used to be.
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[:start] + text[end:]


def _replace_managed_block(text: str, snippet: str) -> str:
    start = text.find(START_MARKER)
    end = text.find(END_MARKER)
    if start != -1 and end != -1 and start < end:
        end += len(END_MARKER)
        return f"{text[:start]}{snippet.rstrip()}{text[end:]}"

    if not text:
        return f"{snippet.rstrip()}\n"

    prefix = text
    if not prefix.endswith("\n"):
        prefix += "\n"
    return f"{prefix}\n{snippet.rstrip()}\n"


def main(argv: list[str] | None = None) -> int:
    """Print or install the zsh shim for the local launcher."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--install-zshrc", action="store_true", help="replace the managed block in a zsh rc file")
    parser.add_argument("--uninstall-zshrc", action="store_true", help="remove the managed block from a zsh rc file")
    parser.add_argument("--rc-path", type=Path, default=Path.home() / ".zshrc", help="zsh rc file to update")
    parser.add_argument(
        "--python-executable",
        type=Path,
        default=None,
        help="Python interpreter to record in SERENA_AGENT_PYTHON (default: auto-detect)",
    )
    args = parser.parse_args(argv)
    launcher_path = Path(__file__).resolve().with_name("serena_agent_launcher.py")
    python_executable = args.python_executable or default_python_executable()
    codex_binary = default_binary_path("codex")
    claude_binary = default_binary_path("claude")
    if args.uninstall_zshrc:
        rc = args.rc_path.expanduser()
        backup_path = uninstall_zshrc_shim(rc_path=rc)
        if backup_path is None:
            print(f"no zsh rc file at {rc}; nothing to remove")
        else:
            print(f"removed Serena zsh shim from {rc}")
            print(f"backup written to {backup_path}")
        return 0
    if args.install_zshrc:
        backup_path = install_zshrc_shim(
            rc_path=args.rc_path.expanduser(),
            launcher_path=launcher_path,
            python_executable=python_executable,
            codex_binary=codex_binary,
            claude_binary=claude_binary,
        )
        print(f"installed Serena zsh shim into {args.rc_path.expanduser()}")
        print(f"backup written to {backup_path}")
        return 0

    print(
        render_zsh_shim(
            launcher_path=launcher_path,
            python_executable=python_executable,
            codex_binary=codex_binary,
            claude_binary=claude_binary,
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
