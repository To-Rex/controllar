import SwiftUI

/// The main window: custom sidebar + content.
public struct MainWindow: View {
    @ObservedObject var model: AppModel
    @State private var page: Page = .dashboard

    public enum Page: String, CaseIterable, Identifiable {
        case dashboard
        case settings
        case logs

        public var id: String { rawValue }

        var title: String {
            switch self {
            case .dashboard: return "Dashboard"
            case .settings: return "Settings"
            case .logs: return "Logs"
            }
        }

        var symbol: String {
            switch self {
            case .dashboard: return "rectangle.on.rectangle"
            case .settings: return "gearshape"
            case .logs: return "doc.text.magnifyingglass"
            }
        }
    }

    public init(model: AppModel) {
        self.model = model
    }

    public var body: some View {
        Group {
            if !model.onboarded {
                OnboardingView(model: model)
            } else {
                HStack(spacing: 0) {
                    sidebar
                    Divider()
                    content
                }
            }
        }
        .frame(minWidth: 880, minHeight: 580)
        .background(DS.bg)
    }

    // MARK: Sidebar

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                GlyphBadge(size: 30)
                VStack(alignment: .leading, spacing: 1) {
                    Text("FlowLink")
                        .font(.system(.body, design: .rounded).weight(.bold))
                    Text("v\(model.status?.version ?? CoreVersion.string)")
                        .font(.system(.caption2))
                        .foregroundStyle(DS.textFaint)
                }
                Spacer()
            }
            .padding(.horizontal, 14)
            .padding(.top, 16)
            .padding(.bottom, 10)

            ForEach(Page.allCases) { item in
                sidebarButton(item)
            }

            Spacer()

            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    StatusPill(status: model.status)
                    Spacer()
                }
                if let peer = model.status?.peer, model.status?.isConnected == true {
                    HStack(spacing: 6) {
                        Image(systemName: "display")
                            .font(.system(size: 11))
                            .foregroundStyle(DS.textDim)
                        Text(peer.name)
                            .font(.system(.caption, weight: .medium))
                            .lineLimit(1)
                    }
                }
                HStack(spacing: 8) {
                    Picker("", selection: Binding(
                        get: { model.mode },
                        set: { model.setMode($0) }
                    )) {
                        Text("Server").tag(FLMode.server)
                        Text("Client").tag(FLMode.client)
                    }
                    .pickerStyle(.segmented)
                    .controlSize(.small)
                }
                if model.isRunning {
                    Button {
                        model.stopEngine()
                    } label: {
                        Label("Stop", systemImage: "stop.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(SecondaryButtonStyle())
                } else {
                    Button {
                        model.startEngine()
                    } label: {
                        Label("Start", systemImage: "play.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(PrimaryButtonStyle())
                }
            }
            .padding(14)
            .background(DS.card.opacity(0.6), in: RoundedRectangle(cornerRadius: DS.radius, style: .continuous))
            .padding(12)
        }
        .frame(width: 208)
        .background(DS.sidebar)
    }

    private func sidebarButton(_ item: Page) -> some View {
        let selected = page == item
        return Button {
            page = item
        } label: {
            HStack(spacing: 9) {
                Image(systemName: item.symbol)
                    .font(.system(size: 13, weight: .medium))
                    .frame(width: 18)
                Text(item.title)
                    .font(.system(.callout, weight: selected ? .semibold : .regular))
                Spacer()
            }
            .foregroundStyle(selected ? DS.text : DS.textDim)
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(
                selected ? DS.card : Color.clear,
                in: RoundedRectangle(cornerRadius: 9, style: .continuous)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .stroke(selected ? DS.border : .clear, lineWidth: 1)
            )
            .contentShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 8)
    }

    // MARK: Content

    @ViewBuilder
    private var content: some View {
        switch page {
        case .dashboard: DashboardView(model: model)
        case .settings: SettingsView(model: model)
        case .logs: LogsView(model: model)
        }
    }
}
