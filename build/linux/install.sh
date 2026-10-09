#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
# Adds PlateSolver (this folder) to your applications menu. Run it from the unpacked folder.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.local/share/applications"
sed "s|PLATESOLVER_DIR|$DIR|g" "$DIR/platesolver.desktop" > "$HOME/.local/share/applications/platesolver.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "PlateSolver added to the applications menu (it runs from $DIR)."
