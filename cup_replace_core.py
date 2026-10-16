#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cup_replace_core.py — 杯贴替换的纯逻辑层（不依赖 mitmproxy）

这一层只做"字节运算"，不碰网络、不碰代理，所以可以独立单测：

    boundary_of()      从 Content-Type 里抠出 boundary
    rewrite_upload()   把 multipart 里的图片字节换成新的
    sniff_ext()        靠魔数判断图片真实类型（不信任扩展名）
    fit_to_canvas()    把任意图适配到 618×927
    describe()         把一个 multipart 拆成可读的结构（排错用）
    Artwork            作品文件管理：找文件 / 载入 / 变更自动重载

cup_replace_addon.py 只是把这层挂到 mitmproxy 的钩子上；
selftest_replace.py 用真实抓包数据验证这一层。
"""

from __future__ import annotations

import io
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

# ──────────────────────────────────────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────────────────────────────────────

TARGET_HOST = "webapi.qmai.cn"
TARGET_PATH = "/web/common-center/upload/image/index"

# 画布：618×927 = 精确 2:3（618 = 2×309，927 = 3×309），RGBA 8bit
CANVAS = (618, 927)
CANVAS_DESC = "618×927"

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024

# 上传字段名。抓包实测小程序用的是 upfile。
FILE_FIELD_NAMES = (b'name="upfile"', b"name='upfile'")


class MultipartError(Exception):
    """multipart 结构不对，无法改写。"""


# ──────────────────────────────────────────────────────────────────────────────
# 日志注入：默认 print，addon 会换成 ctx.log
# ──────────────────────────────────────────────────────────────────────────────

def _default_info(msg: str) -> None:
    print(f"[杯贴替换] {msg}", flush=True)


def _default_warn(msg: str) -> None:
    print(f"[杯贴替换] ⚠ {msg}", flush=True)


_INFO: Callable[[str], None] = _default_info
_WARN: Callable[[str], None] = _default_warn


def set_logger(info: Callable[[str], None] | None = None,
               warn: Callable[[str], None] | None = None) -> None:
    """让调用方接管日志输出（mitmproxy 走 ctx.log，命令行走 print）。"""
    global _INFO, _WARN
    if info is not None:
        _INFO = info
    if warn is not None:
        _WARN = warn


def say(msg: str) -> None:
    try:
        _INFO(msg)
    except Exception:  # noqa: BLE001
        pass


def warn(msg: str) -> None:
    try:
        _WARN(msg)
    except Exception:  # noqa: BLE001
        pass


# ──────────────────────────────────────────────────────────────────────────────
# ① 解析 Content-Type
# ──────────────────────────────────────────────────────────────────────────────

_BOUNDARY_RE = re.compile(r'boundary=("?)([^";,]+)\1', re.I)


def boundary_of(content_type: str | None) -> bytes | None:
    """从 `multipart/form-data; boundary=----WebKitFormBoundaryXXX` 里取出 boundary。"""
    m = _BOUNDARY_RE.search(content_type or "")
    return m.group(2).encode() if m else None


def is_multipart(content_type: str | None) -> bool:
    return "multipart/form-data" in (content_type or "").lower()


# ──────────────────────────────────────────────────────────────────────────────
# ② multipart 结构解析（只读，给改写和排错共用）
# ──────────────────────────────────────────────────────────────────────────────

def split_parts(body: bytes, boundary: bytes) -> list[bytes]:
    """按 `--boundary` 切分。切出来每段长这样：
        \\r\\n<headers>\\r\\n\\r\\n<data>\\r\\n
    用同一个 delimiter join 回去就能还原 —— 所以改写不需要重新生成 boundary。
    """
    delim = b"--" + boundary
    if delim not in body:
        raise MultipartError(
            f"请求体里找不到分隔符 {delim[:40]!r}，可能不是 multipart 或 boundary 不匹配")
    return body.split(delim)


def _parse_head(part: bytes) -> tuple[bytes, bytes] | None:
    """拆出 (headers, data)。part 以 \\r\\n 开头，以 \\r\\n 结尾。"""
    hdr_end = part.find(b"\r\n\r\n")
    if hdr_end < 0:
        return None
    head = part[2:hdr_end]           # 跳过开头的 \r\n
    data = part[hdr_end + 4:]        # 跳过 \r\n\r\n
    if data.endswith(b"\r\n"):
        data = data[:-2]
    return head, data


def _head_field(head: bytes, name: bytes) -> str | None:
    m = re.search(rb"(?im)^" + re.escape(name) + rb":\s*([^\r\n]*)", head)
    return m.group(1).decode("utf-8", "replace").strip() if m else None


def describe(body: bytes, boundary: bytes) -> list[dict[str, Any]]:
    """把 multipart 拆成人能看的结构，排错/自测用。"""
    out: list[dict[str, Any]] = []
    parts = split_parts(body, boundary)
    for i, part in enumerate(parts):
        parsed = _parse_head(part)
        if not parsed:
            out.append({"index": i, "kind": "trailer" if i in (0, len(parts) - 1)
                        else "?", "bytes": len(part)})
            continue
        head, data = parsed
        cd = _head_field(head, b"Content-Disposition") or ""
        m = re.search(r'name="([^"]*)"', cd)
        mf = re.search(r'filename="([^"]*)"', cd)
        out.append({
            "index": i,
            "kind": "field",
            "name": m.group(1) if m else None,
            "filename": mf.group(1) if mf else None,
            "content_type": _head_field(head, b"Content-Type"),
            "data_len": len(data),
            "magic": data[:8].hex(),
        })
    return out


def find_file_part(body: bytes, boundary: bytes) -> int | None:
    """返回承载文件的那一段在 split 结果里的下标。"""
    parts = split_parts(body, boundary)
    for i, part in enumerate(parts):
        parsed = _parse_head(part)
        if not parsed:
            continue
        head, _ = parsed
        if any(fn in head for fn in FILE_FIELD_NAMES) or b"filename=" in head:
            return i
    return None


# ──────────────────────────────────────────────────────────────────────────────
# ③ 核心：把文件部分的字节换掉
# ──────────────────────────────────────────────────────────────────────────────

def rewrite_upload(body: bytes, boundary: bytes, new_bytes: bytes,
                   new_name: str, new_mime: str) -> bytes | None:
    """把 multipart 里的文件部分换成 new_bytes。

    返回改写后的完整请求体；找不到文件字段时返回 None；
    结构有问题时抛 MultipartError。

    注意：这里**只动文件那一段**，其余字段（sellerId / token / 时间戳……）
    原样保留，字节级不变 —— 所以服务端看到的还是小程序自己发的那个请求，
    只是像素换了。
    """
    delim = b"--" + boundary

    # 我们的图片字节里若恰好含分隔符，会破坏 multipart 结构。概率极低但必须拦。
    if delim in new_bytes:
        raise MultipartError(
            f"新图片字节里含有分隔符 {delim!r}，会破坏 multipart 结构，已放弃本次替换")

    parts = split_parts(body, boundary)
    idx = find_file_part(body, boundary)
    if idx is None:
        return None

    parsed = _parse_head(parts[idx])
    if not parsed:
        raise MultipartError(f"第 {idx} 段没有头部/正文分隔符")
    head, old_data = parsed

    # ── 改 filename ──
    if b"filename=" in head:
        new_head = re.sub(rb'filename="[^"]*"',
                          b'filename="' + new_name.encode() + b'"', head)
    else:
        # 没有 filename 就补一个，避免服务端按纯字段处理
        new_head = re.sub(rb"(?im)^(Content-Disposition:[^\r\n]*)",
                          rb'\1; filename="' + new_name.encode() + b'"', head)

    # ── 改 Content-Type（注意 [^\r\n]* 而不是 .*，否则会把 \r 一起吃掉） ──
    if re.search(rb"(?im)^Content-Type:", new_head):
        new_head = re.sub(rb"(?im)^Content-Type:[^\r\n]*",
                          b"Content-Type: " + new_mime.encode(), new_head)
    else:
        new_head = new_head + b"\r\nContent-Type: " + new_mime.encode()

    parts[idx] = b"\r\n" + new_head + b"\r\n\r\n" + new_bytes + b"\r\n"
    out = delim.join(parts)

    say(f"改写第 {idx} 段：{len(old_data) / 1024:.1f} KB → "
        f"{len(new_bytes) / 1024:.1f} KB，filename={new_name}，{new_mime}")
    return out


# ──────────────────────────────────────────────────────────────────────────────
# ③b 把新 body 写回请求对象（只动 body 与 Content-Length，绝不碰鉴权头）
# ──────────────────────────────────────────────────────────────────────────────

def _ci_get_key(headers, name: str) -> str | None:
    """在大小写不敏感的映射里找到某个 header 的真实键名。"""
    for k in list(headers.keys()):
        if k.lower() == name:
            return k
    return None


def apply_body(message, new_body: bytes) -> None:
    """把改写后的 body 写回请求，并修好 Content-Length。

    message 只需要有 `.content` 和 `.headers`（一个映射）——
    mitmproxy 的 http.Request 满足，测试用的假对象也满足。

    这里**只碰 body 和 content-length**：
        · qm-user-token / qm-from / referer / user-agent …… 一个都不动；
        · 鉴权、WAF 签名、设备指纹全部维持原样。
    这正是"不绕过任何安全控制"的落地方式。
    """
    message.content = new_body

    headers = message.headers
    # 改写后长度已知，去掉 chunked 编码声明，免得服务端解析混乱
    te = _ci_get_key(headers, "transfer-encoding")
    if te is not None:
        del headers[te]

    cl = _ci_get_key(headers, "content-length") or "content-length"
    headers[cl] = str(len(new_body))


# ──────────────────────────────────────────────────────────────────────────────
# ④ 类型识别：只信魔数，不信扩展名
# ──────────────────────────────────────────────────────────────────────────────
def sniff_ext(data: bytes) -> tuple[str, str]:
    """返回 (扩展名, MIME)。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png", "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg", "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif", "image/gif"
    if data[:2] == b"BM":
        return ".bmp", "image/bmp"
    return ".png", "image/png"


