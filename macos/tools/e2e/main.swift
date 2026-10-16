// ────────────────────────────────────────────────────────────────
// app 层端到端联调（开发用）
//
// 验证的是「界面背后的那套逻辑」而不是像素：
//   AppModel.startProxy() → 起 replace_proxy.py → 看门狗就位
//                        → 系统代理被接管 → stopProxy() → 代理还原
//
// 刻意在临时目录里跑（argv[1]），这样 artwork.png 写在临时目录，
// 不会覆盖用户自己的作品。
//
//   macos/tools/e2e/run.sh
// ────────────────────────────────────────────────────────────────

import SwiftUI
import AppKit

let argv = CommandLine.arguments
guard argv.count > 1 else {
    print("✘ 用法：e2e <临时项目目录>")
    exit(2)
}
let project = URL(fileURLWithPath: argv[1])
let fixture = URL(fileURLWithPath: argv[2])

var pass = 0, fail = 0
func check(_ ok: Bool, _ what: String, _ detail: String = "") {
    if ok { pass += 1; print("  ✔ \(what)\(detail.isEmpty ? "" : "  \(detail)")") }
    else  { fail += 1; print("  ✘ \(what)\(detail.isEmpty ? "" : "  \(detail)")") }
}

// ── 跑外部命令 ──

@discardableResult
func sh(_ launch: String, _ args: [String]) -> String {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: launch)
    p.arguments = args
    let out = Pipe()
    p.standardOutput = out
    p.standardError = out
    do { try p.run() } catch { return "" }
    let d = out.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return String(decoding: d, as: UTF8.self)
}

/// 读 Wi-Fi 的 HTTP 代理状态
func proxyState() -> (enabled: Bool, port: Int) {
    let s = sh("/usr/sbin/networksetup", ["-getwebproxy", "Wi-Fi"])
    let enabled = s.contains("Enabled: Yes")
    var port = 0
    for line in s.split(separator: "\n") where line.hasPrefix("Port:") {
        port = Int(line.split(separator: ":")[1].trimmingCharacters(in: .whitespaces)) ?? 0
    }
    return (enabled, port)
}

func watchdogRunning() -> Bool {
    let s = sh("/usr/bin/pgrep", ["-fl", "proxy_watchdog"])
    return s.contains("proxy_watchdog")
}

func mitmRunning() -> Bool {
    sh("/usr/bin/pgrep", ["-fl", "mitmdump"]).contains("mitmdump")
}

/// 泵 runloop，让 @MainActor 的 Task 和 Pipe 回调有机会跑
@MainActor
func pump(_ seconds: Double) {
    let end = Date().addingTimeInterval(seconds)
    while Date() < end {
        RunLoop.current.run(until: Date().addingTimeInterval(0.2))
    }
}

/// 反复尝试直到条件成立或超时；每轮都泵一下 runloop
@MainActor
func waitUntil(_ seconds: Double, _ cond: () -> Bool) -> Bool {
    let end = Date().addingTimeInterval(seconds)
    while Date() < end {
        if cond() { return true }
        RunLoop.current.run(until: Date().addingTimeInterval(0.2))
    }
    return cond()
}

// ── 主流程 ──

@MainActor
func run() {
    print("临时项目目录：\(project.path)")
    print("测试图：\(fixture.path)")
    print("")

    let before = proxyState()
    print("① 起始状态")
    check(!before.enabled, "系统代理未启用", "Enabled=\(before.enabled) port=\(before.port)")
    check(!mitmRunning(), "没有 mitmdump 在跑")
    check(!watchdogRunning(), "没有看门狗在跑")
    print("")

    let m = AppModel()
    m.projectDir = project
    m.maxMinutes = 1                  // 兜底：最多接管 1 分钟就自己还原
    m.fit = .contain
    m.accept(url: fixture)

    print("② 选图 → 适配")
    check(m.prepared != nil, "适配成功")
    if let p = m.prepared {
        check(p.cgImage.width == 618 && p.cgImage.height == 927,
              "画布 618×927", "\(p.cgImage.width)×\(p.cgImage.height)  \(p.kbDesc)")
    }
    check(m.canStart, "「启动代理」按钮可用")
    print("")

    print("③ 启动代理")
    m.startProxy()
    check(m.proxy.phase == .starting || m.proxy.phase == .running,
          "进入启动流程", "phase=\(m.proxy.phase.title)")

    let artPath = project.appendingPathComponent("artwork.png").path
    check(waitUntil(3) { FileManager.default.fileExists(atPath: artPath) },
          "artwork.png 已写进项目目录")

    check(waitUntil(20) { mitmRunning() }, "mitmdump 起来了")
    let took = waitUntil(25) { proxyState().enabled && proxyState().port == 8899 }
    let now = proxyState()
    check(took, "系统代理被接管到 127.0.0.1:8899", "Enabled=\(now.enabled) port=\(now.port)")
    check(waitUntil(25) { watchdogRunning() }, "看门狗就位（脱终端独立会话）")

    print("④ 运行中的日志（界面读到的内容）")
    let log = m.proxy.log
    check(log.contains("系统代理已指向") || log.contains("看门狗"),
          "日志里有接管记录")
    check(!log.contains("\u{1B}"), "日志里没有 ANSI 颜色码（非 TTY 已关色 + 界面兜底剥离）")
    let tail = log.split(separator: "\n").suffix(6).joined(separator: "\n")
    print("  ┌─ 日志末尾 ─")
    for line in tail.split(separator: "\n") { print("  │ \(line)") }
    print("  └─")
    print("")

    print("⑤ 停止代理（走 --close 手动总闸）")
    m.stopProxy()
    check(waitUntil(20) { !proxyState().enabled }, "系统代理已还原（不再启用）")
    let after = proxyState()
    check(after.port == before.port || !after.enabled,
          "代理地址回到原样", "port=\(after.port)（原 \(before.port)）")
    check(waitUntil(20) { !mitmRunning() }, "mitmdump 已退出")
    check(waitUntil(20) { !watchdogRunning() }, "看门狗已退出")
    check(waitUntil(10) {
        !FileManager.default.fileExists(atPath: project.appendingPathComponent("watchdog.json").path)
    }, "watchdog.json 已清理")
    print("")

    print("⑥ 最终状态")
    let fin = proxyState()
    check(fin.enabled == before.enabled && fin.port == before.port,
          "系统代理与起始状态完全一致",
          "before(\(before.enabled),\(before.port)) → after(\(fin.enabled),\(fin.port))")

    print("")
    print(String(repeating: "─", count: 56))
    print(fail == 0 ? "✔ 全部通过（\(pass) 项）" : "✘ \(fail) 项失败 / 共 \(pass + fail) 项")
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)

MainActor.assumeIsolated { run() }
exit(fail == 0 ? 0 : 1)
