import SwiftUI
import AppKit

struct AboutView: View {
    @ObservedObject var model: AppModel

    private let repoURL = "https://github.com/1128-66/chagee-cup-sticker-swap"

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                // 头部
                HStack(spacing: 14) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 14, style: .continuous)
                            .fill(LinearGradient(colors: [Theme.accent, Theme.accentDeep],
                                                 startPoint: .topLeading, endPoint: .bottomTrailing))
                            .frame(width: 54, height: 54)
                        Image(systemName: "cup.and.saucer.fill")
                            .font(.system(size: 26, weight: .semibold))
                            .foregroundStyle(.white)
                    }
                    VStack(alignment: .leading, spacing: 3) {
                        Text("CHAGEE SWAP")
                            .font(.system(size: 20, weight: .heavy, design: .rounded))
                        Text("霸王茶姬定制杯贴 · 把你自己画的图变成杯贴")
                            .font(.system(size: 12))
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }

                // 做什么 / 不做什么
                HStack(alignment: .top, spacing: 16) {
                    bulletCard(title: "它做了什么", icon: "checkmark.circle.fill",
                               tint: Theme.accent, items: [
                        "请求由真实小程序发出，代理只在传输途中把图片字节换成你的图",
                        "WAF 的 JS 签名由小程序自己算，被正常满足",
                        "登录态是你自己的，机审 / 风控照常走",
                        "换图不用重启，随时可以换一张",
                    ])
                    bulletCard(title: "它不做什么", icon: "xmark.circle.fill",
                               tint: Theme.danger, items: [
                        "不伪造签名，不绕过任何安全控制",
                        "不碰请求里的 token / User-Agent / referer",
                        "不修改你自己的代理设置（Loon / Clash 原样不动）",
                        "不收集、不上传任何数据",
                    ])
                }

                // 使用步骤
                VStack(alignment: .leading, spacing: 12) {
                    cardTitle("怎么用", icon: "list.number")
                    step(1, "选一张图", "拖进来或点击选择，任意尺寸都行")
                    step(2, "点「启动代理」", "会自动接管系统代理，并起一个看门狗兜底")
                    step(3, "去小程序操作", "下单 → 定制杯贴 → 创作杯贴 → 随便画一笔 → 提交创作")
                    step(4, "回来点「停止代理」", "代理不关就会断网，所以用完一定要停")
                }
                .card()

                // 环境
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        cardTitle("运行环境", icon: "wrench.and.screwdriver")
                        Spacer()
                        Button("重新检测") { model.refreshEnvironment() }
                            .buttonStyle(.borderless)
                            .font(.system(size: 11))
                            .foregroundStyle(Theme.accent)
                    }
                    envRow("Python", model.toolchain.pythonDesc, model.toolchain.python != nil)
                    envRow("mitmdump", model.toolchain.mitmdumpDesc, model.toolchain.mitmdump != nil)
                    envRow("项目目录",
                           model.projectDir?.path ?? "没找到（点右边「更换」指定）",
                           model.projectDir != nil,
                           action: ("更换", { model.chooseProjectDir() }))
                    if !model.toolchain.ready {
                        Text("缺 mitmdump 的话：brew install mitmproxy")
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .textSelection(.enabled)
                    }
                }
                .card()

                // 链接
                HStack(spacing: 10) {
                    Button {
                        if let u = URL(string: repoURL) { NSWorkspace.shared.open(u) }
                    } label: {
                        Label("GitHub 仓库", systemImage: "link")
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(Theme.accent)

                    Button {
                        NSWorkspace.shared.activateFileViewerSelecting(
                            [model.projectDir ?? URL(fileURLWithPath: NSHomeDirectory())])
                    } label: {
                        Label("打开项目目录", systemImage: "folder")
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(Theme.accent)

                    Spacer()
                    Text("MIT License")
                        .font(.system(size: 11))
                        .foregroundStyle(.tertiary)
                }
                .padding(.horizontal, 4)
            }
            .padding(24)
        }
    }

    // ── 组件 ──

    private func cardTitle(_ t: String, icon: String) -> some View {
        HStack(spacing: 7) {
            Image(systemName: icon)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.accent)
            Text(t).font(.system(size: 13, weight: .semibold))
        }
    }

    private func bulletCard(title: String, icon: String, tint: Color, items: [String]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 7) {
                Image(systemName: icon).font(.system(size: 13)).foregroundStyle(tint)
                Text(title).font(.system(size: 13, weight: .semibold))
            }
            ForEach(items, id: \.self) { s in
                HStack(alignment: .top, spacing: 8) {
                    Circle().fill(tint.opacity(0.6)).frame(width: 4, height: 4).padding(.top, 6)
                    Text(s)
                        .font(.system(size: 12))
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .card()
    }

    private func step(_ n: Int, _ title: String, _ detail: String) -> some View {
        HStack(alignment: .top, spacing: 11) {
            ZStack {
                Circle().fill(Theme.accent.opacity(0.14)).frame(width: 22, height: 22)
                Text("\(n)")
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(Theme.accent)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 12.5, weight: .semibold))
                Text(detail).font(.system(size: 11.5)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
    }

    private func envRow(_ name: String, _ value: String, _ ok: Bool,
                        action: (String, () -> Void)? = nil) -> some View {
        HStack(spacing: 10) {
            Image(systemName: ok ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                .font(.system(size: 12))
                .foregroundStyle(ok ? Theme.accent : .orange)
            Text(name)
                .font(.system(size: 12, weight: .medium))
                .frame(width: 74, alignment: .leading)
            Text(value)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(.secondary)
                .lineLimit(1).truncationMode(.middle)
                .textSelection(.enabled)
            Spacer(minLength: 0)
            if let (label, fn) = action {
                Button(label, action: fn)
                    .buttonStyle(.borderless)
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.accent)
            }
        }
    }
}
