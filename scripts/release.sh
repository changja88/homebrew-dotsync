#!/usr/bin/env bash
# Interactive release script for dotsync.
#
# Ordering invariant: origin/main must NEVER serve a Formula with a
# placeholder sha256 — brew reads the tap's main directly, so a placeholder
# there breaks `brew install` for everyone (see the v0.1.19 incident).
# The tag is therefore pushed FIRST (GitHub serves the tarball from the tag
# alone), the sha is computed and patched locally, and main is pushed ONCE
# with both commits — it jumps atomically from the previous release to the
# new one. Any failure mid-script leaves the tap serving the previous
# release untouched.
#
# Steps:
#   1. Preflight: on main, clean tree, not behind origin
#   2. Ask: patch / minor / major
#   3. Bump version in pyproject.toml, lib/dotsync/__init__.py, Formula/dotsync.rb,
#      macos/project.yml and Casks/dotsync-app.rb
#   4. Run the Python and Swift tests
#   5. Build, sign and zip dotsync.app; put its sha256 into the Cask
#   6. Commit bump + tag (local only), push the TAG only
#   7. Download the tag tarball, compute sha256, patch the Formula, commit
#   8. gh release create with the app zip — required: the Cask downloads it.
#      If it fails, main is not pushed and the tap keeps serving the old release.
#   9. Push main (bump + sha commits land together)
set -euo pipefail

cd "$(dirname "$0")/.."

GREEN='\033[32m'; YELLOW='\033[33m'; CYAN='\033[36m'; RED='\033[31m'; RESET='\033[0m'
step() { printf "${CYAN}▶ %s${RESET}\n" "$*"; }
ok()   { printf "  ${GREEN}✓${RESET} %s\n" "$*"; }
warn() { printf "  ${YELLOW}⚠${RESET} %s\n" "$*"; }
die()  { printf "  ${RED}✗${RESET} %s\n" "$*" >&2; exit 1; }

# 0. preflight ---------------------------------------------------------------
command -v shasum >/dev/null 2>&1 || die "shasum not available"
command -v gh >/dev/null 2>&1 || die "gh CLI not found — the Cask downloads dotsync.app from the GitHub release"
gh auth status >/dev/null 2>&1 || die "gh not authenticated (gh auth login) — needed to upload dotsync.app"
command -v xcodegen >/dev/null 2>&1 || die "xcodegen not found (brew install xcodegen) — needed to build dotsync.app"
command -v xcodebuild >/dev/null 2>&1 || die "Xcode not found — needed to build dotsync.app"
security find-identity -v -p codesigning | grep -q "Developer ID Application: Numchida (GR53VV7ZD2)" \
  || die "signing identity 'Developer ID Application: Numchida (GR53VV7ZD2)' not found"

[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || die "Not on main branch"
git diff --quiet && git diff --cached --quiet || die "Uncommitted changes — commit/stash first"

step "Syncing with origin"
git fetch origin
git merge-base --is-ancestor origin/main main \
  || die "origin/main has commits main lacks — pull first"
ok "main contains origin/main"

# 1. current version ---------------------------------------------------------
CURRENT=$(grep -E '^version = "[0-9]+\.[0-9]+\.[0-9]+"' pyproject.toml | head -1 | cut -d'"' -f2)
[[ -n "$CURRENT" ]] || die "Could not parse current version from pyproject.toml"
step "현재 버전: v$CURRENT"

step "Checking pytest runner"
PYTEST_RUNNER=()
if [[ -n "${PYTHON:-}" ]]; then
  "$PYTHON" -m pytest --version >/dev/null 2>&1 \
    || die "pytest is not available for $PYTHON — install test deps before releasing"
  PYTEST_RUNNER=("$PYTHON" -m pytest)
  ok "pytest available via $PYTHON"
else
  DEFAULT_PY=".venv/bin/python3"
  if "$DEFAULT_PY" -m pytest --version >/dev/null 2>&1; then
    PYTEST_RUNNER=("$DEFAULT_PY" -m pytest)
    ok "pytest available via $DEFAULT_PY"
  elif command -v uv >/dev/null 2>&1 \
      && uv run --with pytest python -m pytest --version >/dev/null 2>&1; then
    PYTEST_RUNNER=(uv run --with pytest python -m pytest)
    ok "pytest available via uv run --with pytest"
  else
    die "pytest is not available via $DEFAULT_PY or uv run --with pytest — install test deps before releasing"
  fi
fi

# 2. ask bump kind -----------------------------------------------------------
echo
echo "1) patch  (v$(echo "$CURRENT" | awk -F. '{printf "%d.%d.%d", $1, $2, $3+1}')) — 버그 수정, 성능 개선"
echo "2) minor  (v$(echo "$CURRENT" | awk -F. '{printf "%d.%d.0", $1, $2+1}')) — 새 기능 추가"
echo "3) major  (v$(echo "$CURRENT" | awk -F. '{printf "%d.0.0", $1+1}')) — 핵심 아키텍처 변경"
read -rp "선택 [1/2/3]: " choice

IFS='.' read -r MAJ MIN PAT <<< "$CURRENT"
case "$choice" in
  1) PAT=$((PAT+1)) ;;
  2) MIN=$((MIN+1)); PAT=0 ;;
  3) MAJ=$((MAJ+1)); MIN=0; PAT=0 ;;
  *) die "Invalid choice: $choice" ;;
