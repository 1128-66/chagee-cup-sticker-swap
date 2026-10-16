import AppKit
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

/// 适配方式（和 Python 侧 `--fit` 一一对应）
enum FitMode: String, CaseIterable, Identifiable {
    case contain, cover, stretch

    var id: String { rawValue }

    var label: String {
        switch self {
        case .contain: return "留白"
        case .cover:   return "铺满"
        case .stretch: return "拉伸"
        }
    }

    var hint: String {
        switch self {
        case .contain: return "等比缩放后居中补白边，不裁切任何内容"
        case .cover:   return "等比缩放后居中裁切，铺满画布，边缘会被切掉"
        case .stretch: return "直接拉伸到画布尺寸，会变形"
        }
    }

    /// 对应 prepare_artwork.py 的取值
    var pythonValue: String { rawValue }
}

struct PreparedImage {
    let cgImage: CGImage
    let pngData: Data
    let sourceSize: CGSize
    let byteCount: Int

    var sourceDesc: String {
        "\(Int(sourceSize.width))×\(Int(sourceSize.height))"
    }
    var canvasDesc: String { "\(ImagePreparer.canvasW)×\(ImagePreparer.canvasH)" }
    var kbDesc: String { String(format: "%.1f KB", Double(byteCount) / 1024) }
}

enum PrepError: LocalizedError {
    case cannotRead
    case cannotCreateContext
    case cannotRender
    case cannotEncode
    case tooLarge(Int)

    var errorDescription: String? {
        switch self {
        case .cannotRead:          return "读不出这张图，换一张试试"
        case .cannotCreateContext: return "创建绘图上下文失败"
        case .cannotRender:        return "渲染失败"
        case .cannotEncode:        return "编码 PNG 失败"
        case .tooLarge(let b):     return "图片太大（\(b / 1024) KB），建议压到 5 MB 以内"
        }
    }
}

/// 原生实现 618×927 适配 —— 不依赖 Python / Pillow。
enum ImagePreparer {

    /// 杯贴画布尺寸（实测）
    static let canvasW = 618
    static let canvasH = 927

    static let maxInputBytes = 5 * 1024 * 1024

    static func prepare(url: URL, fit: FitMode) throws -> PreparedImage {
        let attrs = (try? FileManager.default.attributesOfItem(atPath: url.path)) ?? [:]
        let size = (attrs[.size] as? Int) ?? 0
        if size > maxInputBytes { throw PrepError.tooLarge(size) }

        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
              CGImageSourceGetCount(src) > 0,
              let cg = CGImageSourceCreateImageAtIndex(src, 0, nil)
        else { throw PrepError.cannotRead }

        return try render(cg, fit: fit)
    }

    static func render(_ cg: CGImage, fit: FitMode) throws -> PreparedImage {
        let W = canvasW, H = canvasH

        guard let ctx = CGContext(
            data: nil, width: W, height: H,
            bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { throw PrepError.cannotCreateContext }

        // 白底 —— 印在杯子上等于不印
        ctx.setFillColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))
        ctx.interpolationQuality = .high

        let iw = CGFloat(cg.width), ih = CGFloat(cg.height)
        guard iw > 0, ih > 0 else { throw PrepError.cannotRead }

        let sx = CGFloat(W) / iw, sy = CGFloat(H) / ih
        var dw = CGFloat(W), dh = CGFloat(H)
        switch fit {
        case .contain:
            let s = min(sx, sy); dw = iw * s; dh = ih * s
        case .cover:
            let s = max(sx, sy); dw = iw * s; dh = ih * s
        case .stretch:
            break
        }
        let rect = CGRect(x: (CGFloat(W) - dw) / 2,
                          y: (CGFloat(H) - dh) / 2,
                          width: dw, height: dh)
        ctx.draw(cg, in: rect)

        guard let out = ctx.makeImage() else { throw PrepError.cannotRender }

        let buf = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(
            buf, UTType.png.identifier as CFString, 1, nil
        ) else { throw PrepError.cannotEncode }
        CGImageDestinationAddImage(dest, out, nil)
        guard CGImageDestinationFinalize(dest) else { throw PrepError.cannotEncode }

        return PreparedImage(cgImage: out,
                             pngData: buf as Data,
                             sourceSize: CGSize(width: iw, height: ih),
                             byteCount: buf.length)
    }

    /// 支持的类型（拖拽时用来过滤）
    static let allowedExtensions: Set<String> = [
        "png", "jpg", "jpeg", "heic", "heif", "webp", "tif", "tiff", "bmp", "gif",
    ]

    static func isSupported(_ url: URL) -> Bool {
        allowedExtensions.contains(url.pathExtension.lowercased())
    }
}
