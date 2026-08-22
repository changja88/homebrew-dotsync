#!/usr/bin/env bash
# Build and validate a signed DotSync.app release, then stop before publication.
set -euo pipefail

die() {
  printf 'release_macos_app: %s\n' "$*" >&2
  exit 1
}

capture_git_line() {
  local target="$1"
  shift
  local captured="" command_status=0
  captured="$(
    set +e
    "$@"
    command_status="$?"
    printf '\034'
    exit "$command_status"
  )" || return 1
  [[ "$captured" == *$'\n\034' ]] || return 1
  captured="${captured%$'\n\034'}"
  [[ -n "$captured" && "$captured" != *$'\n'* && "$captured" != *$'\r'* ]] \
    || return 1
  printf -v "$target" '%s' "$captured"
}

require_empty_git_output() {
  local captured="" command_status=0
  captured="$(
    set +e
    "$@"
    command_status="$?"
    printf '\034'
    exit "$command_status"
  )" || return 1
  [[ "$captured" == $'\034' ]]
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
ZERO_GIT_OID="0000000000000000000000000000000000000000"
ASSET_NAME="DotSync-$VERSION-macOS.zip"
NOTARY_NAME="DotSync-notarization-$VERSION.zip"
RELEASE_URL="https://github.com/changja88/homebrew-dotsync/releases/download/$TAG/$ASSET_NAME"
REPOSITORY_SLUG="changja88/homebrew-dotsync"
CASK_OUTPUT="$REPO_ROOT/Casks/dotsync-app.rb"
RELEASE_SUPPORT="$REPO_ROOT/scripts/macos_release_support.py"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python3}"

for tool in git tar node swift bash security lipo codesign ditto xcrun spctl shasum gh brew; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required"
done
[[ -x "$PYTHON" ]] || die "an executable Python test runner is required"
[[ -f "$RELEASE_SUPPORT" && ! -L "$RELEASE_SUPPORT" ]] \
  || die "release filesystem support is required"

cd -- "$REPO_ROOT"
capture_git_line TOPLEVEL git rev-parse --show-toplevel \
  || die "repository root could not be resolved exactly"
