import SwiftUI
import AppKit

// ────────────────────────────────────────────────────────────────
// 入口：@main + 生命周期
// 视图壳在 Shell.swift，状态在 AppModel.swift
// ────────────────────────────────────────────────────────────────

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var model: AppModel?

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        // 代理不关就会断网 —— 退出前一定兜底关掉
        model?.proxy.shutdownSync()
    }
}

@main
struct ChageeSwapApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup("CHAGEE SWAP") {
            RootView(model: model)
                .onAppear {
                    delegate.model = model
                    model.refreshEnvironment()
                }
        }
        .windowResizability(.contentMinSize)
        .defaultSize(width: 1000, height: 680)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(after: .appInfo) {
                Button("重新检测环境") { model.refreshEnvironment() }
            }
        }
    }
}
