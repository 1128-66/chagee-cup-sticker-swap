import Foundation

/// 运行这个 app 需要的外部命令（都是系统里已有的，不需要额外装 Python 包）
struct Toolchain {
    var python: String?
    var mitmdump: String?

    var ready: Bool { python != nil && mitmdump != nil }

    var pythonDesc: String { python ?? "没找到" }
    var mitmdumpDesc: String { mitmdump ?? "没找到" }
}

enum Environment {

    /// 找 Python：只要标准库即可（代理脚本不依赖 Pillow）
    static func findPython() -> String? {
        if let env = ProcessInfo.processInfo.environment["CUP_PYTHON"], !env.isEmpty,
           FileManager.default.isExecutableFile(atPath: env) {
            return env
        }
        let cands = [
            "/usr/bin/python3",
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
        ]
        for c in cands where FileManager.default.isExecutableFile(atPath: c) { return c }
        return which("python3")
    }

    static func findMitmdump() -> String? {
        let cands = [
            "/opt/homebrew/bin/mitmdump",
            "/usr/local/bin/mitmdump",
            "/usr/bin/mitmdump",
        ]
        for c in cands where FileManager.default.isExecutableFile(atPath: c) { return c }
        return which("mitmdump")
    }

    static func probe() -> Toolchain {
        Toolchain(python: findPython(), mitmdump: findMitmdump())
    }

    /// `which`：跑一次 `/usr/bin/which`，不依赖用户的 PATH 配置
    static func which(_ name: String) -> String? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/which")
        p.arguments = [name]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = Pipe()
        do { try p.run() } catch { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard p.terminationStatus == 0 else { return nil }
        let s = String(decoding: data, as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return s.isEmpty ? nil : s
    }
}

/// 定位项目目录（里面有 replace_proxy.py / cup_replace_addon.py）
enum ProjectLocator {

    private static let defaultsKey = "projectDir"

    /// 按优先级找：记住的路径 → .app 所在目录 → 其上一级
    static func locate() -> URL? {
        if let saved = UserDefaults.standard.string(forKey: defaultsKey) {
            let u = URL(fileURLWithPath: saved)
            if isProject(u) { return u }
        }
        // .app 就放在项目目录里，所以先看 bundle 的父目录
        var dir = Bundle.main.bundleURL.deletingLastPathComponent()
        for _ in 0..<3 {
            if isProject(dir) { return dir }
            let up = dir.deletingLastPathComponent()
            if up.path == dir.path { break }
            dir = up
        }
        return nil
    }

    static func remember(_ url: URL) {
        UserDefaults.standard.set(url.path, forKey: defaultsKey)
    }

    static func isProject(_ url: URL) -> Bool {
        FileManager.default.fileExists(atPath: url.appendingPathComponent("replace_proxy.py").path)
            && FileManager.default.fileExists(atPath: url.appendingPathComponent("cup_replace_addon.py").path)
    }

    static func artworkURL(in project: URL) -> URL {
        project.appendingPathComponent("artwork.png")
    }
}
