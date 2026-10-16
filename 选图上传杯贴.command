#!/bin/bash
# ============================================================================
#  选图上传杯贴.command
#
#  双击运行：弹出 macOS 选图面板 → 选一张你画好的图 → 自动适配尺寸 → 上传
#           → 保存为你的霸王茶姬杯贴
#
#  也可以把图片直接拖到这个文件上运行。
#
#  依赖同目录的 cup_sticker.py
# ============================================================================

set -uo pipefail

# ── 切到脚本所在目录（路径含空格，必须引号）────────────────────────────────
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"
PY_SCRIPT="$DIR/cup_sticker.py"
LOG="$DIR/last_run.log"

# ── 外观 ────────────────────────────────────────────────────────────────────
B=$'\033[1m'; DIM=$'\033[2m'; R=$'\033[0m'
GRN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'; CYA=$'\033[36m'

say()  { printf '%s\n' "$*"; }
rule() { printf '%s\n' "${DIM}────────────────────────────────────────────────────────────${R}"; }

# ── 弹窗小工具 ──────────────────────────────────────────────────────────────
# 用法：alert <标题> <正文> [按钮...]
# 正文支持多行（直接写真实换行）。回显被点击的按钮名，取不到就返回空。
alert() {
  local title="$1" body="$2"; shift 2
  local btns="{\"好\"}"
  if [ $# -gt 0 ]; then
    btns="{\"$1\""
    shift
    for b in "$@"; do btns="$btns, \"$b\""; done
    btns="$btns}"
  fi
  osascript <<AS 2>/dev/null
try
  set r to display dialog "$body" with title "$title" buttons $btns default button 1 with icon note
  return button returned of r
on error
  return ""
end try
AS
}

fail() {
  say ""
  say "${RED}✖ $1${R}"
  say ""
  alert "上传失败" "$1" "好" >/dev/null
  say "${DIM}按任意键关闭…${R}"
  read -n 1 -s -r
  exit 1
}

# ── 找一个装了 Pillow 的 Python ─────────────────────────────────────────────
pick_python() {
  local cands=(
    "${CUP_PYTHON:-}"
    "$(command -v python3 2>/dev/null)"
    /opt/homebrew/bin/python3
    /usr/local/bin/python3
    /usr/bin/python3
  )
  local p
  for p in "${cands[@]}"; do
    [ -n "$p" ] && [ -x "$p" ] || continue
    if "$p" -c "import PIL" >/dev/null 2>&1; then echo "$p"; return 0; fi
  done
  for p in "${cands[@]}"; do
    [ -n "$p" ] && [ -x "$p" ] && { echo "$p"; return 0; }
  done
  return 1
}

# ── 开场 ────────────────────────────────────────────────────────────────────
clear
say ""
say "${B}  霸王茶姬 · 定制杯贴  ·  选图上传${R}"
rule
say "  ${DIM}把你自己画的图印到杯子上 —— 绕过小程序那个简陋画布${R}"
say ""

[ -f "$PY_SCRIPT" ] || fail "同目录下找不到 cup_sticker.py，请确认文件完整。"

PY="$(pick_python)" || fail "找不到可用的 python3，请先安装 Python 3。"
say "  ${DIM}解释器：$PY${R}"

if ! "$PY" -c "import PIL" >/dev/null 2>&1; then
  say "  ${YEL}提示：这个 Python 没装 Pillow，只能上传已经是 618×927 的图${R}"
  say "  ${YEL}      装一下就能自动适配任意尺寸： $PY -m pip install Pillow${R}"
fi
say ""

# ── 选图：拖拽优先，其次弹面板 ──────────────────────────────────────────────
IMG="${1:-}"

if [ -n "$IMG" ] && [ -f "$IMG" ]; then
  say "  ${DIM}使用拖入的文件：$(basename "$IMG")${R}"
else
  IMG="$(osascript <<'AS'
try
  set f to choose file with prompt "选一张要印在杯子上的图
（建议竖版，任何尺寸都行，脚本会自动适配）" of type {"public.image"}
  return POSIX path of f
on error number -128
  return ""
end try
AS
)"
fi

if [ -z "${IMG:-}" ]; then
  say "  ${DIM}已取消。${R}"
  say ""
  exit 0
fi

if [ ! -f "$IMG" ]; then
  fail "图片不存在：$IMG"
fi

say "  ${B}已选图片${R}"
say "    $(basename "$IMG")"
say "    ${DIM}$(du -h "$IMG" | cut -f1)${R}"
say ""

# ── 选适配方式 ──────────────────────────────────────────────────────────────
FIT_LABEL="$(osascript <<'AS'
set opts to {"不裁切，留白边（推荐）", "铺满画布，裁掉边缘", "拉伸填满，会变形"}
try
  set c to choose from list opts with title "尺寸适配" ¬
    with prompt "图片要放进 618×927 的杯贴画布，怎么放？" ¬
    default items {"不裁切，留白边（推荐）"}
  if c is false then return ""
  return item 1 of c
on error
  return ""
end try
AS
)"

