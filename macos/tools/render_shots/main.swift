// ────────────────────────────────────────────────────────────────
// 界面离屏渲染（开发用）
//
// 为什么要这个：
//   这个环境没有「屏幕录制」权限，screencapture 抓不到窗口内容。
//   而且全屏截图会误抓到用户的私人屏幕内容 —— 不做那种事。
//
// 怎么做：
//   把 SwiftUI 视图塞进一个不显示的 NSWindow 里，走完整的 AppKit
//   布局流程，再 cacheDisplay 成位图。比 ImageRenderer 可靠 ——
//   ImageRenderer 不跑布局，ScrollView 里的内容会渲染成空白。
//
// 用法：
//   macos/tools/render_shots/run.sh            # 输出到 macos/build/shots/
//   macos/tools/render_shots/run.sh <输出目录>
// ────────────────────────────────────────────────────────────────

import SwiftUI
import AppKit
import ImageIO

// ── 参数与路径 ──

let argv = CommandLine.arguments
let outDir = URL(fileURLWithPath: argv.count > 1 ? argv[1]
                                             : FileManager.default.currentDirectoryPath + "/build/shots")
let scale = argv.count > 2 ? (CGFloat(Double(argv[2]) ?? 2)) : 2

/// 本文件在 <project>/macos/tools/render_shots/main.swift
let projectRoot = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent()    // render_shots
    .deletingLastPathComponent()    // tools
    .deletingLastPathComponent()    // macos
    .deletingLastPathComponent()    // <project>

let fixtureImage = projectRoot.appendingPathComponent("fixtures/sample_artwork.png")

/// 演示模式：界面上「项目目录」那一栏会显示真实绝对路径，
/// 而截图是要公开的 —— 换成占位路径，别把人家的工作目录发到 GitHub 上。
let demoMode = ProcessInfo.processInfo.environment["CUP_SHOT_DEMO"] != nil
let shownProject = demoMode ? URL(fileURLWithPath: "/Users/you/chagee-cup-sticker-swap")
                            : projectRoot

// ── 离屏窗口渲染 ──

@MainActor
enum Offscreen {

    static func png<V: View>(_ view: V,
                             width: CGFloat, height: CGFloat,
                             appearance: NSAppearance.Name = .aqua,
                             scale: CGFloat = 2) -> Data? {

        let hosting = NSHostingView(rootView: view)
        hosting.frame = NSRect(x: 0, y: 0, width: width, height: height)
        hosting.appearance = NSAppearance(named: appearance)

        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                           styleMask: [.borderless],
                           backing: .buffered,
                           defer: false)
        win.appearance = NSAppearance(named: appearance)
        win.backgroundColor = .windowBackgroundColor
        win.isReleasedWhenClosed = false
        win.contentView = hosting

        // 跑一遍布局，并泵几圈 runloop 让 SwiftUI 完成首帧
        hosting.layoutSubtreeIfNeeded()
        for _ in 0..<6 {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
            hosting.layoutSubtreeIfNeeded()
        }
        hosting.displayIfNeeded()

        guard let rep = hosting.bitmapImageRepForCachingDisplay(in: hosting.bounds) else { return nil }
        rep.size = NSSize(width: width, height: height)
        hosting.cacheDisplay(in: hosting.bounds, to: rep)

        // 放大到 2x，看得清细节
        let px = NSSize(width: width * scale, height: height * scale)
        let big = NSBitmapImageRep(bitmapDataPlanes: nil,
                                   pixelsWide: Int(px.width), pixelsHigh: Int(px.height),
                                   bitsPerSample: 8, samplesPerPixel: 4,
                                   hasAlpha: true, isPlanar: false,
                                   colorSpaceName: .deviceRGB,
                                   bytesPerRow: 0, bitsPerPixel: 0)
        guard let big else { return rep.representation(using: .png, properties: [:]) }

