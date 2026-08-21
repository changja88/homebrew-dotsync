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
APP_EXECUTABLE="$APP_PATH/Contents/MacOS/DotSync"
INFO_PLIST="$APP_PATH/Contents/Info.plist"

[[ "$(uname -s)" == "Darwin" ]] || die "macOS is required"
for tool in xcrun swift lipo plutil strip; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required"
done

[[ "$BUILD_ROOT" == "$REPO_ROOT/build" ]] || die "unsafe build directory"
cd -- "$REPO_ROOT"

VERSION_LINES="$(LC_ALL=C sed -nE 's/^version = "([0-9]+\.[0-9]+\.[0-9]+)"$/\1/p' pyproject.toml)"
[[ "$VERSION_LINES" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
  || die "pyproject.toml must contain one exact semantic version"
VERSION="$VERSION_LINES"
BUILD_VERSION="$VERSION"

SDK="$(xcrun --sdk macosx --show-sdk-path)"
[[ -n "$SDK" ]] || die "macOS SDK could not be resolved"

rm -rf -- "$ARM_SCRATCH" "$X86_SCRATCH" "$APP_PATH"
mkdir -p -- "$BUILD_ROOT"
chmod 0755 "$BUILD_ROOT"

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

mkdir -p -- "$APP_PATH/Contents/MacOS"
chmod 0755 "$APP_PATH" "$APP_PATH/Contents" "$APP_PATH/Contents/MacOS"
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

if LC_ALL=C grep -aRF "$REPO_ROOT" "$APP_PATH" >/dev/null; then
  die "bundle contains the developer checkout path"
fi
if LC_ALL=C grep -aER '[?&]token=[A-Za-z0-9_-]{43}' "$APP_PATH" >/dev/null; then
  die "bundle contains a launch capability value"
fi
for provider_home in '/.codex' '/.claude' '~/.codex' '~/.claude'; do
  if LC_ALL=C grep -aRF "$provider_home" "$APP_PATH" >/dev/null; then
    die "bundle contains a default provider-home path"
  fi
done

printf '%s\n' "$APP_PATH"
