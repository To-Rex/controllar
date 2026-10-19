import CFlowLinkCore
import CoreGraphics
import Foundation

/// Injects input events received from the core into this Mac.
///
/// All state lives on a private serial queue; `handle(_:)` is safe to call
/// from any thread (typically the core's runtime).
/// Marks events produced by the injector so the local event tap can pass them
/// through without forwarding them back to the peer (input loop protection).
enum InjectionMarker {
    static let userData: Int64 = 0x464C_4B31 // "FLK1"
}

public final class Injector {

    private let queue = DispatchQueue(label: "app.flowlink.injector")
    /// Events are posted from a *private* source state so our own event tap
    /// can tell them apart from physical hardware and never forwards them
    /// back to the peer (which would loop input).
    private let source = CGEventSource(stateID: .privateState)

    /// The focused shell's injector saw the synthetic pointer pushed past the
    /// edge facing the peer (physical mouse is on the other desk). The app
    /// forwards this to `flowlink_hand_off`.
    public var onEdgeReturn: ((FLEdge, Double) -> Void)?

    // State — queue-only.
    private var pressedKeys = Set<UInt16>()
    private var pressedButtons = Set<UInt8>()
    private var heldModifiers: UInt8 = 0
    private var cursorHidden = false
    private var hiddenDisplays: [CGDirectDisplayID] = []
    private var metrics = ScreenMetrics.fallback

    /// Injected-edge return state machine (pure, unit-tested).
    private var edgeTracker = EdgePushTracker()

    public init() {}

    /// Tag an event as synthetic and post it.
    private func post(_ event: CGEvent?) {
        guard let event else { return }
        event.setIntegerValueField(.eventSourceUserData, value: InjectionMarker.userData)
        event.post(tap: .cghidEventTap)
    }

    // MARK: Public API

    public func handle(_ event: FlInputEvent) {
        queue.async { [weak self] in
            self?.handleLocked(event)
        }
    }

    public func hideCursor() {
        queue.async { [weak self] in
            self?.hideCursorLocked()
        }
    }

    public func showCursor() {
        queue.async { [weak self] in
            self?.showCursorLocked()
        }
    }

    /// Synchronously restore the cursor (app termination: queued work would
    /// be lost when the process exits).
    public func restoreCursorNow() {
        queue.sync { [weak self] in
            self?.showCursorLocked()
        }
    }

    /// Update the local edge facing the peer from the engine status. Needed
    /// before the first `Enter` (e.g. the server owns the cursor at startup).
    public func setPeerEdge(_ edge: FLEdge?) {
        queue.async { [weak self] in
            self?.edgeTracker.setPeerEdge(edge)
        }
    }

    /// Refresh the cached screen metrics (display reconfiguration).
    public func refreshMetrics() {
        queue.async { [weak self] in
            self?.metrics = ScreenGeometry.unionMetrics()
        }
    }

    // MARK: Cursor visibility

    /// Hide the system cursor on every active display.
    ///
    /// `CGDisplayHideCursor` is per display, so hiding only the main display
    /// leaves a stray cursor behind when the pointer sits on a second screen.
    private func hideCursorLocked() {
        guard !cursorHidden else { return }
        var count: UInt32 = 0
        guard CGGetActiveDisplayList(0, nil, &count) == .success, count > 0 else {
            return
        }
        var ids = [CGDirectDisplayID](repeating: 0, count: Int(count))
        guard CGGetActiveDisplayList(count, &ids, &count) == .success else { return }
        let active = Array(ids.prefix(Int(count)))
        for id in active {
            CGDisplayHideCursor(id)
        }
        hiddenDisplays = active
        cursorHidden = true
    }

    private func showCursorLocked() {
        guard cursorHidden else { return }
        for id in hiddenDisplays {
            CGDisplayShowCursor(id)
        }
        hiddenDisplays.removeAll()
        cursorHidden = false
        metrics = ScreenGeometry.unionMetrics()
    }

    // MARK: Queue-confined implementation

