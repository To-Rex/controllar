import SwiftUI

/// Renders a QR matrix (from `CoreBridge.pairingQR()`): white card, black
/// modules, deliberately crisp (module size floored to whole points).
public struct QRCodeView: View {
    public let size: Int
    public let modules: [UInt8]

    public init(size: Int, modules: [UInt8]) {
        self.size = size
        self.modules = modules
    }

    public var body: some View {
        Canvas { context, canvasSize in
            let count = CGFloat(max(size, 1))
            let module = floor(min(canvasSize.width, canvasSize.height) / count)
            guard module >= 1 else { return }
            let total = module * count
            let offsetX = (canvasSize.width - total) / 2
            let offsetY = (canvasSize.height - total) / 2
            for y in 0..<size {
                for x in 0..<size {
                    guard y * size + x < modules.count, modules[y * size + x] == 1 else { continue }
                    let rect = CGRect(
                        x: offsetX + CGFloat(x) * module,
                        y: offsetY + CGFloat(y) * module,
                        width: module,
                        height: module
                    )
                    context.fill(Path(rect), with: .color(.black))
                }
            }
        }
        .aspectRatio(1, contentMode: .fit)
        .padding(12)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(DS.border, lineWidth: 1))
    }
}

/// The pairing sheet: QR code + code + invite link, with a refresh timer so a
/// regenerated code is reflected immediately.
public struct PairingSheet: View {
    @ObservedObject public var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var matrix: (size: Int, modules: [UInt8])?
    @State private var copied = false

    private let refresh = Timer.publish(every: 2, on: .main, in: .common).autoconnect()

    public init(model: AppModel) {
        self.model = model
    }

    public var body: some View {
        VStack(spacing: 16) {
            HStack {
                Text("Pair a computer")
                    .font(.system(.title3, weight: .semibold))
                Spacer()
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(QuietButtonStyle())
            }

            if let matrix {
                QRCodeView(size: matrix.size, modules: matrix.modules)
                    .frame(width: 260, height: 260)
            } else {
                RoundedRectangle(cornerRadius: 14)
                    .fill(DS.cardAlt)
                    .frame(width: 260, height: 260)
                    .overlay(ProgressView())
            }

            if let code = model.status?.pairing?.formattedCode {
                VStack(spacing: 4) {
                    Text("PAIRING CODE")
                        .font(.system(.caption2, weight: .semibold))
                        .tracking(1.2)
                        .foregroundStyle(DS.textFaint)
                    Text(code)
                        .font(DS.monoLarge)
                        .foregroundStyle(DS.text)
                        .textSelection(.enabled)
                    if let remaining = model.status?.pairing?.expiresInMs, remaining > 0 {
                        Text("expires in \(Int(remaining / 1000))s")
                            .font(.system(.caption))
                            .foregroundStyle(DS.textDim)
                    }
                }
            } else {
                Text("No active code — generate one")
                    .font(.system(.callout))
                    .foregroundStyle(DS.textDim)
            }

            if let link = model.inviteLink() {
                HStack(spacing: 8) {
                    Text(link)
                        .font(DS.monoSmall)
                        .foregroundStyle(DS.textDim)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer()
                    Button(copied ? "Copied" : "Copy link") {
                        let pasteboard = NSPasteboard.general
                        pasteboard.clearContents()
                        pasteboard.setString(link, forType: .string)
                        copied = true
                        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
                    }
                    .buttonStyle(SecondaryButtonStyle())
                }
                .padding(10)
                .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous))
            }

            Text("Scan this code with FlowLink on the other computer — or type the 6-digit code there.")
                .font(.system(.caption))
                .foregroundStyle(DS.textDim)
                .multilineTextAlignment(.center)

            Button("Generate a new code") {
                model.regenerateCode()
                refreshMatrix()
            }
            .buttonStyle(SecondaryButtonStyle())
        }
        .padding(24)
        .frame(width: 400)
        .background(DS.bg)
        .onAppear { refreshMatrix() }
        .onReceive(refresh) { _ in refreshMatrix() }
    }

    private func refreshMatrix() {
        let new = model.qrMatrix()
        if let new, matrix?.size != new.size || matrix?.modules != new.modules {
            matrix = new
        }
    }
}
