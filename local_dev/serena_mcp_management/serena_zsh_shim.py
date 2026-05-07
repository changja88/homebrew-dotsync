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

_dotsync_agent_graphify_initialized() {
  local project_root="$1"

  [[ -f "$project_root/graphify-out/graph.json" ]]
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
  local proj_dir="$HOME/.claude/projects/${PWD//\//-}"
  local total=0 deleted=0 kept=0 mem_deleted=0

  if [[ -d "$proj_dir" ]]; then
    total=$(find "$proj_dir" -maxdepth 1 -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ')
    deleted=$(find "$proj_dir" -maxdepth 1 -name '*.jsonl' -mtime +3 2>/dev/null | wc -l | tr -d ' ')
  fi
  kept=$(( total - deleted ))

  local mem_dir="$proj_dir/memory"
  [[ -d "$mem_dir" ]] && mem_deleted=$(find "$mem_dir" -type f 2>/dev/null | wc -l | tr -d ' ')

  local cleanup_phrase="${deleted} to delete . ${kept} to keep"
  local memory_phrase="${mem_deleted} files to reset"
  local serena_status="managed"
  _dotsync_agent_serena_project_available "$project_root" || serena_status="missing"
  local graphify_status="installed"
  if ! _dotsync_agent_graphify_available; then
    graphify_status="missing"
  elif ! _dotsync_agent_graphify_initialized "$project_root"; then
    graphify_status="not-initialized"
  fi

  SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE="$cleanup_phrase" \
  SERENA_AGENT_PREFLIGHT_MEMORY_VALUE="$memory_phrase" \
  SERENA_AGENT_PREFLIGHT_SERENA_STATUS="$serena_status" \
  SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS="$graphify_status" \
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
  local codex_home="${CODEX_HOME:-$HOME/.codex}"
  local sessions_dir="$codex_home/sessions"
  local mem_dir="$codex_home/memories"
  local total=0 deleted=0 kept=0 mem_deleted=0
  local can_scan_sessions=1

  if [[ -d "$sessions_dir" ]]; then
    if ! command -v jq >/dev/null 2>&1; then
      can_scan_sessions=0
    else
      local f=""
      while IFS= read -r -d '' f; do
        if jq -e --arg cwd "$PWD" \
          'select(.type == "session_meta" and .payload.cwd == $cwd)' \
          "$f" >/dev/null 2>&1; then
          ((++total))
          if [[ $(find "$f" -maxdepth 0 -mtime +3 -print 2>/dev/null) == "$f" ]]; then
            ((++deleted))
          fi
        fi
      done < <(find "$sessions_dir" -type f -name '*.jsonl' -print0 2>/dev/null)
    fi
  fi
  kept=$(( total - deleted ))

  [[ -d "$mem_dir" ]] && mem_deleted=$(find "$mem_dir" -type f 2>/dev/null | wc -l | tr -d ' ')

  local cleanup_phrase=""
  if (( ! can_scan_sessions )); then
    cleanup_phrase="scan skipped (jq missing)"
  else
    cleanup_phrase="${deleted} to delete . ${kept} to keep"
  fi
  local memory_phrase="${mem_deleted} files to reset"
  local serena_status="managed"
  _dotsync_agent_serena_project_available "$project_root" || serena_status="missing"
  local graphify_status="installed"
  if ! _dotsync_agent_graphify_available; then
    graphify_status="missing"
  elif ! _dotsync_agent_graphify_initialized "$project_root"; then
    graphify_status="not-initialized"
  fi

  SERENA_AGENT_PREFLIGHT_CLEANUP_VALUE="$cleanup_phrase" \
  SERENA_AGENT_PREFLIGHT_MEMORY_VALUE="$memory_phrase" \
  SERENA_AGENT_PREFLIGHT_SERENA_STATUS="$serena_status" \
  SERENA_AGENT_PREFLIGHT_GRAPHIFY_STATUS="$graphify_status" \
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
    parser.add_argument("--rc-path", type=Path, default=Path.home() / ".zshrc", help="zsh rc file to update")
    args = parser.parse_args(argv)
    launcher_path = Path(__file__).resolve().with_name("serena_agent_launcher.py")
    python_executable = default_python_executable()
    codex_binary = default_binary_path("codex")
    claude_binary = default_binary_path("claude")
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
