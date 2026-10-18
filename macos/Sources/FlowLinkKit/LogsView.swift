import AppKit
import SwiftUI

public struct LogsView: View {
    @ObservedObject var model: AppModel

    @State private var text = ""
    @State private var autoRefresh = true

    private let refresh = Timer.publish(every: 3, on: .main, in: .common).autoconnect()

    public init(model: AppModel) {
        self.model = model
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("Logs")
                    .font(.system(size: 22, weight: .semibold, design: .rounded))
                Spacer()
                Toggle("Auto-refresh", isOn: $autoRefresh)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                Button("Refresh") { reload() }
                    .buttonStyle(SecondaryButtonStyle())
                Button("Open folder") {
                    if let dir = model.logsDirectory() {
                        NSWorkspace.shared.open(URL(fileURLWithPath: dir))
                    }
                }
                .buttonStyle(SecondaryButtonStyle())
            }

            ScrollViewReader { proxy in
                ScrollView {
                    Text(text.isEmpty ? "No log entries yet." : text)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(DS.text)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                        .id("log-end")
                }
                .background(DS.card, in: RoundedRectangle(cornerRadius: DS.radius, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: DS.radius, style: .continuous)
                        .stroke(DS.border, lineWidth: 1)
                )
                .onChange(of: text) { _ in
                    proxy.scrollTo("log-end", anchor: .bottom)
                }
            }
        }
        .padding(24)
        .frame(maxWidth: 900, maxHeight: .infinity, alignment: .topLeading)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .onAppear { reload() }
        .onReceive(refresh) { _ in
            if autoRefresh { reload() }
        }
    }

    private func reload() {
        text = model.recentLogs(maxLines: 500)
    }
}
