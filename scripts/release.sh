#!/usr/bin/env bash
# Interactive release script for dotsync.
#
# Steps:
#   1. Read current version from pyproject.toml
#   2. Ask: major / minor / patch (1 / 2 / 3)
#   3. Bump version in pyproject.toml, lib/dotsync/__init__.py, Formula/dotsync.rb
#   4. Reset Formula sha256 to placeholder
#   5. Commit, push main, tag, push tag
#   6. gh release create
#   7. Compute sha256 of release tarball
#   8. Patch Formula sha256, commit, push
set -euo pipefail

cd "$(dirname "$0")/.."

GREEN='\033[32m'; YELLOW='\033[33m'; CYAN='\033[36m'; RED='\033[31m'; RESET='\033[0m'
step() { printf "${CYAN}▶ %s${RESET}\n" "$*"; }
ok()   { printf "  ${GREEN}✓${RESET} %s\n" "$*"; }
warn() { printf "  ${YELLOW}⚠${RESET} %s\n" "$*"; }
die()  { printf "  ${RED}✗${RESET} %s\n" "$*" >&2; exit 1; }

# 0. preflight ---------------------------------------------------------------
command -v gh >/dev/null 2>&1 || die "gh CLI not found (brew install gh && gh auth login)"
command -v shasum >/dev/null 2>&1 || die "shasum not available"

[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || die "Not on main branch"
git diff --quiet && git diff --cached --quiet || die "Uncommitted changes — commit/stash first"

# 1. current version ---------------------------------------------------------
CURRENT=$(grep -E '^version = "[0-9]+\.[0-9]+\.[0-9]+"' pyproject.toml | head -1 | cut -d'"' -f2)
[[ -n "$CURRENT" ]] || die "Could not parse current version from pyproject.toml"
step "Current version: $CURRENT"

# 2. ask bump kind -----------------------------------------------------------
echo
echo "Which part to bump?"
echo "  1) major  ($(echo "$CURRENT" | awk -F. '{printf "%d.0.0", $1+1}'))"
echo "  2) minor  ($(echo "$CURRENT" | awk -F. '{printf "%d.%d.0", $1, $2+1}'))"
echo "  3) patch  ($(echo "$CURRENT" | awk -F. '{printf "%d.%d.%d", $1, $2, $3+1}'))"
read -rp "Choice [1/2/3]: " choice

IFS='.' read -r MAJ MIN PAT <<< "$CURRENT"
case "$choice" in
  1) MAJ=$((MAJ+1)); MIN=0; PAT=0 ;;
  2) MIN=$((MIN+1)); PAT=0 ;;
  3) PAT=$((PAT+1)) ;;
  *) die "Invalid choice: $choice" ;;
esac
NEW="${MAJ}.${MIN}.${PAT}"

step "New version: v$NEW"
read -rp "Proceed? [y/N]: " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || die "Cancelled"

# 3. bump version strings ----------------------------------------------------
step "Bumping version strings"
# pyproject.toml
sed -i.bak -E "s/^version = \"[0-9]+\.[0-9]+\.[0-9]+\"/version = \"$NEW\"/" pyproject.toml
# lib/dotsync/__init__.py
sed -i.bak -E "s/^__version__ = \"[0-9]+\.[0-9]+\.[0-9]+\"/__version__ = \"$NEW\"/" lib/dotsync/__init__.py
# Formula url + test assertion
sed -i.bak -E "s|/v[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz|/v${NEW}.tar.gz|" Formula/dotsync.rb
sed -i.bak -E "s/dotsync [0-9]+\.[0-9]+\.[0-9]+/dotsync $NEW/" Formula/dotsync.rb
# reset sha256 to placeholder (filled in step 7)
sed -i.bak -E "s/sha256 \"[a-f0-9]{64}\"/sha256 \"0000000000000000000000000000000000000000000000000000000000000000\"/" Formula/dotsync.rb
rm -f pyproject.toml.bak lib/dotsync/__init__.py.bak Formula/dotsync.rb.bak
ok "pyproject.toml, lib/dotsync/__init__.py, Formula/dotsync.rb updated"

# 4. tests must pass before tagging ------------------------------------------
step "Running tests"
PY="${PYTHON:-.venv/bin/python3}"
"$PY" -m pytest -q || die "Tests failed — aborting release. Changes left in place."
ok "All tests passed"

# 5. commit + tag + push -----------------------------------------------------
step "Commit + tag + push"
git add pyproject.toml lib/dotsync/__init__.py Formula/dotsync.rb
git commit -m "chore: bump version to $NEW"
git push origin main
git tag -a "v$NEW" -m "v$NEW"
git push origin "v$NEW"
ok "main + v$NEW pushed"

# 6. GitHub release ----------------------------------------------------------
step "Creating GitHub release"
gh release create "v$NEW" --title "v$NEW" --notes "Release v$NEW"
ok "release v$NEW created"

# 7. compute sha256 of the release tarball -----------------------------------
step "Computing tarball sha256"
TARBALL_URL="https://github.com/changja88/homebrew-dotsync/archive/refs/tags/v${NEW}.tar.gz"
SHA=$(curl -sL "$TARBALL_URL" | shasum -a 256 | awk '{print $1}')
[[ "${#SHA}" == 64 ]] || die "sha256 length unexpected: $SHA"
ok "sha256: $SHA"

# 8. patch formula + commit + push -------------------------------------------
step "Patching Formula sha256 + push"
sed -i.bak -E "s/sha256 \"[a-f0-9]{64}\"/sha256 \"$SHA\"/" Formula/dotsync.rb
rm -f Formula/dotsync.rb.bak
git add Formula/dotsync.rb
git commit -m "chore: real sha256 for v$NEW"
git push origin main
ok "Formula pushed"

echo
printf "${GREEN}✔ Release complete: v$NEW${RESET}\n"
echo "Verify: brew install changja88/dotsync/dotsync && dotsync --version"
