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

echo "[4/5] Sign"
# A fixed local certificate keeps the System Audio Recording permission across rebuilds
# (ad-hoc signatures change every build). Not notarized: other Macs still ask once (right-click → Open).
./packaging/make-signing-identity.sh || true
if security find-certificate -c "Dolmi Local Signing" >/dev/null 2>&1; then
  codesign --force --deep --sign "Dolmi Local Signing" dist/Dolmi.app
else
  echo "    no local signing certificate: ad-hoc signing (macOS asks for audio permission again after rebuilds)"
  codesign --force --deep --sign - dist/Dolmi.app
fi
codesign --verify --deep --strict dist/Dolmi.app

echo "[5/5] Disk image"
STAGE=$(mktemp -d)
cp -R dist/Dolmi.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Dolmi $VERSION" -srcfolder "$STAGE" -ov -format UDZO "dist/Dolmi-$VERSION.dmg"
rm -rf "$STAGE"
echo "Done: dist/Dolmi.app and dist/Dolmi-$VERSION.dmg"
