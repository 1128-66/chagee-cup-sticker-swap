// ────────────────────────────────────────────────────────────────
// 适配逻辑自检（开发用）
//
// 造一张四象限纯色图（左上红 / 右上绿 / 左下蓝 / 右下黄），
// 过一遍 ImagePreparer，再按算好的坐标去点像素 —— 颜色对了才算过。
//
// 这样能一次性抓出三类错：方向翻转、缩放比例错、居中偏移错。
//
//   macos/tools/verify_fit/run.sh
// ────────────────────────────────────────────────────────────────

import AppKit
import CoreGraphics
import ImageIO

var pass = 0
var fail = 0

func check(_ ok: Bool, _ what: String, _ detail: String = "") {
    if ok { pass += 1; print("  ✔ \(what)\(detail.isEmpty ? "" : "  \(detail)")") }
    else  { fail += 1; print("  ✘ \(what)\(detail.isEmpty ? "" : "  \(detail)")") }
}

// ── 造四象限源图 ──
// 注意：CGContext 原点在左下，所以「图像空间的左上」对应 CG 的 y 上半部分。

let SW = 1200, SH = 600

enum Quad: String {
    case red, green, blue, yellow
    var cg: CGColor {
        switch self {
        case .red:    return CGColor(srgbRed: 0.90, green: 0.13, blue: 0.13, alpha: 1)
        case .green:  return CGColor(srgbRed: 0.10, green: 0.75, blue: 0.25, alpha: 1)
        case .blue:   return CGColor(srgbRed: 0.10, green: 0.30, blue: 0.92, alpha: 1)
        case .yellow: return CGColor(srgbRed: 0.98, green: 0.82, blue: 0.10, alpha: 1)
        }
    }
}

