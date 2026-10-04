#!/usr/bin/env bash
# Build, sign and zip dotsync.app.
#   scripts/build-app.sh VERSION   →   dist/dotsync-app-VERSION.zip
#
# Built unsigned, then signed inside-out with the Developer ID: hardened
# runtime, the widget sandboxed, both in the app group. No provisioning
# profile and no notarization (spike, 2026-10-04).
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=${1:?usage: scripts/build-app.sh VERSION}
# A new build number on every build: macOS re-reads the widget list and
# restarts the widget only when CFBundleVersion changes.
BUILD=$(date +%Y%m%d.%H%M%S)
IDENTITY="${DOTSYNC_SIGN_IDENTITY:-Developer ID Application: Numchida (GR53VV7ZD2)}"
APP=build/app/Build/Products/Release/dotsync.app

(cd macos && xcodegen generate --quiet)
xcodebuild -project macos/dotsync.xcodeproj -scheme dotsync -configuration Release \
  -derivedDataPath build/app CODE_SIGNING_ALLOWED=NO MARKETING_VERSION="$VERSION" CURRENT_PROJECT_VERSION="$BUILD" \
  build -quiet
# xcodebuild never re-dates the bundle folder, and the Dock keeps showing an
# app's old icon while that date stays the same — on this Mac and after brew
# installs the zip, which carries the date.
touch "$APP"
codesign --force --options runtime --timestamp \
  --entitlements macos/Widget/Widget.entitlements -s "$IDENTITY" "$APP/Contents/PlugIns/dotsyncWidget.appex"
codesign --force --options runtime --timestamp \
  --entitlements macos/App/App.entitlements -s "$IDENTITY" "$APP"
codesign --verify --deep --strict "$APP"
mkdir -p dist
rm -f "dist/dotsync-app-$VERSION.zip"
ditto -c -k --keepParent "$APP" "dist/dotsync-app-$VERSION.zip"
echo "dist/dotsync-app-$VERSION.zip"
