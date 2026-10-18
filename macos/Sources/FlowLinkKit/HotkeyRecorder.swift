import CFlowLinkCore
import SwiftUI

/// Sheet that records a new toggle hotkey (modifiers + key).
public struct HotkeyRecorderSheet: View {
    public let current: FLHotkey?
    public let onRecord: (UInt8, UInt16) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var hint = "Press the new combination"

    public init(current: FLHotkey?, onRecord: @escaping (UInt8, UInt16) -> Void) {
        self.current = current
        self.onRecord = onRecord
    }

    public var body: some View {
        VStack(spacing: 14) {
            Text("Toggle hotkey")
                .font(.system(.title3, weight: .semibold))
            Text("Press the key combination that should switch control between computers. It must include at least one modifier.")
                .font(.system(.caption))
                .foregroundStyle(DS.textDim)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)

            KeyCaptureView { keyCode, flags in
                guard let usage = KeyCodes.hid(fromCGKeyCode: keyCode) else { return }
                let modifiers = KeyCodes.modifiers(from: flags)
                guard modifiers != 0 else {
                    hint = "Include ⌃ ⌥ ⇧ or ⌘"
                    return
                }
                onRecord(modifiers, usage)
                dismiss()
            } onEscape: {
                dismiss()
            }
            .frame(height: 74)
            .frame(maxWidth: .infinity)
            .background(DS.card, in: RoundedRectangle(cornerRadius: DS.radius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: DS.radius, style: .continuous)
                    .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [5]))
                    .foregroundStyle(DS.accent.opacity(0.6))
            )

            Text(hint)
                .font(.system(.callout))
                .foregroundStyle(DS.textDim)

            if let current {
                HStack(spacing: 6) {
                    Text("Current:").foregroundStyle(DS.textDim).font(.system(.caption))
                    KeyCap(KeyCodes.hotkeyDisplay(modifiers: current.modifiers, usage: current.usage))
                }
            }

            HStack {
                Button("Cancel") { dismiss() }
                    .buttonStyle(SecondaryButtonStyle())
            }
        }
        .padding(24)
        .frame(width: 380)
        .background(DS.bg)
    }
}

/// A focusable view that reports the first key combination pressed.
struct KeyCaptureView: NSViewRepresentable {
    let onKey: (UInt16, CGEventFlags) -> Void
    let onEscape: () -> Void

    func makeNSView(context: Context) -> CaptureNSView {
        let view = CaptureNSView()
        view.onKey = onKey
        view.onEscape = onEscape
        return view
    }

    func updateNSView(_ nsView: CaptureNSView, context: Context) {}

    final class CaptureNSView: NSView {
        var onKey: ((UInt16, CGEventFlags) -> Void)?
        var onEscape: (() -> Void)?

        override var acceptsFirstResponder: Bool { true }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            window?.makeFirstResponder(self)
        }

        override func keyDown(with event: NSEvent) {
            if event.keyCode == 53 { // escape
                onEscape?()
                return
            }
            // NSEvent.ModifierFlags and CGEventFlags share raw bit values.
            let flags = CGEventFlags(rawValue: UInt64(event.modifierFlags.rawValue))
            onKey?(event.keyCode, flags)
        }
    }
}