        NSGraphicsContext.saveGraphicsState()
        if let ctx = NSGraphicsContext(bitmapImageRep: big) {
            NSGraphicsContext.current = ctx
            ctx.imageInterpolation = .high
            rep.draw(in: NSRect(origin: .zero, size: px))
        }
        NSGraphicsContext.restoreGraphicsState()

        win.contentView = nil
        return big.representation(using: .png, properties: [:])
    }

    static func write<V: View>(_ view: V,
                               width: CGFloat, height: CGFloat,
                               appearance: NSAppearance.Name = .aqua,
                               to name: String) {
        guard let data = png(view, width: width, height: height,
                             appearance: appearance, scale: scale) else {
            print("✘ \(name)：渲染失败")
            return
        }
        do {
            try data.write(to: outDir.appendingPathComponent(name))
            print("✔ \(name)  \(Int(width * scale))×\(Int(height * scale))  \(data.count / 1024) KB")
        } catch {
            print("✘ \(name)：\(error.localizedDescription)")
        }
    }
}

// ── 造一份像真的运行日志（故意带上 ANSI 颜色码，
//    用来验证界面确实把它们剥掉了）──

func demoLog() -> String {
    let dim = "\u{1B}[2m", b = "\u{1B}[1m", g = "\u{1B}[32m", r = "\u{1B}[0m"
    return """
    \(dim)  ────────────────────────────────────────────────────────────\(r)
      \(b)霸王茶姬 · 定制杯贴替换代理\(r)
    \(dim)  ────────────────────────────────────────────────────────────\(r)
      代理端口    8899
      作品文件    artwork.png  (618×927, 42.1 KB)
      最长运行    180 分钟（到点自动还原系统代理）

    \(dim)  → 正在启动代理（端口 8899）…\(r)
    \(dim)  → 端口就绪，正在接管系统代理…\(r)
      \(g)✔\(r) 系统代理已指向 127.0.0.1:8899
      \(g)✔\(r) 看门狗已就位（PID 88121）
      \(g)✔\(r) 现在可以去小程序里创作杯贴了

    [proxy] 127.0.0.1:53210  POST  https://mapi.qmai.cn/cup/image/upload
    [proxy]   ├─ 命中替换规则：multipart/form-data 字段名 "file"
      \(g)✔\(r) 已替换 第 1 次  （1.8 MB → 42.1 KB）
    [proxy]   └─ 服务端返回图片地址：https://images.qmai.cn/common/s00000/2026/10/16/8f2c41d90ab34e77.png

    \(dim)  → 请求已放行，图片已在传输途中换成你自己的图\(r)
    """
}

// ── 构造各种状态的 model ──

@MainActor
func goodModel() -> AppModel {
    let m = AppModel()
    m.toolchain = Toolchain(python: "/usr/bin/python3", mitmdump: "/opt/homebrew/bin/mitmdump")
    m.projectDir = shownProject
    return m
}

