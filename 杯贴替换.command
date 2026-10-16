#!/bin/bash
# ============================================================================
#  杯贴替换.command
#
#  双击运行：选一张你画好的图 → 预适配成 618×927 → 起 mitm 代理
#           → 在小程序里随便画一笔点「提交创作」→ 服务端收到的却是你的画
#
#  原理：请求由真实小程序发出（WAF 签名/登录态/机审全部照常），
#        代理只在传输途中把 multipart 里的图片字节换成你的图。
#        绕过的只有"画布太简陋"这个体验限制，不是任何安全控制。
#
#  依赖同目录：cup_replace_addon.py / cup_replace_core.py / replace_proxy.py
#             / prepare_artwork.py
# ============================================================================

set -uo pipefail

cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"

# ── 外观 ────────────────────────────────────────────────────────────────────
B=$'\033[1m'; DIM=$'\033[2m'; R=$'\033[0m'
GRN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'; CYA=$'\033[36m'

say()  { printf '%s\n' "$*"; }
rule() { printf '%s\n' "${DIM}────────────────────────────────────────────────────────────${R}"; }
die()  { say ""; say "${RED}✖ $1${R}"; say ""; alert "出错了" "$1" "好" >/dev/null; exit 1; }

# ── 弹窗（回显被点击的按钮名，Esc 时返回空）─────────────────────────────────
alert() {
  local title="$1" body="$2"; shift 2
  local btns='{"好"}'
  if [ $# -gt 0 ]; then
    btns="{\"$1\""; shift
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

find_mitmdump() {
  command -v mitmdump 2>/dev/null || {
    [ -x /opt/homebrew/bin/mitmdump ] && echo /opt/homebrew/bin/mitmdump
  }
}

as_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

# ── 开场 ────────────────────────────────────────────────────────────────────
# 快速判断：有没有任何代理是开着的（一次 scutil，比逐个 networksetup 快得多）
proxy_maybe_on() {
  scutil --proxy 2>/dev/null | awk '
    /HTTPEnable|HTTPSEnable|SOCKSEnable/ { if ($3 == 1) found = 1 }
    END { exit found ? 0 : 1 }'
}

banner() {
  clear
  say ""
  say "${B}  霸王茶姬 · 定制杯贴  ·  mitm 代理替换${R}"
  rule
  say "  ${DIM}把你画好的图直接印到杯子上 —— 不用那个简陋画布${R}"
  say "  ${DIM}请求仍由真实小程序发出，只换掉传输中的像素${R}"
  say ""
  # 已经断网了就先把这事顶到最前面（先做便宜的判断，避免每次回菜单都慢）
  if [ -n "${PY:-}" ] && [ -x "${PY:-/nonexistent}" ] && proxy_maybe_on; then
    if "$PY" "$DIR/replace_proxy.py" --status --port 0 2>/dev/null | grep -q "你现在就是断网状态"; then
      say "  ${RED}${B}⚠ 检测到系统代理没关干净 —— 你现在是断网状态${R}"
      say "  ${RED}  选菜单「关闭代理（断网急救）」立刻修好${R}"
      say ""
    fi
  fi
}

# ── 预检 ────────────────────────────────────────────────────────────────────
precheck() {
  local missing=()
  for f in cup_replace_addon.py cup_replace_core.py replace_proxy.py \
           prepare_artwork.py proxy_watchdog.py; do
    [ -f "$DIR/$f" ] || missing+=("$f")
  done
  if [ ${#missing[@]} -gt 0 ]; then
    die "同目录下缺少文件：${missing[@]+"${missing[@]}"}"
  fi

  # PY 可能已经在 banner 之前挑好了，别重复挑
  [ -n "${PY:-}" ] || { PY="$(pick_python)" || die "找不到可用的 python3，请先安装 Python 3。"; }
  if ! "$PY" -c "import PIL" >/dev/null 2>&1; then
    say "  ${YEL}⚠ 当前 Python 没装 Pillow，无法自动适配尺寸${R}"
    say "  ${YEL}  $PY -m pip install Pillow${R}"
    say ""
  fi
  MITM="$(find_mitmdump)" || true
  if [ -z "${MITM:-}" ]; then
    die "找不到 mitmdump。
装一下：brew install mitmproxy"
  fi
}

# ── 选图 + 选适配方式 → 写出 artwork.png ────────────────────────────────────
pick_and_prepare() {
  local src
  src="$(osascript <<'AS'
try
  set f to choose file with prompt "选一张要印在杯子上的图
（任意尺寸、任意常见格式，会自动适配成 618×927）" of type {"public.image"}
  return POSIX path of f
on error number -128
  return ""
end try
AS
)"
  [ -n "$src" ] || { say "  ${DIM}已取消。${R}"; return 1; }
  [ -f "$src" ] || die "图片不存在：$src"

  say "  ${B}已选${R}  $(basename "$src")"
  say ""

  local fit_label fit bg pick
  fit_label="$(osascript <<'AS'
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
  [ -n "$fit_label" ] || { say "  ${DIM}已取消。${R}"; return 1; }

  case "$fit_label" in
    "铺满画布，裁掉边缘") fit="cover"   ;;
    "拉伸填满，会变形")   fit="stretch" ;;
    *)                    fit="contain" ;;
  esac

  bg="white"
  if [ "$fit" = "contain" ]; then
    pick="$(osascript <<'AS'
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
    [ "$pick" = "透明" ] && bg="transparent"
  fi

  say "  适配：${B}$fit${R}   留白：${B}$bg${R}"
  say ""
  rule
  "$PY" "$DIR/prepare_artwork.py" "$src" -o "$DIR/artwork.png" --fit "$fit" --bg "$bg"
  local rc=$?
  [ $rc -eq 0 ] || die "预适配失败（退出码 $rc）。"
  return 0
}

# ── 起代理 ──────────────────────────────────────────────────────────────────
run_proxy() {
  local extra=()
  [ -f "$DIR/artwork.png" ] || {
    say "${YEL}还没有 artwork.png${R}"
    say "先选一张图（菜单第 1 项），或手动放一张到："
    say "  $DIR/artwork.png"
    say ""
    return 1
  }

  local mode
  mode="$(osascript <<'AS'
set opts to {"标准（拦截全部流量，和之前抓包一致）", ¬
             "安静（只拦 *.qmai.cn，其余流量直通）", ¬
             "顺便抓 HAR（事后可自己核对）"}
try
  set c to choose from list opts with title "运行方式" ¬
    with prompt "代理怎么跑？" default items {item 1 of opts}
  if c is false then return ""
  return item 1 of c
on error
  return ""
end try
AS
)"
  [ -n "$mode" ] || { say "  ${DIM}已取消。${R}"; return 1; }

  case "$mode" in
    "安静"*) extra+=(--only-qmai) ;;
    "顺便抓 HAR"*) extra+=(--har) ;;
  esac

  say ""
  say "${B}  即将接管系统代理${R}"
  say "  ${DIM}⚠ 代理不关就断网 —— 所以做了三道保险：${R}"
  say "  ${DIM}  ① Ctrl-C / 关窗口，自动还原${R}"
  say "  ${DIM}  ② 看门狗独立盯着，进程被强杀也能还原${R}"
  say "  ${DIM}  ③ 最多 ${MAX_MIN} 分钟自动关闭${R}"
  say ""
  local go
  go="$(alert "准备就绪" "接下来：
