#!/usr/bin/env bash
# Builds (if needed) and launches FlowLink.app.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -d "$ROOT/dist/FlowLink.app" ]]; then
  "$ROOT/scripts/build-macos.sh"
fi

open "$ROOT/dist/FlowLink.app"
echo "FlowLink launched — look for the arrows icon in the menu bar."
