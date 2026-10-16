#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest_mitm.py — 在 mitmproxy 真实运行时里验证 cup_replace_addon 的接线

为什么单独一个文件
    selftest_replace.py 验证的是"字节运算对不对"，跑在普通 Python 里。
    但真正上线时，addon 是跑在 mitmproxy 自带的 Python（3.14，无 Pillow）里、
    面对的是 mitmproxy 自己的 http.HTTPFlow 对象。这一层必须单独验，
    否则"逻辑对但接线错"（比如 pretty_host 取不到、Headers 大小写、content-length 没更新）
    会在真实抓包时才炸。

它不联网
    用仓库自带的合成抓包素材（`fixtures/upload_request.har`）里的 multipart
    手工构造一个 HTTPFlow，直接喂给 addon.request()，然后断言改写结果。
    全程零网络请求。想换成你自己的真实抓包：设 `CUP_TEST_HAR=<path>`。

跑法
    mitmdump -s selftest_mitm.py --listen-host 127.0.0.1 --listen-port 18899
    跑完自动退出（exit code 0 = 全绿）
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

from mitmproxy import ctx, http

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cup_replace_core as core          # noqa: E402
import cup_replace_addon as addon        # noqa: E402

HAR_CANDIDATES = (
    # 仓库自带的合成素材，clone 下来就能跑
    HERE / "fixtures/upload_request.har",
)

FAILED: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> bool:
    print(f"  {'✔' if cond else '✘'} {label}" + (f"   {detail}" if detail else ""),
          flush=True)
    if not cond:
        FAILED.append(label)
    return cond


def find_har() -> Path:
    env = os.environ.get("CUP_TEST_HAR")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p
    for c in HAR_CANDIDATES:
        if c.exists():
            return c
    raise SystemExit(
        "找不到 fixtures/upload_request.har。\n"
        "  想跑自己的真实抓包：CUP_TEST_HAR=/path/to/traffic.har \\\n"
        "    mitmdump -s selftest_mitm.py --listen-host 127.0.0.1 --listen-port 18899")


def real_request() -> tuple[bytes, bytes, dict, str]:
    """从真实抓包里取出 (body, boundary, headers, url)。"""
    har = json.loads(find_har().read_text(encoding="utf-8"))
    for e in har["log"]["entries"]:
        req = e.get("request", {})
        if core.TARGET_PATH not in req.get("url", ""):
            continue
        pd = req.get("postData") or {}
        raw = (base64.b64decode(pd["text"]) if pd.get("encoding") == "base64"
               else pd["text"].encode("latin-1"))
        headers = {h["name"]: h["value"] for h in req.get("headers", [])}
        b = core.boundary_of(headers.get("content-type", ""))
        if b:
            return raw, b, headers, req["url"]
    raise SystemExit("HAR 里没有上传请求")


def build_flow(body: bytes, headers: dict, url: str) -> http.HTTPFlow:
    """构造一个 mitmproxy 真实对象，模拟小程序发出的那个上传请求。"""
    from urllib.parse import urlsplit
    u = urlsplit(url)
    f = http.HTTPFlow(None, None)
    # mitmproxy 12：Headers 只吃 bytes；Request.make 的 method 要 str
    hdrs = http.Headers([
        (str(k).encode("latin-1"), str(v).encode("latin-1"))
        for k, v in headers.items()
    ])
    f.request = http.Request.make("POST", url, content=body, headers=hdrs)
    return f


def find_har_image() -> Path | None:
    """自检用的替换素材，优先用仓库自带的合成图（省得要求先放 artwork.png）。"""
    for c in (HERE / "fixtures/sample_artwork.png",
              HERE / "last_upload.png"):
        if c.exists():
            return c
    return None


