#!/usr/bin/env bash
# Build, sign and optionally package ToRexLink for macOS.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.12 is required (set PYTHON_BIN=/path/to/python3.12)." >&2
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip wheel
.venv/bin/python -m pip install -r requirements-dev.txt

.venv/bin/python scripts/generate_icons.py

rm -rf build dist
.venv/bin/python -m PyInstaller packaging/ToRexLink.spec --noconfirm --clean

APP="dist/ToRexLink.app"
if [ ! -d "$APP" ]; then
  echo "Build did not produce $APP" >&2
  exit 1
fi

# A stable Developer ID is strongly recommended. macOS TCC permissions for
# Input Monitoring and Accessibility are tied to the code signature and may be
# requested again after an ad-hoc build changes.
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
  echo "Signing with Developer ID: $CODESIGN_IDENTITY"
  codesign --force --deep --options runtime \
    --entitlements packaging/entitlements.plist \
    --sign "$CODESIGN_IDENTITY" "$APP"
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    ditto -c -k --keepParent "$APP" dist/ToRexLink-notarize.zip
    xcrun notarytool submit dist/ToRexLink-notarize.zip \
      --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$APP"
    rm -f dist/ToRexLink-notarize.zip
  fi
else
  echo "No CODESIGN_IDENTITY set; applying an ad-hoc signature (local testing only)."
  # Ad-hoc identities have no Team ID, so hardened runtime + library validation
  # would prevent the PyInstaller app from loading its own Python dylib.
  codesign --force --deep --sign - "$APP" || true
fi

codesign --verify --deep --strict --verbose=2 "$APP" || true
"$APP/Contents/MacOS/ToRexLink" --self-test || {
  echo "Packaged dependency self-test failed." >&2
  exit 1
}
echo "Built: $APP"
echo "Run:   open '$ROOT/$APP'"

if [ "${MAKE_DMG:-0}" = "1" ]; then
  STAGE="$(mktemp -d)"
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname "ToRexLink" -srcfolder "$STAGE" -ov -format UDZO dist/ToRexLink.dmg
  rm -rf "$STAGE"
  echo "Built: dist/ToRexLink.dmg"
fi
