import AVFoundation
import SwiftUI

/// Camera QR scanner sheet (used by the client to scan the server's QR code).
public struct QRScannerSheet: View {
    public let onCode: (String) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var authorization = AVCaptureDevice.authorizationStatus(for: .video)

    public init(onCode: @escaping (String) -> Void) {
        self.onCode = onCode
    }

    public var body: some View {
        VStack(spacing: 14) {
            HStack {
                Text("Scan pairing QR code")
                    .font(.system(.title3, weight: .semibold))
                Spacer()
                Button { dismiss() } label: { Image(systemName: "xmark") }
                    .buttonStyle(QuietButtonStyle())
            }

            switch authorization {
            case .authorized:
                ScannerPreview(onCode: { code in
                    onCode(code)
                    dismiss()
                })
                .frame(width: 400, height: 300)
                .clipShape(RoundedRectangle(cornerRadius: DS.radius, style: .continuous))
            case .notDetermined:
                stateCard(
                    symbol: "camera",
                    title: "Camera access needed",
                    message: "FlowLink uses the camera only to scan a pairing QR code.",
                    buttonTitle: "Allow camera access"
                ) {
                    AVCaptureDevice.requestAccess(for: .video) { granted in
                        DispatchQueue.main.async {
                            authorization = granted ? .authorized : .denied
                        }
                    }
                }
            default:
                stateCard(
                    symbol: "camera.badge.ellipsis",
                    title: "Camera is off",
                    message: "Enable camera access for FlowLink in System Settings → Privacy & Security → Camera.",
                    buttonTitle: "Open System Settings"
                ) {
                    if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera") {
                        NSWorkspace.shared.open(url)
                    }
                }
            }

            Text("Point the camera at the QR code shown in FlowLink on the other computer.")
                .font(.system(.caption))
                .foregroundStyle(DS.textDim)
        }
        .padding(24)
        .frame(width: 450)
        .background(DS.bg)
    }

    private func stateCard(
        symbol: String,
        title: String,
        message: String,
        buttonTitle: String,
        action: @escaping () -> Void
    ) -> some View {
        VStack(spacing: 10) {
            Image(systemName: symbol)
                .font(.system(size: 28))
                .foregroundStyle(DS.textFaint)
            Text(title).font(.system(.callout, weight: .semibold))
            Text(message)
                .font(.system(.caption))
                .foregroundStyle(DS.textDim)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)
            Button(buttonTitle, action: action)
                .buttonStyle(PrimaryButtonStyle())
        }
        .frame(maxWidth: .infinity)
        .frame(height: 220)
        .background(DS.card, in: RoundedRectangle(cornerRadius: DS.radius, style: .continuous))
    }
}

// MARK: - AVFoundation preview

struct ScannerPreview: NSViewRepresentable {
    let onCode: (String) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onCode: onCode)
    }

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        view.wantsLayer = true
        context.coordinator.start(preview: view)
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {}

    static func dismantleNSView(_ nsView: NSView, coordinator: Coordinator) {
        coordinator.stop()
    }

    final class Coordinator: NSObject, AVCaptureMetadataOutputObjectsDelegate {
        private let session = AVCaptureSession()
        private let onCode: (String) -> Void
        private let queue = DispatchQueue(label: "app.flowlink.scanner")
        private var handled = false

        init(onCode: @escaping (String) -> Void) {
            self.onCode = onCode
        }

        func start(preview: NSView) {
            guard !session.isRunning else { return }
            guard let device = AVCaptureDevice.default(for: .video),
                  let input = try? AVCaptureDeviceInput(device: device),
                  session.canAddInput(input)
            else { return }
            session.addInput(input)

            let output = AVCaptureMetadataOutput()
            guard session.canAddOutput(output) else { return }
            session.addOutput(output)
            output.setMetadataObjectsDelegate(self, queue: DispatchQueue.main)
            if output.availableMetadataObjectTypes.contains(.qr) {
                output.metadataObjectTypes = [.qr]
            }

            let layer = AVCaptureVideoPreviewLayer(session: session)
            layer.videoGravity = .resizeAspectFill
            layer.frame = preview.bounds
            layer.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
            preview.layer?.addSublayer(layer)
            preview.layer?.backgroundColor = NSColor.black.cgColor

            queue.async { [weak self] in
                self?.session.startRunning()
            }
        }

        func stop() {
            queue.async { [weak self] in
                guard let self, self.session.isRunning else { return }
                self.session.stopRunning()
            }
        }

        func metadataOutput(
            _ output: AVCaptureMetadataOutput,
            didOutput metadataObjects: [AVMetadataObject],
            from connection: AVCaptureConnection
        ) {
            guard !handled else { return }
            for object in metadataObjects where object.type == .qr {
                guard let readable = object as? AVMetadataMachineReadableCodeObject,
                      let value = readable.stringValue
                else { continue }
                handled = true
                onCode(value)
            }
        }
    }
}