if [ -z "${FIT_LABEL:-}" ]; then
  say "  ${DIM}已取消。${R}"
  exit 0
fi

case "$FIT_LABEL" in
  "铺满画布，裁掉边缘") FIT="cover"   ;;
  "拉伸填满，会变形")   FIT="stretch" ;;
  *)                    FIT="contain" ;;
esac

BG="white"
if [ "$FIT" = "contain" ]; then
  BG_PICK="$(osascript <<'AS'
try
  set c to choose from list {"白色（推荐）", "透明"} with title "留白颜色" ¬
    with prompt "补边的部分用什么颜色？" default items {"白色（推荐）"}
  if c is false then return "白色（推荐）"
  return item 1 of c
on error
  return "白色（推荐）"
end try
AS
)"
  [ "$BG_PICK" = "透明" ] && BG="transparent"
fi

say "  适配方式：${B}$FIT${R}   留白：${B}$BG${R}"
say ""
rule
say ""

# ── 跑 ──────────────────────────────────────────────────────────────────────
"$PY" "$PY_SCRIPT" --image "$IMG" --fit "$FIT" --bg "$BG" 2>&1 | tee "$LOG"
STATUS="${PIPESTATUS[0]}"

say ""
rule

if [ "$STATUS" -ne 0 ]; then
  case "$STATUS" in
    2) fail "请求被服务端拦下了（退出码 2）。
注意：这**不是**登录态过期 —— 上传那一步成功了就说明 token 是好的。
是保存接口上的 WAF / 风控在拦，换 token 解决不了。

先换个网络（手机热点）再试，然后跑一次只读诊断看清卡在哪一层：
$PY cup_sticker.py --diagnose" ;;
    3) fail "登录凭证失效（退出码 3）。
在微信里重新点开一次「定制杯贴」，抓一次包导出 HAR，然后跑：
$PY cup_sticker.py --har <新har> --save-config" ;;
    4) fail "图片有问题（退出码 4）。换个 PNG/JPG 再试。" ;;
    5) fail "接口返回失败（退出码 5），凭证可能已过期。重新抓 HAR 即可。" ;;
    *) fail "出错了（退出码 $STATUS）。详情见终端或 last_run.log。" ;;
  esac
fi

# ── 成功 ────────────────────────────────────────────────────────────────────
IMG_URL="$(grep -oE 'https://images\.qmai\.cn/[^ ]+' "$LOG" | head -1)"
HOST_USED="$(grep -oE '保存入口：[^ ]+' "$LOG" | head -1 | sed 's/保存入口：//')"

say ""
say "  ${GRN}${B}✔ 杯贴已保存${R}"
say ""
if [ -n "$IMG_URL" ]; then
  say "    ${CYA}$IMG_URL${R}"
  printf '%s' "$IMG_URL" | pbcopy 2>/dev/null && say "    ${DIM}（地址已复制到剪贴板）${R}"
fi
[ -n "$HOST_USED" ] && say "    ${DIM}保存入口：$HOST_USED${R}"
say ""

BTN="$(alert "上传成功" "杯贴已保存 ✔

去小程序：定制杯贴 → 我的杯贴，就能看到它了。
图片地址已复制到剪贴板。" "好" "在预览中打开")"

if [ "$BTN" = "在预览中打开" ]; then
  PREVIEW="$DIR/last_upload.png"
  [ -f "$PREVIEW" ] || PREVIEW="$IMG"
  open -a Preview "$PREVIEW" 2>/dev/null
fi

say "  ${DIM}按任意键关闭…${R}"
read -n 1 -s -r
exit 0