def random_name(ext: str = ".png") -> str:
    """复刻小程序的文件名风格：10 位数字 + 后缀。"""
    import random
    import string
    return "".join(random.choice(string.digits) for _ in range(10)) + ext


def image_size(data: bytes) -> tuple[int, int] | None:
    """不依赖 Pillow 读出图片尺寸（PNG / JPEG / WebP）。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return (int.from_bytes(data[16:20], "big"),
                int.from_bytes(data[20:24], "big"))

    if data[:3] == b"\xff\xd8\xff":          # JPEG：扫 SOFn 段
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
                i += 2
                continue
            seg = int.from_bytes(data[i + 2:i + 4], "big")
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h = int.from_bytes(data[i + 5:i + 7], "big")
                w = int.from_bytes(data[i + 7:i + 9], "big")
                return (w, h)
            i += 2 + seg

    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = data[12:16]
        if kind == b"VP8X":
            w = int.from_bytes(data[24:27], "little") + 1
            h = int.from_bytes(data[27:30], "little") + 1
            return (w, h)
        if kind == b"VP8 ":                  # 有损：跳过 3 字节起始码再取 14 位
            i = data.find(b"\x9d\x01\x2a", 20)
            if i > 0 and i + 7 <= len(data):
                w = int.from_bytes(data[i + 3:i + 5], "little") & 0x3FFF
                h = int.from_bytes(data[i + 5:i + 7], "little") & 0x3FFF
                return (w, h)
        if kind == b"VP8L" and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    return None


# ── 解释器选择：mitmproxy 自带的 Python 没有 Pillow，缩放必须交给外部解释器 ──

PYTHON_CANDIDATES = (
    Path("/opt/homebrew/bin/python3"),   # Apple Silicon Homebrew
    Path("/usr/local/bin/python3"),      # Intel Homebrew
    Path("/usr/bin/python3"),            # 系统自带
)


def preferred_python() -> str:
    """返回一个大概率装了 Pillow 的解释器路径。

    想指定就设环境变量 CUP_PYTHON=/path/to/python3。
    """
    env = os.environ.get("CUP_PYTHON")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return str(p)
    for p in PYTHON_CANDIDATES:
        if p.exists():
            return str(p)
    return "python3"


# ──────────────────────────────────────────────────────────────────────────────
# ⑤ 尺寸适配（自包含，不依赖 cup_sticker.py，也不会 SystemExit 掉代理）
# ──────────────────────────────────────────────────────────────────────────────

def _has_pillow() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def fit_to_canvas(src: Path, canvas: tuple[int, int] = CANVAS,
                  fit: str = "contain", bg: str = "white",
                  max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    """把任意图片适配到画布尺寸，返回 PNG/原图字节。

    fit: contain（默认，不裁切，补边）/ cover（铺满，裁切）/ stretch / none
    bg : white（默认）/ transparent
    没有 Pillow 时原样返回，不抛异常 —— 代理里绝不能因为图片库缺失就崩。
    """
    data = Path(src).read_bytes()
    if fit == "none":
        return data

    if not _has_pillow():
        warn(f"未安装 Pillow，跳过尺寸适配，原样使用（{len(data) / 1024:.1f} KB）")
        return data

    from PIL import Image  # type: ignore

    cw, ch = canvas
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGBA")
            ow, oh = im.width, im.height

            if fit == "stretch":
                out = im.resize((cw, ch), Image.LANCZOS)
            else:
                scale = (max(cw / ow, ch / oh) if fit == "cover"
                         else min(cw / ow, ch / oh))
                nw = max(1, round(ow * scale))
                nh = max(1, round(oh * scale))
                resized = im.resize((nw, nh), Image.LANCZOS)

                if fit == "cover":
                    left, top = (nw - cw) // 2, (nh - ch) // 2
                    out = resized.crop((left, top, left + cw, top + ch))
                else:  # contain
                    fill = (0, 0, 0, 0) if bg == "transparent" else (255, 255, 255, 255)
                    plate = Image.new("RGBA", (cw, ch), fill)
                    plate.paste(resized, ((cw - nw) // 2, (ch - nh) // 2), resized)
                    out = plate

            buf = io.BytesIO()
            out.save(buf, "PNG", optimize=True)
            data = buf.getvalue()
            say(f"尺寸适配 {ow}×{oh} → {cw}×{ch}（{fit}/{bg}），"
                f"{len(data) / 1024:.1f} KB")
    except Exception as e:  # noqa: BLE001
        warn(f"尺寸适配失败（{e}），原样使用原图")
        return Path(src).read_bytes()

    # 超限就逐级缩小
    if len(data) > max_bytes:
        warn(f"{len(data) / 1024:.0f} KB 超过上限 {max_bytes / 1024:.0f} KB，压缩中…")
        try:
            with Image.open(io.BytesIO(data)) as im:
                im = im.convert("RGB")
                for _ in range(6):
                    buf = io.BytesIO()
                    im.save(buf, "PNG", optimize=True)
                    data = buf.getvalue()
                    if len(data) <= max_bytes:
                        break
                    im = im.resize((max(1, int(im.width * 0.85)),
                                    max(1, int(im.height * 0.85))), Image.LANCZOS)
        except Exception as e:  # noqa: BLE001
            warn(f"压缩失败：{e}")
        if len(data) > max_bytes:
            warn(f"压缩后仍为 {len(data) / 1024:.0f} KB，服务端可能拒绝")
        else:
            say(f"压缩后 {len(data) / 1024:.1f} KB")

    return data


# ──────────────────────────────────────────────────────────────────────────────
# ⑥ 作品文件管理
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_ARTWORK_STEMS = ("artwork", "cup", "杯贴")


def find_artwork(here: Path) -> Path | None:
    """按 CUP_REPLACE_IMAGE → artwork.* → cup.* → 杯贴.* 的顺序找。"""
    env = os.environ.get("CUP_REPLACE_IMAGE")
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else None

    for stem in DEFAULT_ARTWORK_STEMS:
        for ext in IMAGE_EXTS:
            p = here / f"{stem}{ext}"
            if p.exists():
                return p
    return None


class Artwork:
    """持有当前要用的作品字节；文件被替换时能自动重载。"""

    def __init__(self, here: Path, canvas: tuple[int, int] = CANVAS) -> None:
        self.here = here
        self.canvas = canvas
        self.path: Path | None = None
        self.data: bytes | None = None
        self.mtime: float | None = None
        self.ext: str = ".png"
        self.mime: str = "image/png"
        self.size: tuple[int, int] | None = None
        self.count = 0
        self.last_url: str | None = None

    # ── 载入 ──

    @property
    def enabled(self) -> bool:
        return os.environ.get("CUP_REPLACE_ENABLE", "1") != "0"

    def load(self, force: bool = False) -> bool:
        if not self.enabled:
            if force:
                say("CUP_REPLACE_ENABLE=0，替换已停用（插件仍加载，只放行）")
            self.data = None
            return False

        if self.data is not None and not force:
            return True

        src = find_artwork(self.here)
        if not src:
            self.data = None
            self.path = None
            return False

        fit = os.environ.get("CUP_REPLACE_FIT", "contain")
        bg = os.environ.get("CUP_REPLACE_BG", "white")
        try:
            data = fit_to_canvas(src, self.canvas, fit, bg)
        except Exception as e:  # noqa: BLE001
            warn(f"读取作品失败：{e}")
            self.data = None
            return False

        ext, mime = sniff_ext(data)
        self.path = src
        self.data = data
        self.mime = mime
        self.ext = ext
        self.size = image_size(data)
        try:
            self.mtime = src.stat().st_mtime
        except OSError:
            self.mtime = None
        dim = f"{self.size[0]}×{self.size[1]}" if self.size else "尺寸未知"
        say(f"作品已载入：{src.name}（{len(data) / 1024:.1f} KB，{mime}，{dim}）")

        # mitmproxy 自带的 Python 没有 Pillow —— 尺寸不对时必须提前处理，
        # 否则会把一张尺寸不符的图直接塞给服务端。
        if self.size and self.size != self.canvas and not _has_pillow():
            warn(f"这张图是 {self.size[0]}×{self.size[1]}，不是画布尺寸 "
                 f"{self.canvas[0]}×{self.canvas[1]}；"
                 f"mitmproxy 内置的 Python 没有 Pillow，代理里没法缩放。")
            warn(f"先跑一次预适配：{preferred_python()} "
                 f"{self.here / 'prepare_artwork.py'} \"{src}\"")
        return True

    def reload_if_changed(self) -> bool:
        """文件被覆盖保存后自动重载 —— 换图不用重启 mitmdump。"""
        if not self.path or self.mtime is None:
            return False
        try:
            m = self.path.stat().st_mtime
        except OSError:
            return False
        if m == self.mtime:
            return False
        say(f"检测到 {self.path.name} 已更新，重新载入…")
        self.data = None
        return self.load(force=True)

    # ── 替换额度 ──

    def quota_left(self) -> bool:
        max_n = int(os.environ.get("CUP_REPLACE_MAX", "0") or 0)
        return (max_n == 0) or (self.count < max_n)

    def next_filename(self) -> str:
        return random_name(self.ext)


# ──────────────────────────────────────────────────────────────────────────────
# ⑦ 从响应里找图片地址
# ──────────────────────────────────────────────────────────────────────────────

_URL_KEYS = ("url", "imgUrl", "imageUrl", "path", "src")


def find_image_url(obj: Any, depth: int = 0) -> str | None:
    """递归找第一个像图片地址的字符串。"""
    if depth > 8:
        return None
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith("http") and any(
                k in s for k in ("images.qmai.cn", "qmai.cn/", ".png", ".jpg", ".jpeg")):
            return s
        return None
    if isinstance(obj, dict):
        # 优先看常见字段名，提高命中准确度
        for k in _URL_KEYS:
            if k in obj:
                r = find_image_url(obj[k], depth + 1)
                if r:
                    return r
        for v in obj.values():
            r = find_image_url(v, depth + 1)
            if r:
                return r
    if isinstance(obj, list):
        for v in obj:
            r = find_image_url(v, depth + 1)
            if r:
                return r
    return None


# ──────────────────────────────────────────────────────────────────────────────
# ⑧ 给命令行/自测用的便捷入口
# ──────────────────────────────────────────────────────────────────────────────

def plan_summary(body: bytes, boundary: bytes, art: bytes) -> str:
    """生成一段人类可读的替换预览（不真正改写）。"""
    lines = [f"  请求体 {len(body)} B，boundary={boundary.decode(errors='replace')}"]
    try:
        idx = find_file_part(body, boundary)
    except MultipartError as e:
        return f"  ✗ 结构异常：{e}"
    if idx is None:
        return "  ✗ 没有找到文件字段（name=\"upfile\" 或带 filename= 的段）"
    for d in describe(body, boundary):
        mark = " ← 将被替换" if d["index"] == idx else ""
        if d["kind"] == "field":
            lines.append(f"  [{d['index']}] name={d['name']!r} "
                         f"filename={d['filename']!r} "
                         f"type={d['content_type']!r} {d['data_len']} B{mark}")
        else:
            lines.append(f"  [{d['index']}] {d['kind']} {d['bytes']} B")
    ext, mime = sniff_ext(art)
    lines.append(f"  替换为：{len(art)} B，{mime}（{time.strftime('%H:%M:%S')}）")
    return "\n".join(lines)
