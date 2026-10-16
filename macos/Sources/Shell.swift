import SwiftUI
import AppKit

// ────────────────────────────────────────────────────────────────
// 窗口外壳：侧边栏 + 内容区
// 与 @main 分开，方便离屏渲染（见 macos/tools/render_shots）
// ────────────────────────────────────────────────────────────────

struct RootView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        HStack(spacing: 0) {
            SidebarView(model: model)
            Divider()
            ZStack {
                Color(nsColor: .windowBackgroundColor)
                switch model.page {
                case .swap:  SwapView(model: model)
                case .about: AboutView(model: model)
                }
            }
            .frame(minWidth: 760, maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(minWidth: 968, minHeight: 600)
    }
}

struct SidebarView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // 品牌区
            HStack(spacing: 10) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(LinearGradient(colors: [Theme.accent, Theme.accentDeep],
                                             startPoint: .topLeading, endPoint: .bottomTrailing))
                        .frame(width: 30, height: 30)
                    Image(systemName: "cup.and.saucer.fill")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                }
                VStack(alignment: .leading, spacing: 1) {
                    Text("CHAGEE SWAP")
                        .font(.system(size: 13, weight: .heavy, design: .rounded))
                        .foregroundStyle(.white)
                    Text("定制杯贴")
                        .font(.system(size: 10))
                        .foregroundStyle(.white.opacity(0.45))
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 16)
            .padding(.top, 20)
            .padding(.bottom, 18)

            // 导航
            VStack(spacing: 3) {
                ForEach(Page.allCases) { p in
                    SidebarItem(page: p, selected: model.page == p) {
                        withAnimation(.easeOut(duration: 0.16)) { model.page = p }
                    }
                }
            }
            .padding(.horizontal, 10)

            Spacer()

            // 底部状态
            VStack(alignment: .leading, spacing: 8) {
                Divider().overlay(Color.white.opacity(0.08))

                HStack(spacing: 7) {
                    StatusDot(color: statusColor, pulsing: model.proxy.phase.isBusy)
                    Text(model.proxy.phase.title)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(.white.opacity(0.85))
                    Spacer(minLength: 0)
                }

                if model.proxy.replacedCount > 0 {
                    HStack(spacing: 6) {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 10))
                            .foregroundStyle(Theme.accent)
                        Text("已替换 \(model.proxy.replacedCount) 次")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(Theme.accent)
                    }
                }

                if !model.toolchain.ready {
                    HStack(spacing: 6) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .font(.system(size: 10))
                            .foregroundStyle(.orange)
                        Text("环境不完整")
                            .font(.system(size: 11))
                            .foregroundStyle(.orange)
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 16)
        }
        .frame(width: 208)
        .background(
            LinearGradient(colors: [Theme.sidebarTop, Theme.sidebarBottom],
                           startPoint: .top, endPoint: .bottom)
        )
    }

    private var statusColor: Color {
        switch model.proxy.phase {
        case .idle:     return Color.white.opacity(0.3)
        case .starting, .stopping: return .orange
        case .running:  return Theme.accent
        case .failed:   return Theme.danger
        }
    }
}

struct SidebarItem: View {
    let page: Page
    let selected: Bool
    let action: () -> Void

    @State private var hover = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                Image(systemName: page.icon)
                    .font(.system(size: 13, weight: .medium))
                    .frame(width: 17)
                Text(page.title)
                    .font(.system(size: 13, weight: selected ? .semibold : .regular))
                Spacer(minLength: 0)
            }
            .foregroundStyle(selected ? .white : .white.opacity(0.62))
            .padding(.horizontal, 11)
            .padding(.vertical, 8)
            .background(
                RoundedRectangle(cornerRadius: 7, style: .continuous)
                    .fill(selected ? Color.white.opacity(0.11)
                                   : (hover ? Color.white.opacity(0.05) : .clear))
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }
}
