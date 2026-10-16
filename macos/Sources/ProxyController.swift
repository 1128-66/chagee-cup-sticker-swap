import Foundation
import Combine

/// 管理 replace_proxy.py 子进程：启动 / 实时日志 / 停止。
///
/// 为什么不自己实现代理逻辑：
///   mitmproxy 的插件必须是 Python，而且系统代理接管、看门狗、端口顺延
///   这些都已经在 replace_proxy.py 里验证过了 —— 直接复用，不重复造。
@MainActor
final class ProxyController: ObservableObject {

    enum Phase: Equatable {
        case idle, starting, running, stopping
        case failed(String)

        var title: String {
            switch self {
            case .idle:     return "未启动"
            case .starting: return "启动中…"
            case .running:  return "代理运行中"
            case .stopping: return "正在停止…"
            case .failed:   return "出错了"
            }
        }

        var isBusy: Bool {
            switch self {
            case .starting, .running, .stopping: return true
            default: return false
            }
        }
    }

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var log = ""
    @Published private(set) var replacedCount = 0
    @Published private(set) var lastImageURL: String?

    private var proc: Process?
    private var pipe: Pipe?
    private var projectDir: URL?
    private var stopFallback: DispatchWorkItem?

    var isBusy: Bool { phase.isBusy }
    var isRunning: Bool { phase == .running || phase == .starting }

    // ────────────────────────────────────────────────────────────
    // 启动
    // ────────────────────────────────────────────────────────────

    func start(project: URL, maxMinutes: Int = 180) {
        guard !isBusy else { return }
        guard let python = Environment.findPython() else {
            phase = .failed("找不到 python3")
            return
        }

        projectDir = project
        log = ""
        replacedCount = 0
        lastImageURL = nil
        phase = .starting

        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [
            project.appendingPathComponent("replace_proxy.py").path,
            "--max-minutes", String(maxMinutes),
        ]
        p.currentDirectoryURL = project

        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env

        let out = Pipe()
        p.standardOutput = out
        p.standardError = out
        p.standardInput = FileHandle.nullDevice

        out.fileHandleForReading.readabilityHandler = { [weak self] fh in
            let d = fh.availableData
            guard !d.isEmpty else { return }
            let s = String(decoding: d, as: UTF8.self)
            Task { @MainActor [weak self] in self?.ingest(s) }
        }

        p.terminationHandler = { [weak self] proc in
            let status = proc.terminationStatus
            Task { @MainActor [weak self] in self?.handleExit(status) }
        }

        do {
            try p.run()
            proc = p
            pipe = out
            append("[界面] 已启动 replace_proxy.py（PID \(p.processIdentifier)）\n")

            // 端口起来要一两秒，之后再显示「运行中」
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 2_500_000_000)
                guard let self else { return }
                if self.phase == .starting { self.phase = .running }
            }
        } catch {
            phase = .failed("启动失败：\(error.localizedDescription)")
        }
    }

    // ────────────────────────────────────────────────────────────
    // 停止：走 --close 这个「手动总闸」，先救网络再收进程
    // ────────────────────────────────────────────────────────────

    func stop(project explicit: URL? = nil) {
        let dir = explicit ?? projectDir
        guard let dir, let python = Environment.findPython() else {
            hardKill()
            return
        }

        phase = .stopping
        append("\n[界面] 正在关闭代理（--close）…\n")

        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [dir.appendingPathComponent("replace_proxy.py").path, "--close"]
        p.currentDirectoryURL = dir
        let out = Pipe()
        p.standardOutput = out
        p.standardError = out

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            do { try p.run() } catch {
                Task { @MainActor in self?.hardKill() }
                return
            }
            let d = out.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            let s = String(decoding: d, as: UTF8.self)
            Task { @MainActor [weak self] in
                self?.ingest(s)
                self?.settleAfterStop()
            }
        }

        // 兜底：8 秒后主进程还活着就强杀（看门狗仍会保底还原代理）
        let fb = DispatchWorkItem { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                if self.proc != nil {
                    self.append("[界面] 停止超时，强制结束主进程（看门狗会兜底还原代理）\n")
                    self.hardKill()
                }
            }
        }
        stopFallback = fb
        DispatchQueue.main.asyncAfter(deadline: .now() + 8, execute: fb)
    }

    /// 退出 app 前的兜底 —— 代理不关就会断网
    func shutdownSync() {
        guard let dir = projectDir, let python = Environment.findPython() else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [dir.appendingPathComponent("replace_proxy.py").path, "--close"]
        p.currentDirectoryURL = dir
        p.standardOutput = Pipe()
        p.standardError = Pipe()
        try? p.run()
        p.waitUntilExit()
    }

    // ────────────────────────────────────────────────────────────
    // 内部
    // ────────────────────────────────────────────────────────────

    private func hardKill() {
        proc?.terminate()
        proc = nil
        pipe?.fileHandleForReading.readabilityHandler = nil
        pipe = nil
        settleAfterStop()
    }

    private func settleAfterStop() {
        stopFallback?.cancel()
        stopFallback = nil
        if phase == .stopping || phase == .running || phase == .starting {
            phase = .idle
            append("\n[界面] 已停止，系统代理已还原。\n")
        }
    }

    private func handleExit(_ status: Int32) {
        pipe?.fileHandleForReading.readabilityHandler = nil
        pipe = nil
        proc = nil

        if phase == .stopping {
            settleAfterStop()
            return
        }
        if status == 0 {
            phase = .idle
            append("\n[界面] 代理进程已退出。\n")
        } else {
            phase = .failed("代理异常退出（退出码 \(status)）")
        }
    }

    /// 把新输出并进日志，并顺手解析「替换次数」和「图片地址」
    private func ingest(_ s: String) {
        append(s)
    }

    private func append(_ s: String) {
        log += Self.stripANSI(s)
        if log.count > 300_000 { log = String(log.suffix(150_000)) }

        replacedCount = log.components(separatedBy: "✔ 已替换").count - 1

        if let r = log.range(of: "服务端返回图片地址：", options: .backwards) {
            let tail = log[r.upperBound...].prefix { !$0.isNewline && !$0.isWhitespace }
            let url = String(tail)
            if url.hasPrefix("http") && url.contains(".png") { lastImageURL = url }
        }
    }

    /// 日志面板是纯文本视图，颜色码会显示成 ^[[2m 这样的乱码。
    /// replace_proxy.py 在非 TTY 下已经不上色了，这里再兜一道 ——
    /// 防止 mitmdump 之类第三方输出里夹带转义序列。
    static func stripANSI(_ s: String) -> String {
        guard s.contains("\u{1B}") else { return s }
        var out = ""
        out.reserveCapacity(s.count)
        var it = s.makeIterator()
        while let c = it.next() {
            guard c == "\u{1B}" else { out.append(c); continue }
            // ESC [ ... 字母  （CSI 序列）
            if let n = it.next(), n == "[" {
                while let d = it.next() {
                    if d.isLetter { break }
                }
            } else {
                _ = it.next()   // ESC X：吞掉下一个字符
            }
        }
        return out
    }

    func clearLog() { log = "" }

    // ────────────────────────────────────────────────────────────
    // 仅用于界面离屏渲染 / 自检（macos/tools/render_shots）
    // 不启动任何子进程，只把界面切到指定状态，方便看真实观感。
    // ────────────────────────────────────────────────────────────

    func loadStateForPreview(phase: Phase, log text: String) {
        self.phase = phase
        self.log = ""
        self.replacedCount = 0
        self.lastImageURL = nil
        // 走和真实运行完全同一条解析路径，这样预览和实际观感一致
        append(text)
    }
}
