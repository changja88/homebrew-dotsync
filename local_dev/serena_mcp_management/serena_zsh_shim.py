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

_dotsync_agent_create_serena_project() {
  local project_root="$1"

  command -v serena >/dev/null 2>&1 || return 2
  serena project create "$project_root" < <(yes "")
}

_dotsync_agent_ensure_serena() {
  local client="$1"
  local project_root="$2"
  local reply=""

  _dotsync_agent_serena_project_available "$project_root" && return 0

  print -nP "  %F{226}!%f serena    missing   . initialize this project? [y/N] "
  read -r reply
  case "$reply" in
    y|Y|yes|YES)
      if ! _dotsync_agent_create_serena_project "$project_root"; then
        print -P "  %F{226}!%f serena    skipped   . project setup failed; launching ${client} without Serena project config"
        return 1
      fi
      if ! _dotsync_agent_serena_project_available "$project_root"; then
        print -P "  %F{226}!%f serena    skipped   . project config still missing; launching ${client} without Serena project config"
        return 1
      fi
      return 0
      ;;
    *)
      print -P "  %F{226}!%f serena    skipped   . launching ${client} without Serena project config"
      return 1
      ;;
  esac
}

_dotsync_agent_short_path() {
  local path="$1"
  [[ "$path" == "$HOME"* ]] && path="~${path#$HOME}"
  print -r -- "$path"
}

_dotsync_agent_dashes() {
  local n="$1"
  local out=""
  local i
  for ((i=0; i<n; i++)); do
    out+="-"
  done
  print -n -- "$out"
}

_dotsync_agent_stream_row() {
  local accent="$1"
  local state="$2"
  local label="$3"
  local value="$4"
  local marker="o"
  local marker_color="244"

  case "$state" in
    active)  marker=">"; marker_color="$accent" ;;
    done)    marker="*"; marker_color="046" ;;
    warn)    marker="!"; marker_color="226" ;;
  esac

  print -nP "  %F{${marker_color}}${marker}%f "
  print -nP "%F{244}"
  printf "%-10s" "$label"
  print -nP "%f  "
  print -P -- "$value"
}

_dotsync_agent_preflight() {
  local accent="$1"
  local client="$2"
  local project_root="$3"
  local session_line="$4"
  local memory_line="$5"
  local context="$6"
  local serena_state="${7:-active}"
  local serena_phrase="${8:-managed by scoped launcher}"
  local workspace="$(_dotsync_agent_short_path "$project_root")"
  local cleanup_phrase="$session_line"
  local memory_phrase="$memory_line"

  # Existing preflight contract: show sessions delete/keep and memory reset.
  if [[ "$session_line" == "sessions scan=skip"* ]]; then
    cleanup_phrase="scan skipped (jq missing)"
  elif [[ "$session_line" =~ "delete=([0-9]+) keep=([0-9]+)" ]]; then
    cleanup_phrase="${match[1]} to delete %F{244}.%f ${match[2]} to keep"
  fi

  if [[ "$memory_line" =~ "files=([0-9]+)" ]]; then
    memory_phrase="${match[1]} files to reset"
  fi

  local rule="$(_dotsync_agent_dashes 60)"
  print
  print -P "  %B%F{${accent}}${client}%f%b %F{244}.%f preflight                         %F{226}pending%f"
  print -P "  %F{244}${rule}%f"
  _dotsync_agent_stream_row "$accent" active  "workspace" "$workspace"
  _dotsync_agent_stream_row "$accent" "$serena_state" "serena" "$serena_phrase"
  _dotsync_agent_stream_row "$accent" pending "context"   "$context"
  _dotsync_agent_stream_row "$accent" pending "cleanup"   "$cleanup_phrase"
  _dotsync_agent_stream_row "$accent" pending "memory"    "$memory_phrase"
  print -P "  %F{244}${rule}%f"
  print -nP "  %F{${accent}}>%f %B%F{${accent}}Enter%f%b to run  %F{244}.%f  %B%F{${accent}}Ctrl-C%f%b to abort "

  local reply=""
  read -r reply
  print
}

_dotsync_agent_event() {
  local client="$1"
  local phase="$2"
  local state="$3"
  local detail="$4"
  local accent="081"
  local state_color="046"
  [[ "$client" == "claude" ]] && accent="141"
  [[ "$state" == "failed" ]] && state_color="203"
  [[ "$state" == "skipped" ]] && state_color="226"

  printf "  "
  print -nP "%F{${state_color}}*%f "
  print -nP "%F{244}"
  printf "%-10s" "$phase"
  print -nP "%f "
  print -nP "%F{${state_color}}"
  printf "%-10s" "$state"
  print -nP "%f%F{244}.%f "
  print -r -- "$detail"
}

