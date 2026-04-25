#!/usr/bin/env bash
# scripts/demo.sh — Walk through the full first-time user journey.
#
# Steps:
#   1. brew install (using current working tree as a local tarball + temp formula)
#      — This shows the same `Pouring` + caveats screen a real user sees.
#   2. dotsync welcome
#   3. dotsync init
#   4. dotsync from --all
#   5. dotsync status
#   6. (optional) brew uninstall + cleanup
#
# Safe by default: only the `zsh` app is tracked in the demo, and `dotsync to`
# is intentionally NOT executed (it would overwrite your real local files).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
DEMO_DIR="${DEMO_DIR:-/tmp/dotsync-demo}"
TARBALL=/tmp/dotsync-demo.tar.gz
LOCAL_FORMULA=/tmp/dotsync-demo.rb

# colors (subset of ui.py PRIMARY/DIM/BOLD)
PURPLE='\033[38;2;167;139;250m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

step()  { echo; printf "${PURPLE}▶${RESET} ${BOLD}%s${RESET}\n" "$*"; echo; }
note()  { printf "${DIM}  %s${RESET}\n" "$*"; }
pause() { printf "${DIM}  press enter to continue...${RESET}"; read -r _; }

# --- preflight --------------------------------------------------------------
command -v brew >/dev/null    || { echo "brew not found — install Homebrew first"; exit 1; }
command -v shasum >/dev/null  || { echo "shasum not available"; exit 1; }
command -v git >/dev/null     || { echo "git not available"; exit 1; }

if [[ -d "$DEMO_DIR" ]]; then
  printf "${DIM}  $DEMO_DIR already exists — remove it? [y/N]: ${RESET}"
  read -r yn
  [[ "$yn" =~ ^[Yy]$ ]] || { echo "aborted"; exit 1; }
  rm -rf "$DEMO_DIR"
fi

# --- step 1: simulated brew install -----------------------------------------
step "1/5  brew install (using your current working tree)"
note "packaging working tree as tarball: $TARBALL"
git -C "$REPO" archive --format=tar.gz \
  --prefix=homebrew-dotsync-demo/ -o "$TARBALL" HEAD
SHA=$(shasum -a 256 "$TARBALL" | awk '{print $1}')
note "sha256: $SHA"

cp "$REPO/Formula/dotsync.rb" "$LOCAL_FORMULA"
sed -i.bak -E "s|url \".*\"|url \"file://$TARBALL\"|" "$LOCAL_FORMULA"
sed -i.bak -E "s|sha256 \"[a-f0-9]{64}\"|sha256 \"$SHA\"|" "$LOCAL_FORMULA"
rm -f "$LOCAL_FORMULA.bak"

# uninstall any leftover from a previous demo run (silent if not installed)
brew uninstall dotsync >/dev/null 2>&1 || true

echo
brew install --build-from-source "$LOCAL_FORMULA"
echo
note "installed: $(which dotsync)  ($(dotsync --version))"
pause

# --- step 2: welcome --------------------------------------------------------
step "2/5  dotsync welcome — the first thing a new user runs"
dotsync welcome
pause

# --- step 3: init -----------------------------------------------------------
step "3/5  dotsync init — pick a sync folder, auto-detect apps"
note "demo uses --apps zsh and --dir $DEMO_DIR  (safe sandbox)"
echo
dotsync init --dir "$DEMO_DIR" --apps zsh --yes --quiet
pause

# --- step 4: from -----------------------------------------------------------
step "4/5  dotsync from --all — pull current local configs into the folder"
echo
DOTSYNC_DIR="$DEMO_DIR" dotsync from --all
pause

# --- step 5: status ---------------------------------------------------------
step "5/5  dotsync status — sha256 diff per file"
echo
DOTSYNC_DIR="$DEMO_DIR" dotsync status
echo
note "skipping 'dotsync to --all' — it would overwrite your real local files"
note "to try it on a sandbox, modify $DEMO_DIR/zsh/.zshrc then rerun: dotsync to --all"
pause

# --- cleanup ----------------------------------------------------------------
step "cleanup"
printf "${DIM}  uninstall dotsync? [Y/n]: ${RESET}"
read -r yn
if [[ ! "$yn" =~ ^[Nn]$ ]]; then
  brew uninstall dotsync
fi
printf "${DIM}  remove demo folder $DEMO_DIR? [Y/n]: ${RESET}"
read -r yn
if [[ ! "$yn" =~ ^[Nn]$ ]]; then
  rm -rf "$DEMO_DIR"
fi
rm -f "$TARBALL" "$LOCAL_FORMULA"

echo
printf "${PURPLE}✔${RESET} demo complete\n"
