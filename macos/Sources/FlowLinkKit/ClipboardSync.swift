import AppKit
import Foundation

/// Polls `NSPasteboard.general` and mirrors text changes to/from the core,
/// with echo suppression so the two sides don't loop.
public final class ClipboardSync {

    /// Called when the *local* pasteboard changed and should be sent.
    public var onLocalChange: ((String) -> Void)?

    private var timer: Timer?
    private var lastChangeCount = -1
    private var lastAppliedHash: Int?
    /// Mirrors the core's `clipboard_max_bytes`; the core enforces the same
    /// limit on both directions, this only avoids reading pathologically
    /// huge pasteboard payloads. Always capped at 32 MiB for safety.
    public var maxBytes = 1 << 20 {
        didSet { maxBytes = min(max(maxBytes, 16), 32 << 20) }
    }

    public init() {}

    public func start() {
        stop()
        lastChangeCount = NSPasteboard.general.changeCount
        let timer = Timer(timeInterval: 0.4, repeats: true) { [weak self] _ in
            self?.poll()
        }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    public func stop() {
        timer?.invalidate()
        timer = nil
    }

    /// Apply text received from the peer.
    public func applyFromPeer(_ text: String) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(text, forType: .string)
        lastChangeCount = pasteboard.changeCount
        lastAppliedHash = text.hashValue
    }

    private func poll() {
        let pasteboard = NSPasteboard.general
        guard pasteboard.changeCount != lastChangeCount else { return }
        lastChangeCount = pasteboard.changeCount

        // Never relay password-manager / transient clipboard content.
        let types = Set(pasteboard.types ?? [])
        let blocked = [
            NSPasteboard.PasteboardType("org.nspasteboard.ConcealedType"),
            NSPasteboard.PasteboardType("org.nspasteboard.TransientType"),
            NSPasteboard.PasteboardType("com.agilebits.onepassword"),
        ]
        if types.contains(where: { blocked.contains($0) }) { return }

        guard let text = pasteboard.string(forType: .string) else { return }
        guard !text.isEmpty else { return }
        if let lastAppliedHash, lastAppliedHash == text.hashValue {
            // Echo of what we just applied from the peer.
            self.lastAppliedHash = nil
            return
        }
        guard text.utf8.count <= maxBytes else { return }
        onLocalChange?(text)
    }
}
