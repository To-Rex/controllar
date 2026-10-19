import AppKit
import CFlowLinkCore
import Combine
import Foundation

/// The app's central state: owns the core bridge, drives capture/injection,
/// clipboard sync and permission monitoring, and publishes status to the UI.
public final class AppModel: ObservableObject {

    public static let shared = AppModel()

    // MARK: Published state

    @Published public private(set) var status: FLStatus?
    @Published public private(set) var notices: [FLNotice] = []
    @Published public private(set) var onboarded: Bool
    @Published public private(set) var lastError: String?
    @Published public var permissions = Permissions()

    // MARK: Collaborators

    public let bridge: CoreBridge
    public let injector = Injector()
    public let clipboard = ClipboardSync()

    private let proxy: CoreHostProxy
    private let tapShared = TapSharedState()
    private var tap: InputTap?
    private var tapRunning = false

    // MARK: Init

    private init() {
        let proxy = CoreHostProxy()
        self.proxy = proxy
        self.bridge = CoreBridge(configDir: AppModel.configDirectory(), proxy: proxy)
        self.onboarded = UserDefaults.standard.bool(forKey: "flowlink.onboarded")

        proxy.onStatus = { [weak self] json in
            self?.handleStatusJSON(json)
        }
        proxy.onInput = { [weak self] event in
            self?.injector.handle(event)
        }
        proxy.onClipboard = { [weak self] text in
            DispatchQueue.main.async { self?.clipboard.applyFromPeer(text) }
        }
        proxy.onNotice = { [weak self] json in
            DispatchQueue.main.async { self?.handleNoticeJSON(json) }
        }
        clipboard.onLocalChange = { [weak self] text in
            self?.bridge.setClipboardText(text)
        }
        // Nested ObservableObject: republish permission changes through the
        // model so any view reading `model.permissions.*` updates too.
        permissions.objectWillChange
            .sink { [weak self] _ in
                self?.objectWillChange.send()
                // Permissions may have just been granted: retry the tap on the
                // next runloop turn (values are updated by then) and tell the
                // core whether local capture is actually live.
                DispatchQueue.main.async { [weak self] in
                    self?.installTap()
                }
            }
            .store(in: &cancellables)
    }

    /// Human-readable reason shown (and logged by the core) while the macOS
    /// event tap is not capturing this computer's input.
    private func captureInactiveReason() -> String {
        if !permissions.accessibility {
            return "Accessibility permission is missing — FlowLink cannot capture this Mac's keyboard and mouse. System Settings → Privacy & Security → Accessibility → FlowLink: toggle it off and on again after every update."
        }
        if !permissions.inputMonitoring {
            return "Input Monitoring permission is missing — System Settings → Privacy & Security → Input Monitoring → FlowLink: toggle it off and on again after every update."
        }
        return "the macOS event tap could not be created — re-grant Accessibility to FlowLink, then quit and reopen the app."
    }

    private var tapRetryScheduled = false

