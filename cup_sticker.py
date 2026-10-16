#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cup_sticker.py — 霸王茶姬「定制杯贴」外部上传工具

用途
    小程序自带的画布只能手绘，太简陋。本脚本把你在 Photoshop / Procreate /
    Illustrator 里画好的图片，走同一套官方接口上传并保存为你的杯贴，
    保存后在小程序「定制杯贴 → 我的杯贴」里就能直接选用。

调用链（与小程序完全一致，共两步）
    1) POST https://webapi.qmai.cn/web/common-center/upload/image/index
       multipart/form-data，字段名 upfile，filename=<随机10位数字>.png
       → 返回图片 CDN 地址（形如 https://images.qmai.cn/common/s49006/.../xxx.png）

    2) POST https://cuswebapp.qmai.cn/web/custom-center/cup-label/save
       application/json  {"imgUrl":"<上一步地址>","createType":1,"language":"zh-CN"}

依赖
    仅标准库 + Pillow（Pillow 只在需要裁切/缩放时用；不装也能跑 --no-resize）

凭证从哪来
    脚本可以从你自己抓的 HAR 里自动提取 token / cookie，无需手填：
        python3 cup_sticker.py --har <你的 traffic.har> --list
    也可以复制 config.example.json 为 config.json 手工填写。

⚠️ 凭证安全
    user_token / cookie 等同于你的登录态，config.json 已按 600 权限写入，
    不要提交到 git、不要发群里。token 会过期（通常几小时～几天），
    过期后重新抓一次 HAR 即可。

用法示例
    python3 cup_sticker.py --har <har> --check                    # 体检：token 是否有效、WAF 是否放行
    python3 cup_sticker.py --har <har> --list                     # 列出我已有的杯贴
    python3 cup_sticker.py --har <har> --image art.png            # 上传并保存（自动适配画布）
    python3 cup_sticker.py --har <har> --image art.png --fit cover
    python3 cup_sticker.py --har <har> --image art.png --dry-run  # 只生成适配后的图，不发请求
    python3 cup_sticker.py --har <har> --img-url https://...      # 跳过上传，直接用已有地址保存
