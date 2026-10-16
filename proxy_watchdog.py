#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
proxy_watchdog.py — 代理看门狗：主进程意外死掉时，自动把系统代理关掉

为什么需要它
    系统代理一旦指向 127.0.0.1:8899 而 8899 上没有进程在听，**整台机器就断网**。
    正常 Ctrl-C 时主进程会自己还原，但这几种情况它还原不了：
        · 直接点终端窗口的 × 关掉（SIGHUP，进程被立刻杀掉）
        · 在活动监视器里强制退出 / kill -9
        · 系统崩溃、断电、笔记本电量耗尽
    所以主进程会额外起这个**独立会话**的看门狗（start_new_session=True）。
    它脱离了终端窗口的进程组，窗口关掉也带不走它。
    每 2 秒检查一次：mitmdump 还在吗？端口还在听吗？
    一旦确认不在跑、而系统代理还指着我们 → 立刻还原，并弹一条 macOS 通知。

它绝不做的事
    · 不碰任何"不指向本工具"的代理设置（你自己的 Loon / Shadowrocket 原样不动）
    · 不杀任何不是 mitmdump 的进程
    · 不写任何日志到网络或磁盘以外的地方

它不是常驻服务
    主进程正常收尾时会把它一起停掉。它也不会自己重启。
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import replace_proxy as rp  # noqa: E402


def log(log_path: Path, msg: str) -> None:
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  [看门狗] {msg}\n")
    except OSError:
        pass


def notify(title: str, body: str) -> None:
    """弹一条 macOS 通知 —— 用户得知道代理被自动关了，不然会一头雾水。"""
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {json.dumps(body, ensure_ascii=False)} '
             f'with title {json.dumps(title, ensure_ascii=False)} '
             f'sound name "Ping"'],
            capture_output=True, timeout=8)
    except Exception:  # noqa: BLE001
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="杯贴替换代理看门狗")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--mitm-pid", type=int, required=True)
    ap.add_argument("--state", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--max-minutes", type=int, default=180)
    ap.add_argument("--interval", type=float, default=2.0)
    args = ap.parse_args(argv)

    log_path = Path(args.log)
    state_path = Path(args.state)
    deadline = time.time() + args.max_minutes * 60

    # 自己写一份状态，方便 --status 看到
    try:
        state_path.write_text(json.dumps({
            "pid": os.getpid(),
            "mitm_pid": args.mitm_pid,
            "port": args.port,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "deadline": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(deadline)),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass

    _done = {"v": False}

    def bye(reason: str) -> int:
        """收尾：直接复用 close_all —— 先救网络，再清理。

        close_all 只会动"当前指着我们"的服务，所以拿旧的备份来跑也安全。

        重入保护：SIGTERM 信号处理器也会调进来，而 close_all 里有可能
        再发信号，不挡住就是无限递归。
        """
        if _done["v"]:
            return 0
        _done["v"] = True

        rp.close_all(args.port, verbose=False)
        state_path.unlink(missing_ok=True)

        msg = f"{reason}，已自动还原系统代理（否则你会断网）"
        log(log_path, msg)
        notify("杯贴替换 · 代理已自动关闭", msg)
        return 0

    def _term(signum, frame):
        bye("收到停止信号")
        sys.exit(0)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(sig, _term)
        except (AttributeError, ValueError, OSError):
            pass

    log(log_path, f"启动：盯着 mitmdump PID {args.mitm_pid}，端口 {args.port}，"
                  f"最长 {args.max_minutes} 分钟")

    while True:
        time.sleep(args.interval)

        alive = rp.pid_alive(args.mitm_pid)
        listening = rp.port_in_use(args.port)
        if alive and listening:
            if time.time() > deadline:
                return bye(f"已达 {args.max_minutes} 分钟上限")
            continue

        # 代理看起来没了 —— 给主进程 1.5 秒自己收尾（正常 Ctrl-C 时它会先还原）
        time.sleep(1.5)
        if rp.pid_alive(args.mitm_pid) or rp.port_in_use(args.port):
            continue

        # 主进程收尾干净了 → 我们没事干，安静退出
        still_on = [s for s in rp.list_services() if rp.points_at_us(s, args.port)]
        if not still_on:
            state_path.unlink(missing_ok=True)
            log(log_path, "代理已还原，看门狗正常退出")
            return 0

        # 代理没了但系统代理还指着我们 —— 就是这一刻会断网，马上救
        return bye(f"代理进程已退出（{', '.join(still_on)} 仍指向 "
                   f"{rp.PROXY_HOST}:{args.port}）")


if __name__ == "__main__":
    sys.exit(main())