esac
NEW="${MAJ}.${MIN}.${PAT}"

step "New version: v$NEW"

git rev-parse -q --verify "refs/tags/v$NEW" >/dev/null && die "tag v$NEW already exists locally"
git ls-remote --exit-code --tags origin "v$NEW" >/dev/null 2>&1 && die "tag v$NEW already exists on origin"

# 3. bump version strings ----------------------------------------------------
step "Bumping version strings"
PLACEHOLDER="0000000000000000000000000000000000000000000000000000000000000000"
# pyproject.toml
sed -i.bak -E "s/^version = \"[0-9]+\.[0-9]+\.[0-9]+\"/version = \"$NEW\"/" pyproject.toml
# lib/dotsync/__init__.py
sed -i.bak -E "s/^__version__ = \"[0-9]+\.[0-9]+\.[0-9]+\"/__version__ = \"$NEW\"/" lib/dotsync/__init__.py
# Formula url
sed -i.bak -E "s|/v[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz|/v${NEW}.tar.gz|" Formula/dotsync.rb
# reset sha256 to placeholder (patched with the real value in step 7)
sed -i.bak -E "s/sha256 \"[a-f0-9]{64}\"/sha256 \"$PLACEHOLDER\"/" Formula/dotsync.rb
rm -f pyproject.toml.bak lib/dotsync/__init__.py.bak Formula/dotsync.rb.bak
sed -i.bak -E "s/^(    MARKETING_VERSION: )\"[0-9]+\.[0-9]+\.[0-9]+\"/\1\"$NEW\"/" macos/project.yml
sed -i.bak -E "s/^  version \"[0-9]+\.[0-9]+\.[0-9]+\"/  version \"$NEW\"/" Casks/dotsync-app.rb
rm -f macos/project.yml.bak Casks/dotsync-app.rb.bak
# sed silently no-ops when a pattern doesn't match — verify every rewrite took.
grep -q "^version = \"$NEW\"" pyproject.toml || die "version bump failed in pyproject.toml"
grep -q "^__version__ = \"$NEW\"" lib/dotsync/__init__.py || die "version bump failed in lib/dotsync/__init__.py"
grep -q "/v${NEW}.tar.gz" Formula/dotsync.rb || die "url bump failed in Formula/dotsync.rb"
grep -q "sha256 \"$PLACEHOLDER\"" Formula/dotsync.rb || die "sha256 placeholder reset failed in Formula/dotsync.rb"
grep -q "MARKETING_VERSION: \"$NEW\"" macos/project.yml || die "version bump failed in macos/project.yml"
grep -q "version \"$NEW\"" Casks/dotsync-app.rb || die "version bump failed in Casks/dotsync-app.rb"
ok "pyproject.toml, lib/dotsync/__init__.py, Formula/dotsync.rb, macos/project.yml, Casks/dotsync-app.rb updated"

# 4. tests must pass before tagging ------------------------------------------
step "Running tests"
"${PYTEST_RUNNER[@]}" -q || die "Tests failed — aborting release. Changes left in place."
ok "All tests passed"
swift test --package-path macos/DotsyncKit -q || die "Swift tests failed — aborting release. Changes left in place."
ok "Swift tests passed"