def load(loader) -> None:
    print()
    print("=" * 72)
    print("  mitmproxy 运行时 · addon 接线自检")
    print("=" * 72)
    print(f"  mitmproxy Python：{sys.version.split()[0]}")

    # 没有 artwork.png 时，退回到抓包里那张真图，保证自检随时能跑
    if addon.ART.data is None and not os.environ.get("CUP_REPLACE_IMAGE"):
        fb = find_har_image()
        if fb:
            os.environ["CUP_REPLACE_IMAGE"] = str(fb)
            addon.ART.load(force=True)
            print(f"  （未放 artwork.png，改用自检素材：{fb.name}）")

    print(f"  作品文件：{addon.ART.path or '（未载入）'}")
    ok(addon.ART.data is not None, "addon 载入到作品文件",
       f"{len(addon.ART.data or b'')} B" if addon.ART.data else
       "既没有 artwork.png，也找不到抓包还原图")
    print()

    body, boundary, headers, url = real_request()
    print(f"  真实请求：{len(body)} B，boundary={boundary.decode(errors='replace')}")
    print()

    # ── 跑真实钩子 ──
    flow = build_flow(body, headers, url)
    ok(flow.request.pretty_host == core.TARGET_HOST,
       "flow.request.pretty_host 命中目标主机", flow.request.pretty_host)

    addon.request(flow)
    print()

    # ── 断言 ──
    out = flow.request.content
    ok(out != body, "addon.request() 改写了请求体",
       f"{len(body)} B → {len(out)} B")

    idx = core.find_file_part(out, boundary)
    parsed = core._parse_head(core.split_parts(out, boundary)[idx]) if idx is not None else None
    ok(parsed is not None and parsed[1] == addon.ART.data,
       "改写后 upfile 段 = 作品字节",
       f"{len(parsed[1])} B" if parsed else "没找到文件段")

    cl = flow.request.headers.get("content-length")
    ok(cl == str(len(out)), "Content-Length 已同步更新", f"content-length={cl}")

    token_before = headers.get("qm-user-token")
    token_after = flow.request.headers.get("qm-user-token")
    ok(token_before == token_after and token_after,
       "鉴权头 qm-user-token 未被触碰",
       f"{len(token_after or '')} 字符")

    ua_before = headers.get("user-agent")
    ok(ua_before == flow.request.headers.get("user-agent"),
       "User-Agent 未被触碰（设备指纹保持）")

    # 拿标准库再解一次，确认服务端视角能正常解析
    from email.parser import BytesParser
    head = (f"Content-Type: multipart/form-data; boundary={boundary.decode()}"
            "\r\nMIME-Version: 1.0\r\n\r\n").encode()
    msg = BytesParser().parsebytes(head + out)
    parts = list(msg.iter_parts()) if hasattr(msg, "iter_parts") else msg.get_payload()
    up = [p for p in parts if p.get_param("name", header="content-disposition") == "upfile"]
    ok(bool(up) and up[0].get_payload(decode=True) == addon.ART.data,
       "服务端视角（email 解析）能拿到新图",
       f"filename={up[0].get_filename()}" if up else "解析失败")

    # ── 放行路径：不该动的东西必须原样放行 ──
    print()
    print("  放行路径（这些都不该被改）：")
    other = build_flow(b"{}", {"content-type": "application/json"}, url)
    addon.request(other)
    ok(other.request.content == b"{}", "非 multipart 请求原样放行")

    plain = build_flow(body, headers, "https://webapi.qmai.cn/web/other/api")
    addon.request(plain)
    ok(plain.request.content == body, "同主机但不同路径，原样放行")

    absent = build_flow(body, headers, "https://example.com/web/common-center/upload/image/index")
    addon.request(absent)
    ok(absent.request.content == body, "同路径但不同主机，原样放行")

    # ── 收尾 ──
    print()
    print("=" * 72)
    if FAILED:
        print(f"  ✘ {len(FAILED)} 项未通过：")
        for f in FAILED:
            print(f"      · {f}")
    else:
        print("  ✔ 全部通过 —— addon 在 mitmproxy 真实运行时里接线正确")
    print("=" * 72)
    print()

    ctx.master.shutdown()
    # 让 exit code 反映结果
    if FAILED:
        raise SystemExit(1)
