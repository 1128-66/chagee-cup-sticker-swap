#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prepare_artwork.py — 把任意图片预适配成 618×927，供 mitmproxy 插件直接使用

为什么需要这一步
    mitmproxy 12 是打包成独立二进制的（内置 Python 3.14.4），里面**没有 Pillow**。
    所以代理运行期无法做缩放/补边。图片处理必须在启动代理之前做完 ——
    用你系统里那个装了 Pillow 的 Python 跑一次本脚本即可。

用法
    python3 prepare_artwork.py 我的画.jpg
        → 生成 ./artwork.png（618×927，contain 补白边），插件会自动用它

    python3 prepare_artwork.py 我的画.jpg --fit cover      # 铺满，裁掉超出部分
    python3 prepare_artwork.py 我的画.jpg --bg transparent  # 补透明边而不是白边
    python3 prepare_artwork.py 我的画.jpg -o 杯贴.png       # 指定输出名

    --info 只看看不改：
    python3 prepare_artwork.py 我的画.jpg --info
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cup_replace_core as core  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="把图片预适配成小程序画布尺寸，供 mitm 替换插件使用")
    ap.add_argument("source", help="你的原图（任意尺寸、任意常见格式）")
    ap.add_argument("-o", "--out", help="输出路径，默认 ./artwork.png")
    ap.add_argument("--fit", default="contain",
                    choices=["contain", "cover", "stretch", "none"],
                    help="contain=补边不裁切(默认) / cover=铺满裁切 / stretch=拉伸 / none=不动")
    ap.add_argument("--bg", default="white", choices=["white", "transparent"],
                    help="contain 补边用什么底色，默认 white")
    ap.add_argument("--info", action="store_true", help="只看尺寸信息，不写文件")
    args = ap.parse_args(argv)

    src = Path(args.source).expanduser()
    if not src.exists():
        print(f"找不到文件：{src}")
        return 4

    raw = src.read_bytes()
    size = core.image_size(raw)
    ext, mime = core.sniff_ext(raw)

    print()
    print("=" * 68)
    print("  杯贴图片预适配")
    print("=" * 68)
    print(f"  源文件：{src}")
    print(f"  格式  ：{mime}（{ext}）")
    print(f"  尺寸  ：{f'{size[0]}×{size[1]}' if size else '无法识别（可能是罕见的编码）'}")
    print(f"  体积  ：{len(raw) / 1024:.1f} KB")

    cw, ch = core.CANVAS
    print(f"  画布  ：{cw}×{ch}（精确 2:3）")

    if size:
        sw, sh = size
        src_ratio, dst_ratio = sw / sh, cw / ch
        if abs(src_ratio - dst_ratio) < 1e-6:
            print(f"  比例  ：{src_ratio:.4f} ≈ {dst_ratio:.4f} ✔ 与原画布同比")
        else:
            print(f"  比例  ：{src_ratio:.4f} vs 画布 {dst_ratio:.4f} "
                  f"→ 需要适配（{args.fit}）")

    if args.info:
        print()
        print("  --info 模式，未写文件。")
        print("=" * 68)
        print()
        return 0

    if not core._has_pillow():
        print()
        print("  ✘ 当前 Python 没有 Pillow，无法缩放。")
        print(f"    换这个解释器再跑：{core.preferred_python()}")
        print(f"    或者：pip install Pillow")
        print("=" * 68)
        return 4

    out = Path(args.out).expanduser() if args.out else (HERE / "artwork.png")

    print()
    data = core.fit_to_canvas(src, core.CANVAS, args.fit, args.bg)
    out.write_bytes(data)

    new_size = core.image_size(data)
    print()
    print(f"  ✔ 已写出：{out}")
    print(f"    尺寸  ：{f'{new_size[0]}×{new_size[1]}' if new_size else '?'}")
    print(f"    体积  ：{len(data) / 1024:.1f} KB")
    print()
    print("  下一步：")
    print(f"    mitmdump -s cup_replace_addon.py --listen-host 127.0.0.1 --listen-port 8899")
    print("    然后在小程序里随便画一笔 → 提交创作")
    print("=" * 68)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
