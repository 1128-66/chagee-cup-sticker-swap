#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cup_replace_addon.py — mitmproxy 插件：把小程序上传的杯贴换成你自己的图

思路（重要）
    这里**不做任何"绕过"**。请求由真实的小程序 webview 发出：
      · WAF 的 JS 签名由小程序自己算，被正常满足，一点没绕；
      · 登录态是你自己的，真实有效；
      · 机审/风控照常走。
    本插件只做一件事：在 `upload/image/index` 这个上传请求经过代理时，
    把 multipart 里**属于图片的那一段字节**换成你准备好的画。
    其余字段（sellerId / token / 时间戳…）字节级原样保留。

    于是：你在小程序里随便涂一笔点「提交创作」，服务端收到的却是你的作品。
    绕过的只有"画布太简陋"这个体验限制，不是任何安全控制。

用法
    1) 把画好的图放到本文件同目录，命名 artwork.png（jpg/webp/gif/bmp 都行）
       —— 任意尺寸，插件会自动适配到 618×927。
    2) 起代理（本仓库的 replace_proxy.py 会自动配好 CA 与系统代理）：
           python3 replace_proxy.py
       想手动起、并同时抓 HAR，就再挂一个抓包脚本：
           mitmdump -s <你的 har_addon.py> -s cup_replace_addon.py ...
    3) 微信里打开小程序 → 定制杯贴 → 随便画一笔 → 提交创作
    4) 看终端：出现 "✔ 已替换" 即成功，紧接着会打出服务端返回的图片地址

    换图不用重启：直接覆盖 artwork.png，插件下次请求会自动重载。

环境变量
    CUP_REPLACE_IMAGE   指定作品路径（默认找同目录 artwork.* / cup.* / 杯贴.*）
    CUP_REPLACE_ENABLE  设为 0 临时停用替换（插件仍加载，只放行）
    CUP_REPLACE_MAX     最多替换几次，默认 0 = 不限
    CUP_REPLACE_FIT     contain(默认) / cover / stretch / none
    CUP_REPLACE_BG      white(默认) / transparent
    CUP_REPLACE_LOG     日志文件路径，默认同目录 replace.log
    CUP_REPLACE_DRYRUN  设为 1 只打印将要做什么，不真的改请求

先自检（不起代理也能跑）：
    python3 selftest_replace.py
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from mitmproxy import ctx, http

import cup_replace_core as core

HERE = Path(__file__).resolve().parent

ART = core.Artwork(HERE)


# ──────────────────────────────────────────────────────────────────────────────
# 日志：接管 core 的输出，同时落盘
# ──────────────────────────────────────────────────────────────────────────────

def _log_path() -> Path:
    return Path(os.environ.get("CUP_REPLACE_LOG") or (HERE / "replace.log"))


def _file(msg: str, level: str = "INFO") -> None:
    try:
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  [{level}] {msg}\n")
    except OSError:
        pass


def _info(msg: str) -> None:
    ctx.log.info(f"[杯贴替换] {msg}")
    _file(msg)


def _warn(msg: str) -> None:
    ctx.log.warn(f"[杯贴替换] {msg}")
    _file(msg, "WARN")


core.set_logger(_info, _warn)


def _dry_run() -> bool:
    return os.environ.get("CUP_REPLACE_DRYRUN", "0") == "1"


def _is_target(flow: http.HTTPFlow) -> bool:
    return (flow.request.pretty_host == core.TARGET_HOST
            and flow.request.path.split("?")[0] == core.TARGET_PATH)


# ──────────────────────────────────────────────────────────────────────────────
# 钩子
# ──────────────────────────────────────────────────────────────────────────────

def request(flow: http.HTTPFlow) -> None:
    if not _is_target(flow):
        return

    body = flow.request.content or b""
    _info(f"捕获上传请求（{len(body)} B）")

    if not ART.load():
        _warn("没有找到作品文件，本次放行不替换。把你的图存成 "
              f"{HERE / 'artwork.png'} 即可")
        return
    ART.reload_if_changed()

    if not ART.quota_left():
        _info(f"已达 CUP_REPLACE_MAX 上限，本次放行不替换")
        return

    ct = flow.request.headers.get("content-type", "")
    boundary = core.boundary_of(ct)
    if not boundary:
        _warn(f"这不是 multipart 请求（content-type={ct!r}），放行不替换")
        return

    assert ART.data is not None
    new_name = ART.next_filename()
    try:
        new_body = core.rewrite_upload(body, boundary, ART.data,
                                       new_name, ART.mime)
    except core.MultipartError as e:
        _warn(f"{e}；放行不替换")
        return

    if new_body is None:
        _warn('没在 multipart 里找到文件字段（name="upfile" 或 filename=），放行不替换')
        return

    if _dry_run():
        _info("DRYRUN=1，只预览不改写：")
        for line in core.plan_summary(body, boundary, ART.data).splitlines():
            _info(line)
        return

    core.apply_body(flow.request, new_body)

    ART.count += 1
    _info(f"✔ 已替换（第 {ART.count} 次）：{len(body)} B → {len(new_body)} B，"
          f"filename={new_name}")


def response(flow: http.HTTPFlow) -> None:
    """把服务端返回的图片地址打出来，方便确认替换生效。"""
    if not _is_target(flow):
        return
    if not flow.response:
        return
    try:
        j = flow.response.json()
    except Exception:  # noqa: BLE001
        return

    url = core.find_image_url(j)
    if url:
        ART.last_url = url
        _info(f"服务端返回图片地址：{url}")
        _info("→ 接着小程序会自己调 save，那是它自己的合法请求，不用管。")


def load(loader) -> None:
    """插件加载时打招呼 + 预热作品。"""
    ok = ART.load(force=True)
    w = 72
    print()
    print("=" * w)
    print("  杯贴替换插件已加载")
    print(f"  监听目标：https://{core.TARGET_HOST}{core.TARGET_PATH}")
    print(f"  画布尺寸：{core.CANVAS_DESC}")
    if _dry_run():
        print("  模式    ：DRYRUN（只预览，不改请求）")
    if ok:
        print(f"  作品文件：{ART.path}")
        print(f"  尺寸策略：{os.environ.get('CUP_REPLACE_FIT', 'contain')}"
              f" / 底色 {os.environ.get('CUP_REPLACE_BG', 'white')}")
        print(f"  已启用  ：把小程序上传的图替换成上面这张")
    else:
        print("  作品文件：未找到（本次只放行，不替换）")
        print(f"  放这里  ：{HERE / 'artwork.png'}")
        print("  也可以  ：CUP_REPLACE_IMAGE=/path/to/your.png 指定")
    print("  用法    ：在小程序里随便画一笔 → 提交创作，剩下交给插件")
    print("  自检    ：python3 selftest_replace.py")
    print("=" * w)
    print()


def done() -> None:
    if ART.count:
        _info(f"共替换 {ART.count} 次，插件退出")