1) 微信里打开霸王茶姬小程序
2) 下单 → 定制杯贴 → 创作杯贴
3) 随便画一笔，点「提交创作」

看到 ✔ 已替换 就成功了。

用完记得关（Ctrl-C 即可，会自动还原）。
万一断网了，双击同目录的「关闭代理.command」。" "开始" "算了")"
  [ "$go" = "开始" ] || { say "  ${DIM}已取消。${R}"; return 1; }

  say ""
  # 注意：macOS 自带 /bin/bash 是 3.2，set -u 下空数组 "${extra[@]}" 会报
  # unbound variable，必须用 ${extra[@]+"${extra[@]}"} 这种写法。
  "$PY" "$DIR/replace_proxy.py" --max-minutes "$MAX_MIN" ${extra[@]+"${extra[@]}"}
  local rc=$?

  say ""
  rule
  local again
  again="$(alert "代理已停止" "系统代理已还原，网络恢复正常。

想再来一次就点「再跑一次」，否则回到菜单。" "再跑一次" "回菜单")"
  if [ "$again" = "再跑一次" ]; then
    run_proxy
  fi
  return $rc
}

# ── 查看状态 ────────────────────────────────────────────────────────────────
show_status() {
  say ""
  # --port 0 = 扫我们的整个端口区间，不猜具体是哪个
  "$PY" "$DIR/replace_proxy.py" --status --port 0
  local rc=$?
  say ""
  if [ $rc -eq 2 ]; then
    local fix
    fix="$(alert "检测到断网" "系统代理开着，但代理进程没在跑 —— 现在是断网状态。

要立刻修好吗？" "立刻修" "先不管")"
    if [ "$fix" = "立刻修" ]; then
      say ""
      "$PY" "$DIR/replace_proxy.py" --close --port 0
      say ""
      alert "已修好" "系统代理已还原，网络应该恢复了。" "好" >/dev/null
    fi
  fi
  return 0
}

