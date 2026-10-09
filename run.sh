#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
# Starts PlateSolver from the source on macOS and Linux. The first start creates a private Python
# environment in the .venv folder and installs the libraries (takes a few minutes).
#   bash run.sh            normal start
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
if [ ! -x .venv/bin/python ]; then
    echo "Creating the Python environment - first start only..."
    "$PY" -m venv .venv || { echo "Python 3.10 or newer with venv is needed (Debian/Ubuntu: sudo apt install python3-venv)."; exit 1; }
fi
if ! cmp -s requirements.txt .venv/installed-requirements.txt; then
    echo "Installing libraries..."
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
    cp requirements.txt .venv/installed-requirements.txt
fi
exec .venv/bin/python -m platesolver "$@"