[[ "$TOPLEVEL" == /* && "$TOPLEVEL" == "$REPO_ROOT" ]] \
  || die "script must run from its repository root"
capture_git_line GIT_DIR git rev-parse --path-format=absolute --git-dir \
  || die "Git directory could not be resolved exactly"
capture_git_line GIT_COMMON_DIR git rev-parse --path-format=absolute --git-common-dir \
  || die "Git common directory could not be resolved exactly"
[[ "$GIT_DIR" == /* && "$GIT_COMMON_DIR" == /* ]] \
  || die "Git directories must be absolute"
GIT_DIR_PHYSICAL="$(cd -- "$GIT_DIR" && pwd -P)" \
  || die "Git directory was not a real directory"
GIT_COMMON_DIR_PHYSICAL="$(cd -- "$GIT_COMMON_DIR" && pwd -P)" \
  || die "Git common directory was not a real directory"
[[ "$GIT_DIR" == "$GIT_DIR_PHYSICAL" && "$GIT_COMMON_DIR" == "$GIT_COMMON_DIR_PHYSICAL" ]] \
  || die "Git directories were not canonical physical paths"
[[ "$GIT_DIR" == "$GIT_COMMON_DIR" ]] || die "release requires the primary checkout"
capture_git_line BRANCH git branch --show-current \
  || die "release branch could not be resolved exactly"
[[ "$BRANCH" == "main" ]] || die "release requires main"
require_empty_git_output git status --porcelain=v1 --untracked-files=all \
  || die "release requires a clean checkout with exact Git status output"
capture_git_line HEAD_COMMIT git rev-parse HEAD \
  || die "HEAD could not be resolved exactly"
[[ "$HEAD_COMMIT" =~ ^[0-9a-f]{40}$ && "$HEAD_COMMIT" != "$ZERO_GIT_OID" ]] \
  || die "HEAD was not a canonical non-zero commit id"
capture_git_line TAG_TYPE git cat-file -t "$TAG_REF" \
  || die "$TAG does not resolve exactly to a tag object"
[[ "$TAG_TYPE" == "tag" ]] || die "$TAG must be an annotated tag"
capture_git_line TAG_COMMIT git rev-parse --verify "$TAG_REF^{commit}" \
  || die "$TAG does not resolve exactly to a commit"
[[ "$TAG_COMMIT" =~ ^[0-9a-f]{40}$ && "$TAG_COMMIT" != "$ZERO_GIT_OID" ]] \
  || die "$TAG commit was not a canonical non-zero commit id"
[[ "$HEAD_COMMIT" == "$TAG_COMMIT" ]] || die "$TAG must resolve to exact HEAD"
[[ ! -e "$CASK_OUTPUT" && ! -L "$CASK_OUTPUT" ]] \
  || die "refusing to replace an existing Cask"

WORK_ACTIVE=0
IN_SOURCE=0
FINALIZER_ACTIVE=0
FINALIZER_SIGNAL_STATUS=0
COMMITTED=0
TEMPORARY_ROOT=""
TEMPORARY_ROOT_IDENTITY=""
WORK_NAME=""
WORK_IDENTITY=""
CASK_TRANSACTION_ACTIVE=0
CASK_STYLE_SUCCEEDED=0
CASKS_DIRECTORY_CREATED=0
CASKS_BINDING_OWNERSHIP=""
CASKS_DEV=""
CASKS_INO=""
CASKS_FD=9
CASK_BINDING_OWNERSHIP=""
CASK_BINDING_FIELDS=""
OWNED_CHILDREN=()

record_finalizer_signal() {
  if [[ "$COMMITTED" -eq 0 && "$FINALIZER_SIGNAL_STATUS" -eq 0 ]]; then
    FINALIZER_SIGNAL_STATUS="$1"
  fi
}

revalidate_casks_binding() {
  "$PYTHON" "$RELEASE_SUPPORT" verify-canonical-directory-fd \
    "$CASKS_FD" "$CASKS_DEV:$CASKS_INO" "$REPO_ROOT" Casks >/dev/null
}

rollback_bound_cask() {
  local cask_binding_fields="$1"
  local cask_dev="" cask_ino=""
  if [[ -n "$cask_binding_fields" ]]; then
    local rendered_casks_dev="" rendered_casks_ino=""
    read -r rendered_casks_dev rendered_casks_ino cask_dev cask_ino \
      <<< "$cask_binding_fields"
    if [[ "$rendered_casks_dev" != "$CASKS_DEV" \
        || "$rendered_casks_ino" != "$CASKS_INO" ]]; then
      return 1
    fi
  fi
  local rollback_arguments=(
    --rollback-created --repository-root "$REPO_ROOT"
    --casks-dev "$CASKS_DEV" --casks-ino "$CASKS_INO"
    --casks-fd "$CASKS_FD"
  )
  if [[ -n "$cask_dev" && -n "$cask_ino" ]]; then
    rollback_arguments+=(--cask-dev "$cask_dev" --cask-ino "$cask_ino")
  fi
  if [[ "$CASKS_DIRECTORY_CREATED" -eq 1 ]]; then
    rollback_arguments+=(--remove-casks-directory)
  fi
  "$PYTHON" "$REPO_ROOT/scripts/render_cask.py" "${rollback_arguments[@]}"
}

finalize() {
  local status="$1"
  local cleanup_failed=0
  if [[ "$FINALIZER_ACTIVE" -eq 1 ]]; then
    return
  fi
  FINALIZER_ACTIVE=1
  trap - EXIT
  trap 'record_finalizer_signal 129' HUP
  trap 'record_finalizer_signal 130' INT
  trap 'record_finalizer_signal 143' TERM
  set +e
  set +u

  local cask_binding_fields="$CASK_BINDING_FIELDS"
  if [[ "$CASK_TRANSACTION_ACTIVE" -eq 1 && -z "$cask_binding_fields" ]]; then
    cask_binding_fields="$("$PYTHON" "$RELEASE_SUPPORT" read-cask-binding \
      cask-binding.json "$CASK_BINDING_OWNERSHIP")" 2>/dev/null || true
  fi

  if [[ "$WORK_ACTIVE" -eq 1 ]]; then
    if [[ "$IN_SOURCE" -eq 1 ]]; then
      cd -- .. || cleanup_failed=1
      IN_SOURCE=0
    fi
    "$PYTHON" "$RELEASE_SUPPORT" cleanup-current \
      --parent "$TEMPORARY_ROOT" \
      --name "$WORK_NAME" \
      --parent-identity "$TEMPORARY_ROOT_IDENTITY" \
      --work-identity "$WORK_IDENTITY" \
      "${OWNED_CHILDREN[@]}" \
      || cleanup_failed=1
  fi

  if [[ "$FINALIZER_SIGNAL_STATUS" -ne 0 ]]; then
    status="$FINALIZER_SIGNAL_STATUS"
  fi

  if [[ "$status" -eq 0 && "$CASK_STYLE_SUCCEEDED" -eq 1 \
      && "$cleanup_failed" -eq 0 ]]; then
    printf '%s\n' \
      "Signed app uploaded and Cask style checked. Review and commit this generated Cask only if this process exits 0; run the name-based audit and stop for explicit publication confirmation." \
      || cleanup_failed=1
  fi

  if [[ "$CASK_TRANSACTION_ACTIVE" -eq 1 ]]; then
    revalidate_casks_binding || cleanup_failed=1
  fi
  if [[ "$FINALIZER_SIGNAL_STATUS" -ne 0 ]]; then
    status="$FINALIZER_SIGNAL_STATUS"
  fi

  if [[ "$status" -eq 0 && "$CASK_STYLE_SUCCEEDED" -eq 1 \
      && "$cleanup_failed" -eq 0 ]]; then
    COMMITTED=1
    if [[ "$FINALIZER_SIGNAL_STATUS" -eq 0 ]]; then
      trap '' HUP INT TERM
      exit 0
    fi
    COMMITTED=0
    status="$FINALIZER_SIGNAL_STATUS"
  fi

  if [[ "$CASK_TRANSACTION_ACTIVE" -eq 1 ]]; then
    rollback_bound_cask "$cask_binding_fields" || cleanup_failed=1
  fi
  if [[ "$FINALIZER_SIGNAL_STATUS" -ne 0 ]]; then
    status="$FINALIZER_SIGNAL_STATUS"
  elif [[ "$cleanup_failed" -ne 0 ]]; then
    status=1
  fi
  if [[ "$cleanup_failed" -ne 0 ]]; then
    printf '%s\n' "release_macos_app: release cleanup did not complete exactly" >&2
  fi
  if [[ "$FINALIZER_SIGNAL_STATUS" -ne 0 ]]; then
    status="$FINALIZER_SIGNAL_STATUS"
  fi
  trap '' HUP INT TERM
  exit "$status"
}

on_exit() { finalize "$?"; }
on_hup() { record_finalizer_signal 129; finalize "$FINALIZER_SIGNAL_STATUS"; }
on_int() { record_finalizer_signal 130; finalize "$FINALIZER_SIGNAL_STATUS"; }
on_term() { record_finalizer_signal 143; finalize "$FINALIZER_SIGNAL_STATUS"; }

trap on_exit EXIT
trap on_hup HUP
trap on_int INT
trap on_term TERM

own_here() {
  local binding
  binding="$("$PYTHON" "$RELEASE_SUPPORT" identity-here "$1")" \
    || die "release output $1 could not be identity-bound"
  OWNED_CHILDREN+=(--owned "$binding")
}

own_parent() {
  local binding
  binding="$("$PYTHON" "$RELEASE_SUPPORT" identity-parent "$1")" \
    || die "release output $1 could not be identity-bound"
  OWNED_CHILDREN+=(--owned "$binding")
}

TEMPORARY_ROOT="${TMPDIR:-}"
[[ -n "$TEMPORARY_ROOT" ]] || die "TMPDIR is required"
while [[ "$TEMPORARY_ROOT" != "/" && "$TEMPORARY_ROOT" == */ ]]; do
  TEMPORARY_ROOT="${TEMPORARY_ROOT%/}"
