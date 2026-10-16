#!/bin/bash
# ============================================================================
#  关闭代理.command —— 一键关掉代理，网络立刻恢复
#
#  什么时候用：
#    · 代理还开着，你不想用了
#    · 断网了（浏览器打不开、微信转圈）—— 十有八九就是代理没关
#    · 强关过终端窗口 / 电脑重启过 / 进程被 kill 过
#
#  它只动"指向本工具端口（127.0.0.1:8899-8918）"的代理设置，
#  **绝不碰你自己配的 Loon / Shadowrocket / Clash**（那些一般在 7890 之类）。
#
#  两层保障：
#    ① 有 Python 就走 replace_proxy.py --close（精确还原到接管前的状态）
#    ② 没 Python 也照样能修好网络 —— 纯 bash 兜底，扫端口区间把指向我们的关掉
# ============================================================================

set -uo pipefail

cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"

B=$'\033[1m'; DIM=$'\033[2m'; R=$'\033[0m'
GRN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'

PORT_FROM=8899
PORT_TO=8918

say()  { printf '%s\n' "$*"; }
rule() { printf '%s\n' "${DIM}────────────────────────────────────────────────────────────${R}"; }

clear
say ""
say "${B}  关闭代理${R}"
rule
say "  ${DIM}还原系统代理、停掉 mitmdump 和看门狗，网络立刻恢复${R}"
say "  ${DIM}只动指向 127.0.0.1:${PORT_FROM}-${PORT_TO} 的设置，不碰你的 Loon${R}"
say ""

CHANGED=0

# ── ① 有 Python 就精确还原 ──────────────────────────────────────────────────
PY=""
for c in \
  "${CUP_PYTHON:-}" \
  "$(command -v python3 2>/dev/null || true)" \
  /opt/homebrew/bin/python3 \
  /usr/bin/python3
do
  [ -n "$c" ] && [ -x "$c" ] && { PY="$c"; break; }
done

if [ -n "$PY" ] && [ -f "$DIR/replace_proxy.py" ]; then
  say "${B}  ① 精确还原（按接管前的备份）${R}"
  # --port 0 = 扫我们的整个端口区间，不猜具体是哪个
  if "$PY" "$DIR/replace_proxy.py" --close --port 0; then
    CHANGED=1
  else
    say "  ${YEL}（精确还原没成功，继续用兜底方案）${R}"
  fi
  say ""
else
  say "${DIM}  跳过精确还原（没有可用的 Python 或脚本不在了）${R}"
  say ""
fi

# ── ② 纯 bash 兜底：扫端口区间，把指向我们的一律关掉 ────────────────────────
say "${B}  ② 兜底清理${R}"

SERVICES="$(networksetup -listallnetworkservices 2>/dev/null | tail -n +2 | grep -v '^\*')"

off_ours() {
  # $1=服务名 $2=查询参数 $3=状态参数 → 命中则关掉并返回 0
  local svc="$1" flag="$2" stater="$3" out server port
  out="$(networksetup "$flag" "$svc" 2>/dev/null)"
  server="$(printf '%s\n' "$out" | awk -F': ' '/^Server:/{print $2}')"
  port="$(printf '%s\n' "$out" | awk -F': ' '/^Port:/{print $2}')"
  [ "$server" = "127.0.0.1" ] || return 1
  case "$port" in
    ''|*[!0-9]*) return 1 ;;
  esac
  if [ "$port" -ge "$PORT_FROM" ] && [ "$port" -le "$PORT_TO" ]; then
    networksetup "$stater" "$svc" off 2>/dev/null
    return 0
  fi
  return 1
}

while IFS= read -r svc; do
  [ -n "$svc" ] || continue
  hit=0
  off_ours "$svc" -getwebproxy        -setwebproxystate        && hit=1
  off_ours "$svc" -getsecurewebproxy  -setsecurewebproxystate  && hit=1
  if [ "$hit" = "1" ]; then
    say "  · $svc：已关闭指向本工具的代理"
    CHANGED=1
  fi