_dotsync_agent_cleanup_claude() {
  local project_dir="$1"
  local deleted=0
  local memory_files_reset=0
  local mem_dir="$project_dir/memory"

  [[ -d "$mem_dir" ]] && memory_files_reset=$(find "$mem_dir" -type f 2>/dev/null | wc -l | tr -d ' ')
  if [[ -d "$project_dir" ]]; then
    local f=""
    while IFS= read -r -d '' f; do
      local uuid="${f:t:r}"
      rm -f "$f"
      rm -rf "$project_dir/$uuid"
      ((++deleted))
    done < <(find "$project_dir" -maxdepth 1 -name '*.jsonl' -mtime +3 -print0 2>/dev/null)

    [[ -d "$mem_dir" ]] && rm -rf "$mem_dir"
  fi

  _dotsync_agent_event claude cleanup done "sessions_deleted=${deleted} memory_files_reset=${memory_files_reset}"
}

_dotsync_agent_cleanup_codex() {
  local codex_home="$1"
  local cwd="$2"
  local sessions_dir="$codex_home/sessions"
  local mem_dir="$codex_home/memories"
  local deleted=0
  local memory_files_reset=0

  [[ -d "$mem_dir" ]] && memory_files_reset=$(find "$codex_home/memories" -type f 2>/dev/null | wc -l | tr -d ' ')
  if [[ -d "$sessions_dir" ]] && command -v jq >/dev/null 2>&1; then
    local f=""
    while IFS= read -r -d '' f; do
      if jq -e --arg cwd "$cwd" \
        'select(.type == "session_meta" and .payload.cwd == $cwd)' \
        "$f" >/dev/null 2>&1; then
        if [[ $(find "$f" -maxdepth 0 -mtime +3 -print 2>/dev/null) == "$f" ]]; then
          rm -f "$f"
          ((++deleted))
        fi
      fi
    done < <(find "$sessions_dir" -type f -name '*.jsonl' -print0 2>/dev/null)
  fi

  [[ -d "$mem_dir" ]] && rm -rf "$mem_dir"
  _dotsync_agent_event codex cleanup done "sessions_deleted=${deleted} memory_files_reset=${memory_files_reset}"
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
    total=$(find "$HOME/.claude/projects/${PWD//\//-}" -maxdepth 1 -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ')
    deleted=$(find "$proj_dir" -maxdepth 1 -name '*.jsonl' -mtime +3 2>/dev/null | wc -l | tr -d ' ')
  fi
  kept=$(( total - deleted ))

  local mem_dir="$proj_dir/memory"
  [[ -d "$mem_dir" ]] && mem_deleted=$(find "$mem_dir" -type f 2>/dev/null | wc -l | tr -d ' ')

  local serena_state="active"
  local serena_phrase="managed by scoped launcher"
  if ! _dotsync_agent_serena_project_available "$project_root"; then
    serena_state="warn"
    serena_phrase="project config missing"
  fi

  if (( interactive )); then
    _dotsync_agent_preflight 141 "claude" "$project_root" \
      "sessions total=${total} delete=${deleted} keep=${kept}" \
      "memory reset files=${mem_deleted}" \
      "claude-code" \
      "$serena_state" \
      "$serena_phrase"
  fi

  if ! _dotsync_agent_ensure_serena "claude" "$project_root"; then
    "$real_binary" "$@"
    return $?
  fi

  _dotsync_agent_cleanup_claude "$proj_dir"
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

  local session_line="sessions total=${total} delete=${deleted} keep=${kept}"
  (( can_scan_sessions )) || session_line="sessions scan=skip reason=jq-missing"

  local serena_state="active"
  local serena_phrase="managed by scoped launcher"
  if ! _dotsync_agent_serena_project_available "$project_root"; then
    serena_state="warn"
    serena_phrase="project config missing"
  fi

  if (( interactive )); then
    _dotsync_agent_preflight 081 "codex" "$project_root" \
      "${session_line}" \
      "memory reset files=${mem_deleted}" \
      "codex" \
      "$serena_state" \
      "$serena_phrase"
  fi

  if ! _dotsync_agent_ensure_serena "codex" "$project_root"; then
    "$real_binary" "$@"
    return $?
  fi

  _dotsync_agent_cleanup_codex "$codex_home" "$PWD"
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
