// makeicon.swift — 生成 app 图标（绿色圆角方块 + 白色杯子）
// 用法：swift makeicon.swift <输出目录>      产出 AppIcon.iconset/ 与 AppIcon.png
import AppKit
import CoreGraphics

let outDir = URL(fileURLWithPath: CommandLine.arguments.count > 1
                 ? CommandLine.arguments[1] : FileManager.default.currentDirectoryPath)
let iconset = outDir.appendingPathComponent("AppIcon.iconset")
try? FileManager.default.removeItem(at: iconset)
try! FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)

/// 在 size×size 像素上画一个图标
func renderIcon(size: Int) -> NSBitmapImageRep {
    let S = CGFloat(size)
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                               isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    guard let ctx = NSGraphicsContext.current?.cgContext else {
        NSGraphicsContext.restoreGraphicsState(); return rep
    }

    // 圆角方块（留出 macOS 图标惯用的边距）
    let pad = S * 0.085
    let rect = CGRect(x: pad, y: pad, width: S - pad * 2, height: S - pad * 2)
    let radius = rect.width * 0.225
    ctx.addPath(CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius,
                       transform: nil))
    ctx.clip()

    // 渐变底
    let cs = CGColorSpace(name: CGColorSpace.sRGB)!
    let grad = CGGradient(colorsSpace: cs, colors: [
        CGColor(srgbRed: 0.18, green: 0.87, blue: 0.49, alpha: 1),
        CGColor(srgbRed: 0.016, green: 0.58, blue: 0.28, alpha: 1),
    ] as CFArray, locations: [0, 1])!
    ctx.drawLinearGradient(grad,
                           start: CGPoint(x: rect.minX, y: rect.maxY),
                           end: CGPoint(x: rect.maxX, y: rect.minY),
                           options: [])

    // 顶部高光
    let hl = CGGradient(colorsSpace: cs, colors: [
        CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 0.22),
        CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 0),
    ] as CFArray, locations: [0, 1])!
    ctx.drawLinearGradient(hl,
                           start: CGPoint(x: rect.minX, y: rect.maxY),
                           end: CGPoint(x: rect.minX, y: rect.midY),
                           options: [])
    ctx.resetClip()

    // 白色杯子
    let cfg = NSImage.SymbolConfiguration(pointSize: rect.width * 0.44, weight: .semibold)
    if let sym = NSImage(systemSymbolName: "cup.and.saucer.fill",
                         accessibilityDescription: nil)?.withSymbolConfiguration(cfg) {
        let sz = sym.size
        let tinted = NSImage(size: sz)
        tinted.lockFocus()
        NSColor.white.set()
        NSRect(origin: .zero, size: sz).fill(using: .sourceOver)
        sym.draw(in: NSRect(origin: .zero, size: sz), from: .zero,
                 operation: .destinationIn, fraction: 1)
        tinted.unlockFocus()

        let dst = NSRect(x: rect.midX - sz.width / 2,
                         y: rect.midY - sz.height / 2 + rect.height * 0.015,
                         width: sz.width, height: sz.height)
        tinted.draw(in: dst, from: .zero, operation: .sourceOver, fraction: 1)
    }

    NSGraphicsContext.restoreGraphicsState()
    return rep
}

func write(_ rep: NSBitmapImageRep, to url: URL) {
    guard let data = rep.representation(using: .png, properties: [:]) else { return }
    try? data.write(to: url)
}

// iconutil 需要的文件名 → 像素尺寸
let entries: [(String, Int)] = [
    ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
]
for (name, px) in entries {
    write(renderIcon(size: px), to: iconset.appendingPathComponent(name))
}
// 顺带出一张 512 的预览图
write(renderIcon(size: 512), to: outDir.appendingPathComponent("AppIcon.png"))
print("✔ 图标已生成：\(iconset.path)")