done
[[ -n "$TEMPORARY_ROOT" && "$TEMPORARY_ROOT" != "/" ]] \
  || die "temporary directory root was unsafe"
TEMPORARY_ROOT_IDENTITY="$("$PYTHON" "$RELEASE_SUPPORT" validate-temp-root "$TEMPORARY_ROOT")" \
  || die "TMPDIR did not satisfy the private release root contract"
cd -- "$TEMPORARY_ROOT" || die "TMPDIR could not be entered"
[[ "$("$PYTHON" "$RELEASE_SUPPORT" identity-current)" == "$TEMPORARY_ROOT_IDENTITY" ]] \
  || die "TMPDIR identity changed while entering it"
TEMPORARY_ROOT="$(pwd -P)"

umask 077
WORK_CREATED="$(/usr/bin/mktemp -d "./dotsync-macos-release.XXXXXXXX")" \
  || die "private release directory could not be created"
cd -- "$WORK_CREATED" || die "private release directory could not be entered"
WORK_NAME="${WORK_CREATED#./}"
[[ "$WORK_NAME" =~ ^dotsync-macos-release\.[A-Za-z0-9]+$ ]] \
  || die "private release directory name was not canonical"
WORK_IDENTITY="$("$PYTHON" "$RELEASE_SUPPORT" identity-current --require-mode 0700)" \
  || die "private release directory identity could not be bound"
