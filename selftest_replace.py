#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest_replace.py — 用真实抓包数据验证 cup_replace_core 的 multipart 改写

不连网、不起代理、不需要 mitmproxy，纯本地字节比对。

用法：
    python3 selftest_replace.py                 # 用仓库自带的合成素材（推荐）
    python3 selftest_replace.py --har <path>    # 用你自己的真实抓包
    python3 selftest_replace.py --image <path>  # 指定替换图

它做 8 项断言，全绿才说明改写是安全的：
    1. 分隔符数量不变（结构没被破坏）
    2. 图片部分的新字节与替换图逐字节一致
    3. 图片部分的 filename / Content-Type 已正确更新
    4. 头部没有出现裸 \n（CRLF 完整，不会把 header 写坏）
    5. 其余字段（sellerId / token / 时间戳…）字节级原样保留
    6. 重新解析改写后的 body，字段数量与顺序不变
    7. Content-Length 增量 = 图片体积增量
    8. 用标准库 email 模块能解析出 name="upfile" 且内容与替换图一致
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from email.parser import BytesParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cup_replace_core as core  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    RESULTS.append((ok, label))
    mark = f"{GREEN}✔{RESET}" if ok else f"{RED}✘{RESET}"
    line = f"  {mark} {label}"
    if detail:
        line += f"  {DIM}{detail}{RESET}"
    print(line, flush=True)
    return ok


# ──────────────────────────────────────────────────────────────────────────────
# 找素材
# ──────────────────────────────────────────────────────────────────────────────

