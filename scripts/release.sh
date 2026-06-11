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
#   1. Preflight: on main, clean tree, in sync with origin
#   2. Ask: patch / minor / major
#   3. Bump version in pyproject.toml, lib/dotsync/__init__.py, Formula/dotsync.rb
#      (Formula sha256 reset to placeholder — the tarball can't contain its
#      own hash, so the real value is patched in step 7)
#   4. Run tests
#   5. Commit bump + tag (local only), push the TAG only
#   6. Download the tag tarball, compute sha256
#   7. Patch Formula sha256, commit
#   8. Push main (bump + sha commits land together)
#   9. gh release create — best-effort; the tap works from the tag tarball
#      alone, so a missing/unauthenticated gh must not abort the release
set -euo pipefail

cd "$(dirname "$0")/.."

GREEN='\033[32m'; YELLOW='\033[33m'; CYAN='\033[36m'; RED='\033[31m'; RESET='\033[0m'
step() { printf "${CYAN}▶ %s${RESET}\n" "$*"; }
ok()   { printf "  ${GREEN}✓${RESET} %s\n" "$*"; }
warn() { printf "  ${YELLOW}⚠${RESET} %s\n" "$*"; }
die()  { printf "  ${RED}✗${RESET} %s\n" "$*" >&2; exit 1; }

# 0. preflight ---------------------------------------------------------------
command -v shasum >/dev/null 2>&1 || die "shasum not available"
if ! command -v gh >/dev/null 2>&1; then
  warn "gh CLI not found — GitHub release object will be skipped (tap works without it)"
elif ! gh auth status >/dev/null 2>&1; then
  warn "gh not authenticated (gh auth login) — GitHub release object will be skipped"
fi

[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || die "Not on main branch"
git diff --quiet && git diff --cached --quiet || die "Uncommitted changes — commit/stash first"

step "Syncing with origin"
git fetch origin
[[ "$(git rev-parse main)" == "$(git rev-parse origin/main)" ]] \
  || die "local main != origin/main — pull/push first"
ok "main is in sync with origin"

# 1. current version ---------------------------------------------------------
CURRENT=$(grep -E '^version = "[0-9]+\.[0-9]+\.[0-9]+"' pyproject.toml | head -1 | cut -d'"' -f2)
[[ -n "$CURRENT" ]] || die "Could not parse current version from pyproject.toml"
step "현재 버전: v$CURRENT"

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
# Formula url + test assertion
sed -i.bak -E "s|/v[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz|/v${NEW}.tar.gz|" Formula/dotsync.rb
sed -i.bak -E "s/dotsync [0-9]+\.[0-9]+\.[0-9]+/dotsync $NEW/" Formula/dotsync.rb
# reset sha256 to placeholder (patched with the real value in step 7)
sed -i.bak -E "s/sha256 \"[a-f0-9]{64}\"/sha256 \"$PLACEHOLDER\"/" Formula/dotsync.rb
rm -f pyproject.toml.bak lib/dotsync/__init__.py.bak Formula/dotsync.rb.bak
# sed silently no-ops when a pattern doesn't match — verify every rewrite took.
grep -q "^version = \"$NEW\"" pyproject.toml || die "version bump failed in pyproject.toml"
grep -q "^__version__ = \"$NEW\"" lib/dotsync/__init__.py || die "version bump failed in lib/dotsync/__init__.py"
grep -q "/v${NEW}.tar.gz" Formula/dotsync.rb || die "url bump failed in Formula/dotsync.rb"
grep -q "dotsync $NEW" Formula/dotsync.rb || die "test assertion bump failed in Formula/dotsync.rb"
grep -q "sha256 \"$PLACEHOLDER\"" Formula/dotsync.rb || die "sha256 placeholder reset failed in Formula/dotsync.rb"
ok "pyproject.toml, lib/dotsync/__init__.py, Formula/dotsync.rb updated"

# 4. tests must pass before tagging ------------------------------------------
step "Running tests"
PY="${PYTHON:-.venv/bin/python3}"
"$PY" -m pytest -q || die "Tests failed — aborting release. Changes left in place."
ok "All tests passed"

# 5. commit + tag, push the tag only ------------------------------------------
# Pushing the tag uploads its commit objects without moving origin/main, and
# GitHub starts serving the tag tarball immediately — main stays on the
# previous release until the real sha is committed below.
step "Commit + tag, push tag only"
git add pyproject.toml lib/dotsync/__init__.py Formula/dotsync.rb
git commit -m "chore: bump version to $NEW"
git tag -a "v$NEW" -m "v$NEW"
git push origin "v$NEW"
ok "tag v$NEW pushed (origin/main still on v$CURRENT)"

# 6. compute sha256 of the tag tarball ----------------------------------------
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
    git add Formula/dotsync.rb && git commit -m \"chore: real sha256 for v$NEW\" && git push origin main"
fi
ok "sha256: $SHA"

# 7. patch formula -------------------------------------------------------------
step "Patching Formula sha256"
sed -i.bak -E "s/sha256 \"[a-f0-9]{64}\"/sha256 \"$SHA\"/" Formula/dotsync.rb
rm -f Formula/dotsync.rb.bak
grep -q "sha256 \"$SHA\"" Formula/dotsync.rb || die "sha256 patch failed in Formula/dotsync.rb"
git add Formula/dotsync.rb
git commit -m "chore: real sha256 for v$NEW"
ok "Formula sha256 patched"

# 8. push main — bump + sha land together --------------------------------------
step "Pushing main"
git push origin main
ok "origin/main: v$CURRENT → v$NEW (placeholder never published)"

# 9. GitHub release (best-effort — the tap installs from the tag tarball) ------
step "Creating GitHub release"
if gh release create "v$NEW" --title "v$NEW" --notes "Release v$NEW" >/dev/null 2>&1; then
  ok "release v$NEW created"
else
  warn "gh release create failed — tap is fine without it; create later with:"
  warn "  gh release create v$NEW --title v$NEW --notes \"Release v$NEW\""
fi

echo
printf "${GREEN}✔ Release complete: v$NEW${RESET}\n"
echo "Verify: brew update && brew install changja88/dotsync/dotsync && dotsync --version"