WORK_ACTIVE=1

GIT_DIR="$GIT_DIR" git archive \
  --format=tar --prefix=source/ --output source.tar "$TAG_REF"
own_here source.tar
tar -xf source.tar -C .
SOURCE_BINDING="$("$PYTHON" "$RELEASE_SUPPORT" identity-here source)" \
  || die "tag export did not create an exact source directory"
OWNED_CHILDREN+=(--owned "$SOURCE_BINDING")
SOURCE_IDENTITY="${SOURCE_BINDING#source:}"
SOURCE_IDENTITY="${SOURCE_IDENTITY%:d}"
cd -- source || die "tag export source could not be entered"
IN_SOURCE=1
[[ "$("$PYTHON" "$RELEASE_SUPPORT" identity-current)" == "$SOURCE_IDENTITY" ]] \
  || die "tag export source identity changed while entering it"

TAGGED_VERSION="$("$PYTHON" -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])')" \
  || die "tagged project version could not be parsed"
[[ "$TAGGED_VERSION" == "$VERSION" ]] \
  || die "tagged project version must exactly match VERSION"
PYTHONPATH=lib "$PYTHON" -m dotsync ui --check
PYTHON="$PYTHON" bash scripts/build_macos_app.sh

APP="build/DotSync.app"
APP_EXECUTABLE="$APP/Contents/MacOS/DotSync"
[[ -d "$APP" && ! -L "$APP" ]] || die "local builder did not create DotSync.app"
[[ -f "$APP_EXECUTABLE" && ! -L "$APP_EXECUTABLE" && -x "$APP_EXECUTABLE" ]] \
  || die "local builder did not create the expected executable"
BUILT_PLIST_VERSIONS="$("$PYTHON" "$RELEASE_SUPPORT" read-app-plist-versions)" \
  || die "built Info.plist must be an exact readable nofollow regular file"
EXPECTED_PLIST_VERSIONS="$(printf '%s\n%s' "$VERSION" "$VERSION")"
[[ "$BUILT_PLIST_VERSIONS" == "$EXPECTED_PLIST_VERSIONS" ]] \
  || die "built Info.plist versions must exactly match VERSION"
lipo "$APP_EXECUTABLE" -verify_arch arm64 x86_64
ARCHITECTURE_CAPTURE="$({
  lipo "$APP_EXECUTABLE" -archs
  architecture_status="$?"
  printf '\034'
  exit "$architecture_status"
})" \
  || die "universal architecture listing failed"
[[ "$ARCHITECTURE_CAPTURE" == $'arm64 x86_64\n\034' \
    || "$ARCHITECTURE_CAPTURE" == $'x86_64 arm64\n\034' ]] \
  || die "app executable must report exactly one arm64/x86_64 architecture line"

if [[ ! "${DEVELOPER_ID_APPLICATION:-}" =~ ^Developer\ ID\ Application:\ [^[:space:]].*[^[:space:]]$ ]] \
    || [[ "$DEVELOPER_ID_APPLICATION" == *$'\n'* ]]; then
  die "DEVELOPER_ID_APPLICATION must be one exact Developer ID Application identity"
