#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "Build the macOS app on a Mac." >&2
    exit 1
fi
if [ "$(uname -m)" != "arm64" ]; then
    echo "This initial build supports Apple Silicon (arm64) only." >&2
    exit 1
fi

PYTHON=${PYTHON:-python3}
"$PYTHON" -m PyInstaller --noconfirm kkMemorizeVocabulary-macos.spec
ditto -c -k --sequesterRsrc --keepParent \
    dist/kkMemorizeVocabulary.app dist/kkMemorizeVocabulary-macos-arm64.zip
echo "Created dist/kkMemorizeVocabulary.app and dist/kkMemorizeVocabulary-macos-arm64.zip"