done <<< "$SERVICES"

# ── ③ 停进程（只杀 mitmdump，绝不误伤别的）──────────────────────────────────
say ""
say "${B}  ③ 停掉相关进程${R}"

for p in $(seq "$PORT_FROM" "$PORT_TO"); do
  # lsof -F pc 直接给 pid + 命令名，不依赖 ps
  pid=""; comm=""
  while IFS= read -r line; do
    case "$line" in
      p*) pid="${line#p}" ;;
      c*)
        comm="${line#c}"
        if [ -n "$pid" ] && [ "$comm" = "mitmdump" ]; then
          kill -TERM "$pid" 2>/dev/null && say "  · mitmdump (PID $pid) 已停止，端口 $p"
          CHANGED=1
        elif [ -n "$pid" ]; then
          say "  ${YEL}· 端口 $p 上是别的进程（$comm，PID $pid），没动它${R}"
        fi
        pid=""; comm=""
        ;;
    esac
  done < <(lsof -nP -iTCP:"$p" -sTCP:LISTEN -F pc 2>/dev/null)
done

if [ -f "$DIR/watchdog.json" ]; then
  wpid="$(grep -o '"pid"[[:space:]]*:[[:space:]]*[0-9]*' "$DIR/watchdog.json" 2>/dev/null \
          | grep -o '[0-9]*$' | head -1)"
  if [ -n "${wpid:-}" ] && kill -0 "$wpid" 2>/dev/null; then
    kill -TERM "$wpid" 2>/dev/null && say "  · 看门狗 (PID $wpid) 已停止"
  fi
fi

# 只删看门狗状态；proxy_backup.json 故意留着 —— 它是"接管前状态"的记录，
# 万一下次又崩了，还有据可依。
rm -f "$DIR/watchdog.json"

if [ "$CHANGED" = "0" ]; then
  say "  ${DIM}（没有找到在跑的东西）${R}"
fi

# ── ④ 复查：确认网络真的恢复了 ──────────────────────────────────────────────
say ""
rule
say "${B}  复查${R}"

STILL=""
while IFS= read -r svc; do
  [ -n "$svc" ] || continue
  for flag in -getwebproxy -getsecurewebproxy; do
    out="$(networksetup "$flag" "$svc" 2>/dev/null)"
    en="$(printf '%s\n' "$out"  | awk -F': ' '/^Enabled:/{print $2}')"
    srv="$(printf '%s\n' "$out" | awk -F': ' '/^Server:/{print $2}')"
    prt="$(printf '%s\n' "$out" | awk -F': ' '/^Port:/{print $2}')"
    [ "$en" = "Yes" ] || continue
    [ "$srv" = "127.0.0.1" ] || continue
    case "$prt" in
      ''|*[!0-9]*) continue ;;
    esac
    if [ "$prt" -ge "$PORT_FROM" ] && [ "$prt" -le "$PORT_TO" ]; then
      STILL="$STILL $svc"
    fi
  done
done <<< "$SERVICES"

if [ -n "$STILL" ]; then
  say "  ${RED}✘ 还有服务指着本工具：$STILL${R}"
  say "  手动关：系统设置 → 网络 → 详细信息 → 代理 → 关掉 Web 代理(HTTP) 和 安全 Web 代理(HTTPS)"
  osascript -e 'display notification "还有代理没关掉，请看终端提示" with title "杯贴替换 · 关闭未完全成功"' 2>/dev/null
else
  say "  ${GRN}✔ 没有任何服务指向本工具 —— 网络已恢复${R}"
  osascript -e 'display notification "系统代理已还原，网络恢复正常" with title "杯贴替换 · 代理已关闭" sound name "Ping"' 2>/dev/null
fi

if [ -n "$PY" ]; then
  say ""
  say "  ${DIM}想确认：$PY \"$DIR/replace_proxy.py\" --status${R}"
fi

say ""
rule
say "  ${DIM}按任意键关闭…${R}"
read -n 1 -s -r
exit 0
