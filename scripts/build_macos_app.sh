#!/usr/bin/env bash
# Assemble an unsigned universal DotSync.app for local development only.
set -euo pipefail

die() {
  printf 'build_macos_app: %s\n' "$*" >&2
  exit 1
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
BUILD_ROOT="$REPO_ROOT/build"
ARM_SCRATCH="$BUILD_ROOT/swift-arm64"
X86_SCRATCH="$BUILD_ROOT/swift-x86_64"
APP_PATH="$BUILD_ROOT/DotSync.app"

[[ "$(uname -s)" == "Darwin" ]] || die "macOS is required"
if [[ -L "$BUILD_ROOT" ]]; then
  die "build directory must not be a symlink"
fi
if [[ -e "$BUILD_ROOT" && ! -d "$BUILD_ROOT" ]]; then
  die "build path must be a directory"
fi
mkdir -p -- "$BUILD_ROOT"
PHYSICAL_BUILD_ROOT="$(cd -- "$BUILD_ROOT" && pwd -P)"
[[ "$PHYSICAL_BUILD_ROOT" == "$REPO_ROOT/build" ]] || die "unsafe build directory"
BUILD_ROOT="$PHYSICAL_BUILD_ROOT"
ARM_SCRATCH="$BUILD_ROOT/swift-arm64"
X86_SCRATCH="$BUILD_ROOT/swift-x86_64"
APP_PATH="$BUILD_ROOT/DotSync.app"
chmod 0755 "$BUILD_ROOT"

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

cd -- "$REPO_ROOT"

VERSION="$("$PACKAGING_PYTHON" scripts/macos_app_support.py version pyproject.toml)" \
  || die "pyproject.toml must define project.version as N.N.N"
BUILD_VERSION="$VERSION"

SDK="$(xcrun --sdk macosx --show-sdk-path)"
[[ -n "$SDK" ]] || die "macOS SDK could not be resolved"

remove_build_directory() {
  local child_name="$1"
  "$PACKAGING_PYTHON" scripts/macos_app_support.py \
    remove-child "$BUILD_ROOT" "$child_name" \
    || die "unsafe build cleanup target: $child_name"
}

remove_build_directory swift-arm64
remove_build_directory swift-x86_64

STAGING_ROOT="$(mktemp -d "$BUILD_ROOT/.dotsync-app-stage.XXXXXXXX")" \
  || die "private staging directory could not be created"
STAGING_NAME="${STAGING_ROOT##*/}"
[[ "$STAGING_ROOT" == "$BUILD_ROOT/$STAGING_NAME" ]] \
  || die "unsafe staging directory"
chmod 0700 "$STAGING_ROOT"
STAGED_APP="$STAGING_ROOT/DotSync.app"
APP_EXECUTABLE="$STAGED_APP/Contents/MacOS/DotSync"
INFO_PLIST="$STAGED_APP/Contents/Info.plist"

cleanup_staging() {
  local exit_code=$?
  if [[ -n "${STAGING_NAME:-}" ]]; then
    "$PACKAGING_PYTHON" scripts/macos_app_support.py \
      remove-child "$BUILD_ROOT" "$STAGING_NAME" >/dev/null 2>&1 \
      || printf 'build_macos_app: private staging cleanup failed\n' >&2
  fi
  return "$exit_code"
}
trap cleanup_staging EXIT

build_architecture() {
  local triple="$1"
  local scratch="$2"
  local -a arguments=(
    build
    --package-path macos/DotSyncApp
    --configuration release
    --triple "$triple"
    --sdk "$SDK"
    --scratch-path "$scratch"
  )
  local binary_dir
  local binary

  swift "${arguments[@]}" >&2
  binary_dir="$(swift "${arguments[@]}" --show-bin-path)"
  binary="$binary_dir/DotSync"
  [[ -f "$binary" && -x "$binary" ]] || die "Swift build did not produce an executable"
  printf '%s\n' "$binary"
}

ARM_BINARY="$(build_architecture arm64-apple-macosx13.0 "$ARM_SCRATCH")"
X86_BINARY="$(build_architecture x86_64-apple-macosx13.0 "$X86_SCRATCH")"

mkdir -p -- "$STAGED_APP/Contents/MacOS"
chmod 0755 "$STAGED_APP" "$STAGED_APP/Contents" "$STAGED_APP/Contents/MacOS"
lipo -create "$ARM_BINARY" "$X86_BINARY" -output "$APP_EXECUTABLE" >&2
chmod 0755 "$APP_EXECUTABLE"
strip -S "$APP_EXECUTABLE" >&2
lipo "$APP_EXECUTABLE" -verify_arch arm64 x86_64 >&2

sed \
  -e "s/__DOTSYNC_VERSION__/$VERSION/g" \
  -e "s/__DOTSYNC_BUILD__/$BUILD_VERSION/g" \
  packaging/DotSync-Info.plist.in > "$INFO_PLIST"
chmod 0644 "$INFO_PLIST"
if LC_ALL=C grep -aE '__DOTSYNC_(VERSION|BUILD)__' "$INFO_PLIST" >/dev/null; then
  die "Info.plist contains an unresolved build sentinel"
fi
plutil -lint "$INFO_PLIST" >&2

"$PACKAGING_PYTHON" scripts/macos_app_support.py \
  scan "$STAGED_APP" "$REPO_ROOT" \
  || die "bundle safety scan failed"

"$PACKAGING_PYTHON" scripts/macos_app_support.py \
  publish "$BUILD_ROOT" "$STAGING_NAME" DotSync.app \
  || die "verified app could not be published"
printf '%s\n' "$APP_PATH"
