#!/bin/zsh
# Builds dist/Dolmi.app and dist/Dolmi-<version>.dmg (Apple Silicon or Intel, whichever builds it).
# Run from anywhere: ./packaging/build.sh   (needs Xcode Command Line Tools: xcode-select --install)
set -euo pipefail
MAC="${0:A:h:h}"
cd "$MAC"
VERSION=$(cat VERSION)

echo "[1/5] Python environment"
[[ -x venv/bin/python ]] || /usr/bin/env python3.12 -m venv venv
venv/bin/python -m pip install -q --upgrade pip
venv/bin/python -m pip install -q -r requirements.txt pyinstaller

echo "[2/5] System audio helper"
swiftc -O audio/dolmi-audio.swift -o audio/dolmi-audio

echo "[3/5] Dolmi.app"
rm -rf build dist
venv/bin/pyinstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build packaging/dolmi.spec

echo "[4/5] Sign (ad hoc: not notarized)"
codesign --force --deep --sign - dist/Dolmi.app
codesign --verify --deep --strict dist/Dolmi.app

echo "[5/5] Disk image"
STAGE=$(mktemp -d)
cp -R dist/Dolmi.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Dolmi $VERSION" -srcfolder "$STAGE" -ov -format UDZO "dist/Dolmi-$VERSION.dmg"
rm -rf "$STAGE"
echo "Done: dist/Dolmi.app and dist/Dolmi-$VERSION.dmg"