    private func handleLocked(_ event: FlInputEvent) {
        switch event.kind {
        case FLInputKindC.enter:
            handleEnter(event)
        case FLInputKindC.key:
            handleKey(event)
        case FLInputKindC.mouseMove:
            handleMouseMove(event)
        case FLInputKindC.mouseButton:
            handleMouseButton(event)
        case FLInputKindC.scroll:
            handleScroll(event)
        case FLInputKindC.releaseAll:
            releaseAll()
        default:
            break
        }
    }

    private func handleEnter(_ event: FlInputEvent) {
        metrics = ScreenGeometry.unionMetrics()
        // The cursor is coming here: make sure it is visible again before it
        // is warped (the status update that follows is asynchronous).
        showCursorLocked()

        let serverEdge = edge(from: event.flags)
        edgeTracker.enter(remoteEdge: serverEdge)

        let along = max(0.0, min(1.0, event.x))
        // Land a few pixels *inside* the edge so ordinary sideways motion is
        // not mistaken for an edge push.
        let inset = 24.0
        let local: CGPoint
        switch serverEdge {
        // The cursor continues in the same direction: it enters through the
        // mirrored side of this screen.
        case .right: local = CGPoint(x: inset, y: along * metrics.height)
        case .left: local = CGPoint(x: metrics.width - inset, y: along * metrics.height)
        case .top: local = CGPoint(x: along * metrics.width, y: metrics.height - inset)
        case .bottom: local = CGPoint(x: along * metrics.width, y: inset)
        }
        CGWarpMouseCursorPosition(metrics.global(local))
        // Warping can disassociate the hardware pointer from the cursor; put
        // them back together so the next physical movement moves the cursor.
        CGAssociateMouseAndMouseCursorPosition(1)
    }

    private func handleKey(_ event: FlInputEvent) {
        ensureModifiers(event.modifiers)
        let down = event.down != 0
        if down {
            pressedKeys.insert(event.usage)
        } else {
            pressedKeys.remove(event.usage)
        }
        guard let cgKey = KeyCodes.cgKeyCode(fromHID: event.usage) else {
            // Unknown key on this layout — dropped (logged via core notices).
            return
        }
        let cgEvent = CGEvent(
            keyboardEventSource: source,
            virtualKey: CGKeyCode(cgKey),
            keyDown: down
        )
        cgEvent?.flags = KeyCodes.eventFlags(from: heldModifiers)
        post(cgEvent)
    }

    private func handleMouseMove(_ event: FlInputEvent) {
        let current = CGEvent(source: nil)?.location ?? .zero
        // Edge detection must run on the *intended* position: once the cursor
        // is pinned to a screen edge the next moves are clamped, and that is
        // exactly the "keep pushing" gesture that hands control back.
        detectInjectedEdge(from: current, dx: event.x, dy: event.y)

        let target = CGPoint(x: current.x + event.x, y: current.y + event.y)
        let type: CGEventType
        if pressedButtons.contains(1) {
            type = .leftMouseDragged
        } else if pressedButtons.contains(2) {
            type = .rightMouseDragged
        } else if !pressedButtons.isEmpty {
            type = .otherMouseDragged
        } else {
            type = .mouseMoved
        }
        let cgEvent = CGEvent(
            mouseEventSource: source,
            mouseType: type,
            mouseCursorPosition: target,
            mouseButton: .left
        )
        post(cgEvent)
    }

    private func handleMouseButton(_ event: FlInputEvent) {
        let usage = UInt8(clamping: Int(event.usage))
        let down = event.down != 0
        if down {
            pressedButtons.insert(usage)
        } else {
            pressedButtons.remove(usage)
        }
        ensureModifiers(event.modifiers)

        let (type, button): (CGEventType, CGMouseButton)
        switch usage {
        case 1:
            (type, button) = (down ? .leftMouseDown : .leftMouseUp, .left)
        case 2:
            (type, button) = (down ? .rightMouseDown : .rightMouseUp, .right)
        default:
            (type, button) = (down ? .otherMouseDown : .otherMouseUp, .center)
        }
        let pos = CGEvent(source: nil)?.location ?? .zero
        let cgEvent = CGEvent(
            mouseEventSource: source,
            mouseType: type,
            mouseCursorPosition: pos,
            mouseButton: button
        )
        cgEvent?.flags = KeyCodes.eventFlags(from: heldModifiers)
        post(cgEvent)
    }

