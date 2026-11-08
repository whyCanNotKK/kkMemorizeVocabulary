#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This launcher is for macOS."
    read -r -p "Press Return to close... " _
    exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
    echo "This initial macOS source launcher supports Apple Silicon (arm64) only."
    read -r -p "Press Return to close... " _
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required. Install Python 3.12, then double-click this file again."
    read -r -p "Press Return to close... " _
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    echo "Creating the project virtual environment..."
    python3 -m venv .venv
fi

if ! .venv/bin/python -c 'import PySide6' >/dev/null 2>&1; then
    echo "Installing the locked macOS dependencies (internet access required)..."
    if ! .venv/bin/python -m pip install -r requirements-macos.lock.txt; then
        echo "Dependency installation failed. Check the output above and try again."
        read -r -p "Press Return to close... " _
        exit 1
    fi
fi

DATA_DIR="$ROOT/artifacts/development"
mkdir -p "$DATA_DIR"
exec .venv/bin/python main.py --data-dir "$DATA_DIR"
