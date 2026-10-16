#!/bin/bash
# 离屏渲染界面截图（不需要屏幕录制权限）
#
#   macos/tools/render_shots/run.sh                        # 输出到 macos/build/shots/，2 倍图
#   macos/tools/render_shots/run.sh /tmp/shots             # 指定输出目录
#   CUP_SHOT_DEMO=1 macos/tools/render_shots/run.sh docs/screenshots 1
#                                                          # 演示模式（假项目路径）+ 1 倍图
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACOS="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$MACOS/build/shots}"
SCALE="${2:-2}"
BIN="$MACOS/build/render_shots"

# 和 build.sh 同样的工具链处理（CLT 的 modulemap 有 bug）
if [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Applications/Xcode.app/Contents/Developer ]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi

mkdir -p "$(dirname "$BIN")" "$OUT"

# 注意：@main 和 main.swift 不能共存，所以这里排除入口文件，
# 只编译「视图 + 数据」层，再由 main.swift 提供顶层入口。
SRCS=()
for f in "$MACOS/Sources/"*.swift; do
  case "$(basename "$f")" in
    ChageeSwapApp.swift) continue ;;   # 里面有 @main
  esac
  SRCS+=("$f")
done

echo "==> 编译离屏渲染器（$((${#SRCS[@]} + 1)) 个文件）"
xcrun swiftc \
  -swift-version 5 \
  -target "$(uname -m)-apple-macos14.0" \
  -o "$BIN" \
  "${SRCS[@]}" \
  "$HERE/main.swift"

echo "==> 渲染（${SCALE} 倍图）"
"$BIN" "$OUT" "$SCALE"

echo ""
echo "✔ 截图目录：$OUT"
ls -1 "$OUT"