# ── 关闭代理（断网急救）──────────────────────────────────────────────────────
close_proxy() {
  say ""
  "$PY" "$DIR/replace_proxy.py" --close --port 0
  local rc=$?
  say ""
  alert "代理已关闭" "系统代理已还原，mitmdump 和看门狗都停了。

如果浏览器还是打不开网页，去：
系统设置 → 网络 → 详细信息 → 代理
确认「Web 代理(HTTP)」和「安全 Web 代理(HTTPS)」都是关闭的。" "好" >/dev/null
  return $rc
}

# ── 只预适配 ────────────────────────────────────────────────────────────────
prepare_only() {
  pick_and_prepare || return 1
  say ""
  local open_it
  open_it="$(alert "预适配完成" "已写出：artwork.png

要打开看一眼吗？" "打开看" "不用")"
  [ "$open_it" = "打开看" ] && open -a Preview "$DIR/artwork.png" 2>/dev/null
  say ""
  say "  ${GRN}✔ artwork.png 已就绪${R}  ${DIM}（$DIR/artwork.png）${R}"
  say "  之后选菜单第 2 项即可直接起代理。"
  say ""
}

# ── 自检 ────────────────────────────────────────────────────────────────────
run_selftest() {
  say ""
  rule
  say "${B}  ① 改写逻辑自检${R}  ${DIM}（普通 Python，字节级比对）${R}"
  rule
  "$PY" "$DIR/selftest_replace.py"
  local rc1=$?

  say ""
  rule
  say "${B}  ② addon 接线自检${R}  ${DIM}（mitmproxy 真实运行时，不联网）${R}"
  rule
  "$MITM" -s "$DIR/selftest_mitm.py" --listen-host 127.0.0.1 --listen-port 18899
  local rc2=$?

  say ""
  if [ $rc1 -eq 0 ] && [ $rc2 -eq 0 ]; then
    alert "自检通过" "两套自检全绿 ✔

① 改写逻辑：18 项（字节级）
② addon 接线：12 项（mitmproxy 真实运行时）

可以放心上代理了。" "好" >/dev/null
  else
    alert "自检未通过" "① 改写逻辑：退出码 $rc1
② addon 接线：退出码 $rc2

详情见终端输出。" "好" >/dev/null
  fi
  return $(( rc1 | rc2 ))
}

