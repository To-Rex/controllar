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
        injector.onEdgeReturn = { [weak self] edge, along in
            self?.bridge.handOff(edge: edge, along: along)
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
                    guard let self else { return }
                    // A revoked grant takes the running tap down with it. Drop
                    // the old tap and report/recreate instead of silently
                    // hammering input into a dead CFMachPort.
                    if self.tapRunning && !self.permissions.allGranted {
                        self.tap?.stop()
                        self.tapRunning = false
                    }
                    self.installTap()
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
    /// Pending "capture is down" report. Right after launch (and right after
    /// an update) permissions/code-signature checks settle a moment late; a
    /// one-second grace avoids a scary banner that disappears immediately.
    private var captureReportWork: DispatchWorkItem?

    /// Report capture state to the core, debouncing the inactive case.
    private func reportCapture(active: Bool, reason: String?) {
        if active {
            captureReportWork?.cancel()
            captureReportWork = nil
            bridge.setInputCapture(active: true, reason: nil)
            return
        }
        // Status snapshots arrive several times per second; rescheduling on
        // each one would postpone the report forever.
        guard captureReportWork == nil else { return }
        let reason = reason ?? "local input capture is not active"
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.captureReportWork = nil
            guard !self.tapRunning else { return }
            self.bridge.setInputCapture(active: false, reason: reason)
        }
        captureReportWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0, execute: work)
    }

    /// Install the event tap, retrying once after a short delay. Right after
    /// a relaunch the system may still hold the previous instance's tap slot,
    /// so a first failure is usually transient — the user is only told once
    /// the retry has failed too.
    private func installTap() {
        if tapRunning { return }
        guard permissions.allGranted, let tap else {
            reportCapture(active: false, reason: captureInactiveReason())
            return
        }
        tapRunning = tap.start()
        if tapRunning {
            reportCapture(active: true, reason: nil)
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
            self.reportCapture(
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
        // Never leave the system cursor hidden after the app goes away.
        injector.restoreCursorNow()
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

        // Routing state must reach the tap/injector on *this* thread: waiting
        // for the main queue can leave the keyboard swallowed on the wrong
        // computer for a whole frame (or longer under load). Both objects are
        // lock-protected and safe to update here.
        tapShared.update { snapshot in
            snapshot.forwarding = decoded.isConnected && decoded.isForwarding
            snapshot.hotkeyModifiers = decoded.hotkey.modifiers
            snapshot.hotkeyUsage = decoded.hotkey.usage
        }
        injector.setPeerEdge(decoded.peerEdge)
        injector.setForwarding(decoded.isConnected && decoded.isForwarding)
        if decoded.isConnected && decoded.isForwarding {
            injector.hideCursor()
        } else {
            injector.showCursor()
        }

        DispatchQueue.main.async { [weak self] in
            self?.apply(decoded)
        }
    }

    private func apply(_ new: FLStatus) {
        status = new
        if let limit = new.clipboardMaxBytes {
            clipboard.maxBytes = limit
        }

        // Retry installing the tap once permissions are available. Routing
        // state is applied by `handleStatusJSON` directly on the callback
        // thread; re-applying it here would let an *older* snapshot that is
        // still queued on the main thread overwrite a newer one.
        installTap()
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
            let edge = (status.peerEdge ?? status.edge).title.lowercased()
            return status.ownsCursor
                ? "Cursor on this Mac — press the hotkey or push it through the \(edge) edge"
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
