#!/usr/bin/env bash
# Runs the full test suites: Rust core (+ e2e) and Swift package.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -d /Applications/Xcode.app/Contents/Developer && -z "${DEVELOPER_DIR:-}" ]]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi
export PATH="$HOME/.cargo/bin:$PATH"

echo "==> cargo test (workspace)"
(cd "$ROOT/rust" && cargo test --workspace)

echo "==> swift test"
(cd "$ROOT/macos" && swift test)

echo "==> all tests passed"
