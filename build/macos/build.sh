#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
# Builds the macOS app:  build/dist/macos/PlateSolver.app  and a .dmg disk image for sharing.
#
#   In Terminal:  bash build/macos/build.sh
#
# Needs Python 3.10+ (python.org installer or Homebrew). The app is built for the Mac's own
# processor (Apple Silicon or Intel). It is not signed by Apple: on first start, right-click the
# app > Open (or System Settings > Privacy & Security > Open Anyway).
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$PWD"
OUT="$ROOT/build/dist/macos"
VENV="$ROOT/build/.venv-macos"
PY="${PYTHON:-python3}"

if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating a Python environment for building..."
  "$PY" -m venv "$VENV"
fi
P="$VENV/bin/python"

echo "[1/5] Installing libraries and PyInstaller..."
"$P" -m pip install --upgrade pip >/dev/null
"$P" -m pip install -r requirements.txt pyinstaller
VERSION="$("$P" -c 'import platesolver; print(platesolver.__version__)')"
ARCH="$(uname -m)"
echo "      PlateSolver $VERSION ($ARCH)"

echo "[2/5] Drawing the app icon and fetching the newest OpenNGC catalogue..."
"$P" build/make_icons.py
"$P" tools/update_openngc.py || echo "      Keeping the OpenNGC catalogue already in the program."

echo "[3/5] Building the app (takes a few minutes)..."
"$P" -m PyInstaller build/platesolver.spec --noconfirm --clean --distpath "$OUT" --workpath "$ROOT/build/work/macos"
APP="$OUT/PlateSolver.app"
# ad-hoc signature: required for apps on Apple Silicon, harmless on Intel
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "      (codesign not available - skipped)"

echo "[4/5] Self-test of the built app..."
"$APP/Contents/MacOS/PlateSolver" --selftest "$OUT/selftest.txt" || { echo "The self-test failed - see above."; exit 1; }

echo "[5/5] Making the disk image..."
DMG="$OUT/PlateSolver-$VERSION-macos-$ARCH.dmg"
rm -f "$OUT"/PlateSolver-*.dmg
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
cp LICENSE "$STAGE/LICENSE.txt"
cp THIRD-PARTY-NOTICES.md "$STAGE/THIRD-PARTY-NOTICES.txt"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "PlateSolver $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

echo
echo "Done. Results in $OUT:"
echo "  PlateSolver.app                          - the app (drag it to Applications)"
echo "  $(basename "$DMG")   - disk image for sharing"
