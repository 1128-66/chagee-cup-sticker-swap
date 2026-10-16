import SwiftUI
import AppKit
import UniformTypeIdentifiers

enum Page: String, CaseIterable, Identifiable {
    case swap, about
    var id: String { rawValue }
    var title: String { self == .swap ? "换图" : "关于" }
    var icon: String { self == .swap ? "photo.on.rectangle.angled" : "info.circle" }
}

@MainActor
final class AppModel: ObservableObject {
    @Published var page: Page = .swap
    @Published var fit: FitMode = .contain
    @Published var sourceURL: URL?
    @Published var prepared: PreparedImage?
    @Published var prepError: String?
    @Published var projectDir: URL?
    @Published var toolchain = Environment.probe()
    @Published var maxMinutes = 180
    @Published var showLog = false

    let proxy = ProxyController()

    var canStart: Bool {
        prepared != nil && projectDir != nil && !proxy.isBusy && toolchain.ready
    }

    // ── 选图 ──
    func pickImage() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.message = "选一张你画好的图（任意尺寸，会自动适配到 618×927）"
        panel.prompt = "选择"
        if let t = UTType(filenameExtension: "heic") {
            panel.allowedContentTypes = [.png, .jpeg, .tiff, .bmp, .gif, t]
        } else {
            panel.allowedContentTypes = [.png, .jpeg, .tiff, .bmp, .gif]
        }
        if panel.runModal() == .OK, let u = panel.url { accept(url: u) }
    }

    func accept(url: URL) {
        guard ImagePreparer.isSupported(url) else {
            prepError = "不支持的格式：.\(url.pathExtension)。用 JPG / PNG / HEIC。"
            return
        }
        sourceURL = url
        reprepare()
    }

    func reprepare() {
        guard let url = sourceURL else { return }
        do {
            prepared = try ImagePreparer.prepare(url: url, fit: fit)
            prepError = nil
        } catch {
            prepared = nil
            prepError = error.localizedDescription
        }
    }

    // ── 写 artwork.png + 起代理 ──
    @discardableResult
    func writeArtwork() -> Bool {
        guard let prepared, let dir = projectDir else { return false }
        let dst = ProjectLocator.artworkURL(in: dir)
        do {
            try prepared.pngData.write(to: dst, options: .atomic)
            return true
        } catch {
            prepError = "写入 artwork.png 失败：\(error.localizedDescription)"
            return false
        }
    }

    func startProxy() {
        guard let dir = projectDir else { return }
        guard writeArtwork() else { return }
        showLog = true
        proxy.start(project: dir, maxMinutes: maxMinutes)
    }

    func stopProxy() {
        proxy.stop(project: projectDir)
    }

    func refreshEnvironment() {
        toolchain = Environment.probe()
        projectDir = ProjectLocator.locate()
    }

    func chooseProjectDir() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.message = "选到包含 replace_proxy.py 的目录"
        panel.prompt = "选择"
        if panel.runModal() == .OK, let u = panel.url {
            if ProjectLocator.isProject(u) {
                ProjectLocator.remember(u)
                projectDir = u
            } else {
                prepError = "这个目录里没有 replace_proxy.py / cup_replace_addon.py"
            }
        }
    }
}
