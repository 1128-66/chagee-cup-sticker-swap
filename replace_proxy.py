#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
replace_proxy.py — 前台启动 mitm 代理，把小程序上传的杯贴换成你的图

它干四件事：
    1. 把系统代理指向 127.0.0.1:<port>（先存好原状态）
    2. 前台跑 mitmdump，挂上 cup_replace_addon.py，日志直接打在屏幕上
    3. 另起一个**独立会话的看门狗**，主进程被强杀/窗口被关也能自动还原代理
    4. 退出时（Ctrl-C / 关窗口 / 到时限）自动把系统代理还原

⚠ 为什么"关闭"这件事这么重要
    系统代理一旦指向 127.0.0.1:8899 而 8899 上没有进程在听，**整台机器就断网**。
    所以这里做了三道保险：
        ① 正常退出（Ctrl-C / 关窗口 → SIGHUP）自己还原
        ② 看门狗：独立会话，主进程被 SIGKILL / 系统崩了也照样还原
        ③ --close 手动总闸：连看门狗也死了的极端情况
    另有 --max-minutes 兜底（默认 180 分钟），到点自动关。

用法
    python3 replace_proxy.py                    # 默认 8899 端口
    python3 replace_proxy.py --port 8899
    python3 replace_proxy.py --no-proxy         # 只起代理，不动系统设置
    python3 replace_proxy.py --har              # 顺便抓 HAR，事后可以自己核对
    python3 replace_proxy.py --only-qmai        # 只拦截 *.qmai.cn，其余流量直通
    python3 replace_proxy.py --max-minutes 60   # 最长接管 60 分钟
    python3 replace_proxy.py --keep-proxy       # 退出时保留系统代理（调试用）

    python3 replace_proxy.py --status           # 现在什么状态？有没有断网？
    python3 replace_proxy.py --close            # 立刻关闭并恢复网络
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cup_replace_core as core  # noqa: E402

BACKUP = HERE / "proxy_backup.json"

# 顺手抓 HAR 时用的 addon（可选）。优先 $CUP_HAR_ADDON，
# 其次找项目同目录 / 上级目录下的 har_addon.py。
HAR_ADDON_CANDIDATES = tuple(
    p for p in (
        Path(os.environ["CUP_HAR_ADDON"]).expanduser()
        if os.environ.get("CUP_HAR_ADDON") else None,
        HERE / "har_addon.py",
        HERE.parent / "har_addon.py",
    ) if p is not None
)

