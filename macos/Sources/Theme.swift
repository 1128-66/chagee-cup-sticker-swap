import SwiftUI

/// 全局配色与尺寸。
/// 侧边栏固定深色（原型就是深色导航），内容区跟随系统明暗。
enum Theme {
    /// 主色：和原型一致的微信绿
    static let accent      = Color(red: 0.027, green: 0.757, blue: 0.376)
    static let accentDeep  = Color(red: 0.016, green: 0.600, blue: 0.290)
    static let danger      = Color(red: 0.906, green: 0.298, blue: 0.235)

    /// 侧边栏渐变
    static let sidebarTop    = Color(red: 0.086, green: 0.114, blue: 0.188)
    static let sidebarBottom = Color(red: 0.047, green: 0.067, blue: 0.114)

    static let cardRadius: CGFloat = 12
    static let dropRadius: CGFloat = 10

    /// 杯贴画布尺寸（实测）—— 真正的定义在 ImagePreparer 里
    static var canvasW: Int { ImagePreparer.canvasW }
    static var canvasH: Int { ImagePreparer.canvasH }
    static var canvas: CGSize { CGSize(width: canvasW, height: canvasH) }
}

extension View {
    /// 统一卡片外观：圆角 + 描边 + 轻阴影
    func card(padding: CGFloat = 18) -> some View {
        self
            .padding(padding)
            .background(
                RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                    .fill(Color(nsColor: .controlBackgroundColor))
            )
            .overlay(
                RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                    .strokeBorder(Color.primary.opacity(0.09), lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.05), radius: 6, y: 2)
    }
}

/// 圆形状态点
struct StatusDot: View {
    let color: Color
    var pulsing: Bool = false
    @State private var on = false

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: 8, height: 8)
            .opacity(pulsing && on ? 0.35 : 1)
            .animation(pulsing ? .easeInOut(duration: 0.7).repeatForever(autoreverses: true) : .default,
                       value: on)
            .onAppear { if pulsing { on = true } }
    }
}