/// 生成一张「宽扁」的测试图，专门用来对比三种适配方式。
/// 四个角有不同颜色的小方块，中间一个十字 —— 一旦被裁掉或拉伸，一眼就能看出来。
func makeWideTestImage() -> URL {
    let W = 1200, H = 600
    let url = outDir.appendingPathComponent("_test-wide.png")
    guard let ctx = CGContext(data: nil, width: W, height: H,
                              bitsPerComponent: 8, bytesPerRow: 0,
                              space: CGColorSpace(name: CGColorSpace.sRGB)!,
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { return url }

    ctx.setFillColor(CGColor(srgbRed: 0.98, green: 0.96, blue: 0.92, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))

    // 外框
    ctx.setStrokeColor(CGColor(srgbRed: 0.85, green: 0.20, blue: 0.16, alpha: 1))
    ctx.setLineWidth(16)
    ctx.stroke(CGRect(x: 8, y: 8, width: W - 16, height: H - 16))

    // 四个角的标记块：左上红、右上绿、左下蓝、右下黄
    let s = 130
    let corners: [(CGRect, CGColor)] = [
        (CGRect(x: 30, y: H - 30 - s, width: s, height: s),
         CGColor(srgbRed: 0.90, green: 0.22, blue: 0.21, alpha: 1)),
        (CGRect(x: W - 30 - s, y: H - 30 - s, width: s, height: s),
         CGColor(srgbRed: 0.13, green: 0.72, blue: 0.34, alpha: 1)),
        (CGRect(x: 30, y: 30, width: s, height: s),
         CGColor(srgbRed: 0.16, green: 0.47, blue: 0.90, alpha: 1)),
        (CGRect(x: W - 30 - s, y: 30, width: s, height: s),
         CGColor(srgbRed: 0.97, green: 0.75, blue: 0.15, alpha: 1)),
    ]
    for (r, c) in corners { ctx.setFillColor(c); ctx.fill(r) }

    // 中心十字
    ctx.setFillColor(CGColor(srgbRed: 0.15, green: 0.15, blue: 0.18, alpha: 1))
    ctx.fill(CGRect(x: W / 2 - 6, y: 120, width: 12, height: H - 240))
    ctx.fill(CGRect(x: 220, y: H / 2 - 6, width: W - 440, height: 12))

    guard let cg = ctx.makeImage(),
          let dest = CGImageDestinationCreateWithURL(url as CFURL,
                                                     "public.png" as CFString, 1, nil)
    else { return url }
    CGImageDestinationAddImage(dest, cg, nil)
    CGImageDestinationFinalize(dest)
    return url
}

// ── 主流程 ──

@MainActor
func renderAll() {
    try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
    print("输出目录：\(outDir.path)")
    print("项目根：\(projectRoot.path)")

    // ① 换图页 · 空状态
    do {
        let m = goodModel(); m.page = .swap
        Offscreen.write(RootView(model: m), width: 1000, height: 680, to: "01-swap-empty.png")
    }

    // ② 换图页 · 已选图
    do {
        let m = goodModel(); m.page = .swap
        m.accept(url: fixtureImage)
        Offscreen.write(RootView(model: m), width: 1000, height: 680, to: "02-swap-picked.png")
    }

    // ③ 换图页 · 运行中 + 日志 + 成功横幅
    do {
        let m = goodModel(); m.page = .swap
        m.accept(url: fixtureImage)
        m.showLog = true
        m.proxy.loadStateForPreview(phase: .running, log: demoLog())
        Offscreen.write(RootView(model: m), width: 1000, height: 1020, to: "03-swap-running.png")
    }

    // ④ 换图页 · 环境不完整（告警态）
    do {
        let m = AppModel(); m.page = .swap
        m.toolchain = Toolchain(python: "/usr/bin/python3", mitmdump: nil)
        m.projectDir = nil
        Offscreen.write(RootView(model: m), width: 1000, height: 680, to: "04-swap-noenv.png")
    }

    // ⑤ 关于页
    do {
        let m = goodModel(); m.page = .about
        Offscreen.write(RootView(model: m), width: 1000, height: 900, to: "05-about.png")
    }

    // ⑥ 三种适配方式（用宽扁测试图，差别才看得出来）
    let wide = makeWideTestImage()
    for (i, fit) in [FitMode.contain, .cover, .stretch].enumerated() {
        let m = goodModel(); m.page = .swap
        m.fit = fit
        m.accept(url: wide)
        Offscreen.write(RootView(model: m), width: 1000, height: 680,
                        to: "06-fit-\(i + 1)-\(fit.rawValue).png")
    }

    // ⑦ 深色模式（内容区跟随系统，侧边栏本来就深色）
    do {
        let m = goodModel(); m.page = .swap
        m.accept(url: fixtureImage)
        Offscreen.write(RootView(model: m), width: 1000, height: 680,
                        appearance: .darkAqua, to: "07-swap-dark.png")
    }
}

// ── 入口 ──

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)

MainActor.assumeIsolated { renderAll() }
print("完成")
