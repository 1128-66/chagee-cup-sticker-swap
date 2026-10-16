#!/bin/bash
# app 层端到端联调：起代理 → 接管系统代理 → 看门狗就位 → 停止 → 还原
#
# 注意：这个测试会**真的**短暂接管系统代理（约 10 秒），
# 结束后必须还原。所以：
#   · 在临时目录里跑，不碰你项目里的 artwork.png
#   · --max-minutes 1 兜底，最多接管 1 分钟就自己还原
#   · 无论成败，退出前都再跑一次 --close
#
#   macos/tools/e2e/run.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MACOS="$(cd "$HERE/../.." && pwd)"
PROJECT="$(cd "$MACOS/.." && pwd)"
BIN="$MACOS/build/e2e"

if [ -z "${DEVELOPER_DIR:-}" ] && [ -d /Applications/Xcode.app/Contents/Developer ]; then
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
fi

TMP="$(mktemp -d /tmp/cup_e2e.XXXXXX)"

cleanup() {
  echo ""
  echo "==> 收尾：确保系统代理已还原"
  ( cd "$TMP" && /usr/bin/python3 replace_proxy.py --close 2>&1 | tail -6 )
  echo "==> 最终代理状态"
  /usr/sbin/networksetup -getwebproxy Wi-Fi | head -3
  rm -rf "$TMP"
  echo "==> 临时目录已删除：$TMP"
}
trap cleanup EXIT

echo "==> 准备临时项目目录（不碰你的 artwork.png）"
cp "$PROJECT"/*.py "$TMP"/
cp -R "$PROJECT/fixtures" "$TMP"/
ls -1 "$TMP" | sed 's/^/    /'

mkdir -p "$(dirname "$BIN")"

SRCS=()
for f in "$MACOS/Sources/"*.swift; do
  case "$(basename "$f")" in
    ChageeSwapApp.swift) continue ;;   # 里面有 @main，和 main.swift 冲突
  esac
  SRCS+=("$f")
done

echo ""
echo "==> 编译联调程序"
xcrun swiftc \
  -swift-version 5 \
  -target "$(uname -m)-apple-macos14.0" \
  -o "$BIN" \
  "${SRCS[@]}" \
  "$HERE/main.swift"

echo ""
echo "==> 开跑"
"$BIN" "$TMP" "$PROJECT/fixtures/sample_artwork.png"
RC=$?

echo ""
if [ "$RC" -eq 0 ]; then echo "✔ 联调通过"; else echo "✘ 联调失败（退出码 $RC）"; fi
exit "$RC"
