#!/bin/bash
# 适配逻辑自检：像素级验证 contain / cover / stretch 三种模式
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACOS="$(cd "$HERE/../.." && pwd)"
BIN="$MACOS/build/verify_fit"

if [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Applications/Xcode.app/Contents/Developer ]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi

mkdir -p "$(dirname "$BIN")"

xcrun swiftc \
  -swift-version 5 \
  -target "$(uname -m)-apple-macos14.0" \
  -o "$BIN" \
  "$MACOS/Sources/ImagePreparer.swift" \
  "$HERE/main.swift"

exec "$BIN"