"""

from __future__ import annotations

import argparse
import gzip
import json
import mimetypes
import os
import random
import re
import socket
import ssl
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# 常量（全部来自 traffic.har 实测，勿随意改动）
# ──────────────────────────────────────────────────────────────────────────────

VERSION = "1.0.0"

HOST_UPLOAD = "webapi.qmai.cn"
HOST_SAVE = "cuswebapp.qmai.cn"
HOST_LIST = "cuswebapi.qmai.cn"

URL_UPLOAD = f"https://{HOST_UPLOAD}/web/common-center/upload/image/index"
URL_SAVE_PATH = "/web/custom-center/cup-label/save"
URL_LIST = f"https://{HOST_LIST}/web/custom-center/cup-label/list"

# 保存接口首选 cuswebapp（小程序走的就是它）；被 WAF 拦时退回 cuswebapi
# —— 抓包显示 cuswebapi 上的所有调用全程未被挑战。
SAVE_HOSTS = (HOST_SAVE, HOST_LIST)

# 小程序 webview 的真实 UA，服务端与 WAF 都会看
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Mac MacWechat/WMPF MacWechat/3.8.7(0x13080712) "
    "UnifiedPCMacWechat(0xf2641f16) XWEB/25755"
)

APPID = "wxafec6f8422cb357b"
REFERER = f"https://servicewechat.com/{APPID}/377/page-frame.html"

# 画布尺寸：实测小程序导出图恒为 618x927（RGBA，比例约 2:3）
CANVAS_W, CANVAS_H = 618, 927

DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 上传体积上限保护

# 阿里云 WAF 挑战页特征
WAF_MARKERS = (
    b"aliyun_waf_aa",
    b"_waf_",
    b"acw_sc__v2",
    b'id="renderData"',
    b"aliyun_waf_oo",
)

# 业务侧风控拦截的特征话术（网关/风控返回 JSON，不是 WAF 的 HTML 挑战页）
RISK_MARKERS = ("已被拦截", "异常请求", "请求异常", "拦截", "风险", "forbidden", "risk")

EXIT_OK = 0
EXIT_HTTP = 1
EXIT_WAF = 2
EXIT_CONFIG = 3
EXIT_IMAGE = 4
EXIT_API = 5


# ──────────────────────────────────────────────────────────────────────────────
# 小工具
# ──────────────────────────────────────────────────────────────────────────────

class Log:
    """极简日志，支持 --quiet / --debug。"""

    quiet = False
    debug_on = False

    @classmethod
    def info(cls, msg: str) -> None:
        if not cls.quiet:
            print(msg, flush=True)

    @classmethod
    def warn(cls, msg: str) -> None:
        print(f"⚠️  {msg}", file=sys.stderr, flush=True)

    @classmethod
    def err(cls, msg: str) -> None:
        print(f"✖  {msg}", file=sys.stderr, flush=True)

    @classmethod
    def ok(cls, msg: str) -> None:
        if not cls.quiet:
            print(f"✔  {msg}", flush=True)

    @classmethod
    def debug(cls, msg: str) -> None:
        if cls.debug_on:
            print(f"   [debug] {msg}", file=sys.stderr, flush=True)


def rand_filename() -> str:
    """复刻小程序的文件名风格：10 位数字 + .png"""
    return "".join(random.choice(string.digits) for _ in range(10)) + ".png"


def rand_boundary() -> str:
    """复刻 WebKitFormBoundary：16 位大小写字母数字"""
    pool = string.ascii_letters + string.digits
    return "----WebKitFormBoundary" + "".join(random.choice(pool) for _ in range(16))


def mask(s: str | None, keep: int = 6) -> str:
    if not s:
        return "(空)"
    if len(s) <= keep * 2:
        return s[:keep] + "…"
    return f"{s[:keep]}…{s[-4:]}(len {len(s)})"


# ──────────────────────────────────────────────────────────────────────────────
# HTTP 层（仅标准库；刻意不宣告 br，避免拿到无法解压的 Brotli 响应）
# ──────────────────────────────────────────────────────────────────────────────

class CookieStore:
    """按域名分别保存 cookie，模拟浏览器行为（跨域 cookie 不串门）。"""

    def __init__(self) -> None:
        self._jar: dict[str, dict[str, str]] = {}

    def set(self, host: str, name: str, value: str) -> None:
        self._jar.setdefault(host, {})[name] = value

    def set_from_headers(self, host: str, headers: list[str]) -> list[str]:
        """解析 Set-Cookie，返回本次新增/更新的 cookie 名。"""
        changed = []
        for raw in headers:
            first = raw.split(";")[0].strip()
            if "=" not in first:
                continue
            name, _, value = first.partition("=")
            name, value = name.strip(), value.strip()
            if not value:
                continue
            if self._jar.get(host, {}).get(name) != value:
                changed.append(name)
            self.set(host, name, value)
        return changed

    def header(self, host: str) -> str:
        jar = self._jar.get(host, {})
        return "; ".join(f"{k}={v}" for k, v in jar.items())

    def dump(self, host: str) -> dict[str, str]:
        return dict(self._jar.get(host, {}))

    def merge(self, other: "CookieStore") -> None:
        for host, jar in other._jar.items():
            for k, v in jar.items():
                self.set(host, k, v)


class Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes, url: str) -> None:
        self.status = status
        self.headers = headers
        self.body = body
        self.url = url

    @property
    def is_waf(self) -> bool:
        if "text/html" not in self.headers.get("content-type", ""):
            return False
        return any(m in self.body for m in WAF_MARKERS)

    def json(self):
        return json.loads(self.body.decode("utf-8", "replace"))

    def text(self, limit: int = 2000) -> str:
        return self.body.decode("utf-8", "replace")[:limit]


def _decompress(raw: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower()
    try:
        if "gzip" in enc:
            return gzip.decompress(raw)
        if "deflate" in enc:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -15)
    except Exception:
        pass
    return raw


def http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    cookies: CookieStore | None = None,
    timeout: float = 30.0,
) -> Response:
    host = urllib.parse.urlsplit(url).netloc.split(":")[0]

    hdrs = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "zh-CN",
        "Accept-Encoding": "gzip, deflate",  # 不宣告 br，服务端就会回明文/ gzip
        "Connection": "close",
    }
    if headers:
        hdrs.update(headers)
    if cookies:
        ck = cookies.header(host)
        if ck:
            hdrs["Cookie"] = ck

    req = urllib.request.Request(url, data=body, method=method)
    for k, v in hdrs.items():
        req.add_header(k, v)

    Log.debug(f"→ {method} {url}")
    Log.debug(f"  headers: {json.dumps(hdrs, ensure_ascii=False)}")
    if body is not None:
        Log.debug(f"  body: {len(body)} bytes")

    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read()
            rh = {k.lower(): v for k, v in resp.headers.items()}
            data = _decompress(raw, rh.get("content-encoding", ""))
            set_cookies = resp.headers.get_all("Set-Cookie") or []
            if cookies:
                for name in cookies.set_from_headers(host, set_cookies):
                    Log.debug(f"  set-cookie: {name}={mask(cookies.dump(host)[name])}")
            return Response(resp.status, rh, data, url)
    except urllib.error.HTTPError as e:
        raw = e.read()
        rh = {k.lower(): v for k, v in (e.headers or {}).items()}
        data = _decompress(raw, rh.get("content-encoding", ""))
        set_cookies = (e.headers.get_all("Set-Cookie") if e.headers else None) or []
        if cookies:
            cookies.set_from_headers(host, set_cookies)
        return Response(e.code, rh, data, url)
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as e:
        raise RuntimeError(f"网络请求失败 {url}：{e}") from e


def build_multipart(field_name: str, filename: str, content_type: str, data: bytes,
                    boundary: str) -> bytes:
    """手工构造 multipart body，字节结构与 WebKit 完全一致。"""
    b = boundary.encode()
    out = bytearray()
    out += b"--" + b + b"\r\n"
    out += f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
    out += f"Content-Type: {content_type}\r\n\r\n".encode()
    out += data
    out += b"\r\n" + b"--" + b + b"--\r\n"
    return bytes(out)


# ──────────────────────────────────────────────────────────────────────────────
# 配置：从 HAR 自动提取 或 从 config.json 读取
# ──────────────────────────────────────────────────────────────────────────────

def _headers_of(entry: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in entry.get("request", {}).get("headers", [])}


def _all_header_values(entry: dict, name: str) -> list[str]:
    """取某个头的**全部**值。

    HAR 里同名的 Cookie 头会出现多次（每个 cookie 一条），
    用 dict 会把前面的覆盖掉，必须按列表收集。
    """
    name = name.lower()
    return [h["value"] for h in entry.get("request", {}).get("headers", [])
            if h["name"].lower() == name and h["value"]]


def extract_from_har(har_path: Path) -> dict:
    """从 HAR 中取出最近一次可用的 token / cookie / 门店信息。"""
    with har_path.open("r", encoding="utf-8") as f:
        har = json.load(f)

    entries = har["log"]["entries"]
    entries.sort(key=lambda x: x.get("startedDateTime", ""))

    cfg: dict = {
        "app_id": None, "seller_id": None, "shop_id": None,
        "user_token_qm": None, "user_token_cus": None, "cookies": {},
        "canvas": {"width": CANVAS_W, "height": CANVAS_H},
    }
    cookies = CookieStore()
    newest_ts = ""

    for e in entries:
        url = e["request"]["url"]
        h = _headers_of(e)

        # qm-user-token：上传接口用的
        if h.get("qm-user-token"):
            cfg["user_token_qm"] = h["qm-user-token"]
        # cus-* 头：保存/列表接口用的
        if h.get("cus-user-token"):
            cfg["user_token_cus"] = h["cus-user-token"]
        for key, field in (("cus-app-id", "app_id"), ("cus-seller-id", "seller_id")):
            if h.get(key):
                cfg[field] = h[key]

        host = urllib.parse.urlsplit(url).netloc.split(":")[0]
        for ck in _all_header_values(e, "cookie"):
            cookies.set_from_headers(host, [ck])
        # 响应侧 Set-Cookie 也要收（acw_tc 在这里）
        for sc in e.get("response", {}).get("headers", []):
            if sc["name"].lower() == "set-cookie":
                cookies.set_from_headers(host, [sc["value"]])

        # shopId：从 cup-label/list 的请求体里取
        pd = (e["request"].get("postData") or {}).get("text") or ""
        m = re.search(r'"shopId"\s*:\s*"?(\d+)"?', pd)
        if m:
            cfg["shop_id"] = m.group(1)

        if e.get("startedDateTime", "") > newest_ts:
            newest_ts = e["startedDateTime"]

    for host in (HOST_UPLOAD, HOST_SAVE, HOST_LIST):
        for k, v in cookies.dump(host).items():
            cfg["cookies"].setdefault(host, {})[k] = v

    cfg["_source"] = str(har_path)
    cfg["_newest"] = newest_ts
    return cfg


def load_config(args) -> dict:
    cfg: dict = {}

    # 1) --config 指定的文件
    cfg_path = Path(args.config) if args.config else Path(__file__).with_name("config.json")
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        Log.debug(f"读取配置 {cfg_path}")

    # 2) --har 覆盖（更权威，因为 token 更新）
    har = Path(args.har).expanduser() if args.har else None
    if har is None:
        har = find_latest_har()
    if har and har.exists():
        try:
            hcfg = extract_from_har(har)
            for k, v in hcfg.items():
                if v:
                    cfg[k] = v
            Log.debug(f"从 HAR 提取凭证 {har}（最新事务 {hcfg.get('_newest')}）")
        except Exception as e:
            Log.warn(f"解析 HAR 失败，忽略：{e}")
    elif args.har:
        Log.err(f"找不到 HAR 文件：{args.har}")

    cfg.setdefault("cookies", {})
    cfg.setdefault("canvas", {"width": CANVAS_W, "height": CANVAS_H})

    # 3) 命令行覆盖
    if args.shop_id:
        cfg["shop_id"] = args.shop_id
    if args.token:
        cfg["user_token_cus"] = args.token
        cfg["user_token_qm"] = cfg.get("user_token_qm") or args.token

    return cfg


def find_latest_har() -> Path | None:
    """找最新的 traffic.har。

    顺序：$CUP_HAR → 项目同目录/上级目录 → 下载、文稿目录（一层）。
    想指定就直接用 $CUP_HAR，或命令行 --har <path>。
    """
    env = os.environ.get("CUP_HAR")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p

    base = Path(__file__).resolve().parent
    cands: list[Path] = []
    for root in (base, base.parent, Path.home() / "Downloads",
                 Path.home() / "Documents"):
        if not root.exists():
            continue
        for pat in ("traffic.har", "*/traffic.har",
                    "**/har_data/sessions/*/traffic.har"):
            try:
                cands += [c for c in root.glob(pat) if c.is_file()]
            except OSError:
                pass
    if not cands:
        return None
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    Log.debug(f"自动发现 HAR：{cands[0]}")
    return cands[0]


def check_config(cfg: dict) -> list[str]:
    missing = []
    if not cfg.get("user_token_qm"):
        missing.append("user_token_qm（qm-user-token）")
    if not cfg.get("user_token_cus"):
        missing.append("user_token_cus（cus-user-token）")
    if not cfg.get("shop_id"):
        missing.append("shop_id")
    return missing


def save_config(cfg: dict, path: Path) -> None:
    clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# 图片处理
# ──────────────────────────────────────────────────────────────────────────────

def png_size(path: Path) -> tuple[int, int] | None:
    """不依赖 Pillow 读 PNG 尺寸（IHDR 在固定偏移）。"""
    try:
        head = path.read_bytes()[:26]
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n" or len(head) < 26:
        return None
    import struct
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def prepare_image(src: Path, dst: Path, fit: str, canvas: tuple[int, int],
                  bg: str, max_bytes: int) -> Path:
    """把任意图片适配到小程序画布尺寸。

    fit:
        contain  等比缩放后居中补边（默认，不裁切任何内容）
        cover    等比缩放后居中裁切（铺满画布，边缘会被切）
        stretch  拉伸到画布尺寸（会变形）
        none     原样不动
    """
    if fit == "none":
        return src

    try:
        from PIL import Image
    except ImportError:
        # 没有 Pillow 时的降级：如果原图恰好就是画布尺寸，直接放行
        size = png_size(src)
        if size == canvas:
            Log.warn(f"未安装 Pillow，但原图已是 {canvas[0]}x{canvas[1]}，直接原样上传。")
            return src
        Log.err("需要 Pillow 才能做尺寸适配（等比缩放/补边）。")
        print()
        print("  三种解决办法：")
        print("    1) pip install Pillow")
        print("    2) 加 --fit none，前提是原图已经是 "
              f"{canvas[0]}x{canvas[1]}（当前 {size[0] if size else '?'}x{size[1] if size else '?'}）")
        print("    3) 用任何工具先把图导成 "
              f"{canvas[0]}x{canvas[1]} PNG，再加 --fit none")
        raise SystemExit(EXIT_IMAGE)

    cw, ch = canvas
    with Image.open(src) as im:
        im = im.convert("RGBA")

        if fit == "stretch":
            out = im.resize((cw, ch), Image.LANCZOS)
        else:
            scale = max(cw / im.width, ch / im.height) if fit == "cover" \
                else min(cw / im.width, ch / im.height)
            nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
            resized = im.resize((nw, nh), Image.LANCZOS)

            if fit == "cover":
                left = (nw - cw) // 2
                top = (nh - ch) // 2
                out = resized.crop((left, top, left + cw, top + ch))
            else:  # contain
                if bg == "transparent":
                    canvas_img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                else:
                    canvas_img = Image.new("RGBA", (cw, ch), (255, 255, 255, 255))
                canvas_img.paste(resized, ((cw - nw) // 2, (ch - nh) // 2), resized)
                out = canvas_img

        out.save(dst, "PNG", optimize=True)
        Log.debug(f"适配 {im.width}x{im.height} → {cw}x{ch} ({fit})，输出 {dst}")

    size = dst.stat().st_size
    if size > max_bytes:
        Log.warn(f"图片 {size / 1024:.0f} KB 超过上限 {max_bytes / 1024:.0f} KB，尝试压缩…")
        try:
            from PIL import Image
            with Image.open(dst) as im:
                im = im.convert("RGB")
                for _ in range(6):
                    im.save(dst, "PNG", optimize=True)
                    if dst.stat().st_size <= max_bytes:
                        break
                    im = im.resize((int(im.width * 0.85), int(im.height * 0.85)), Image.LANCZOS)
        except Exception as e:
            Log.warn(f"压缩失败：{e}")
        if dst.stat().st_size > max_bytes:
            Log.err(f"仍然超过上限（{dst.stat().st_size / 1024:.0f} KB），服务端可能拒绝。")
    return dst


def sniff_mime(path: Path) -> str:
    head = path.read_bytes()[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


# ──────────────────────────────────────────────────────────────────────────────
# 业务接口
# ──────────────────────────────────────────────────────────────────────────────

def _find_url(obj) -> str | None:
    """在任意嵌套的 JSON 里找出第一个 images.qmai.cn 图片地址。

    上传接口的响应体是 Brotli 压缩的、抓包里读不出来，字段名未知，
    所以这里用递归搜索，比猜字段名稳。
    """
    if isinstance(obj, str):
        if obj.startswith("http") and "images.qmai.cn" in obj:
            return obj
        return None
    if isinstance(obj, dict):
        for k in ("imgUrl", "url", "img_url", "imageUrl", "fileUrl", "path"):
            if k in obj:
                got = _find_url(obj[k])
                if got:
                    return got
        for v in obj.values():
            got = _find_url(v)
            if got:
                return got
    if isinstance(obj, list):
        for v in obj:
            got = _find_url(v)
            if got:
                return got
    return None


def api_upload(image: Path, cfg: dict, cookies: CookieStore, *, dry_run: bool = False) -> str:
    data = image.read_bytes()
    mime = sniff_mime(image)
    fname = rand_filename()
    boundary = rand_boundary()
    body = build_multipart("upfile", fname, mime, data, boundary)

    Log.info(f"① 上传图片  {image.name}  ({len(data) / 1024:.1f} KB, {mime}) → {HOST_UPLOAD}")

    if dry_run:
        Log.info("   [dry-run] 跳过实际上传")
        return "https://images.qmai.cn/DRY_RUN/placeholder.png"

    resp = http_request(
        "POST", URL_UPLOAD,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "qm-user-token": cfg["user_token_qm"],
            "qm-from-type": cfg.get("from_biz", "catering"),
            "qm-from": "wechat",
            "xweb_xhr": "1",
            "Accept": "*/*",
            "Referer": REFERER,
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        },
        body=body,
        cookies=cookies,
    )

    Log.debug(f"← {resp.status} {resp.headers.get('content-type')} {len(resp.body)}B")
    if resp.is_waf:
        raise WafBlocked("上传接口被阿里云 WAF 挑战", resp)

    if resp.status != 200:
        raise ApiError(f"上传失败 HTTP {resp.status}：{resp.text(500)}")

    try:
        payload = resp.json()
    except json.JSONDecodeError:
        raise ApiError(f"上传响应不是 JSON：{resp.text(500)}")

    if payload.get("code") not in (0, None) or payload.get("status") is False:
        raise ApiError(f"上传被拒：{json.dumps(payload, ensure_ascii=False)[:400]}")

    url = _find_url(payload)
    if not url:
        raise ApiError("上传响应里找不到图片地址：" +
                       json.dumps(payload, ensure_ascii=False)[:400])

    Log.ok(f"   已上传 → {url}")
    return url


def _save_headers(cfg: dict) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "cus-app-id": cfg["app_id"],
        "cus-from-biz": cfg.get("from_biz", "catering"),
        "cus-seller-id": str(cfg["seller_id"]),
        "cus-user-token": cfg["user_token_cus"],
        "cus-from": "wechat",
        "xweb_xhr": "1",
        "Accept": "*/*",
        "Referer": REFERER,
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }


def api_save(img_url: str, cfg: dict, cookies: CookieStore, create_type: int = 1,
             hosts: tuple[str, ...] = SAVE_HOSTS) -> tuple[dict, str]:
    """保存杯贴。返回 (响应 JSON, 实际生效的主机)。

    依次尝试 hosts 里的主机；每个主机上先直接发，被 WAF 挑战则带新 cookie 重放一次。
    全部失败才抛 WafBlocked。
    """
    payload = {"imgUrl": img_url, "createType": create_type, "language": "zh-CN"}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    last_waf: Response | None = None

    for idx, host in enumerate(hosts):
        url = f"https://{host}{URL_SAVE_PATH}"
        label = f"② 保存杯贴  → {host}" + ("（备用入口）" if idx else "")
        Log.info(label)

        for attempt in (1, 2):
            resp = http_request("POST", url, headers=_save_headers(cfg),
                                body=body, cookies=cookies)
            Log.debug(f"← {resp.status} {resp.headers.get('content-type')} "
                      f"{len(resp.body)}B (第 {attempt} 次)")

            if resp.is_waf:
                last_waf = resp
                if attempt == 1:
                    Log.warn(f"{host} 返回 WAF 挑战页，带新拿到的 acw_tc 重放…")
                    continue
                Log.warn(f"{host} 二次仍被 WAF 拦截，换下一个入口")
                break

            if resp.status == 404:
                Log.warn(f"{host} 上没有这个接口（404），换下一个入口")
                break
            if resp.status != 200:
                raise ApiError(f"保存失败 HTTP {resp.status}：{resp.text(500)}")

            try:
                data = resp.json()
            except json.JSONDecodeError:
                raise ApiError(f"保存响应不是 JSON：{resp.text(500)}")

            if data.get("code") not in (0, None) or data.get("status") is False:
                msg = str(data.get("message") or "")
                if any(k in msg for k in RISK_MARKERS):
                    raise RiskBlocked(host, data)
                raise ApiError(f"保存被拒：{json.dumps(data, ensure_ascii=False)[:400]}")

            Log.ok(f"   杯贴已保存（via {host}）")
            return data, host

    raise WafBlocked("保存接口在所有候选入口都被 WAF 拦截", last_waf)


def api_list(cfg: dict, cookies: CookieStore) -> dict:
    payload = {
        "shopId": str(cfg["shop_id"]),
        "deliveryType": 1,
        "relatedChannel": 1,
        "language": "zh-CN",
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    Log.info(f"查询已有杯贴  shopId={cfg['shop_id']} → {HOST_LIST}")

    resp = http_request(
        "POST", URL_LIST,
        headers={
            "Content-Type": "application/json",
            "Cus-App-Id": cfg["app_id"],
            "Cus-From-Biz": cfg.get("from_biz", "catering"),
            "Cus-Seller-Id": str(cfg["seller_id"]),
            "Cus-User-Token": cfg["user_token_cus"],
            "Cus-From": "wechat",
            "xweb_xhr": "1",
            "Accept": "*/*",
            "Referer": REFERER,
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        },
        body=body,
        cookies=cookies,
    )

    Log.debug(f"← {resp.status} {resp.headers.get('content-type')} {len(resp.body)}B")
    if resp.is_waf:
        raise WafBlocked("列表接口被 WAF 挑战", resp)
    if resp.status != 200:
        raise ApiError(f"查询失败 HTTP {resp.status}：{resp.text(500)}")
    return resp.json()


class ApiError(Exception):
    pass


class WafBlocked(Exception):
    def __init__(self, msg: str, resp: Response | None) -> None:
        super().__init__(msg)
        self.resp = resp


class RiskBlocked(Exception):
    """业务侧风控拦截：HTTP 200 + 正常 JSON，但 code 非 0 且话术是"拦截"。

    和 WafBlocked 要分开 —— 前者说明请求根本没过网关，
    后者说明请求过了网关、被风控判为异常。两者都不是凭证问题。
    """

    def __init__(self, host: str, payload: dict) -> None:
        self.host = host
        self.payload = payload
        super().__init__(str(payload.get("message") or "风控拦截"))


# ──────────────────────────────────────────────────────────────────────────────
# 输出
# ──────────────────────────────────────────────────────────────────────────────

def print_stickers(payload: dict) -> None:
    """打印 cup-label/list 的结果。

    这个接口返回三段（实测）：
      customList      —— 我保存的自定义杯贴（含审核状态）
      recommends      —— 官方推荐，按 categoryName 分类
      halfRecommends  —— 更多推荐
    """
    data = payload.get("data")

    if isinstance(data, list):          # 兼容别的返回形态
        data = {"customList": data}
    if not isinstance(data, dict):
        Log.info("（响应结构不认识）")
        Log.debug("原始响应：" + json.dumps(payload, ensure_ascii=False)[:800])
        return

    printed = False

    # ── 我的杯贴 ────────────────────────────────────────────────────────────
    custom = data.get("customList") or []
    if custom:
        printed = True
        Log.info(f"【我的杯贴】共 {len(custom)} 张")
        for i, it in enumerate(custom, 1):
            if not isinstance(it, dict):
                Log.info(f"  {i}. {it}")
                continue
            url = it.get("imgUrl") or _find_url(it) or "(无地址)"
            Log.info(f"  {i:>2}. {url}")
            bits = []
            if it.get("auditStatus") is not None:
                bits.append(f"审核状态={it['auditStatus']}")
            if it.get("createdAt"):
                bits.append(f"提交于 {it['createdAt']}")
            if it.get("auditName"):
                bits.append(str(it["auditName"]))
            if it.get("auditAt"):
                bits.append(f"审核于 {it['auditAt']}")
            if bits:
                Log.info(f"      {' · '.join(bits)}")
            if it.get("auditReason"):
                Log.info(f"      审核意见：{it['auditReason']}")
            if it.get("id"):
                Log.info(f"      id={it['id']}")
    else:
        printed = True
        Log.info("【我的杯贴】还没有自定义杯贴")

    # ── 官方推荐 ────────────────────────────────────────────────────────────
    for key, label in (("recommends", "官方推荐"), ("halfRecommends", "更多推荐")):
        cats = data.get(key) or []
        if not isinstance(cats, list) or not cats:
            continue
        printed = True
        total = sum(len(c.get("sticker") or []) for c in cats if isinstance(c, dict))
        Log.info("")
        Log.info(f"【{label}】{len(cats)} 个分类 / {total} 张")
        for cat in cats:
            if not isinstance(cat, dict):
                continue
            items = cat.get("sticker") or []
            Log.info(f"  · {cat.get('categoryName', '(未命名)')}  ({len(items)} 张)")
            for it in items:
                if isinstance(it, dict):
                    Log.info(f"      {it.get('name', '')}  →  {it.get('linkUrl', '')}")

    if not printed:
        Log.info("（列表为空）")
        Log.debug("原始响应：" + json.dumps(payload, ensure_ascii=False)[:800])


def print_waf_help(resp: Response | None, cfg: dict) -> None:
    Log.err("请求没通过网关（返回的是 JS 挑战页，不是接口响应）。")
    print()
    print("  这不是登录态问题 —— 上传接口能通就证明 token 是好的。")
    print("  这是阿里云盾的反爬：小程序 webview 里跑着一段混淆 JS，")
    print("  每次请求现算一个签名参数（md5__1695=38bccccd23-…）并维护 acw_tc cookie。")
    print("  纯 HTTP 客户端算不出这个签名，所以永远停在挑战页。")
    print()
    print("  可以试的：")
    print()
    print("  A. 换个网络再跑（WAF 风险分会看来源 IP）")
    print("     手机热点 / 换 Wi-Fi，成本最低，有时直接就过了。")
    print()
    print("  B. 重新抓 HAR 补上新 cookie")
    print("     在微信里点开一次「定制杯贴」让抓包录到一次成功的 save，然后：")
    print("         python3 cup_sticker.py --har <新HAR> --save-config")
    print()
    print("  C. 先跑 --diagnose 看清是哪一层在拦你")
    print("     会分别探测三台主机的可达性与 WAF 状态。")
    print()
    if cfg.get("cookies"):
        safe = {h: list(j) for h, j in cfg["cookies"].items()}
        print(f"  当前已知 cookie：{json.dumps(safe, ensure_ascii=False)}")
    if resp is not None:
        print(f"  挑战页前 120 字节：{resp.text(120)!r}")


def print_risk_help(exc: "RiskBlocked", cfg: dict) -> None:
    Log.err(f"{exc.host} 返回风控拦截：{exc.payload.get('message')}")
    print()
    print("  这是**业务侧风控**，不是 WAF 挑战页，也不是凭证过期。")
    print("  区分很重要：")
    print("    · WAF 挑战页 → 请求根本没到应用层（HTML 混淆页 + acw_tc）")
    print("    · 风控拦截   → 请求到了应用层，被判定为异常来源后拒绝（JSON + trace_id）")
    print()
    print("  也就是说：登录态没问题，是这套请求特征被服务端认成机器人了。")
    print("  换 token 解决不了这个问题 —— 风控看的是请求本身，不是你是谁。")
    print()
    print(f"  原始返回：{json.dumps(exc.payload, ensure_ascii=False)}")
    print()
    print("  建议：跑 --diagnose 看清三层（网络 / WAF / 风控）各是什么状态。")
    print()
    if cfg.get("cookies"):
        safe = {h: list(j) for h, j in cfg["cookies"].items()}
        print(f"  当前已知 cookie：{json.dumps(safe, ensure_ascii=False)}")


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────

def diagnose(cfg: dict, cookies: CookieStore) -> int:
    """只读诊断：分别探测「网络 / WAF / 业务鉴权」三层，判断到底卡在哪。

    不写任何数据 —— 只有 GET / 和 cup-label/list（查询）。
    """
    Log.info("诊断模式（全程只读，不会创建/修改任何杯贴）")
    Log.info("")
    Log.info(f"{'主机':<22} {'探测':<26} {'结果'}")
    Log.info("─" * 78)

    verdicts: list[str] = []

    # ── 第一层：网络 + WAF（GET 首页）────────────────────────────────────────
    for host in (HOST_UPLOAD, HOST_SAVE, HOST_LIST):
        probe = "GET /"
        try:
            r = http_request("GET", f"https://{host}/",
                             headers={"Accept": "*/*", "Referer": REFERER},
                             cookies=cookies, timeout=15)
            if r.is_waf:
                res = f"HTTP {r.status}  ⚠️ WAF 挑战页"
                verdicts.append(f"{host} 前面有 WAF（GET 就被挑战）")
            else:
                res = f"HTTP {r.status}  ✔ 放行"
        except RuntimeError as e:
            res = f"✖ 连不上（{str(e)[:34]}）"
            verdicts.append(f"{host} 网络不可达")
        Log.info(f"{host:<22} {probe:<26} {res}")

    Log.info("")

    # ── 第二层：业务鉴权（查询杯贴列表，只读）────────────────────────────────
    list_body = json.dumps(
        {"shopId": str(cfg.get("shop_id", "")), "deliveryType": 1,
         "relatedChannel": 1, "language": "zh-CN"},
        ensure_ascii=False, separators=(",", ":")).encode()

    for host in (HOST_LIST, HOST_SAVE):
        probe = "POST cup-label/list"
        try:
            r = http_request("POST", f"https://{host}/web/custom-center/cup-label/list",
                             headers=_save_headers(cfg), body=list_body,
                             cookies=cookies, timeout=15)
            if r.is_waf:
                res = f"HTTP {r.status}  ⚠️ WAF 挑战页"
            else:
                try:
                    j = r.json()
                    code = j.get("code")
                    msg = str(j.get("message") or "")[:20]
                    if any(k in msg for k in RISK_MARKERS):
                        res = f"HTTP {r.status}  ⚠️ 风控拦截 code={code} {msg}"
                        verdicts.append(f"{host} 有业务风控")
                    elif code in (0, None) and j.get("status") is not False:
                        res = f"HTTP {r.status}  ✔ 鉴权有效 code={code}"
                    else:
                        res = f"HTTP {r.status}  ⚠️ code={code} {msg}"
                        verdicts.append(f"{host} 业务返回失败 code={code}")
                except json.JSONDecodeError:
                    res = f"HTTP {r.status}  ? 非 JSON（{r.headers.get('content-type', '')[:24]}）"
        except RuntimeError as e:
            res = f"✖ 连不上（{str(e)[:34]}）"
        Log.info(f"{host:<22} {probe:<26} {res}")

    Log.info("")
    Log.info("─" * 78)

    if not verdicts:
        Log.ok("三层全通 —— 可以直接上传了。")
        return EXIT_OK

    Log.warn("发现问题：")
    for v in verdicts:
        Log.info(f"    · {v}")
    Log.info("")
    Log.info("  判读方法：")
    Log.info("    · 如果「POST cup-label/list」是 ✔ 而 save 失败")
    Log.info("      → 登录态是好的，问题出在 save 这一个接口的风控上，换 token 没用。")
    Log.info("    · 如果 list 也是 WAF/风控 → 整台主机都在拦，先换网络再试。")
    return EXIT_WAF


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cup_sticker.py",
        description="霸王茶姬定制杯贴：上传你自己画好的图片（绕过小程序简陋画布）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  %(prog)s --har traffic.har --check
  %(prog)s --har traffic.har --list
  %(prog)s --image my-art.png
  %(prog)s --image my-art.png --fit cover --bg transparent
  %(prog)s --img-url https://images.qmai.cn/.../xxx.png
""",
    )
    p.add_argument("--image", "-i", help="要上传的图片路径（png/jpg/webp）")
    p.add_argument("--img-url", help="跳过上传，直接保存这个已存在的图片地址")
    p.add_argument("--har", help="从该 HAR 文件提取 token/cookie（不填则自动找最新的）")
    p.add_argument("--config", help="配置文件路径（默认脚本同目录 config.json）")
    p.add_argument("--token", help="直接指定 cus-user-token（覆盖配置文件）")
    p.add_argument("--shop-id", help="门店 ID（默认取 HAR 里的）")
    p.add_argument("--save-host", choices=["auto", "cuswebapp", "cuswebapi"], default="auto",
                   help="保存接口走哪台主机，默认 auto（先 cuswebapp，被拦则退回 cuswebapi）")

    p.add_argument("--fit", choices=["contain", "cover", "stretch", "none"], default="contain",
                   help="尺寸适配方式，默认 contain（等比缩放+补边，不裁切内容）")
    p.add_argument("--bg", choices=["white", "transparent"], default="white",
                   help="contain 补边颜色，默认 white")
    p.add_argument("--canvas", help=f"画布尺寸 WxH，默认 {CANVAS_W}x{CANVAS_H}")
    p.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                   help="上传体积上限（字节），默认 5MB")
    p.add_argument("--create-type", type=int, default=1,
                   help="createType 字段，实测手绘/上传为 1，默认 1")

    p.add_argument("--list", action="store_true", help="列出已有杯贴后退出")
    p.add_argument("--json", action="store_true", help="配合 --list：直接打印接口原始 JSON")
    p.add_argument("--check", action="store_true", help="只做体检（凭证+连通性）后退出")
    p.add_argument("--diagnose", action="store_true",
                   help="只读诊断：分别探测 网络 / WAF / 业务风控 三层，看清卡在哪")
    p.add_argument("--dry-run", action="store_true", help="只准备图片，不发任何请求")
    p.add_argument("--keep", help="保存适配后图片的路径（默认写到临时目录）")
    p.add_argument("--save-config", action="store_true",
                   help="把本次从 HAR 提取到的凭证写入 config.json")
    p.add_argument("--debug", action="store_true", help="打印完整请求/响应")
    p.add_argument("--quiet", "-q", action="store_true", help="安静模式")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    Log.quiet = args.quiet
    Log.debug_on = args.debug

    # ── 凭证 ────────────────────────────────────────────────────────────────
    cfg = load_config(args)
    missing = check_config(cfg)

    if args.save_config:
        save_config(cfg, Path(__file__).with_name("config.json"))
        Log.ok("凭证已写入 config.json（权限 600，请勿外传）")

    if missing and not args.dry_run:
        Log.err("缺少必要凭证：" + "、".join(missing))
        print()
        print("  怎么拿到：")
        print("    1) 抓一次微信里霸王茶姬小程序的上传流量，导出成 HAR")
        print("    2) 运行：python3 cup_sticker.py --har <traffic.har> --save-config")
        print("    3) 或手工复制 config.example.json 为 config.json 填写")
        return EXIT_CONFIG

    canvas = (CANVAS_W, CANVAS_H)
    if args.canvas:
        try:
            w, h = args.canvas.lower().split("x")
            canvas = (int(w), int(h))
        except ValueError:
            Log.err(f"--canvas 格式应为 WxH，收到 {args.canvas}")
            return EXIT_CONFIG
    elif cfg.get("canvas"):
        canvas = (int(cfg["canvas"].get("width", CANVAS_W)),
                  int(cfg["canvas"].get("height", CANVAS_H)))

    cookies = CookieStore()
    for host, jar in (cfg.get("cookies") or {}).items():
        for k, v in jar.items():
            cookies.set(host, k, v)
    Log.debug(f"已载入 cookie：{ {h: list(j) for h, j in (cfg.get('cookies') or {}).items()} }")

    Log.info(f"凭证：qm-user-token={mask(cfg.get('user_token_qm'))}  "
             f"cus-user-token={mask(cfg.get('user_token_cus'))}  "
             f"shopId={cfg.get('shop_id')}  sellerId={cfg.get('seller_id')}")
    Log.info("")

    try:
        # ── --diagnose ──────────────────────────────────────────────────────
        if args.diagnose:
            return diagnose(cfg, cookies)

        # ── --check ─────────────────────────────────────────────────────────
        if args.check:
            Log.info("体检中…")
            try:
                payload = api_list(cfg, cookies)
            except WafBlocked as w:
                print_waf_help(w.resp, cfg)
                return EXIT_WAF
            except RiskBlocked as r:
                print_risk_help(r, cfg)
                return EXIT_WAF
            code = payload.get("code")
            ok = code in (0, None) and payload.get("status") is not False
            Log.info(f"   业务返回：code={code} message={payload.get('message')!r}")
            if ok:
                Log.ok("凭证有效，接口可达 —— 可以正式上传了")
                Log.info("   注意：list 通不代表 save 通，save 上可能还有一层风控。")
                Log.info("   想看 save 那一层，跑 --diagnose。")
            else:
                Log.err("凭证可能已过期，请重新抓 HAR")
                return EXIT_CONFIG
            return EXIT_OK

        # ── --list ──────────────────────────────────────────────────────────
        if args.list:
            try:
                payload = api_list(cfg, cookies)
            except WafBlocked as w:
                print_waf_help(w.resp, cfg)
                return EXIT_WAF
            except RiskBlocked as r:
                print_risk_help(r, cfg)
                return EXIT_WAF
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print_stickers(payload)
            return EXIT_OK

        # ── 上传 + 保存 ─────────────────────────────────────────────────────
        if not args.image and not args.img_url:
            Log.err("需要 --image（要上传的图片）或 --img-url（已存在的图片地址）")
            return EXIT_CONFIG

        if args.image:
            src = Path(args.image).expanduser()
            if not src.exists():
                Log.err(f"图片不存在：{src}")
                return EXIT_IMAGE
            if args.keep:
                dst = Path(args.keep).expanduser()
            else:
                dst = Path(__file__).with_name("last_upload.png")
            try:
                prepare_image(src, dst, args.fit, canvas, args.bg,
                              max_bytes=args.max_bytes)
            except SystemExit as e:
                return int(e.code or EXIT_IMAGE)
            Log.info(f"图片就绪：{dst}  ({dst.stat().st_size / 1024:.1f} KB, "
                     f"{canvas[0]}x{canvas[1]})")
            img_url = api_upload(dst, cfg, cookies, dry_run=args.dry_run)
        else:
            img_url = args.img_url
            Log.info(f"跳过上传，直接使用已有地址：{img_url}")

        if args.dry_run:
            Log.info("\n[dry-run] 到此为止，未发送任何保存请求。")
            return EXIT_OK

        hosts = SAVE_HOSTS
        if args.save_host == "cuswebapi":
            hosts = (HOST_LIST,)
        elif args.save_host == "cuswebapp":
            hosts = (HOST_SAVE,)

        result, used_host = api_save(img_url, cfg, cookies,
                                     create_type=args.create_type, hosts=hosts)

        print()
        Log.ok("完成！")
        print(f"   杯贴地址：{img_url}")
        print(f"   保存入口：{used_host}")
        data = result.get("data")
        if data:
            print(f"   接口返回：{json.dumps(data, ensure_ascii=False)[:300]}")
        print()
        print("   去小程序 → 定制杯贴 → 我的杯贴，应该能看到它。")

        # 保存后把 cookie 落盘，下次少一次挑战
        for host in (HOST_UPLOAD, HOST_SAVE, HOST_LIST):
            jar = cookies.dump(host)
            if jar:
                cfg.setdefault("cookies", {})[host] = jar
        if args.save_config:
            save_config(cfg, Path(__file__).with_name("config.json"))
        return EXIT_OK

    except WafBlocked as w:
        print_waf_help(w.resp, cfg)
        return EXIT_WAF
    except RiskBlocked as r:
        print_risk_help(r, cfg)
        return EXIT_WAF
    except ApiError as e:
        Log.err(str(e))
        return EXIT_API
    except RuntimeError as e:
        Log.err(str(e))
        return EXIT_HTTP


if __name__ == "__main__":
    sys.exit(main())