def find_har(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            sys.exit(f"找不到 HAR：{p}")
        return p
    # 环境变量 > 仓库自带的合成素材。合成素材保证 clone 下来就能跑、结果可复现。
    env = os.environ.get("CUP_TEST_HAR")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p
    fixture = HERE / "fixtures/upload_request.har"
    if fixture.exists():
        return fixture
    sys.exit("没找到可用的抓包素材。\n"
             "  仓库应自带 fixtures/upload_request.har；\n"
             "  想跑自己的真实抓包：--har <path> 或 CUP_TEST_HAR=<path>")


def find_image(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            sys.exit(f"找不到图片：{p}")
        return p
    for cand in (HERE / "fixtures/sample_artwork.png",
                 HERE / "last_upload.png"):
        if cand.exists():
            return cand
    sys.exit("没找到可用的替换图，请用 --image 指定")


def extract_upload(har_path: Path) -> tuple[bytes, bytes]:
    """从 HAR 里取出上传请求的 body 与 boundary。"""
    har = json.loads(har_path.read_text(encoding="utf-8"))
    for e in har["log"]["entries"]:
        req = e.get("request", {})
        if core.TARGET_PATH not in req.get("url", ""):
            continue
        pd = req.get("postData") or {}
        text = pd.get("text")
        if not text:
            continue
        raw = base64.b64decode(text) if pd.get("encoding") == "base64" \
            else text.encode("latin-1")
        ct = next((h["value"] for h in req.get("headers", [])
                   if h["name"].lower() == "content-type"), "")
        b = core.boundary_of(ct)
        if not b:
            continue
        return raw, b
    sys.exit(f"{har_path.name} 里没有找到可用的上传请求")


def parse_multipart(body: bytes, boundary: bytes):
    """用标准库 email 解析，作为第三方视角的交叉验证。"""
    head = (f"Content-Type: multipart/form-data; boundary={boundary.decode()}"
            "\r\nMIME-Version: 1.0\r\n\r\n").encode()
    return BytesParser().parsebytes(head + body)


# ──────────────────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="验证杯贴 multipart 改写逻辑")
    ap.add_argument("--har")
    ap.add_argument("--image")
    ap.add_argument("--quiet", action="store_true", help="不打印 core 的日志")
    args = ap.parse_args(argv)

    if args.quiet:
        core.set_logger(lambda m: None, lambda m: None)

    har_path = find_har(args.har)
    img_path = find_image(args.image)

    print()
    print("=" * 72)
    print("  杯贴 multipart 改写 自检")
    print("=" * 72)
    print(f"  抓包：{har_path}")
    print(f"  替换图：{img_path}")
    if har_path.parent.name == "fixtures":
        print(f"  {DIM}（用的是仓库自带的合成素材；要跑你自己的真实抓包，"
              f"加 --har <path>）{RESET}")
    print()

    body, boundary = extract_upload(har_path)
    new_bytes = img_path.read_bytes()
    delim = b"--" + boundary

    # 先看清楚原请求长什么样
    print(f"  {YELLOW}原始请求结构{RESET}")
    for d in core.describe(body, boundary):
        if d["kind"] == "field":
            print(f"    [{d['index']}] name={d['name']!r} filename={d['filename']!r} "
                  f"type={d['content_type']!r} {d['data_len']} B")
        else:
            print(f"    [{d['index']}] {d['kind']} {d['bytes']} B")
    print()

    # ── 执行改写 ──
    new_name = "1234567890.png"
    new_mime = "image/png"
    out = core.rewrite_upload(body, boundary, new_bytes, new_name, new_mime)
    print()

    if out is None:
        check(False, "找到文件字段并完成改写", "rewrite_upload 返回 None")
        return summary()

    check(True, "找到文件字段并完成改写",
          f"{len(body)} B → {len(out)} B")

    # ── 1. 分隔符数量 ──
    n_old, n_new = body.count(delim), out.count(delim)
    check(n_old == n_new, "① 分隔符数量不变（结构未破坏）",
          f"{n_old} → {n_new}")

    # ── 2. 图片字节逐字节一致 ──
    idx = core.find_file_part(out, boundary)
    parsed = core._parse_head(core.split_parts(out, boundary)[idx])
    assert parsed
    _, out_data = parsed
    check(out_data == new_bytes, "② 图片字节与替换图逐字节一致",
          f"md5 {__import__('hashlib').md5(out_data).hexdigest()[:12]}… "
          f"({len(out_data)} B)")

    # ── 3. 头部更新 ──
    head = parsed[0]
    has_name = f'filename="{new_name}"'.encode() in head
    has_ct = b"Content-Type: image/png" in head
    check(has_name and has_ct, "③ filename 与 Content-Type 已更新",
          f'filename={new_name} / image/png')

    # ── 4. CRLF 完整 ──
    bare_lf = head.replace(b"\r\n", b"").count(b"\n")
    check(bare_lf == 0, "④ 头部无裸 \\n（CRLF 完整）",
          f"裸 \\n 数量 = {bare_lf}")

    # ── 5. 其余部分原样保留 ──
    old_parts = core.split_parts(body, boundary)
    new_parts = core.split_parts(out, boundary)
    others_ok = True
    checked = 0
    for i, (a, b) in enumerate(zip(old_parts, new_parts)):
        if i == idx:
            continue
        checked += 1
        if a != b:
            others_ok = False
    check(others_ok and len(old_parts) == len(new_parts),
          f"⑤ 其余 {checked} 段字节级原样保留",
          "multipart 的分隔符与收尾框架，逐字节未动")

    # ── 6. 字段数量与顺序 ──
    names_old = [d.get("name") for d in core.describe(body, boundary)]
    names_new = [d.get("name") for d in core.describe(out, boundary)]
    check(names_old == names_new, "⑥ 字段名与顺序不变",
          f"{len(names_old)} 段：{[n for n in names_old if n] or '仅文件段'}")

    # ── 7. 长度增量 ──
    delta_body = len(out) - len(body)
    old_idx = core.find_file_part(body, boundary)
    old_parsed = core._parse_head(core.split_parts(body, boundary)[old_idx])
    assert old_parsed
    old_len = len(old_parsed[1])
    old_name = core.describe(body, boundary)[old_idx]["filename"] or ""
    delta_img = len(new_bytes) - old_len
    name_delta = len(new_name) - len(old_name)
    check(delta_body == delta_img + name_delta, "⑦ 长度增量 = 图片增量 + 文件名增量",
          f"body {delta_body:+d} = 图片 {delta_img:+d} + 文件名 {name_delta:+d}")

    # ── 8. email 模块交叉验证 ──
    try:
        msg = parse_multipart(out, boundary)
        parts = list(msg.iter_parts()) if hasattr(msg, "iter_parts") else msg.get_payload()
        upfile = [p for p in parts if p.get_param("name", header="content-disposition") == "upfile"]
        if not upfile:
            upfile = [p for p in parts if p.get_filename()]
        payload = upfile[0].get_payload(decode=True)
        fn = upfile[0].get_filename()
        check(payload == new_bytes and fn == new_name,
              "⑧ 标准库 email 能解析出 name=\"upfile\" 且内容一致",
              f"filename={fn}，{len(payload)} B")
    except Exception as e:  # noqa: BLE001
        check(False, "⑧ 标准库 email 能解析出 name=\"upfile\" 且内容一致", f"{e}")

    # ── 9. 鉴权头一个都不动 ──
    # 真实请求的鉴权在 qm-user-token 头里（body 只有一个 upfile 字段）。
    # apply_body 只允许改 body 和 content-length，其余 header 必须逐字节不变。
    class _FakeReq:
        def __init__(self, headers):
            self.headers = dict(headers)
            self.content = b""

    real_headers = {}
    har = json.loads(har_path.read_text(encoding="utf-8"))
    for e in har["log"]["entries"]:
        if core.TARGET_PATH in e.get("request", {}).get("url", ""):
            real_headers = {h["name"]: h["value"] for h in e["request"]["headers"]}
            break

    fake = _FakeReq(real_headers)
    before = dict(fake.headers)
    core.apply_body(fake, out)
    changed = [k for k in before
               if k.lower() not in ("content-length", "transfer-encoding")
               and before[k] != fake.headers.get(k)]
    removed = [k for k in before
               if k.lower() not in ("content-length", "transfer-encoding")
               and k not in fake.headers]
    cl_ok = fake.headers.get("content-length") == str(len(out))
    auth_kept = "qm-user-token" in fake.headers
    check(not changed and not removed and cl_ok and auth_kept,
          "⑨ 鉴权头全部原样保留（只改 Content-Length）",
          f"qm-user-token 保持 {len(before.get('qm-user-token', ''))} 字符，"
          f"content-length → {fake.headers.get('content-length')}")

    # ── 10. 响应里能取到图片地址 ──
    resp_text = ""
    for e in har["log"]["entries"]:
        if core.TARGET_PATH in e.get("request", {}).get("url", ""):
            resp_text = (e.get("response", {}).get("content") or {}).get("text", "") or ""
            break
    got = core.find_image_url(json.loads(resp_text)) if resp_text else None
    check(bool(got) and got.endswith(".png"),
          "⑩ 能从上传响应里取出图片地址",
          got or "解析失败")

    # ── 附带：给用户看一眼将要发生什么 ──
    print()
    print(f"  {YELLOW}替换预览{RESET}")
    for line in core.plan_summary(body, boundary, new_bytes).splitlines():
        print(line)

    edge_cases(body, boundary, new_bytes)

    return summary()


# ──────────────────────────────────────────────────────────────────────────────
# 边界用例：确保异常输入是"安全地放弃"，而不是"崩掉代理"
# ──────────────────────────────────────────────────────────────────────────────

def edge_cases(body: bytes, boundary: bytes, new_bytes: bytes) -> None:
    print()
    print(f"  {YELLOW}边界用例（都必须安全放弃，不能崩）{RESET}")

    # a) 不是 multipart
    try:
        core.rewrite_upload(b"hello world", boundary, new_bytes, "1.png", "image/png")
        check(False, "a) 非 multipart 输入 → 抛 MultipartError")
    except core.MultipartError:
        check(True, "a) 非 multipart 输入 → 抛 MultipartError",
              "addon 会捕获并放行")
    except Exception as e:  # noqa: BLE001
        check(False, "a) 非 multipart 输入 → 抛 MultipartError", f"抛了 {type(e).__name__}")

    # b) 新图字节里含分隔符
    poison = b"\x89PNG\r\n\x1a\n" + b"--" + boundary + b"\r\n"
    try:
        core.rewrite_upload(body, boundary, poison, "1.png", "image/png")
        check(False, "b) 新图含分隔符 → 抛 MultipartError")
    except core.MultipartError:
        check(True, "b) 新图含分隔符 → 抛 MultipartError",
              "拦住会破坏结构的替换")

    # c) multipart 里没有文件字段
    empty = (b"--" + boundary + b"\r\n"
             b'Content-Disposition: form-data; name="sellerId"\r\n\r\n'
             b"49006\r\n--" + boundary + b"--\r\n")
    r = core.rewrite_upload(empty, boundary, new_bytes, "1.png", "image/png")
    check(r is None, "c) 没有文件字段 → 返回 None（放行）", "不误伤纯表单请求")

    # d) 非 multipart 的 content-type
    check(core.boundary_of("application/json") is None,
          "d) 非 multipart 的 Content-Type → boundary 为 None")

    # e) 带引号的 boundary
    check(core.boundary_of('multipart/form-data;boundary="abc"') == b"abc",
          "e) 带引号的 boundary 能正确解析")

    # f) 类型识别只信魔数
    check(core.sniff_ext(b"\xff\xd8\xff\xe0xx")[1] == "image/jpeg",
          "f) 魔数识别：JPEG 头 → image/jpeg",
          "不信任扩展名")

    # g) 各尺寸适配（没装 Pillow 时应原样返回而非抛错）
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "t.png"
        src.write_bytes(new_bytes)
        try:
            data = core.fit_to_canvas(src, core.CANVAS, "contain", "white")
            ok = len(data) > 0
            note = (f"{len(data)} B"
                    + ("" if core._has_pillow() else "（无 Pillow，原样返回）"))
        except Exception as e:  # noqa: BLE001
            ok, note = False, f"抛了 {type(e).__name__}: {e}"
        check(ok, "g) fit_to_canvas 不抛异常", note)


def summary() -> int:
    total = len(RESULTS)
    bad = [l for ok, l in RESULTS if not ok]
    print()
    print("=" * 72)
    if bad:
        print(f"  {RED}✘ {len(bad)}/{total} 项未通过{RESET}")
        for l in bad:
            print(f"      · {l}")
    else:
        print(f"  {GREEN}✔ 全部 {total} 项通过{RESET} —— 改写逻辑可以上代理了")
    print("=" * 72)
    print()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