    private func handleScroll(_ event: FlInputEvent) {
        // Notch-based deltas (non-precise) are scaled to pixels so a single
        // wheel tick moves a comfortable amount on macOS.
        let precise = event.flags & 1 != 0
        let scale = precise ? 1.0 : 12.0
        let dy = Int32(clamping: Int((event.y * scale).rounded()))
        let dx = Int32(clamping: Int((event.x * scale).rounded()))
        guard let cgEvent = CGEvent(
            scrollWheelEvent2Source: source,
            units: .pixel,
            wheelCount: 2,
            wheel1: dy,
            wheel2: dx,
            wheel3: 0
        ) else { return }
        post(cgEvent)
    }

    private func releaseAll() {
        for button in pressedButtons {
            let (type, cgButton): (CGEventType, CGMouseButton)
            switch button {
            case 1: (type, cgButton) = (.leftMouseUp, .left)
            case 2: (type, cgButton) = (.rightMouseUp, .right)
            default: (type, cgButton) = (.otherMouseUp, .center)
            }
            let pos = CGEvent(source: nil)?.location ?? .zero
            post(CGEvent(
                mouseEventSource: source,
                mouseType: type,
                mouseCursorPosition: pos,
                mouseButton: cgButton
            ))
        }
        pressedButtons.removeAll()

        for usage in pressedKeys {
            if let cgKey = KeyCodes.cgKeyCode(fromHID: usage) {
                post(CGEvent(
                    keyboardEventSource: source,
                    virtualKey: CGKeyCode(cgKey),
                    keyDown: false
                ))
            }
        }
        pressedKeys.removeAll()

        ensureModifiers(0)
        heldModifiers = 0
    }

    // MARK: Injected-edge return

    /// Detect a deliberate outward push at the peer-facing edge.
    ///
    /// While this Mac owns the cursor the local event tap ignores synthetic
    /// events (that is what prevents input loops), so an edge reached by the
    /// *other* computer's mouse is invisible to it. The injector is the only
    /// component that sees those moves; it reports the push and the core hands
    /// focus back exactly as if the physical pointer had crossed the edge.
    private func detectInjectedEdge(from current: CGPoint, dx: Double, dy: Double) {
        guard metrics.width > 1, metrics.height > 1 else { return }
        let local = metrics.normalized(current)
        guard let (edge, along) = edgeTracker.feed(
            x: local.x,
            y: local.y,
            dx: dx,
            dy: dy,
            width: metrics.width,
            height: metrics.height,
            nowNanos: DispatchTime.now().uptimeNanoseconds
        ) else { return }
        onEdgeReturn?(edge, along)
    }

    /// Materialize the desired modifier state before injecting a key.
    private func ensureModifiers(_ desired: UInt8) {
        for flag in FLModC.all {
            let want = desired & flag != 0
            let have = heldModifiers & flag != 0
            if want == have { continue }
            guard let usage = KeyCodes.modifierUsage(for: flag),
                  let cgKey = KeyCodes.cgKeyCode(fromHID: usage) else { continue }
            heldModifiers = want ? (heldModifiers | flag) : (heldModifiers & ~flag)
            let cgEvent = CGEvent(
                keyboardEventSource: source,
                virtualKey: CGKeyCode(cgKey),
                keyDown: want
            )
            cgEvent?.flags = KeyCodes.eventFlags(from: heldModifiers)
            post(cgEvent)
        }
    }

    private func edge(from value: Int32) -> FLEdge {
        switch value {
        case FLEdgeC.left: return .left
        case FLEdgeC.top: return .top
        case FLEdgeC.bottom: return .bottom
        default: return .right
        }
    }
}