func makeSource() -> CGImage {
    let ctx = CGContext(data: nil, width: SW, height: SH,
                        bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpace(name: CGColorSpace.sRGB)!,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    let hw = CGFloat(SW / 2), hh = CGFloat(SH / 2)
    // 图像空间左上 = CG 空间的 (x:0..hw, y:hh..SH)
    ctx.setFillColor(Quad.red.cg);    ctx.fill(CGRect(x: 0,  y: hh, width: hw, height: hh))
    ctx.setFillColor(Quad.green.cg);  ctx.fill(CGRect(x: hw, y: hh, width: hw, height: hh))
    ctx.setFillColor(Quad.blue.cg);   ctx.fill(CGRect(x: 0,  y: 0,  width: hw, height: hh))
    ctx.setFillColor(Quad.yellow.cg); ctx.fill(CGRect(x: hw, y: 0,  width: hw, height: hh))
    return ctx.makeImage()!
}

/// 读「图像空间」（左上为原点）的像素。
///
/// 不直接算缓冲区行号 —— CG 位图缓冲的行序是个老坑，很容易搞反。
/// 改成把目标像素挪到 1×1 画布的 (0,0)：只有一行，不存在行序歧义。
func pixel(_ img: CGImage, x: Int, y: Int) -> (Int, Int, Int) {
    var buf = [UInt8](repeating: 0, count: 4)
    buf.withUnsafeMutableBytes { raw in
        let ctx = CGContext(data: raw.baseAddress, width: 1, height: 1,
                            bitsPerComponent: 8, bytesPerRow: 4,
                            space: CGColorSpace(name: CGColorSpace.sRGB)!,
                            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        ctx.interpolationQuality = .none
        // 图像空间 y 从上往下数，CG 的 y 从下往上数 → 差一个 (h-1-y)
        ctx.draw(img, in: CGRect(x: CGFloat(-x),
                                 y: CGFloat(-(img.height - 1 - y)),
                                 width: CGFloat(img.width),
                                 height: CGFloat(img.height)))
    }
    return (Int(buf[0]), Int(buf[1]), Int(buf[2]))
}

func rgb(_ q: Quad) -> (Int, Int, Int) {
    let c = q.cg.components ?? [0, 0, 0, 1]
    return (Int(c[0] * 255), Int(c[1] * 255), Int(c[2] * 255))
}

func near(_ c: (Int, Int, Int), _ q: Quad, tol: Int = 24) -> Bool {
    let e = rgb(q)
    return abs(c.0 - e.0) <= tol && abs(c.1 - e.1) <= tol && abs(c.2 - e.2) <= tol
}

func isWhite(_ c: (Int, Int, Int), tol: Int = 8) -> Bool {
    abs(c.0 - 255) <= tol && abs(c.1 - 255) <= tol && abs(c.2 - 255) <= tol
}

func desc(_ c: (Int, Int, Int)) -> String { "(\(c.0),\(c.1),\(c.2))" }

// ── 开跑 ──

let src = makeSource()
let W = ImagePreparer.canvasW, H = ImagePreparer.canvasH
print("画布 \(W)×\(H)   源图 \(SW)×\(SH)")
print("")

// ⓪ 先验 `pixel()` 自己 —— 免得后面把「我读错了」当成「代码错了」
print("⓪ pixel() 自检（2×2 已知图案）")
do {
    let ctx = CGContext(data: nil, width: 2, height: 2,
                        bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpace(name: CGColorSpace.sRGB)!,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    // CG 的 y 从下往上，图像空间 y 从上往下 —— 差一步换算
    ctx.setFillColor(Quad.red.cg);    ctx.fill(CGRect(x: 0, y: 1, width: 1, height: 1))
    ctx.setFillColor(Quad.green.cg);  ctx.fill(CGRect(x: 1, y: 1, width: 1, height: 1))
    ctx.setFillColor(Quad.blue.cg);   ctx.fill(CGRect(x: 0, y: 0, width: 1, height: 1))
    ctx.setFillColor(Quad.yellow.cg); ctx.fill(CGRect(x: 1, y: 0, width: 1, height: 1))
    let t = ctx.makeImage()!
    check(near(pixel(t, x: 0, y: 0), .red),    "左上红", desc(pixel(t, x: 0, y: 0)))
    check(near(pixel(t, x: 1, y: 0), .green),  "右上绿", desc(pixel(t, x: 1, y: 0)))
    check(near(pixel(t, x: 0, y: 1), .blue),   "左下蓝", desc(pixel(t, x: 0, y: 1)))
    check(near(pixel(t, x: 1, y: 1), .yellow), "右下黄", desc(pixel(t, x: 1, y: 1)))
}
print("")

// ① 尺寸与方向
print("① 尺寸 / 方向（contain）")
do {
    let out = try ImagePreparer.render(src, fit: .contain)
    check(out.cgImage.width == W && out.cgImage.height == H,
          "输出尺寸 = \(W)×\(H)", "实际 \(out.cgImage.width)×\(out.cgImage.height)")
    check(out.pngData.count > 1000, "PNG 编码非空", "\(out.pngData.count / 1024) KB")

    // contain：0.515 倍 → 618×309，垂直居中（上下各留 309）
    // 图像空间 y = 309 + 309/2 = 463 是图像垂直中点
    let tl = pixel(out.cgImage, x: 100, y: 400)
    let tr = pixel(out.cgImage, x: 520, y: 400)
    let bl = pixel(out.cgImage, x: 100, y: 530)
    let br = pixel(out.cgImage, x: 520, y: 530)
    check(near(tl, .red),    "左上 是红（没被翻转）", desc(tl))
    check(near(tr, .green),  "右上 是绿（没被镜像）", desc(tr))
    check(near(bl, .blue),   "左下 是蓝",            desc(bl))
    check(near(br, .yellow), "右下 是黄",            desc(br))

    let topBand = pixel(out.cgImage, x: 309, y: 60)
    let botBand = pixel(out.cgImage, x: 309, y: 880)
    check(isWhite(topBand), "上方留白", desc(topBand))
    check(isWhite(botBand), "下方留白", desc(botBand))
}

// ② 铺满：1.545 倍 → 1854×927，左右各裁 (1854-618)/2 = 618
print("")
print("② 铺满（cover）—— 应裁掉左右、保留完整的上下")
do {
    let out = try ImagePreparer.render(src, fit: .cover)

    // 左右半边。采样点必须躲开两条中线 ——
    // 源图是四象限纯色，中线上是抗锯齿过渡色，读出来谁都不是。
    //   x = 600（源）→ 画布 x = 600*1.545-618 = 309
    //   y = 300（源）→ 画布 y = 300*1.545     = 463
    let l = pixel(out.cgImage, x: 150, y: 200)
    let r = pixel(out.cgImage, x: 470, y: 200)
    check(near(l, .red),   "左半边是红", desc(l))
    check(near(r, .green), "右半边是绿", desc(r))

    // 上下顶到边（没有白边）—— 采样点要躲开 x 正中，
    // 那里正好是源图红/绿的分界线，读到的是抗锯齿过渡色
    let top = pixel(out.cgImage, x: 150, y: 12)
    let bot = pixel(out.cgImage, x: 150, y: 914)
    check(!isWhite(top), "上边没有留白（铺满）", desc(top))
    check(!isWhite(bot), "下边没有留白（铺满）", desc(bot))

    // 关键：源图的下半部分要完整看得到（说明裁的是左右，不是上下）
    check(near(top, .red),  "上边来自源图上半（红）", desc(top))
    check(near(bot, .blue), "下边来自源图下半（蓝）—— 上下没被裁", desc(bot))
}

// ③ 拉伸：x 方向 0.515、y 方向 1.545，四象限边界仍在正中
print("")
print("③ 拉伸（stretch）—— 应变形")
do {
    let out = try ImagePreparer.render(src, fit: .stretch)
    let tl = pixel(out.cgImage, x: 120, y: 180)
    let tr = pixel(out.cgImage, x: 500, y: 180)
    let bl = pixel(out.cgImage, x: 120, y: 760)
    let br = pixel(out.cgImage, x: 500, y: 760)
    check(near(tl, .red),    "左上红", desc(tl))
    check(near(tr, .green),  "右上绿", desc(tr))
    check(near(bl, .blue),   "左下蓝", desc(bl))
    check(near(br, .yellow), "右下黄", desc(br))
    check(!isWhite(pixel(out.cgImage, x: 309, y: 12)), "无留白")
}

// ④ 同比例输入：三种模式结果应完全一致
print("")
print("④ 画布同比例输入（618×927）—— 三种模式应一致")
do {
    let ctx = CGContext(data: nil, width: W, height: H,
                        bitsPerComponent: 8, bytesPerRow: 0,
                        space: CGColorSpace(name: CGColorSpace.sRGB)!,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    ctx.setFillColor(Quad.red.cg); ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))
    let same = ctx.makeImage()!

    var hashes: [String] = []
    for fit in FitMode.allCases {
        let out = try ImagePreparer.render(same, fit: fit)
        check(out.pngData.count > 500, "\(fit.rawValue) 有输出")
        hashes.append("\(out.cgImage.width)×\(out.cgImage.height)")
    }
    check(Set(hashes).count == 1, "三种模式输出尺寸一致", hashes.joined(separator: " / "))
}

// ⑤ 大文件拦截
print("")
print("⑤ 超大输入应被拒绝")
do {
    let tmp = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("cup_big_test.png")
    let big = Data(repeating: 0, count: ImagePreparer.maxInputBytes + 1024)
    try big.write(to: tmp)
    defer { try? FileManager.default.removeItem(at: tmp) }
    do {
        _ = try ImagePreparer.prepare(url: tmp, fit: .contain)
        check(false, "应该抛 tooLarge")
    } catch let e as PrepError {
        if case .tooLarge = e { check(true, "抛了 tooLarge", e.errorDescription ?? "") }
        else { check(false, "抛错类型不对", "\(e)") }
    }
}

// ── 汇总 ──

print("")
print(String(repeating: "─", count: 56))
if fail == 0 {
    print("✔ 全部通过（\(pass) 项）")
} else {
    print("✘ \(fail) 项失败 / 共 \(pass + fail) 项")
}
exit(fail == 0 ? 0 : 1)