fi
IDENTITIES="$(security find-identity -v -p codesigning)" \
  || die "codesigning identities could not be resolved"
"$PYTHON" -c 'import re, sys; identity=sys.argv[1]; pattern=re.compile(r"\s*\d+\)\s+[0-9A-Fa-f]{40}\s+\"" + re.escape(identity) + r"\""); lines=sys.stdin.read().splitlines(); raise SystemExit(0 if sum(pattern.fullmatch(line) is not None for line in lines) == 1 else 1)' \
  "$DEVELOPER_ID_APPLICATION" <<< "$IDENTITIES" \
  || die "DEVELOPER_ID_APPLICATION could not be exactly resolved"

if [[ ! "${NOTARYTOOL_PROFILE:-}" =~ [^[:space:]] ]]; then
  die "NOTARYTOOL_PROFILE is required"
fi
NOTARY_HISTORY_JSON="$(xcrun notarytool history --keychain-profile "$NOTARYTOOL_PROFILE" --output-format json)" \
  || die "stored notarytool credentials could not be used"
"$PYTHON" -c 'import json, sys; data=json.load(sys.stdin); raise SystemExit(0 if isinstance(data, dict) and isinstance(data.get("history"), list) else 1)' \
  <<< "$NOTARY_HISTORY_JSON" \
  || die "notarytool history did not return stable JSON"

codesign --force --options runtime --timestamp --sign "$DEVELOPER_ID_APPLICATION" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
ditto -c -k --keepParent "$APP" "../$NOTARY_NAME"
[[ -f "../$NOTARY_NAME" && ! -L "../$NOTARY_NAME" ]] \
  || die "notarization archive was not created"
own_parent "$NOTARY_NAME"
NOTARY_SUBMIT_JSON="$(xcrun notarytool submit "../$NOTARY_NAME" --keychain-profile "$NOTARYTOOL_PROFILE" --wait --output-format json)" \
  || die "notarytool submission failed"
NOTARY_SUBMISSION_ID="$("$PYTHON" -c 'import json, sys; data=json.load(sys.stdin); submission_id=data.get("id") if isinstance(data, dict) else None; status=data.get("status") if isinstance(data, dict) else None; sys.exit(1) if status != "Accepted" or not isinstance(submission_id, str) or not submission_id.strip() else print(submission_id)' \
  <<< "$NOTARY_SUBMIT_JSON")" \
  || die "notarytool submission was not exactly Accepted JSON"
[[ "$NOTARY_SUBMISSION_ID" != *$'\n'* ]] \
  || die "notarytool submission id was malformed"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
spctl --assess --type execute --verbose=4 "$APP"

ditto -c -k --keepParent "$APP" "../$ASSET_NAME"
[[ -f "../$ASSET_NAME" && ! -L "../$ASSET_NAME" ]] \
  || die "final stapled archive was not created"
own_parent "$ASSET_NAME"
SHA_OUTPUT="$(shasum -a 256 "../$ASSET_NAME")" || die "final SHA-256 failed"
[[ "$SHA_OUTPUT" != *$'\n'* ]] || die "final SHA-256 output was not one line"
FINAL_SHA256="${SHA_OUTPUT%% *}"
[[ "$FINAL_SHA256" =~ ^[0-9a-f]{64}$ && "$FINAL_SHA256" != "$(printf '0%.0s' {1..64})" ]] \
  || die "final SHA-256 was not canonical"
[[ "$SHA_OUTPUT" == "$FINAL_SHA256  ../$ASSET_NAME" ]] \
  || die "final SHA-256 output did not exactly bind the final archive"

GH_RELEASE_JSON="$(gh release view "$TAG" --repo "$REPOSITORY_SLUG" --json id,assets)" \
  || die "matching GitHub release could not be resolved"
