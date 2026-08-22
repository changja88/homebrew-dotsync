#!/usr/bin/env bash
# Build and validate a signed DotSync.app release, then stop before publication.
set -euo pipefail

die() {
  printf 'release_macos_app: %s\n' "$*" >&2
  exit 1
}

if [[ "$#" -ne 1 ]]; then
  die "usage: scripts/release_macos_app.sh VERSION"
fi

VERSION="$1"
if [[ ! "$VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] \
    || [[ "$VERSION" == "0.0.0" ]]; then
  die "VERSION must be one canonical non-zero X.Y.Z value"
fi

SCRIPT_DIR="$(cd -- "${BASH_SOURCE[0]%/*}" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
TAG="v$VERSION"
TAG_REF="refs/tags/$TAG"
ASSET_NAME="DotSync-$VERSION-macOS.zip"
RELEASE_URL="https://github.com/changja88/homebrew-dotsync/releases/download/$TAG/$ASSET_NAME"
REPOSITORY_SLUG="changja88/homebrew-dotsync"
CASK_OUTPUT="$REPO_ROOT/Casks/dotsync-app.rb"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python3}"

for tool in git tar node swift bash security lipo codesign ditto xcrun spctl shasum gh brew; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required"
done
[[ -x "$PYTHON" ]] || die "an executable Python test runner is required"

cd -- "$REPO_ROOT"
TOPLEVEL="$(git rev-parse --show-toplevel)" || die "repository root could not be resolved"
[[ "$(cd -- "$TOPLEVEL" && pwd -P)" == "$REPO_ROOT" ]] \
  || die "script must run from its repository root"
GIT_DIR="$(git rev-parse --path-format=absolute --git-dir)"
GIT_COMMON_DIR="$(git rev-parse --path-format=absolute --git-common-dir)"
[[ "$GIT_DIR" == "$GIT_COMMON_DIR" ]] || die "release requires the primary checkout"
[[ "$(git branch --show-current)" == "main" ]] || die "release requires main"
CLEAN_STATUS="$(git status --porcelain=v1 --untracked-files=all)" \
  || die "checkout cleanliness could not be determined"
[[ -z "$CLEAN_STATUS" ]] \
  || die "release requires a clean checkout"
HEAD_COMMIT="$(git rev-parse HEAD)"
TAG_COMMIT="$(git rev-parse --verify "$TAG_REF^{commit}")" \
  || die "$TAG does not exist"
[[ "$HEAD_COMMIT" == "$TAG_COMMIT" ]] || die "$TAG must resolve to exact HEAD"
[[ ! -e "$CASK_OUTPUT" && ! -L "$CASK_OUTPUT" ]] \
  || die "refusing to replace an existing Cask"

WORK_DIR=""
REMOVE_CASK_ON_FAILURE=0
CASKS_DIRECTORY_CREATED=0
cleanup() {
  local status="$?"
  trap - EXIT
  set +e
  if [[ "$status" -ne 0 && "$REMOVE_CASK_ON_FAILURE" -eq 1 ]]; then
    /bin/rm -f -- "$CASK_OUTPUT"
  fi
  if [[ "$status" -ne 0 && "$CASKS_DIRECTORY_CREATED" -eq 1 ]]; then
    /bin/rmdir -- "$REPO_ROOT/Casks" 2>/dev/null
  fi
  if [[ -n "$WORK_DIR" && -d "$WORK_DIR" && ! -L "$WORK_DIR" ]]; then
    /bin/rm -rf -- "$WORK_DIR"
  fi
  exit "$status"
}
trap cleanup EXIT

TEMPORARY_ROOT="${TMPDIR:-/private/tmp}"
while [[ "$TEMPORARY_ROOT" != "/" && "$TEMPORARY_ROOT" == */ ]]; do
  TEMPORARY_ROOT="${TEMPORARY_ROOT%/}"
done
[[ -n "$TEMPORARY_ROOT" && "$TEMPORARY_ROOT" != "/" ]] \
  || die "temporary directory root was unsafe"
[[ -d "$TEMPORARY_ROOT" && ! -L "$TEMPORARY_ROOT" ]] \
  || die "temporary directory root must be a real directory"
TEMPORARY_ROOT="$(cd -- "$TEMPORARY_ROOT" && pwd -P)"
[[ "$TEMPORARY_ROOT" != "/" ]] || die "temporary directory root was unsafe"
WORK_DIR="$(/usr/bin/mktemp -d "$TEMPORARY_ROOT/dotsync-macos-release.XXXXXXXX")"
[[ -d "$WORK_DIR" && ! -L "$WORK_DIR" ]] || die "private release directory was not created"
/bin/chmod 700 "$WORK_DIR"
SOURCE_ARCHIVE="$WORK_DIR/source.tar"
SOURCE_ROOT="$WORK_DIR/source"
NOTARY_ZIP="$WORK_DIR/DotSync-notarization-$VERSION.zip"
FINAL_ZIP="$WORK_DIR/$ASSET_NAME"

git archive \
  --format=tar \
  --prefix=source/ \
  --output "$SOURCE_ARCHIVE" \
  "$TAG_REF"
tar -xf "$SOURCE_ARCHIVE" -C "$WORK_DIR"
[[ -d "$SOURCE_ROOT" && ! -L "$SOURCE_ROOT" ]] \
  || die "tag export did not create an exact source directory"

cd -- "$SOURCE_ROOT"
"$PYTHON" -m pytest
node --test tests/web/js/state.test.mjs tests/web/js/api-client.test.mjs
swift test --package-path macos/DotSyncApp
PYTHONPATH=lib "$PYTHON" -m dotsync ui --check
PYTHON="$PYTHON" bash scripts/build_macos_app.sh

APP="$SOURCE_ROOT/build/DotSync.app"
APP_EXECUTABLE="$APP/Contents/MacOS/DotSync"
[[ -d "$APP" && ! -L "$APP" ]] || die "local builder did not create DotSync.app"
[[ -f "$APP_EXECUTABLE" && ! -L "$APP_EXECUTABLE" && -x "$APP_EXECUTABLE" ]] \
  || die "local builder did not create the expected executable"
lipo "$APP_EXECUTABLE" -verify_arch arm64 x86_64

if [[ ! "${DEVELOPER_ID_APPLICATION:-}" =~ [^[:space:]] ]]; then
  die "DEVELOPER_ID_APPLICATION is required"
fi
IDENTITIES="$(security find-identity -v -p codesigning)" \
  || die "codesigning identities could not be resolved"
IDENTITY_FOUND=0
while IFS= read -r identity_line; do
  if [[ "$identity_line" == *\""$DEVELOPER_ID_APPLICATION"\"* ]]; then
    IDENTITY_FOUND=1
    break
  fi
done <<< "$IDENTITIES"
[[ "$IDENTITY_FOUND" -eq 1 ]] || die "DEVELOPER_ID_APPLICATION could not be resolved"

if [[ ! "${NOTARYTOOL_PROFILE:-}" =~ [^[:space:]] ]]; then
  die "NOTARYTOOL_PROFILE is required"
fi
xcrun notarytool history --keychain-profile "$NOTARYTOOL_PROFILE" >/dev/null

codesign --force --options runtime --timestamp \
  --sign "$DEVELOPER_ID_APPLICATION" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
ditto -c -k --keepParent "$APP" "$NOTARY_ZIP"
[[ -f "$NOTARY_ZIP" && ! -L "$NOTARY_ZIP" ]] \
  || die "notarization archive was not created"
xcrun notarytool submit "$NOTARY_ZIP" \
  --keychain-profile "$NOTARYTOOL_PROFILE" --wait
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
spctl --assess --type execute --verbose=4 "$APP"

ditto -c -k --keepParent "$APP" "$FINAL_ZIP"
[[ -f "$FINAL_ZIP" && ! -L "$FINAL_ZIP" ]] \
  || die "final stapled archive was not created"
SHA_OUTPUT="$(shasum -a 256 "$FINAL_ZIP")" || die "final SHA-256 failed"
[[ "$SHA_OUTPUT" != *$'\n'* ]] || die "final SHA-256 output was not one line"
FINAL_SHA256="${SHA_OUTPUT%%[[:space:]]*}"
[[ "$FINAL_SHA256" =~ ^[0-9a-f]{64}$ && "$FINAL_SHA256" != "$(printf '0%.0s' {1..64})" ]] \
  || die "final SHA-256 was not canonical"

gh release view "$TAG" --repo "$REPOSITORY_SLUG" >/dev/null
gh release upload "$TAG" "$FINAL_ZIP" --repo "$REPOSITORY_SLUG"

cd -- "$REPO_ROOT"
if [[ -e "$REPO_ROOT/Casks" || -L "$REPO_ROOT/Casks" ]]; then
  [[ -d "$REPO_ROOT/Casks" && ! -L "$REPO_ROOT/Casks" ]] \
    || die "Casks must be a real directory"
else
  /bin/mkdir -m 755 "$REPO_ROOT/Casks"
  CASKS_DIRECTORY_CREATED=1
fi
REMOVE_CASK_ON_FAILURE=1
"$PYTHON" "$REPO_ROOT/scripts/render_cask.py" \
  --version "$VERSION" \
  --sha256 "$FINAL_SHA256" \
  --url "$RELEASE_URL" \
  --output "$CASK_OUTPUT" \
  --repository-root "$REPO_ROOT"
[[ -f "$CASK_OUTPUT" && ! -L "$CASK_OUTPUT" ]] \
  || die "Cask renderer did not create the exact output"
brew audit --cask --strict "$CASK_OUTPUT"

REMOVE_CASK_ON_FAILURE=0
printf '%s\n' "Signed app uploaded and Cask audited. Stop for explicit publication confirmation."
