#!/bin/bash
# 构建 CHAGEE SWAP.app（原生 SwiftUI，无需 Xcode 工程）
#
#   ./macos/build.sh              # 通用二进制（arm64 + x86_64）→ 项目根目录
#   ./macos/build.sh --native     # 只编当前架构，快一点（开发时用）
#   ./macos/build.sh --zip        # 顺便打成 dist/CHAGEE-SWAP-<版本>-macos.zip
#   ./macos/build.sh --run        # 构建完打开
#
# 参数可组合：./macos/build.sh --zip --run
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "$HERE/.." && pwd)"
APP_NAME="CHAGEE SWAP"
BIN_NAME="ChageeSwap"
APP="$PROJECT/$APP_NAME.app"
BUILD="$HERE/build"
DIST="$PROJECT/dist"
DEPLOY_TARGET="macos14.0"
HOST_ARCH="$(uname -m)"

MODE="universal"
WANT_ZIP=0
WANT_RUN=0
for arg in "$@"; do
  case "$arg" in
    --native) MODE="native" ;;
    --zip)    WANT_ZIP=1 ;;
    --run)    WANT_RUN=1 ;;
    --help|-h)
      # 按内容取用法注释块，不写死行号（以后加参数不会错位）
      sed -n '/^# 构建 /,/^# 参数可组合/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "✘ 未知参数：${arg}（试试 --help）" >&2
      exit 2 ;;
  esac
done

if [ "$MODE" = "native" ]; then
  ARCHS=("$HOST_ARCH")
else
  ARCHS=("arm64" "x86_64")
fi

# 工具链：Command Line Tools 自带的 modulemap 有 bug
# （SwiftBridging 被重复定义），装了 Xcode 就优先用 Xcode 的。
if [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Applications/Xcode.app/Contents/Developer ]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi

if ! xcrun --find swiftc >/dev/null 2>&1; then
  echo "✘ 找不到 swiftc。装一个：xcode-select --install（或完整 Xcode）" >&2
  exit 1
fi

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
             "$HERE/Info.plist" 2>/dev/null || echo "0.0.0")"

echo "==> 工具链：$(xcrun swiftc --version 2>&1 | head -1)"
# 注意：变量后面紧跟全角标点时，bash 会把标点的字节当成变量名的一部分，
# 所以凡是有 CJK 跟在变量后面的地方都必须写成 ${VAR}。
echo "==> 目标：${ARCHS[*]}-apple-$DEPLOY_TARGET   （版本 ${VERSION}）"

echo "==> 清理旧产物"
rm -rf "$BUILD" "$APP"
mkdir -p "$BUILD" "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "==> 生成图标"
xcrun swift "$HERE/makeicon.swift" "$BUILD" >/dev/null
iconutil -c icns "$BUILD/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"

# 逐架构编译，再 lipo 合并。某个架构编不过不致命（降级为单架构）。
SLICES=()
for ARCH in "${ARCHS[@]}"; do
  echo "==> 编译 $ARCH"
  OUT="$BUILD/$BIN_NAME-$ARCH"
  if xcrun swiftc \
      -parse-as-library \
      -swift-version 5 \
      -O \
      -target "$ARCH-apple-$DEPLOY_TARGET" \
      -o "$OUT" \
      "$HERE/Sources/"*.swift; then
    SLICES+=("$OUT")
  else
    echo "   ⚠ $ARCH 编译失败，跳过这个架构" >&2
  fi
done

if [ ${#SLICES[@]} -eq 0 ]; then
  echo "✘ 所有架构都编译失败，放弃" >&2
  exit 1
fi

if [ ${#SLICES[@]} -gt 1 ]; then
  echo "==> 合并为通用二进制"
  lipo -create "${SLICES[@]}" -output "$APP/Contents/MacOS/$BIN_NAME"
else
  cp "${SLICES[0]}" "$APP/Contents/MacOS/$BIN_NAME"
  [ "$MODE" = "universal" ] && echo "   ⚠ 只编出了 ${ARCHS[0]}，这个包不是通用二进制" >&2
fi

echo "==> 组装 bundle"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"

echo "==> ad-hoc 签名"
codesign --force --sign - "$APP" >/dev/null 2>&1 \
  && echo "   已签名（ad-hoc，非公证）" \
  || echo "   （签名跳过，本机运行不受影响）"

ARCH_INFO="$(lipo -archs "$APP/Contents/MacOS/$BIN_NAME" 2>/dev/null || echo "$HOST_ARCH")"
SIZE="$(du -sh "$APP" | cut -f1)"
echo ""
echo "✔ 构建完成：$APP"
echo "   版本 $VERSION · 架构 $ARCH_INFO · 大小 $SIZE"

if [ "$WANT_ZIP" = "1" ]; then
  mkdir -p "$DIST"
  ZIP="$DIST/CHAGEE-SWAP-$VERSION-macos.zip"
  rm -f "$ZIP"
  # 必须用 ditto：普通 zip 会丢掉 bundle 里的符号链接、权限位和扩展属性，
  # 解压出来的 .app 打不开。
  ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
  echo ""
  echo "✔ 发布包：$ZIP  （$(du -sh "$ZIP" | cut -f1)）"
  echo "   sha256  $(shasum -a 256 "$ZIP" | cut -d' ' -f1)"
fi

if [ "$WANT_RUN" = "1" ]; then
  echo "==> 启动"
  open "$APP"
fi
