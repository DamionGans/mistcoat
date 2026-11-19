#!/usr/bin/env bash
# Build Mistcoat.app and a drag-to-Applications .dmg on macOS.
#
#   ./packaging/build_mac.sh
#
# Produces dist/Mistcoat.app and dist/Mistcoat-<version>-macos-<arch>.dmg.
#
# Signing: by default the app is ad-hoc signed, which runs on the machine that
# built it and, after a right-click > Open, on others. For distribution without
# Gatekeeper warnings set these (needs a paid Apple Developer account):
#   MAC_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
#   MAC_NOTARY_PROFILE="mistcoat-notary"   # from `xcrun notarytool store-credentials`
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-python3}
ARCH=$(uname -m)

if [ ! -d .venv-mac ]; then
  "$PY" -m venv .venv-mac
fi
source .venv-mac/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt pyinstaller

VERSION=$(python -c "import mistcoat; print(mistcoat.__version__)")
python tools/make_icons.py
rm -rf build dist
python -m PyInstaller packaging/mistcoat.spec --noconfirm --distpath dist --workpath build

APP=dist/Mistcoat.app
IDENTITY=${MAC_SIGN_IDENTITY:--}
codesign --force --deep --options runtime --timestamp=none --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict "$APP"

# Prove the bundle actually works before shipping it.
"$APP/Contents/MacOS/Mistcoat" --selftest dist/selftest.json
cat dist/selftest.json

DMG="dist/Mistcoat-${VERSION}-macos-${ARCH}.dmg"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Mistcoat" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

if [ "$IDENTITY" != "-" ]; then
  codesign --force --sign "$IDENTITY" "$DMG"
  if [ -n "${MAC_NOTARY_PROFILE:-}" ]; then
    xcrun notarytool submit "$DMG" --keychain-profile "$MAC_NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
  fi
fi

echo "Built $DMG"
