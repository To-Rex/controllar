import CFlowLinkCore
import CoreGraphics
import Foundation

/// Injects input events received from the core into this Mac.
///
/// All state lives on a private serial queue; `handle(_:)` is safe to call
/// from any thread (typically the core's runtime).
public final class Injector {

    private let queue = DispatchQueue(label: "app.flowlink.injector")
    /// Events are posted from a *private* source state so our own event tap
    /// can tell them apart from physical hardware and never forwards them
    /// back to the peer (which would loop input).
    private let source = CGEventSource(stateID: .privateState)

    // State — queue-only.
    private var pressedKeys = Set<UInt16>()
    private var pressedButtons = Set<UInt8>()
    private var heldModifiers: UInt8 = 0
    private var cursorHidden = false
    private var metrics = ScreenMetrics.fallback

    public init() {}

    // MARK: Public API

    public func handle(_ event: FlInputEvent) {
        queue.async { [weak self] in
            self?.handleLocked(event)
        }
    }

    public func hideCursor() {
        queue.async { [weak self] in
            guard let self, !self.cursorHidden else { return }
            CGDisplayHideCursor(CGMainDisplayID())
            self.cursorHidden = true
        }
    }

    public func showCursor() {
        queue.async { [weak self] in
            guard let self, self.cursorHidden else { return }
            CGDisplayShowCursor(CGMainDisplayID())
            self.cursorHidden = false
            self.metrics = ScreenGeometry.unionMetrics()
        }
    }

    /// Refresh the cached screen metrics (display reconfiguration).
    public func refreshMetrics() {
        queue.async { [weak self] in
            self?.metrics = ScreenGeometry.unionMetrics()
        }
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
        let serverEdge = edge(from: event.flags)
        let along = max(0.0, min(1.0, event.x))
        // Land a few pixels *inside* the edge so ordinary sideways motion is
        // not mistaken for an edge push.
        let inset = 6.0
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
        cgEvent?.post(tap: .cghidEventTap)
    }

    private func handleMouseMove(_ event: FlInputEvent) {
        let current = CGEvent(source: nil)?.location ?? .zero
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
        cgEvent?.post(tap: .cghidEventTap)
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
        cgEvent?.post(tap: .cghidEventTap)
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
        cgEvent.post(tap: .cghidEventTap)
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
            CGEvent(
                mouseEventSource: source,
                mouseType: type,
                mouseCursorPosition: pos,
                mouseButton: cgButton
            )?.post(tap: .cghidEventTap)
        }
        pressedButtons.removeAll()

        for usage in pressedKeys {
            if let cgKey = KeyCodes.cgKeyCode(fromHID: usage) {
                CGEvent(
                    keyboardEventSource: source,
                    virtualKey: CGKeyCode(cgKey),
                    keyDown: false
                )?.post(tap: .cghidEventTap)
            }
        }
        pressedKeys.removeAll()

        ensureModifiers(0)
        heldModifiers = 0
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
            cgEvent?.post(tap: .cghidEventTap)
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
