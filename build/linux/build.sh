#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
# Builds the Linux app:  build/dist/linux/PlateSolver/PlateSolver  and a .tar.gz for sharing.
#
#   bash build/linux/build.sh
#
# Needs Python 3.10+ with venv (Debian/Ubuntu: sudo apt install python3-venv). The app runs on
# Linux distributions at least as new as the one it was built on.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$PWD"
OUT="$ROOT/build/dist/linux"
VENV="$ROOT/build/.venv-linux"
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
"$P" -m PyInstaller build/platesolver.spec --noconfirm --clean --distpath "$OUT" --workpath "$ROOT/build/work/linux"
APPDIR="$OUT/PlateSolver"
cp build/icons/platesolver.png "$APPDIR/platesolver.png"
cp build/linux/platesolver.desktop build/linux/install.sh "$APPDIR/"
cp LICENSE "$APPDIR/LICENSE.txt"
cp THIRD-PARTY-NOTICES.md "$APPDIR/THIRD-PARTY-NOTICES.txt"
chmod +x "$APPDIR/install.sh"

echo "[4/5] Self-test of the built app..."
QT_QPA_PLATFORM=offscreen "$APPDIR/PlateSolver" --selftest "$OUT/selftest.txt" || { echo "The self-test failed - see above."; exit 1; }

echo "[5/5] Packaging..."
TGZ="$OUT/PlateSolver-$VERSION-linux-$ARCH.tar.gz"
rm -f "$OUT"/PlateSolver-*.tar.gz
tar -C "$OUT" -czf "$TGZ" PlateSolver

echo
echo "Done. Results in $OUT:"
echo "  PlateSolver/PlateSolver   - the app (PlateSolver/install.sh adds it to the applications menu)"
echo "  $(basename "$TGZ")   - the same folder, packed for sharing"
