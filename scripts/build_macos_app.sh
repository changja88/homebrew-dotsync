#!/usr/bin/env bash
# Assemble an unsigned universal DotSync.app for local development only.
set -euo pipefail

die() {
  printf 'build_macos_app: %s\n' "$*" >&2
  exit 1
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

[[ "$(uname -s)" == "Darwin" ]] || die "macOS is required"
for tool in xcrun swift lipo plutil strip; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required"
done

resolve_python() {
  local candidate
  local -a candidates=()
  if [[ -n "${PYTHON:-}" ]]; then
    candidates+=("$PYTHON")
  else
    candidates+=(
      "$REPO_ROOT/.venv/bin/python3"
      python3.14
      python3.13
      python3.12
      python3
    )
  fi
  for candidate in "${candidates[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

PACKAGING_PYTHON="$(resolve_python)" || die "Python 3.12 or newer is required"
exec "$PACKAGING_PYTHON" "$SCRIPT_DIR/macos_app_support.py" assemble "$REPO_ROOT"
