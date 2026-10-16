import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct SwapView: View {
    @ObservedObject var model: AppModel
    @State private var dropTargeted = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header

                HStack(alignment: .top, spacing: 16) {
                    pickerCard
                    previewCard
                }
                .frame(maxWidth: .infinity)

                fitRow
                actionBar

                if model.showLog { logPanel }
                if let url = model.proxy.lastImageURL { resultBanner(url) }
            }
            .padding(24)
        }
    }

    // ── 标题 ──
    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 3) {
                Text("换图")
                    .font(.system(size: 22, weight: .bold))
                Text("选一张你画好的图，起代理后在小程序里随便涂一笔，服务端收到的就是你这张。")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if model.prepared != nil {
                Label("已就绪", systemImage: "checkmark.seal.fill")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Theme.accent)
                    .padding(.horizontal, 9).padding(.vertical, 4)
                    .background(Capsule().fill(Theme.accent.opacity(0.12)))
            }
        }
    }

    // ── 选图卡片 ──
    private var pickerCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            cardTitle("选择图片", icon: "square.and.arrow.down")

            ZStack {
                RoundedRectangle(cornerRadius: Theme.dropRadius, style: .continuous)
                    .fill(dropTargeted ? Theme.accent.opacity(0.08)
                                       : Color.primary.opacity(0.025))
                RoundedRectangle(cornerRadius: Theme.dropRadius, style: .continuous)
                    .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6, 5]))
                    .foregroundStyle(dropTargeted ? Theme.accent : Color.primary.opacity(0.20))

                if let p = model.prepared, let url = model.sourceURL {
                    chosen(p, url)
                } else {
                    empty
                }
            }
            .frame(height: 232)
            .animation(.easeOut(duration: 0.15), value: dropTargeted)
            .contentShape(Rectangle())
            .onTapGesture { model.pickImage() }
            .onDrop(of: [.fileURL], isTargeted: $dropTargeted) { providers in
                guard let provider = providers.first else { return false }
                _ = provider.loadObject(ofClass: URL.self) { url, _ in
                    guard let url else { return }
                    Task { @MainActor in model.accept(url: url) }
                }
                return true
            }

            if let err = model.prepError {
                Label(err, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("支持 JPG / PNG / HEIC，单张不超过 5 MB")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }
        }
        .card()
        .frame(maxWidth: .infinity)
    }

    private var empty: some View {
        VStack(spacing: 10) {
            Image(systemName: "icloud.and.arrow.up")
                .font(.system(size: 34, weight: .light))
                .foregroundStyle(Theme.accent)
            Text("将图片拖到这里")
                .font(.system(size: 13, weight: .medium))
            Text("或点击选择")
                .font(.system(size: 12))
                .foregroundStyle(Theme.accent)
        }
    }

    private func chosen(_ p: PreparedImage, _ url: URL) -> some View {
        VStack(spacing: 10) {
            Image(decorative: p.cgImage, scale: 1)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(maxHeight: 128)
                .background(Color.white)
                .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(Color.primary.opacity(0.10)))
                .clipShape(RoundedRectangle(cornerRadius: 4))

            Text(url.lastPathComponent)
                .font(.system(size: 12, weight: .medium))
                .lineLimit(1)
                .truncationMode(.middle)
                .padding(.horizontal, 12)

            HStack(spacing: 6) {
                tag("原图 \(p.sourceDesc)")
                tag("→ 画布 \(p.canvasDesc)")
                tag(p.kbDesc)
            }

            Text("点击可重新选择")
                .font(.system(size: 10))
                .foregroundStyle(.secondary)
        }
        .padding(12)
    }

    private func tag(_ s: String) -> some View {
        Text(s)
            .font(.system(size: 10, weight: .medium))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(Capsule().fill(Color.primary.opacity(0.06)))
    }

    // ── 预览卡片 ──
    private var previewCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            cardTitle("效果预览", icon: "eye")

            ZStack {
                RoundedRectangle(cornerRadius: Theme.dropRadius, style: .continuous)
                    .fill(Color.white)
                RoundedRectangle(cornerRadius: Theme.dropRadius, style: .continuous)
                    .strokeBorder(Color.primary.opacity(0.10))

                if let p = model.prepared {
                    Image(decorative: p.cgImage, scale: 1)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .padding(10)
                } else {
                    VStack(spacing: 8) {
                        Image(systemName: "photo")
                            .font(.system(size: 28, weight: .light))
                            .foregroundStyle(.tertiary)
                        Text("选图后这里显示 618×927 的成品")
                            .font(.system(size: 11))
                            .foregroundStyle(.tertiary)
                    }
                }
            }
            .frame(height: 232)

            HStack(spacing: 6) {
                Image(systemName: "info.circle")
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
                Text(model.fit.hint)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .card()
        .frame(maxWidth: .infinity)
    }

    private func cardTitle(_ t: String, icon: String) -> some View {
        HStack(spacing: 7) {
            Image(systemName: icon)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.accent)
            Text(t)
                .font(.system(size: 13, weight: .semibold))
        }
    }

    // ── 适配方式 ──
    private var fitRow: some View {
        HStack(spacing: 14) {
            Text("适配方式")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.secondary)

            Picker("", selection: Binding(
                get: { model.fit },
                set: { model.fit = $0; model.reprepare() }
            )) {
                ForEach(FitMode.allCases) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 230)

            Spacer()

            Stepper(value: $model.maxMinutes, in: 15...600, step: 15) {
                Text("最长 \(model.maxMinutes) 分钟")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
            }
            .frame(width: 190)
        }
    }

    // ── 启动 / 停止 ──
    private var actionBar: some View {
        HStack(spacing: 14) {
            if model.proxy.isRunning {
                Button(action: model.stopProxy) {
                    HStack(spacing: 8) {
                        Image(systemName: "stop.fill")
                        Text(model.proxy.phase == .stopping ? "正在停止…" : "停止代理")
                            .font(.system(size: 15, weight: .semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 46)
                    .foregroundStyle(.white)
                    .background(
                        RoundedRectangle(cornerRadius: 23, style: .continuous)
                            .fill(Theme.danger)
                    )
                }
                .buttonStyle(.plain)
                .disabled(model.proxy.phase == .stopping)
            } else {
                Button(action: model.startProxy) {
                    HStack(spacing: 8) {
                        Image(systemName: "bolt.fill")
                        Text(model.proxy.phase == .starting ? "正在启动…" : "启动代理")
                            .font(.system(size: 15, weight: .bold))
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 46)
                    .foregroundStyle(.white)
                    .background(
                        RoundedRectangle(cornerRadius: 23, style: .continuous)
                            .fill(model.canStart
                                  ? LinearGradient(colors: [Theme.accent, Theme.accentDeep],
                                                   startPoint: .top, endPoint: .bottom)
                                  : LinearGradient(colors: [.gray.opacity(0.5), .gray.opacity(0.5)],
                                                   startPoint: .top, endPoint: .bottom))
                    )
                    .shadow(color: model.canStart ? Theme.accent.opacity(0.35) : .clear, radius: 8, y: 3)
                }
                .buttonStyle(.plain)
                .disabled(!model.canStart)
            }

            Button {
                model.showLog.toggle()
            } label: {
                Image(systemName: model.showLog ? "chevron.down.circle" : "terminal")
                    .font(.system(size: 15))
                    .frame(width: 46, height: 46)
                    .foregroundStyle(.secondary)
                    .background(
                        RoundedRectangle(cornerRadius: 23, style: .continuous)
                            .fill(Color.primary.opacity(0.06))
                    )
            }
            .buttonStyle(.plain)
            .help("显示 / 隐藏运行日志")
        }
    }

    // ── 日志 ──
    private var logPanel: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Label("运行日志", systemImage: "terminal")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Text(model.proxy.phase.title)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                Button("清空") { model.proxy.clearLog() }
                    .buttonStyle(.plain)
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.accent)
            }
            .padding(.horizontal, 12).padding(.vertical, 8)

            Divider()

            ScrollViewReader { sp in
                ScrollView {
                    Text(model.proxy.log.isEmpty ? "（还没有输出）" : model.proxy.log)
                        .font(.system(size: 11, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .id("logBottom")
                }
                .frame(height: 190)
                .background(Color(nsColor: .textBackgroundColor).opacity(0.5))
                .onAppear {
                    // 日志早就产生完了、用户这时候才点开面板 —— onChange 不会触发，
                    // 得自己先滚到底，否则看到的是最老的几行。
                    sp.scrollTo("logBottom", anchor: .bottom)
                }
                .onChange(of: model.proxy.log) { _, _ in
                    sp.scrollTo("logBottom", anchor: .bottom)
                }
            }
        }
        .background(
            RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                .fill(Color(nsColor: .controlBackgroundColor))
        )
        .overlay(
            RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.09))
        )
    }

    // ── 成功横幅 ──
    private func resultBanner(_ url: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 22))
                .foregroundStyle(Theme.accent)
            VStack(alignment: .leading, spacing: 2) {
                Text("替换成功，服务端存下的图")
                    .font(.system(size: 12, weight: .semibold))
                Text(url)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(1).truncationMode(.middle)
                    .textSelection(.enabled)
            }
            Spacer()
            Button("打开") { if let u = URL(string: url) { NSWorkspace.shared.open(u) } }
                .buttonStyle(.borderless)
                .foregroundStyle(Theme.accent)
            Button("复制") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(url, forType: .string)
            }
            .buttonStyle(.borderless)
            .foregroundStyle(Theme.accent)
        }
        .card(padding: 14)
    }
}