GH_RELEASE_ID="$("$PYTHON" -c 'import json, sys; data=json.load(sys.stdin); release_id=data.get("id") if isinstance(data, dict) else None; assets=data.get("assets") if isinstance(data, dict) else None; valid_assets=isinstance(assets, list) and all(isinstance(asset, dict) and isinstance(asset.get("name"), str) and asset["name"] for asset in assets); collision=valid_assets and any(asset["name"] == sys.argv[1] for asset in assets); sys.exit(1) if not isinstance(release_id, str) or not release_id.strip() or not valid_assets or collision else print(release_id)' "$ASSET_NAME" \
  <<< "$GH_RELEASE_JSON")" \
  || die "GitHub release JSON was invalid or the asset already exists"
[[ "$GH_RELEASE_ID" != *$'\n'* ]] || die "GitHub release id was malformed"
gh release upload "$TAG" "../$ASSET_NAME" --repo "$REPOSITORY_SLUG"

cd -- .. || die "pinned release workdir could not be re-entered"
IN_SOURCE=0
[[ "$("$PYTHON" "$RELEASE_SUPPORT" identity-current)" == "$WORK_IDENTITY" ]] \
  || die "pinned release workdir identity changed"

if [[ -e "$REPO_ROOT/Casks" || -L "$REPO_ROOT/Casks" ]]; then
  [[ -d "$REPO_ROOT/Casks" && ! -L "$REPO_ROOT/Casks" ]] \
    || die "Casks must be a real directory"
else
  /bin/mkdir -m 755 "$REPO_ROOT/Casks"
  CASKS_DIRECTORY_CREATED=1
fi
CASKS_BINDING_OWNERSHIP="$("$PYTHON" "$RELEASE_SUPPORT" \
  identity-path-entry "$REPO_ROOT" Casks)" \
  || die "Casks directory could not be identity-bound"
CASKS_IDENTITY_FIELDS="${CASKS_BINDING_OWNERSHIP#Casks:}"
CASKS_IDENTITY_FIELDS="${CASKS_IDENTITY_FIELDS%:d}"
IFS=: read -r CASKS_DEV CASKS_INO <<< "$CASKS_IDENTITY_FIELDS"
exec 9< "$REPO_ROOT/Casks" || die "Casks directory descriptor could not be held"
[[ "$("$PYTHON" "$RELEASE_SUPPORT" identity-directory-fd \
  "$CASKS_FD" "$CASKS_DEV:$CASKS_INO")" == "$CASKS_DEV:$CASKS_INO" ]] \
  || die "held Casks descriptor did not match the path binding"
CASK_TRANSACTION_ACTIVE=1
printf '' > cask-binding.json
CASK_BINDING_OWNERSHIP="$("$PYTHON" "$RELEASE_SUPPORT" identity-here cask-binding.json)" \
  || die "Cask rollback binding file could not be owned"
OWNED_CHILDREN+=(--owned "$CASK_BINDING_OWNERSHIP")
"$PYTHON" "$REPO_ROOT/scripts/render_cask.py" \
  --version "$VERSION" \
  --sha256 "$FINAL_SHA256" \
  --url "$RELEASE_URL" \
  --output "$CASK_OUTPUT" \
  --repository-root "$REPO_ROOT" \
  --expected-casks-dev "$CASKS_DEV" \
  --expected-casks-ino "$CASKS_INO" \
  --casks-fd "$CASKS_FD" \
  > cask-binding.json
[[ -f "$CASK_OUTPUT" && ! -L "$CASK_OUTPUT" ]] \
  || die "Cask renderer did not create the exact output"
CASK_BINDING_FIELDS="$("$PYTHON" "$RELEASE_SUPPORT" read-cask-binding \
  cask-binding.json "$CASK_BINDING_OWNERSHIP")" \
  || die "Cask renderer did not return an exact rollback binding"
read -r RENDERED_CASKS_DEV RENDERED_CASKS_INO _RENDERED_CASK_DEV _RENDERED_CASK_INO \
  <<< "$CASK_BINDING_FIELDS"
[[ "$RENDERED_CASKS_DEV" == "$CASKS_DEV" \
    && "$RENDERED_CASKS_INO" == "$CASKS_INO" ]] \
  || die "Cask renderer changed the bound Casks directory"
revalidate_casks_binding || die "Casks directory changed before style check"
brew style "$CASK_OUTPUT"

CASK_STYLE_SUCCEEDED=1
