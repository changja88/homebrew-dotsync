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
DEFAULT_DEMO_DIR="${HOME}/Desktop/dotsync_config"
TARBALL=/tmp/dotsync-demo.tar.gz

# Homebrew now requires formulae to live in a tap. We create a throwaway tap
# under brew's tap directory and clean it up at the end.
TAP_USER="local"
TAP_NAME="dotsync-demo"
TAP_DIR="$(brew --repository)/Library/Taps/${TAP_USER}/homebrew-${TAP_NAME}"
TAP_REF="${TAP_USER}/${TAP_NAME}/dotsync"
LOCAL_FORMULA="${TAP_DIR}/Formula/dotsync.rb"

# colors (mirrors ui.py PRIMARY/DIM/BOLD/GREEN)
PURPLE='\033[38;2;167;139;250m'
GREEN='\033[32m'
DIM='\033[2m'
BOLD='\033[1m'
RESET='\033[0m'

step()  { echo; printf "${PURPLE}▶${RESET} ${BOLD}%s${RESET}\n" "$*"; echo; }
note()  { printf "${DIM}  %s${RESET}\n" "$*"; }
pause() { printf "${DIM}  press enter to continue...${RESET}"; read -r _; }
ask()   { printf "${PURPLE}${BOLD}?${RESET} %s ${DIM}[%s]${RESET} ${PURPLE}›${RESET} " "$1" "$2"; }
ask_yn(){ printf "${PURPLE}${BOLD}?${RESET} %s ${DIM}[%s]${RESET} ${PURPLE}›${RESET} " "$1" "$2"; }

# --- preflight --------------------------------------------------------------
command -v brew >/dev/null    || { echo "brew not found — install Homebrew first"; exit 1; }
command -v shasum >/dev/null  || { echo "shasum not available"; exit 1; }
command -v git >/dev/null     || { echo "git not available"; exit 1; }

# Idempotent: silently scrub any leftover from a previous run so re-running
# `make demo` is always safe. (DEMO_DIR is asked about explicitly in step 3.)
rm -f "$TARBALL"
[[ -d "$TAP_DIR" ]] && rm -rf "$TAP_DIR"
brew uninstall dotsync >/dev/null 2>&1 || true

DEMO_DIR=""  # determined in step 3

# --- step 1: simulated brew install -----------------------------------------
step "1/5  brew install (using your current working tree)"
note "packaging working tree as tarball: $TARBALL"
git -C "$REPO" archive --format=tar.gz \
  --prefix=homebrew-dotsync-demo/ -o "$TARBALL" HEAD
SHA=$(shasum -a 256 "$TARBALL" | awk '{print $1}')
note "sha256: $SHA"

VERSION=$(grep -E '^__version__' "$REPO/lib/dotsync/__init__.py" | head -1 | cut -d'"' -f2)
note "version: $VERSION"

note "creating throwaway tap at $TAP_DIR"
mkdir -p "${TAP_DIR}/Formula"
cp "$REPO/Formula/dotsync.rb" "$LOCAL_FORMULA"
sed -i.bak -E "s|url \".*\"|url \"file://$TARBALL\"|" "$LOCAL_FORMULA"
sed -i.bak -E "s|sha256 \"[a-f0-9]{64}\"|sha256 \"$SHA\"|" "$LOCAL_FORMULA"
# Homebrew can't infer version from a file:// URL → inject `version "..."` after url
awk -v ver="$VERSION" '
  /^  url "file:/ { print; print "  version \"" ver "\""; next }
  { print }
' "$LOCAL_FORMULA" > "${LOCAL_FORMULA}.tmp" && mv "${LOCAL_FORMULA}.tmp" "$LOCAL_FORMULA"
rm -f "$LOCAL_FORMULA.bak"

echo
brew install --build-from-source "$TAP_REF"
echo
note "installed: $(which dotsync)  ($(dotsync --version))"
note "(Homebrew prints 'Caveats' twice — once during install, once at the end. ignore the duplicate.)"
note "next: dotsync welcome"
pause

# --- step 2: welcome --------------------------------------------------------
step "2/5  dotsync welcome — the first thing a new user runs"
dotsync welcome
pause

# --- step 3: init -----------------------------------------------------------
step "3/5  dotsync init — first command from the quickstart"
note "demo runs it for you, locked to --apps zsh for safety."
note "you only need to choose where the sync folder should live."
echo
ask "sync folder absolute path" "$DEFAULT_DEMO_DIR"
read -r dir_input
DEMO_DIR="${dir_input:-$DEFAULT_DEMO_DIR}"

if [[ -d "$DEMO_DIR" ]]; then
  ask_yn "$DEMO_DIR already exists — remove and continue?" "y/N"
  read -r yn
  [[ "$yn" =~ ^[Yy]$ ]] || { echo "aborted"; exit 1; }
  rm -rf "$DEMO_DIR"
fi
echo
dotsync init --dir "$DEMO_DIR" --apps zsh --yes --quiet
echo
note "next: dotsync from --all (snapshots local app configs into the folder)"
pause

# --- step 4: from -----------------------------------------------------------
step "4/5  dotsync from --all — snapshot local configs into the folder"
echo
DOTSYNC_DIR="$DEMO_DIR" dotsync from --all
note "next: dotsync status (compares local vs folder, file by file)"
pause

# --- step 5: status ---------------------------------------------------------
step "5/5  dotsync status — sha256 diff per tracked file"
echo
DOTSYNC_DIR="$DEMO_DIR" dotsync status
echo
note "skipping 'dotsync to --all' — that would overwrite your real local files."
note "to try 'to' safely: edit $DEMO_DIR/zsh/.zshrc, then run dotsync to --all"
pause

# --- cleanup ----------------------------------------------------------------
step "cleanup"
ask_yn "uninstall dotsync?" "Y/n"
read -r yn
if [[ ! "$yn" =~ ^[Nn]$ ]]; then
  brew uninstall dotsync
fi
ask_yn "remove demo folder $DEMO_DIR?" "Y/n"
read -r yn
if [[ ! "$yn" =~ ^[Nn]$ ]]; then
  rm -rf "$DEMO_DIR"
fi
note "removing throwaway tap and tarball"
rm -rf "$TAP_DIR" "$TARBALL"

echo
printf "${GREEN}✔${RESET} ${BOLD}demo complete${RESET}\n"
