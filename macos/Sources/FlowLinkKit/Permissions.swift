import AppKit
import ApplicationServices
import Foundation

/// Tracks and requests the two macOS privacy permissions FlowLink needs.
public final class Permissions: ObservableObject {

    @Published public private(set) var accessibility: Bool = false
    @Published public private(set) var inputMonitoring: Bool = false
    @Published public private(set) var screenRecordingNotNeeded: Bool = true

    private var timer: Timer?

    public init() {
        refresh()
    }

    /// True when everything needed for full operation is granted.
    public var allGranted: Bool { accessibility && inputMonitoring }

    public func startMonitoring() {
        stopMonitoring()
        let timer = Timer(timeInterval: 1.5, repeats: true) { [weak self] _ in
            self?.refresh()
        }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    public func stopMonitoring() {
        timer?.invalidate()
        timer = nil
    }

    public func refresh() {
        let ax = AXIsProcessTrusted()
        let listen = CGPreflightListenEventAccess()
        DispatchQueue.main.async {
            if self.accessibility != ax { self.accessibility = ax }
            if self.inputMonitoring != listen { self.inputMonitoring = listen }
        }
    }

    /// Prompt for Accessibility (posting events / event taps).
    public func requestAccessibility() {
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        let options = [key: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
        refresh()
    }

    /// Prompt for Input Monitoring (listening to raw events).
    public func requestInputMonitoring() {
        _ = CGRequestListenEventAccess()
        refresh()
    }

    public func openPrivacySettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") {
            NSWorkspace.shared.open(url)
        }
    }
}
