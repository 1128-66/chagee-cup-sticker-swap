#!/bin/bash
# 构建 CHAGEE SWAP.app（原生 SwiftUI，无需 Xcode 工程）
#
#   ./macos/build.sh          # 构建到项目根目录
#   ./macos/build.sh --run    # 构建完顺便打开
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "$HERE/.." && pwd)"
APP_NAME="CHAGEE SWAP"
BIN_NAME="ChageeSwap"
APP="$PROJECT/$APP_NAME.app"
BUILD="$HERE/build"
DEPLOY_TARGET="macos14.0"
ARCH="$(uname -m)"

# 工具链：Command Line Tools 自带的 modulemap 有 bug
# （SwiftBridging 被重复定义），装了 Xcode 就优先用 Xcode 的。
if [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Applications/Xcode.app/Contents/Developer ]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi

if ! xcrun --find swiftc >/dev/null 2>&1; then
  echo "✘ 找不到 swiftc。装一个：xcode-select --install（或完整 Xcode）" >&2
  exit 1
fi

echo "==> 工具链：$(xcrun swiftc --version 2>&1 | head -1)"
echo "==> 目标：$ARCH-apple-$DEPLOY_TARGET"

echo "==> 清理旧产物"
rm -rf "$BUILD" "$APP"
mkdir -p "$BUILD" "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "==> 生成图标"
xcrun swift "$HERE/makeicon.swift" "$BUILD" >/dev/null
iconutil -c icns "$BUILD/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"

echo "==> 编译 Swift 源码"
xcrun swiftc \
  -parse-as-library \
  -swift-version 5 \
  -O \
  -target "$ARCH-apple-$DEPLOY_TARGET" \
  -o "$APP/Contents/MacOS/$BIN_NAME" \
  "$HERE/Sources/"*.swift

echo "==> 组装 bundle"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"

echo "==> ad-hoc 签名"
codesign --force --sign - "$APP" >/dev/null 2>&1 \
  && echo "   已签名" \
  || echo "   （签名跳过，本机运行不受影响）"

SIZE=$(du -sh "$APP" | cut -f1)
echo ""
echo "✔ 构建完成：$APP  （${SIZE}）"

if [ "${1:-}" = "--run" ]; then
  echo "==> 启动"
  open "$APP"
fi
