#!/usr/bin/env bash
# Builds FlowLink.app for macOS:
#   1. builds the Rust core (static library for Swift)
#   2. builds the Swift package
#   3. assembles dist/FlowLink.app (Info.plist, icon, ad-hoc codesign)
#
# Usage: scripts/build-macos.sh [--debug]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="release"
SWIFT_CONFIG="release"
if [[ "${1:-}" == "--debug" ]]; then
  CONFIG="debug"
  SWIFT_CONFIG="debug"
fi

# Prefer the full Xcode toolchain (SwiftUI macros need it); fall back to CLT.
if [[ -z "${DEVELOPER_DIR:-}" && -d /Applications/Xcode.app/Contents/Developer ]]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi
export PATH="$HOME/.cargo/bin:$PATH"

echo "==> building Rust core ($CONFIG)"
export MACOSX_DEPLOYMENT_TARGET=13.0
CARGO_ARGS=(-p flowlink-ffi)
if [[ "$CONFIG" == "release" ]]; then CARGO_ARGS+=(--release); fi
cargo build --manifest-path "$ROOT/rust/Cargo.toml" "${CARGO_ARGS[@]}"

mkdir -p "$ROOT/macos/Vendor"
cp "$ROOT/rust/target/$CONFIG/libflowlink_ffi.a" "$ROOT/macos/Vendor/libflowlink_ffi.a"
cp "$ROOT/rust/crates/flowlink-ffi/include/flowlink.h" "$ROOT/macos/Sources/CFlowLinkCore/include/flowlink.h"

echo "==> building Swift app ($SWIFT_CONFIG)"
(cd "$ROOT/macos" && swift build -c "$SWIFT_CONFIG")

echo "==> assembling FlowLink.app"
APP="$ROOT/dist/FlowLink.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

BIN="$(cd "$ROOT/macos" && swift build -c "$SWIFT_CONFIG" --show-bin-path)/FlowLink"
cp "$BIN" "$APP/Contents/MacOS/FlowLink"
cp "$ROOT/scripts/Info.plist" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"

if command -v iconutil >/dev/null && command -v swift >/dev/null; then
  ICONSET="$ROOT/dist/FlowLink.iconset"
  rm -rf "$ICONSET"
  swift "$ROOT/scripts/make-icon.swift" "$ICONSET" >/dev/null
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns" || true
  rm -rf "$ICONSET"
fi

codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || \
  echo "warning: ad-hoc codesign failed (the app still runs, permissions may re-prompt)"

echo ""
echo "  NOTE: the app is ad-hoc signed, so its code identity just changed."
echo "  macOS may have revoked the privacy permissions with it."
echo "  After EVERY rebuild, re-grant them (toggle OFF then ON):"
echo "    System Settings -> Privacy & Security -> Accessibility    -> FlowLink"
echo "    System Settings -> Privacy & Security -> Input Monitoring -> FlowLink"
echo "  Without them FlowLink cannot capture this Mac's keyboard/mouse"
echo "  (the app shows a banner and the log records the reason)."
echo ""
echo "==> done: $APP"
echo "    run with: open \"$APP\""