    /// Install the event tap, retrying once after a short delay. Right after
    /// a relaunch the system may still hold the previous instance's tap slot,
    /// so a first failure is usually transient — the user is only told once
    /// the retry has failed too.
    private func installTap() {
        if tapRunning { return }
        guard permissions.allGranted, let tap else {
            bridge.setInputCapture(active: false, reason: captureInactiveReason())
            return
        }
        tapRunning = tap.start()
        if tapRunning {
            bridge.setInputCapture(active: true, reason: nil)
            return
        }
        guard !tapRetryScheduled else { return }
        tapRetryScheduled = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
            guard let self else { return }
            self.tapRetryScheduled = false
            if !self.tapRunning, let tap = self.tap, tap.start() {
                self.tapRunning = true
            }
            self.bridge.setInputCapture(
                active: self.tapRunning,
                reason: self.tapRunning ? nil : self.captureInactiveReason()
            )
        }
    }

    private var cancellables = Set<AnyCancellable>()

    // MARK: Lifecycle

    /// Location of the core's config/identity/logs.
    public static func configDirectory() -> String {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)
            .first ?? URL(fileURLWithPath: NSHomeDirectory())
        return base.appendingPathComponent("FlowLink", isDirectory: true).path
    }

    /// Called once at app launch.
    public func bootstrap() {
        guard bridge.isRunning else {
            lastError = "The FlowLink core failed to initialize — check the logs."
            return
        }
        permissions.startMonitoring()
        clipboard.start()
        publishScreenMetrics()

        let tap = InputTap(shared: tapShared)
        tap.onInput = { [weak self] input in
            self?.bridge.sendInput(input)
        }
        self.tap = tap
        installTap()

        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            self?.publishScreenMetrics()
            self?.injector.refreshMetrics()
        }

        if onboarded {
            bridge.start()
        }
    }

    public func shutdown() {
        clipboard.stop()
        tap?.stop()
        permissions.stopMonitoring()
        bridge.stop()
    }

    private func publishScreenMetrics() {
        let metrics = ScreenGeometry.unionMetrics()
        bridge.setLocalScreen(width: metrics.width, height: metrics.height, scale: metrics.scale)
        tapShared.update { $0.metrics = metrics }
    }

    // MARK: Status intake (called on main queue)

    private func handleStatusJSON(_ json: String) {
        guard let data = json.data(using: .utf8) else { return }
        guard let decoded = try? JSONDecoder().decode(FLStatus.self, from: data) else { return }
        DispatchQueue.main.async { [weak self] in
            self?.apply(decoded)
        }
    }

    private func apply(_ new: FLStatus) {
        let previous = status
        status = new
        if let limit = new.clipboardMaxBytes {
            clipboard.maxBytes = limit
        }

        tapShared.update { snapshot in
            snapshot.forwarding = new.isForwarding
            snapshot.hotkeyModifiers = new.hotkey.modifiers
            snapshot.hotkeyUsage = new.hotkey.usage
        }

        // Retry installing the tap once permissions are available.
        installTap()

        // The physical cursor is hidden while this side's input is forwarded
        // to the other computer; it reappears when focus comes back.
        let wasForwarding = previous?.isForwarding ?? false
        if new.isConnected && new.isForwarding && !wasForwarding {
            injector.hideCursor()
        } else if !new.isForwarding && wasForwarding {
            injector.showCursor()
        }
        if !new.isConnected && wasForwarding {
            injector.showCursor()
        }
    }

    private func handleNoticeJSON(_ json: String) {
        guard let data = json.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return }
        let kind = object["kind"] as? String ?? "info"
        let message = object["message"] as? String ?? ""
        guard !message.isEmpty else { return }
        notices.append(FLNotice(kind: kind, message: message, date: Date()))
        if notices.count > 40 { notices.removeFirst(notices.count - 40) }
    }

    // MARK: Actions

    public func startEngine() { bridge.start() }
    public func stopEngine() { bridge.stop() }

    public func setMode(_ mode: FLMode) {
        bridge.setMode(mode)
        bridge.start()
    }

    public func connect(device: FLDevice) {
        guard let addr = device.addr else { return }
        bridge.connect(
            host: addr,
            port: UInt16(clamping: device.port),
            code: nil,
            fingerprint: device.fingerprint.isEmpty ? nil : device.fingerprint
        )
    }

    public func connect(paired: FLPairedDevice) {
        guard !paired.host.isEmpty else { return }
        bridge.connect(
            host: paired.host,
            port: UInt16(clamping: paired.port),
            code: nil,
            fingerprint: paired.fingerprint.isEmpty ? nil : paired.fingerprint
        )
    }

    public func connectManual(host: String, port: UInt16, code: String?) {
        let cleanedCode = code?.trimmingCharacters(in: .whitespacesAndNewlines)
        bridge.connect(
            host: host.trimmingCharacters(in: .whitespaces),
            port: port,
            code: (cleanedCode?.isEmpty ?? true) ? nil : cleanedCode,
            fingerprint: nil
        )
    }

    public func connectLink(_ link: String) {
        bridge.connectLink(link.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    public func disconnect() { bridge.disconnect() }
    public func submitCode(_ code: String) { bridge.submitPairCode(code) }
    public func regenerateCode() { bridge.regeneratePairCode() }
    public func forget(deviceID: String) { bridge.forgetDevice(deviceID) }
    public func setEdge(_ edge: FLEdge) { bridge.setEdge(edge) }
    public func setClipboardEnabled(_ enabled: Bool) { bridge.setClipboardEnabled(enabled) }
    public func rename(_ name: String) { bridge.setDeviceName(name) }
    public func setListenPort(_ port: UInt16) { bridge.setListenPort(port) }
    public func setHotkey(modifiers: UInt8, usage: UInt16) {
        bridge.setHotkey(modifiers: modifiers, usage: usage)
    }

    public func completeOnboarding(mode: FLMode, name: String) {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        if !trimmed.isEmpty { bridge.setDeviceName(trimmed) }
        UserDefaults.standard.set(true, forKey: "flowlink.onboarded")
        onboarded = true
        setMode(mode)
    }

    public func inviteLink() -> String? { bridge.inviteLink() }
    public func qrMatrix() -> (size: Int, modules: [UInt8])? { bridge.pairingQR() }
    public func logsDirectory() -> String? { bridge.logsDir() }
    public func recentLogs(maxLines: Int = 400) -> String { bridge.tailLogs(maxLines: maxLines) ?? "" }

    // MARK: Convenience

    public var mode: FLMode { status?.mode ?? .server }
    public var isConnected: Bool { status?.isConnected ?? false }
    public var isRunning: Bool { status?.isRunning ?? false }
    public var isServer: Bool { mode == .server }

    public var menuBarSymbol: String {
        guard let status else { return "rectangle.on.rectangle.slash" }
        switch (status.state, status.ownsCursor) {
        case ("connected", true): return "rectangle.connected.to.line.below"
        case ("connected", false): return "rectangle.on.rectangle"
        case ("off", _): return "rectangle.on.rectangle.slash"
        default: return "rectangle.on.rectangle.angled"
        }
    }

    public var statusHeadline: String {
        guard let status else { return "Starting…" }
        let peer = status.peer?.name ?? "the other computer"
        switch status.state {
        case "connected":
            return status.ownsCursor
                ? "Cursor on this Mac — press the hotkey or cross the edge to switch"
                : "Cursor on \(peer) — your input is forwarded there"
        case "listening":
            return status.peer == nil ? "Listening for clients" : "Pairing in progress"
        case "awaiting_pair": return "Enter the pairing code"
        case "reconnecting": return "Reconnecting…"
        case "error": return "Connection error"
        default: return status.stateLabel
        }
    }
}