def _use_color() -> bool:
    """只在真终端里上色。

    GUI（CHAGEE SWAP.app）是用管道读我们输出的，不是 TTY ——
    这时候必须输出纯文本，否则界面日志里会满屏 ^[[2m 这种乱码。
    也认 NO_COLOR 这个约定（https://no-color.org）。
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        return False


if _use_color():
    B = "\033[1m"; DIM = "\033[2m"; R = "\033[0m"
    GRN = "\033[32m"; RED = "\033[31m"; YEL = "\033[33m"; CYA = "\033[36m"
else:
    B = DIM = R = GRN = RED = YEL = CYA = ""

# 我们接管代理时用的地址
PROXY_HOST = "127.0.0.1"
DEFAULT_PORT = 8899


def say(msg: str = "") -> None:
    # 终端窗口被关掉后 stdout 会变成坏管道，不能让它把收尾流程炸掉
    try:
        print(msg, flush=True)
    except Exception:  # noqa: BLE001
        pass


def rule() -> None:
    say(f"{DIM}{'─' * 68}{R}")


# ──────────────────────────────────────────────────────────────────────────────
# networksetup 封装（自包含，不依赖其他项目）
# ──────────────────────────────────────────────────────────────────────────────

def run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def list_services() -> list[str]:
    rc, out = run(["networksetup", "-listallnetworkservices"])
    services = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        services.append(line)
    return services


def device_to_service() -> dict[str, str]:
    """{'en0': 'Wi-Fi', ...}

    注意：`(Hardware Port: Wi-Fi, Device: en0)` 这种格式，用 ^Device: 是匹配不到的，
    必须从行内搜索。这里踩过一次坑。
    """
    rc, out = run(["networksetup", "-listnetworkserviceorder"])
    mapping: dict[str, str] = {}
    cur = None
    for line in out.splitlines():
        line = line.strip()
        m = re.match(r"^\(\d+\)\s+(.*)$", line)
        if m:
            cur = m.group(1).strip()
            continue
        m = re.search(r"Device:\s*([^)]+)", line)
        if m and cur:
            mapping.setdefault(m.group(1).strip(), cur)
    return mapping


def active_service() -> str | None:
    """当前默认路由走的是哪个网络服务。"""
    rc, out = run(["route", "-n", "get", "default"])
    m = re.search(r"interface:\s*(\S+)", out)
    if m:
        svc = device_to_service().get(m.group(1))
        if svc:
            return svc
    for s in ("Wi-Fi", "Ethernet", "USB 10/100/1000 LAN"):
        if s in list_services():
            return s
    svcs = list_services()
    return svcs[0] if svcs else None


def parse_proxy_output(out: str) -> dict:
    d = {"enabled": False, "server": "", "port": 0}
    for line in out.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "enabled":
            d["enabled"] = v.lower() == "yes"
        elif k == "server":
            d["server"] = v
        elif k == "port":
            try:
                d["port"] = int(v)
            except ValueError:
                d["port"] = 0
    return d


def get_proxy_one(service: str, flag: str) -> dict:
    rc, out = run(["networksetup", flag, service])
    return parse_proxy_output(out)


def get_proxy_state(service: str) -> dict:
    return {key: get_proxy_one(service, flag)
            for key, flag in (("web", "-getwebproxy"),
                              ("secure", "-getsecurewebproxy"))}


def apply_proxy(service: str, host: str, port: int) -> bool:
    """把系统代理指向 host:port。

    ⚠ 坑：`networksetup -setwebproxy` 会**隐式把代理打开**（实测：
    设完地址后 Enabled 变成 Yes）。所以设置地址之后**必须**再显式设一次
    开关状态，顺序不能反。
    """
    ok = True
    for setter in ("-setwebproxy", "-setsecurewebproxy"):
        rc, _ = run(["networksetup", setter, service, host, str(port)])
        ok = ok and rc == 0
    for st in ("-setwebproxystate", "-setsecurewebproxystate"):
        run(["networksetup", st, service, "on"])
    return ok


def restore_proxy(saved: dict) -> None:
    """按备份还原。

    注意顺序：先设地址（这一步会隐式把代理打开），再设开关状态。
    反过来的话会被 setwebproxy 又打开一次。
    """
    for svc, st in (saved or {}).items():
        for key, setter, stater in (("web", "-setwebproxy", "-setwebproxystate"),
                                    ("secure", "-setsecurewebproxy",
                                     "-setsecurewebproxystate")):
            d = st.get(key) or {}
            if d.get("server"):
                run(["networksetup", setter, svc, d["server"], str(d.get("port") or 0)])
                run(["networksetup", stater, svc, "on" if d.get("enabled") else "off"])
            else:
                run(["networksetup", stater, svc, "off"])


# ──────────────────────────────────────────────────────────────────────────────
# 关闭 / 看门狗 / 状态
#
# 核心风险：系统代理一旦指向 127.0.0.1:8899 而 8899 上没有进程在听，
# 整台机器就断网。所以这里有两道保险：
#   ① 主进程正常退出（含 Ctrl-C）时自己还原；
#   ② 主进程被强杀 / 窗口被关 / 系统崩了 —— 由独立会话的看门狗还原。
# 再加一个手动总闸 --close，覆盖"看门狗也死了"的极端情况。
# ──────────────────────────────────────────────────────────────────────────────

WATCHDOG_STATE = HERE / "watchdog.json"
WATCHDOG_SCRIPT = HERE / "proxy_watchdog.py"


def listening_procs(port: int) -> list[tuple[int, str]]:
    """返回监听该端口的 [(pid, 命令名)]。

    用 `lsof -F pc` 而不是 `ps` —— 一来少一次调用，二来 ps 在某些受限环境里
    会 "operation not permitted"，那就认不出进程了。
    """
    rc, out = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-F", "pc"])
    procs: list[tuple[int, str]] = []
    pid: int | None = None
    for line in out.splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
            except ValueError:
                pid = None
        elif line.startswith("c") and pid is not None:
            procs.append((pid, line[1:]))
    return procs


def mitm_listening(port: int) -> bool:
    """只有真的 mitmdump 在听才算 —— 别的进程占着同一个端口不算。"""
    return any("mitmdump" in comm for _pid, comm in listening_procs(port))


def port_in_use(port: int) -> bool:
    rc, out = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])
    return rc == 0 and bool(out.strip())


def pick_free_port(start: int = DEFAULT_PORT, tries: int = 20) -> int | None:
    """从 start 起找一个空闲端口。"""
    for p in range(start, start + tries):
        if not port_in_use(p):
            return p
    return None


# 我们的端口都在这个区间。--close 在没有会话记录时靠它兜底。
OUR_PORTS = frozenset(range(DEFAULT_PORT, DEFAULT_PORT + 20))


def _is_ours(server: str, port: int, host: str, want: int | None) -> bool:
    if server != host:
        return False
    if want is None:
        return port in OUR_PORTS          # 我们的任意端口
    return port == want


def points_at_us(service: str, port: int | None = None,
                 host: str = PROXY_HOST) -> bool:
    """该服务的代理是否指向我们。port=None 表示"我们的任意端口"。"""
    for flag in ("-getwebproxy", "-getsecurewebproxy"):
        d = get_proxy_one(service, flag)
        if _is_ours(d.get("server", ""), int(d.get("port") or 0), host, port):
            return True
    return False


def turn_off_if_ours(service: str, port: int | None = None,
                     host: str = PROXY_HOST) -> bool:
    """把该服务里指向我们的代理关掉。返回是否动过。

    只关"指向我们的" —— 绝不碰用户自己的 Loon / Shadowrocket 设置。
    """
    touched = False
    for flag, stater in (("-getwebproxy", "-setwebproxystate"),
                         ("-getsecurewebproxy", "-setsecurewebproxystate")):
        d = get_proxy_one(service, flag)
        if _is_ours(d.get("server", ""), int(d.get("port") or 0), host, port):
            run(["networksetup", stater, service, "off"])
            touched = True
    return touched


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def kill_our_mitm(port: int | None = None) -> list[int]:
    """只杀 mitmdump，绝不碰别的进程。

    port=None 时扫我们整个端口区间。注意：端口被别人（比如别的项目的开发
    服务器）占着时，这里会**刻意放过** —— 那是别人的东西，不能动。
    """
    ports = [port] if port is not None else sorted(OUR_PORTS)
    killed: list[int] = []
    for p in ports:
        for pid, comm in listening_procs(p):
            if "mitmdump" not in comm:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
            except OSError:
                pass
    return killed


def watchdog_alive() -> int | None:
    if not WATCHDOG_STATE.exists():
        return None
    try:
        d = json.loads(WATCHDOG_STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    pid = int(d.get("pid") or 0)
    return pid if pid_alive(pid) else None


def kill_watchdog() -> int | None:
    """停掉看门狗。

    ⚠ 必须防自杀：看门狗自己也会调 close_all，而 close_all 会走到这里。
    不判断的话就是给自己发 SIGTERM → 信号处理器再调 close_all → 无限递归。
    """
    pid = watchdog_alive()
    if pid and pid != os.getpid():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    WATCHDOG_STATE.unlink(missing_ok=True)
    return pid


def spawn_watchdog(port: int, mitm_pid: int, max_minutes: int) -> int | None:
    """起一个独立会话的看门狗。

    start_new_session=True 是关键：它脱离当前终端的进程组，
    所以关窗口 / Ctrl-C / 强杀主进程都带不走它。
    """
    if not WATCHDOG_SCRIPT.exists():
        say(f"{YEL}⚠ 缺少 {WATCHDOG_SCRIPT.name}，本次没有看门狗保护{R}")
        return None
    cmd = [sys.executable, str(WATCHDOG_SCRIPT),
           "--port", str(port), "--mitm-pid", str(mitm_pid),
           "--state", str(WATCHDOG_STATE),
           "--log", str(HERE / "replace.log"),
           "--max-minutes", str(max_minutes)]
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             cwd=str(HERE), start_new_session=True)
    except Exception as e:  # noqa: BLE001
        say(f"{YEL}⚠ 看门狗启动失败（{e}）{R}")
        say(f"{YEL}  请务必用 Ctrl-C 停止，别直接关窗口 —— 否则会断网{R}")
        return None
    WATCHDOG_STATE.write_text(json.dumps({
        "pid": p.pid, "mitm_pid": mitm_pid, "port": port,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p.pid


def close_all(port: int | None = None, *, verbose: bool = True) -> int:
    """手动总闸：彻底关掉代理与相关进程。

    顺序很重要 —— **先救网络，再收进程**。哪怕中间崩了，网络也已经好了。

    原则：**只动"当前指着我们"的服务**。
    绝不盲目按备份去还原 —— 备份可能是旧的，那样反而会把用户后来
    改好的设置覆盖掉。

    port=None 表示"我们的任意端口"（不知道具体端口时的兜底）。
    """
    did: list[str] = []

    saved: dict = {}
    if BACKUP.exists():
        try:
            saved = json.loads(BACKUP.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            saved = {}

    # ① 先救网络。
    #    顺序上把**当前默认路由用的那个服务**排最前 —— 用户能不能上网
    #    主要看它，先修好它，剩下的慢慢来。
    svcs = list_services()
    act = active_service()
    if act and act in svcs:
        svcs.remove(act)
        svcs.insert(0, act)

    for svc in svcs:
        if not points_at_us(svc, port):
            continue                      # 不是我们干的，别碰
        orig = saved.get(svc)
        if orig:
            # 还原成接管前的样子（可能本来就是关的，也可能是 Loon 的 7890）
            restore_proxy({svc: orig})
            web = orig.get("web") or {}
            did.append(f"{svc}：还原成接管前的设置"
                       f"（{web.get('server') or '空'}:{web.get('port') or 0}，"
                       f"{'启用' if web.get('enabled') else '停用'}）")
            # 万一原值恰好也指着我们，再确保关掉
            if points_at_us(svc, port):
                turn_off_if_ours(svc, port)
                did.append(f"{svc}：已强制关闭")
        else:
            # 没有备份 —— 只能直接关掉，不去猜原值
            turn_off_if_ours(svc, port)
            did.append(f"{svc}：已关闭（没有备份，只关不猜）")

    # ② 再停进程
    wp = kill_watchdog()
    if wp:
        did.append(f"停掉看门狗 (PID {wp})")
    for pid in kill_our_mitm(port):
        did.append(f"停掉 mitmdump (PID {pid})")

    # ③ 清看门狗状态。
    #    proxy_backup.json **故意保留** —— 它是"接管前状态"的记录，
    #    万一下次崩了，还有据可依。
    WATCHDOG_STATE.unlink(missing_ok=True)

    if verbose:
        if did:
            for d in did:
                say(f"  · {d}")
        else:
            say("  没有发现需要关闭的东西（代理本来就没指向本工具）")
    return 0


def recover_leftover(port: int | None = None, *, verbose: bool = True) -> list[str]:
    """启动前先把上次没关干净的收拾掉。

    上次崩了 → 系统代理还指着我们 → 用户此刻是断网的。
    不先修好就开始接管，等于把问题滚雪球。
    """
    leftover = [s for s in list_services() if points_at_us(s, port)]
    if not leftover:
        return []

    if verbose:
        say()
        say(f"{RED}⚠ 检测到系统代理还指着我们（{PROXY_HOST}），但端口是空闲的{R}")
        say(f"  也就是说：{', '.join(leftover)} 现在是断网状态。")
        say(f"  先修好再继续 ——")
    close_all(port, verbose=verbose)
    if verbose:
        say(f"{GRN}✔ 已恢复{R}")
        say()
    return leftover


def status(port: int | None = None) -> int:
    """看清当前到底是"没接管"、"正常运行"还是"已经断网"。"""
    svc = active_service()
    say(f"{B}  当前状态{R}")
    say(f"  默认网络服务：{svc or '认不出'}")
    say()

    ours_on: list[tuple[str, int]] = []
    any_on = False
    for s in list_services():
        st = get_proxy_state(s)
        for key, label in (("web", "HTTP "), ("secure", "HTTPS")):
            d = st[key]
            if not d.get("enabled"):
                continue
            any_on = True
            srv, prt = d.get("server", ""), int(d.get("port") or 0)
            mark = ""
            if srv == PROXY_HOST and prt in OUR_PORTS:
                mark = f"  {YEL}← 指向本工具{R}"
                ours_on.append((s, prt))
            say(f"  {s} / {label}：开启 → {srv}:{prt}{mark}")
    if not any_on:
        say(f"  {DIM}所有网络服务都没开代理（网络是直连的）{R}")

    # 端口上到底是谁？这一步很关键 —— 别的进程占着同一个端口，
    # 不代表我们的 mitmdump 在跑。
    probe = port if port is not None else DEFAULT_PORT
    procs = listening_procs(probe)
    ours_mitm = [p for p, c in procs if "mitmdump" in c]
    others = [(p, c) for p, c in procs if "mitmdump" not in c]
    wp = watchdog_alive()

    say()
    if ours_mitm:
        say(f"  mitmdump ：{GRN}✔ 在跑{R}（127.0.0.1:{probe}，PID "
            f"{', '.join(str(p) for p in ours_mitm)}）")
    elif others:
        say(f"  mitmdump ：{YEL}✘ 没在跑{R}")
        for p, c in others:
            say(f"             {DIM}端口 {probe} 上是别的进程：{c} (PID {p}){R}")
    else:
        say(f"  mitmdump ：✘ 没在跑（端口 {probe} 空闲）")
    say(f"  看门狗   ：{('✔ 在跑 (PID %d)' % wp) if wp else '✘ 没在跑'}")
    say()

    if ours_on and not ours_mitm:
        say(f"{RED}  ⚠ 代理开着，但代理进程没在跑 —— 你现在就是断网状态{R}")
        say(f"  立刻修：python3 {HERE / 'replace_proxy.py'} --close")
        return 2
    if ours_on and ours_mitm:
        say(f"{GRN}  ✔ 代理运行中，替换已就绪{R}")
        say(f"  {DIM}用完记得停：Ctrl-C，或双击「关闭代理.command」{R}")
        return 0
    say(f"  {DIM}代理未接管，网络直连 —— 没有断网风险{R}")
    return 0


def local_proxy_upstream(saved: dict) -> str | None:
    """本机原本就开着代理（Loon / Shadowrocket / Clash…）时，串成上游，
    免得接管系统代理后把用户自己的链路弄断。"""
    for st in (saved or {}).values():
        for key in ("web", "secure"):
            d = st.get(key) or {}
            srv, port = d.get("server", ""), d.get("port", 0)
            if d.get("enabled") and srv in ("127.0.0.1", "localhost", "::1") and port:
                return f"http://{srv}:{port}"
    return None


def ca_trusted() -> bool:
    ca = Path("~/.mitmproxy/mitmproxy-ca-cert.pem").expanduser()
    if not ca.exists():
        return False
    rc, _ = run(["security", "verify-cert", "-c", str(ca)])
    return rc == 0


def mitmdump_path() -> str | None:
    return shutil.which("mitmdump") or (
        "/opt/homebrew/bin/mitmdump"
        if Path("/opt/homebrew/bin/mitmdump").exists() else None)


# ──────────────────────────────────────────────────────────────────────────────
# 预检
# ──────────────────────────────────────────────────────────────────────────────

def preflight(args) -> tuple[str, str | None, Path | None]:
    """返回 (mitmdump, upstream, har_addon)。失败则退出。"""
    md = mitmdump_path()
    if not md:
        say(f"{RED}✘ 找不到 mitmdump{R}")
        say("  装一下：brew install mitmproxy")
        sys.exit(1)
    say(f"  {DIM}mitmdump ：{md}{R}")

    addon = HERE / "cup_replace_addon.py"
    if not addon.exists():
        say(f"{RED}✘ 缺少 {addon}{R}")
        sys.exit(1)

    # 作品
    art = core.find_artwork(HERE)
    if not art:
        say(f"{YEL}⚠ 还没放作品文件{R}")
        say(f"  把图存成 {HERE / 'artwork.png'}，或先跑：")
        say(f"    {core.preferred_python()} prepare_artwork.py 你的图.jpg")
        say("  代理仍会启动，但不会替换（只放行）。")
    else:
        size = core.image_size(art.read_bytes())
        dim = f"{size[0]}×{size[1]}" if size else "尺寸未知"
        flag = ""
        if size and size != core.CANVAS:
            flag = f"  {YEL}← 不是 {core.CANVAS_DESC}，建议先预适配{R}"
        say(f"  作品     ：{art.name}（{dim}）{flag}")

    if not ca_trusted():
        say(f"{YEL}⚠ mitmproxy CA 未被系统信任 —— HTTPS 会解密失败{R}")
        say("  先装一次：")
        say(f"{DIM}    mitmdump 首次运行会在 ~/.mitmproxy/ 生成 CA；{R}")
        say(f"{DIM}    双击 mitmproxy-ca-cert.pem 导入「钥匙串」，并设为「始终信任」{R}")
    else:
        say(f"  {DIM}CA 证书  ：已信任 ✔{R}")

    # 端口：被占就自动换一个（8899 很常被别的工具/项目占用，别硬撞）
    if not args.port:
        args.port = DEFAULT_PORT
    if port_in_use(args.port):
        procs = listening_procs(args.port)
        who = "，".join(f"{c} (PID {p})" for p, c in procs) or "未知进程"
        if any("mitmdump" in c for _p, c in procs):
            say(f"{YEL}⚠ 端口 {args.port} 上已经有一个 mitmdump 在跑{R}")
            say(f"  先关掉它：python3 {HERE / 'replace_proxy.py'} --close")
            sys.exit(1)
        newp = pick_free_port(args.port + 1)
        if newp is None:
            say(f"{RED}✘ 端口 {args.port} 被占用，附近 20 个端口也不空闲{R}")
            say(f"  占着它的是：{who}")
            sys.exit(1)
        say(f"  {YEL}⚠ 端口 {args.port} 被占用（{who}），自动改用 {newp}{R}")
        args.port = newp
    say(f"  {DIM}端口     ：{args.port} 空闲 ✔{R}")

    # 断网体检：上次是不是没关干净？是的话**现在就修**，别把问题滚雪球
    # 这里扫我们整个端口区间，因为上次崩的时候可能用的是别的端口
    recover_leftover(None)

    leftover = watchdog_alive()
    if leftover:
        say(f"  {YEL}⚠ 上次的看门狗还在跑 (PID {leftover})，本次启动会替换它{R}")
        kill_watchdog()

    har_addon = None
    if args.har:
        for c in HAR_ADDON_CANDIDATES:
            if c.exists():
                har_addon = c
                break
        if har_addon:
            say(f"  {DIM}同时抓 HAR：{har_addon.parent.name}/har_addon.py{R}")
        else:
            say(f"{YEL}⚠ --har 指定了但没找到 har_addon.py，本次不抓包{R}")

    upstream = None
    if not args.no_proxy:
        svc = active_service()
        saved = {svc: get_proxy_state(svc)} if svc else {}
        upstream = local_proxy_upstream(saved)
        if upstream:
            say(f"  {DIM}检测到本机原有代理，串联为上游：{upstream}{R}")

    return md, upstream, har_addon


# ──────────────────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="前台启动 mitm 代理做杯贴替换")
    ap.add_argument("--port", type=int, default=0,
                    help=f"代理端口，默认 {DEFAULT_PORT}；被占用时自动顺延。"
                         f"--close/--status 时给 0 表示扫我们的整个端口区间")
    ap.add_argument("--no-proxy", action="store_true",
                    help="不接管系统代理（你自己配了代理时用）")
    ap.add_argument("--har", action="store_true", help="顺便用 har_addon 抓 HAR")
    ap.add_argument("--only-qmai", action="store_true",
                    help="只拦截 *.qmai.cn / *.chagee.com，其余流量直通")
    ap.add_argument("--keep-proxy", action="store_true",
                    help="退出时保留系统代理（调试用）")
    ap.add_argument("--close", "--restore", dest="close", action="store_true",
                    help="关闭：还原系统代理 + 停掉 mitmdump 和看门狗（断网急救）")
    ap.add_argument("--status", action="store_true",
                    help="看清当前状态：代理有没有被接管、是不是已经断网")
    ap.add_argument("--max-minutes", type=int, default=180,
                    help="最长接管多少分钟后自动关闭，默认 180；0 = 不限")
    ap.add_argument("--no-watchdog", action="store_true",
                    help="不起看门狗（窗口被强关时不会自动还原代理，不建议）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只做预检和打印命令，不真起代理")
    args = ap.parse_args(argv)

    # --close / --status 用 0 表示"我们的任意端口"
    scan_port: int | None = args.port or None

    say()
    say(f"{B}  霸王茶姬 · 定制杯贴  ·  mitm 代理替换{R}")
    rule()
    say(f"  {DIM}请求由真实小程序发出（WAF 签名/登录态/机审都照常），"
        f"只在传输中把像素换成你的图{R}")
    say()

    # ── 关闭 / 状态：这两个不预检、不启动任何东西 ──
    if args.close:
        say(f"{B}  关闭代理{R}")
        say(f"  {DIM}先救网络，再收进程；只动指向本工具的设置，不碰你的 Loon{R}")
        say()
        rc = close_all(scan_port)
        say()
        say(f"{GRN}✔ 已关闭。网络应该立刻恢复正常。{R}")
        say(f"  {DIM}如果浏览器还是打不开，看：--status{R}")
        say()
        return rc

    if args.status:
        return status(scan_port)

    say(f"{B}  预检{R}")
    md, upstream, har_addon = preflight(args)
    say()

    # ── 组装命令 ──
    cmd = [md, "-s", str(HERE / "cup_replace_addon.py")]
    if har_addon:
        cmd += ["-s", str(har_addon)]
    cmd += ["--listen-host", "127.0.0.1", "--listen-port", str(args.port),
            "--set", "termlog_verbosity=info", "--set", "flow_detail=0"]
    if args.only_qmai:
        for pat in (r"\.qmai\.cn", r"\.chagee\.com", r"\.weixin\.qq\.com"):
            cmd += ["--allow-hosts", pat]
    if upstream:
        cmd += ["--upstream", upstream]

    env = os.environ.copy()
    if har_addon:
        out = HERE / "har_capture"
        out.mkdir(exist_ok=True)
        env["HAR_OUT_DIR"] = str(out)
        env["HAR_CLIENT"] = f"proxy@127.0.0.1:{args.port}"
        say(f"  {DIM}HAR 落盘：{out}{R}")

    if args.dry_run:
        say(f"{B}  --dry-run：将要执行的命令{R}")
        say(f"  {' '.join(cmd)}")
        say()
        return 0

    # ── 起代理（前台）──
    #
    #    顺序很关键，别搞反：
    #       ① 先把 mitmdump 拉起来、等端口真的在听
    #       ② 再接管系统代理
    #       ③ 立刻放看门狗
    #    如果反过来（先接管代理再起进程），从"接管"到"端口就绪"这段时间
    #    系统代理指着一个没人听的端口 —— 用户会白白断网一两秒，而且这期间
    #    看门狗还没起来，真被强杀就没人救。
    rc = 0
    proc = None
    wd_pid = None
    old_handlers: dict = {}
    saved: dict = {}
    svc = None

    def _sig(signum, frame):
        if proc and proc.poll() is None:
            proc.terminate()

    # SIGHUP 也要接：直接点终端窗口的 × 发的就是它。
    # 不接的话进程会被立刻杀掉，finally 跑不到，代理就留在原地 → 断网。
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            old_handlers[sig] = signal.signal(sig, _sig)
        except (AttributeError, ValueError, OSError):
            pass

    try:
        say(f"{DIM}  正在启动代理（端口 {args.port}）…{R}")
        proc = subprocess.Popen(cmd, env=env, cwd=str(HERE))

        # ① 等端口真正起来。就绪之前**不碰**系统代理 —— 这样万一代理起不来，
        #    网络从头到尾没被动过，用户根本不会断网。
        ready = False
        for _ in range(80):
            if proc.poll() is not None:
                break
            if port_in_use(args.port):
                ready = True
                break
            time.sleep(0.1)

        if not ready:
            say(f"{RED}✘ 代理没能启动（端口 {args.port} 一直没就绪）{R}")
            say(f"  {DIM}没有动系统代理，网络不受影响{R}")
            rc = 1
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        else:
            # ② 端口就绪，现在才接管系统代理（此刻起才可能断网）
            if not args.no_proxy:
                svc = active_service()
                if not svc:
                    say(f"{YEL}⚠ 认不出当前网络服务，跳过系统代理接管{R}")
                    say("  可以手动把系统代理设成 127.0.0.1:%d" % args.port)
                else:
                    saved = {svc: get_proxy_state(svc)}
                    BACKUP.write_text(json.dumps(saved, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
                    if apply_proxy(svc, "127.0.0.1", args.port):
                        say(f"{GRN}✔ 系统代理已指向 127.0.0.1:{args.port}{R}"
                            f"  （网络服务：{svc}）")
                        say(f"  {DIM}原状态已备份到 {BACKUP.name}，退出时自动还原{R}")
                    else:
                        say(f"{RED}✘ 设置系统代理失败{R}（可能需要管理员权限）")
                        say("  手动设置：系统设置 → 网络 → 详细信息 → 代理 → Web 代理(HTTP)")
                        saved = {}
            else:
                say(f"{YEL}--no-proxy：不动系统代理{R}")
                say(f"  请自行把代理设为 127.0.0.1:{args.port}")

            # ③ 代理已就绪、系统代理已接管 → 立刻放看门狗，把"裸奔窗口"压到最小
            if not args.no_proxy and not args.keep_proxy \
                    and not args.no_watchdog and args.max_minutes != 0:
                wd_pid = spawn_watchdog(args.port, proc.pid, args.max_minutes)
                if wd_pid:
                    say(f"{GRN}✔ 看门狗已启动 (PID {wd_pid}){R}")
                    say(f"  {DIM}它每 2 秒看一眼代理还活着没。万一本窗口被强关、"
                        f"或进程被杀，它会在几秒内自动还原代理，不会让你断网。{R}")
                    say(f"  {DIM}最长接管 {args.max_minutes} 分钟，到点自动关闭。{R}")
            elif args.no_watchdog:
                say(f"{YEL}⚠ --no-watchdog：窗口被强关时不会自动还原代理{R}")

            # 一切就绪，才告诉用户去操作
            say()
            rule()
            say(f"{B}  现在去操作{R}")
            say()
            say("    1. 微信里打开霸王茶姬小程序")
            say("    2. 下单 → 定制杯贴 → 创作杯贴")
            say(f"    3. {B}随便画一笔{R}（画成什么样都无所谓，反正会被换掉）")
            say(f"    4. 点「{B}提交创作{R}」")
            say()
            say(f"  看到 {GRN}✔ 已替换{R} 就说明成功了。")
            say()
            say(f"  {B}用完一定要关{R} —— 代理不关就断网：")
            say(f"    · 在这里按 {B}Ctrl-C{R}（会自动还原）")
            say(f"    · 或者双击 {B}关闭代理.command{R}")
            say(f"    · 或者 {DIM}python3 {HERE / 'replace_proxy.py'} --close{R}")
            rule()
            say()

            rc = proc.wait()
    except FileNotFoundError as e:
        say(f"{RED}✘ 启动失败：{e}{R}")
        rc = 1
    finally:
        for sig, old in old_handlers.items():
            try:
                signal.signal(sig, old)
            except (ValueError, OSError):
                pass

        # 顺序：先还原代理（救网络），再停看门狗。
        # 备份文件**故意不删** —— 保留"接管前状态"的记录，下次崩了还有据可依。
        if saved and not args.keep_proxy:
            restore_proxy(saved)
            say()
            say(f"{GRN}✔ 系统代理已还原{R}（{svc}）")
        if wd_pid:
            kill_watchdog()
        if saved and args.keep_proxy:
            say()
            say(f"{YEL}⚠ --keep-proxy：系统代理仍指向 127.0.0.1:{args.port}{R}")
            say(f"  关掉：python3 {HERE / 'replace_proxy.py'} --close")

    # ── 收尾 ──
    say()
    rule()
    log = HERE / "replace.log"
    if rc != 0:
        say(f"{RED}代理没能正常启动，看上面的报错。{R}")
        say(f"  {DIM}系统代理没有被改动，网络是好的。排完再跑一次即可。{R}")
    elif log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        hits = [l for l in lines if "已替换" in l or "图片地址" in l]
        if hits:
            say(f"{GRN}本次替换记录{R}")
            for l in hits[-8:]:
                say(f"  {l}")
            say()
            urls = [l.split("：", 1)[-1].strip() for l in hits if "图片地址" in l]
            if urls:
                say(f"  {CYA}服务端存下的图：{urls[-1]}{R}")
                say(f"  {DIM}打开看看是不是你那张：open \"{urls[-1]}\"{R}")
        else:
            say(f"{YEL}没有替换记录。可能原因：{R}")
            say("  · 还没在小程序里点「提交创作」")
            say("  · 小程序没走代理（检查系统代理是否生效 / 是否重启过小程序）")
            say("  · 微信小程序有独立代理设置，需在微信开发者工具里确认")
            say(f"  完整日志：{log}")
    else:
        say(f"{YEL}没有生成 replace.log —— 插件可能压根没被加载{R}")
        say("  检查上面 mitmdump 的启动输出有没有报错。")
    say()
    return rc


if __name__ == "__main__":
    sys.exit(main())