# 5. build the app -------------------------------------------------------------
step "Building dotsync.app"
bash scripts/build-app.sh "$NEW" >/dev/null || die "app build failed — aborting release. Changes left in place."
APP_ZIP="dist/dotsync-app-$NEW.zip"
APP_SHA=$(shasum -a 256 "$APP_ZIP" | awk '{print $1}')
sed -i.bak -E "s/sha256 \"[a-f0-9]{64}\"/sha256 \"$APP_SHA\"/" Casks/dotsync-app.rb
rm -f Casks/dotsync-app.rb.bak
grep -q "sha256 \"$APP_SHA\"" Casks/dotsync-app.rb || die "sha256 patch failed in Casks/dotsync-app.rb"
ok "$APP_ZIP ($APP_SHA)"

# 6. commit + tag, push the tag only ------------------------------------------
# Pushing the tag uploads its commit objects without moving origin/main, and
# GitHub starts serving the tag tarball immediately — main stays on the
# previous release until the real sha is committed below.
step "Commit + tag, push tag only"
git add pyproject.toml lib/dotsync/__init__.py Formula/dotsync.rb macos/project.yml Casks/dotsync-app.rb
git commit -m "chore: bump version to $NEW"
git tag -a "v$NEW" -m "v$NEW"
git push origin "v$NEW"
ok "tag v$NEW pushed (origin/main still on v$CURRENT)"

# 7. compute sha256 of the tag tarball, patch the Formula ----------------------
step "Computing tarball sha256"
TARBALL_URL="https://github.com/changja88/homebrew-dotsync/archive/refs/tags/v${NEW}.tar.gz"
RETRIES="${RELEASE_CURL_RETRIES:-5}"
DELAY="${RELEASE_CURL_DELAY:-2}"
SHA=""
for ((i = 1; i <= RETRIES; i++)); do
  if SHA=$(curl -fsSL "$TARBALL_URL" | shasum -a 256 | awk '{print $1}'); then
    break
  fi
  SHA=""
  [[ $i -lt $RETRIES ]] && { warn "tarball fetch failed (attempt $i/$RETRIES), retrying"; sleep "$DELAY"; }
done
if [[ ! "$SHA" =~ ^[a-f0-9]{64}$ ]]; then
  die "could not fetch $TARBALL_URL — origin/main was NOT touched (tap still serves v$CURRENT).
  Finish manually once the network is back:
    curl -sL $TARBALL_URL | shasum -a 256
    # put that hash into Formula/dotsync.rb sha256, then:
    git add Formula/dotsync.rb && git commit -m \"chore: real sha256 for v$NEW\"
    gh release create v$NEW $APP_ZIP --title v$NEW --notes 'Release v$NEW' && git push origin main"
fi
ok "sha256: $SHA"

step "Patching Formula sha256"
sed -i.bak -E "s/sha256 \"[a-f0-9]{64}\"/sha256 \"$SHA\"/" Formula/dotsync.rb
rm -f Formula/dotsync.rb.bak
grep -q "sha256 \"$SHA\"" Formula/dotsync.rb || die "sha256 patch failed in Formula/dotsync.rb"
git add Formula/dotsync.rb
git commit -m "chore: real sha256 for v$NEW"
ok "Formula sha256 patched"

# 8. GitHub release with the app — the Cask downloads it ------------------------
step "Creating GitHub release with dotsync.app"
gh release create "v$NEW" "$APP_ZIP" --title "v$NEW" --notes "Release v$NEW" >/dev/null \
  || die "gh release create failed — origin/main was NOT pushed (tap still serves v$CURRENT). Retry:
    gh release create v$NEW $APP_ZIP --title v$NEW --notes 'Release v$NEW' && git push origin main"
ok "release v$NEW created with $APP_ZIP"

# 9. push main — bump + sha land together --------------------------------------
step "Pushing main"
git push origin main
ok "origin/main: v$CURRENT → v$NEW (placeholder never published)"

echo
printf "${GREEN}✔ Release complete: v$NEW${RESET}\n"
echo "Verify: brew update && brew upgrade && brew install --cask changja88/dotsync/dotsync-app"
