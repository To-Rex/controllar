import CFlowLinkCore
import Foundation

/// Receives core callbacks. Set the closures; the bridge keeps the proxy
/// alive for the core's lifetime.
public final class CoreHostProxy {
    public var onStatus: ((String) -> Void)?
    public var onInput: ((FlInputEvent) -> Void)?
    public var onClipboard: ((String) -> Void)?
    public var onNotice: ((String) -> Void)?
    public init() {}
}

// MARK: - C callback trampolines (non-capturing, thread: core runtime)

private func bridgeStatus(_ userData: UnsafeMutableRawPointer?, _ json: UnsafePointer<CChar>?) {
    guard let userData, let json else { return }
    let proxy = Unmanaged<CoreHostProxy>.fromOpaque(userData).takeUnretainedValue()
    proxy.onStatus?(String(cString: json))
}

private func bridgeInput(_ userData: UnsafeMutableRawPointer?, _ event: UnsafePointer<FlInputEvent>?) {
    guard let userData, let event else { return }
    let proxy = Unmanaged<CoreHostProxy>.fromOpaque(userData).takeUnretainedValue()
    proxy.onInput?(event.pointee)
}

private func bridgeClipboard(_ userData: UnsafeMutableRawPointer?, _ text: UnsafePointer<CChar>?) {
    guard let userData, let text else { return }
    let proxy = Unmanaged<CoreHostProxy>.fromOpaque(userData).takeUnretainedValue()
    proxy.onClipboard?(String(cString: text))
}

private func bridgeNotice(_ userData: UnsafeMutableRawPointer?, _ json: UnsafePointer<CChar>?) {
    guard let userData, let json else { return }
    let proxy = Unmanaged<CoreHostProxy>.fromOpaque(userData).takeUnretainedValue()
    proxy.onNotice?(String(cString: json))
}

/// Swift wrapper around the C ABI in `flowlink.h`.
public final class CoreBridge {
    public private(set) var handle: OpaquePointer?
    private let proxy: CoreHostProxy

    public init(configDir: String, proxy: CoreHostProxy) {
        self.proxy = proxy

        var callbacks = FlHostCallbacks(
            user_data: Unmanaged.passUnretained(proxy).toOpaque(),
            on_status: bridgeStatus,
            on_input: bridgeInput,
            on_clipboard: bridgeClipboard,
            on_notice: bridgeNotice,
            on_release: nil
        )
        self.handle = configDir.withCString { path in
            flowlink_new(path, &callbacks)
        }
    }

    deinit {
        if let handle {
            flowlink_free(handle)
        }
    }

    public var isRunning: Bool { handle != nil }

    // MARK: Commands

    public func start() { withHandle { flowlink_start($0) } }
    public func stop() { withHandle { flowlink_stop($0) } }

    public func setMode(_ mode: FLMode) {
        withHandle { flowlink_set_mode($0, mode == .client ? FLModeC.client : FLModeC.server) }
    }

    public func connect(host: String, port: UInt16, code: String?, fingerprint: String?) {
        withHandle { handle in
            host.withCString { hostPtr in
                if let code, let fingerprint {
                    code.withCString { codePtr in
                        fingerprint.withCString { fpPtr in
                            _ = flowlink_connect(handle, hostPtr, port, codePtr, fpPtr)
                        }
                    }
                } else if let code {
                    code.withCString { codePtr in
                        _ = flowlink_connect(handle, hostPtr, port, codePtr, nil)
                    }
                } else if let fingerprint {
                    fingerprint.withCString { fpPtr in
                        _ = flowlink_connect(handle, hostPtr, port, nil, fpPtr)
                    }
                } else {
                    _ = flowlink_connect(handle, hostPtr, port, nil, nil)
                }
            }
        }
    }

    public func connectLink(_ link: String) {
        withHandle { handle in
            link.withCString { _ = flowlink_connect_link(handle, $0) }
        }
    }

    public func disconnect() { withHandle { flowlink_disconnect($0) } }

    public func submitPairCode(_ code: String) {
        withHandle { handle in
            code.withCString { flowlink_submit_pair_code(handle, $0) }
        }
    }

    public func regeneratePairCode() { withHandle { flowlink_regenerate_pair_code($0) } }