# ── 说明 ────────────────────────────────────────────────────────────────────
show_help() {
  say ""
  rule
  say "${B}  这是怎么做到的${R}"
  rule
  say "  小程序上传时，请求体是一个 multipart，里面只有一个 upfile 字段，"
  say "  装着你手绘的那张 618×927 PNG。鉴权在 ${CYA}qm-user-token${R} 请求头里。"
  say ""
  say "  插件做的事只有一件："
  say "    ${B}把 upfile 那一段的图片字节，换成你的图。${R}"
  say ""
  say "  其余一律不动："
  say "    · 请求由真实 webview 发出 —— WAF 的 JS 签名由它自己算，正常满足"
  say "    · ${CYA}qm-user-token${R} / User-Agent / referer —— 字节级保留"
  say "    · 登录态、机审、风控 —— 全部照常走"
  say ""
  say "  换句话说：绕过的只有「画布太简陋」这个体验限制，"
  say "  不是任何安全控制。这也是它和「改包复用签名」那类做法的分界线。"
  say ""
  rule
  say "${B}  用完一定要关 —— 代理不关就断网${R}"
  rule
  say "  系统代理一旦指向 127.0.0.1:8899 而 8899 上没有进程在听，"
  say "  整台机器就上不了网。所以做了三道保险："
  say ""
  say "    ${GRN}① 正常退出${R}   Ctrl-C，或点窗口的 × —— 都会自动还原"
  say "    ${GRN}② 看门狗${R}      独立会话，主进程被强杀 / 系统崩了也照样还原"
  say "    ${GRN}③ 时限兜底${R}    最多 ${MAX_MIN} 分钟，到点自动关闭"
  say ""
  say "  还是断网了？双击同目录的 ${B}关闭代理.command${R}"
  say "  ${DIM}（它不依赖 Python，纯 bash 也能把网络救回来）${R}"
  say ""
  rule
  say "${B}  常用命令${R}"
  rule
  say "  预适配图片    $PY prepare_artwork.py 你的图.jpg"
  say "  起代理        $PY replace_proxy.py [--har] [--only-qmai]"
  say "  看状态        $PY replace_proxy.py --status"
  say "  关闭代理      $PY replace_proxy.py --close"
  say "  限制时长      $PY replace_proxy.py --max-minutes 60"
  say "  只干跑看看    $PY replace_proxy.py --dry-run"
  say "  逻辑自检      $PY selftest_replace.py"
  say "  接线自检      $MITM -s selftest_mitm.py --listen-port 18899"
  say ""
  say "  日志：$DIR/replace.log"
  say ""
  say "  ${DIM}详细说明见 README.md 的「mitm 代理替换」一节。${R}"
  say ""
}

# ── 主菜单 ──────────────────────────────────────────────────────────────────
main_menu() {
  local choice
  choice="$(osascript <<'AS'
set opts to {"选一张图，然后起代理（推荐）", ¬
             "用现有的 artwork.png 起代理", ¬
             "只预适配图片（不起代理）", ¬
             "查看状态（有没有断网）", ¬
             "关闭代理（断网急救）", ¬
             "运行自检", ¬
             "查看说明", ¬
             "退出"}
try
  set c to choose from list opts with title "杯贴替换" ¬
    with prompt "想做什么？" default items {item 1 of opts}
  if c is false then return "退出"
  return item 1 of c
on error
  return "退出"
end try
AS
)"
  echo "$choice"
}

# ── 跑 ──────────────────────────────────────────────────────────────────────
MAX_MIN="${CUP_MAX_MINUTES:-180}"

# 先把解释器定下来 —— banner 里的断网检测要用到它
PY="$(pick_python)" || die "找不到可用的 python3，请先安装 Python 3。"

banner
precheck
say "  ${DIM}解释器：$PY${R}"
say "  ${DIM}mitmdump：$MITM${R}"
say "  ${DIM}接管上限：${MAX_MIN} 分钟（到点自动关闭）${R}"
say ""

while true; do
  choice="$(main_menu)"

  case "$choice" in
    "选一张图，然后起代理（推荐）")
      pick_and_prepare && run_proxy
      ;;
    "用现有的 artwork.png 起代理")
      run_proxy
      ;;
    "只预适配图片（不起代理）")
      prepare_only
      ;;
    "查看状态（有没有断网）")
      show_status
      ;;
    "关闭代理（断网急救）")
      close_proxy
      ;;
    "运行自检")
      run_selftest
      ;;
    "查看说明")
      show_help
      ;;
    *)
      say "  ${DIM}再见。${R}"
      say ""
      exit 0
      ;;
  esac

  say ""
  say "${DIM}按任意键回到菜单…${R}"
  read -n 1 -s -r
  banner
done
