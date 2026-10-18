import AppKit
import SwiftUI

/// The menu bar popover.
public struct MenuBarContent: View {
    @ObservedObject var model: AppModel
    @Environment(\.openWindow) private var openWindow

    public init(model: AppModel) {
        self.model = model
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                GlyphBadge(size: 26)
                VStack(alignment: .leading, spacing: 1) {
                    Text("FlowLink")
                        .font(.system(.callout, design: .rounded).weight(.bold))
                    Text(model.statusHeadline)
                        .font(.system(.caption))
                        .foregroundStyle(DS.textDim)
                        .lineLimit(1)
                }
                Spacer()
                StatusPill(status: model.status)
            }

            if let peer = model.status?.peer, model.status?.isConnected == true {
                HStack(spacing: 8) {
                    Image(systemName: "display")
                        .font(.system(size: 12))
                        .foregroundStyle(DS.accent)
                    Text(peer.name)
                        .font(.system(.callout, weight: .medium))
                        .lineLimit(1)
                    Spacer()
                    if let metrics = model.status?.metrics {
                        Text(String(format: "%.0f ms", metrics.rttMs))
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(DS.textDim)
                    }
                }
                .padding(9)
                .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous))
            }

            if model.mode == .server, model.status?.isConnected != true,
               let code = model.status?.pairing?.formattedCode {
                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text("PAIRING CODE")
                            .font(.system(size: 9, weight: .semibold))
                            .tracking(1)
                            .foregroundStyle(DS.textFaint)
                        Text(code)
                            .font(.system(.title3, design: .monospaced).weight(.bold))
                    }
                    Spacer()
                    Button("QR") {
                        openWindow(id: "main")
                        NSApp.activate(ignoringOtherApps: true)
                    }
                    .buttonStyle(SecondaryButtonStyle())
                }
                .padding(9)
                .background(DS.accentSoft, in: RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous))
            }

            Divider()

            if model.mode == .server {
                quickRow(
                    title: model.status?.remoteActive == true
                        ? "Return control to this Mac"
                        : "Take control of \(model.status?.peer?.name ?? "the other computer")",
                    symbol: model.status?.remoteActive == true ? "arrow.uturn.backward" : "cursorarrow.rays",
                    enabled: model.isConnected
                ) {
                    model.bridge.toggleControl()
                }
            } else if model.isConnected, model.status?.remoteActive == true {
                quickRow(title: "Take control back", symbol: "arrow.uturn.backward", enabled: true) {
                    model.bridge.requestControl()
                }
            }

            if model.isRunning {
                quickRow(title: "Stop sharing", symbol: "stop.fill", enabled: true) {
                    model.stopEngine()
                }
            } else {
                quickRow(title: "Start sharing", symbol: "play.fill", enabled: true) {
                    model.startEngine()
                }
            }

            quickRow(
                title: "Switch to \(model.mode == .server ? "Client" : "Server") mode",
                symbol: "arrow.left.arrow.right",
                enabled: true
            ) {
                model.setMode(model.mode == .server ? .client : .server)
            }

            Divider()

            quickRow(title: "Open FlowLink", symbol: "macwindow", enabled: true) {
                openWindow(id: "main")
                NSApp.activate(ignoringOtherApps: true)
            }
            quickRow(title: "Quit FlowLink", symbol: "power", enabled: true) {
                NSApp.terminate(nil)
            }
        }
        .padding(14)
        .frame(width: 320)
        .background(DS.bg)
    }

    private func quickRow(
        title: String,
        symbol: String,
        enabled: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 9) {
                Image(systemName: symbol)
                    .font(.system(size: 12, weight: .medium))
                    .frame(width: 16)
                Text(title)
                    .font(.system(.callout))
                    .lineLimit(1)
                Spacer()
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(MenuRowStyle(enabled: enabled))
        .disabled(!enabled)
    }
}

private struct MenuRowStyle: ButtonStyle {
    let enabled: Bool

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(enabled ? DS.text : DS.textFaint)
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
            .background(
                configuration.isPressed && enabled ? DS.hover : .clear,
                in: RoundedRectangle(cornerRadius: 7, style: .continuous)
            )
    }
}