    public func forgetDevice(_ id: String) {
        withHandle { handle in
            id.withCString { flowlink_forget_device(handle, $0) }
        }
    }

    /// Forward captured input to the engine.
    public func sendInput(_ input: FlHostInput) {
        withHandle { handle in
            var mutable = input
            flowlink_send_input(handle, &mutable)
        }
    }

    /// Client role: return control to the server (edge push / hotkey).
    public func requestControl() { withHandle { flowlink_request_control($0) } }

    /// Server role: toggle driving the remote device.
    public func toggleControl() { withHandle { flowlink_toggle_control($0) } }

    public func setClipboardText(_ text: String) {
        withHandle { handle in
            text.withCString { flowlink_set_clipboard_text(handle, $0) }
        }
    }

    public func setDeviceName(_ name: String) {
        withHandle { handle in
            name.withCString { flowlink_set_device_name(handle, $0) }
        }
    }

    /// Report whether the local input source (event tap / hook) is capturing.
    /// The core logs transitions so a dead tap can never look like a dead
    /// hotkey; `reason` is only used while `active` is false.
    public func setInputCapture(active: Bool, reason: String?) {
        withHandle { handle in
            (reason ?? "").withCString {
                flowlink_set_input_capture(handle, active ? 1 : 0, $0)
            }
        }
    }

    public func setEdge(_ edge: FLEdge) {
        let value: Int32
        switch edge {
        case .left: value = FLEdgeC.left
        case .right: value = FLEdgeC.right
        case .top: value = FLEdgeC.top
        case .bottom: value = FLEdgeC.bottom
        }
        withHandle { flowlink_set_edge($0, value) }
    }

    public func setClipboardEnabled(_ enabled: Bool) {
        withHandle { flowlink_set_clipboard_enabled($0, enabled ? 1 : 0) }
    }

    public func setHotkey(modifiers: UInt8, usage: UInt16) {
        let json = "{\"ctrl\":\(modifiers & FLModC.ctrl != 0),\"alt\":\(modifiers & FLModC.alt != 0),\"shift\":\(modifiers & FLModC.shift != 0),\"meta\":\(modifiers & FLModC.meta != 0),\"usage\":\(usage)}"
        withHandle { handle in
            json.withCString { _ = flowlink_set_hotkey(handle, $0) }
        }
    }

    public func setListenPort(_ port: UInt16) {
        withHandle { flowlink_set_listen_port($0, port) }
    }

    public func setLocalScreen(width: Double, height: Double, scale: Double) {
        withHandle { flowlink_set_local_screen($0, width, height, scale) }
    }

    // MARK: Queries

    public func statusJSON() -> String? { stringQuery { flowlink_status_json($0) } }
    public func configJSON() -> String? { stringQuery { flowlink_config_json($0) } }
    public func inviteLink() -> String? { stringQuery { flowlink_invite_link($0) } }
    public func logsDir() -> String? { stringQuery { flowlink_logs_dir($0) } }

    public func tailLogs(maxLines: Int) -> String? {
        guard let handle else { return nil }
        guard let ptr = flowlink_tail_logs(handle, Int32(maxLines)) else { return nil }
        defer { flowlink_free_string(ptr) }
        return String(cString: ptr)
    }

    /// QR matrix of the current invite link: `size × size` modules (1 = dark).
    public func pairingQR() -> (size: Int, modules: [UInt8])? {
        guard let handle else { return nil }
        var size: Int32 = 0
        guard let bytes = flowlink_pairing_qr_matrix(handle, &size), size > 0 else { return nil }
        let count = Int(size) * Int(size)
        let buffer = UnsafeBufferPointer(start: bytes, count: count)
        let modules = Array(buffer)
        flowlink_free_bytes(bytes, count)
        return (Int(size), modules)
    }

    // MARK: Helpers

    private func withHandle(_ body: (OpaquePointer) -> Void) {
        guard let handle else { return }
        body(handle)
    }

    private func stringQuery(_ body: (OpaquePointer) -> UnsafeMutablePointer<CChar>?) -> String? {
        guard let handle else { return nil }
        guard let ptr = body(handle) else { return nil }
        defer { flowlink_free_string(ptr) }
        return String(cString: ptr)
    }
}
