import CFlowLinkCore
import CoreGraphics
import Foundation

/// Shared snapshot the event tap reads on every event. Written by the app
/// model (main thread), read by the tap callback (tap thread).
public final class TapSharedState {
    private let lock = NSLock()
    private var storage = Snapshot()

    public struct Snapshot {
        /// This Mac's input is currently sent to the other computer.
        public var forwarding = false
        public var hotkeyModifiers: UInt8 = 0
        public var hotkeyUsage: UInt16 = 0x2C
        public var metrics: ScreenMetrics = .fallback
    }

    public func update(_ body: (inout Snapshot) -> Void) {
        lock.lock()
        body(&storage)
        lock.unlock()
    }

    public func snapshot() -> Snapshot {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }
}

/// Captures keyboard/mouse input with a CGEventTap on a dedicated thread with
/// its own run loop. Events are forwarded to the core; when this Mac is
/// driving the remote cursor, local delivery is suppressed (nil return).
public final class InputTap {

    public var onInput: ((FlHostInput) -> Void)?

    private let shared: TapSharedState
    private var tap: CFMachPort?
    private var runLoop: CFRunLoop?
    private var thread: Thread?
    private var pressedModifierUsages = Set<UInt16>() // tap-thread only

    public init(shared: TapSharedState) {
        self.shared = shared
    }

    public static var isSupported: Bool { true }

    /// Install the tap. Returns false when the required permission is
    /// missing (the tap cannot be created without Accessibility access).
    @discardableResult
    public func start() -> Bool {
        guard tap == nil else { return true }

        let mask: CGEventMask =
            (1 << CGEventType.keyDown.rawValue) |
            (1 << CGEventType.keyUp.rawValue) |
            (1 << CGEventType.flagsChanged.rawValue) |
            (1 << CGEventType.mouseMoved.rawValue) |
            (1 << CGEventType.leftMouseDown.rawValue) |
            (1 << CGEventType.leftMouseUp.rawValue) |
            (1 << CGEventType.rightMouseDown.rawValue) |
            (1 << CGEventType.rightMouseUp.rawValue) |
            (1 << CGEventType.otherMouseDown.rawValue) |
            (1 << CGEventType.otherMouseUp.rawValue) |
            (1 << CGEventType.leftMouseDragged.rawValue) |
            (1 << CGEventType.rightMouseDragged.rawValue) |
            (1 << CGEventType.otherMouseDragged.rawValue) |
            (1 << CGEventType.scrollWheel.rawValue)

        let context = Unmanaged.passUnretained(self).toOpaque()

        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .defaultTap,
            eventsOfInterest: mask,
            callback: { _, type, event, userInfo in
                guard let userInfo else { return Unmanaged.passUnretained(event) }
                let tap = Unmanaged<InputTap>.fromOpaque(userInfo).takeUnretainedValue()
                return tap.handle(type: type, event: event)
            },
            userInfo: context
        ) else {
            return false
        }

        self.tap = tap
        let thread = Thread { [weak self] in
            guard let self, let tap = self.tap else { return }
            let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
            self.runLoop = CFRunLoopGetCurrent()
            CFRunLoopAddSource(CFRunLoopGetCurrent(), source, .commonModes)
            CGEvent.tapEnable(tap: tap, enable: true)
            CFRunLoopRun()
        }
        thread.name = "app.flowlink.eventtap"
        thread.qualityOfService = .userInteractive
        self.thread = thread
        thread.start()
        return true
    }

    public func stop() {
        if let tap {
            CGEvent.tapEnable(tap: tap, enable: false)
            CFMachPortInvalidate(tap)
        }
        if let runLoop {
            CFRunLoopStop(runLoop)
        }
        tap = nil
        runLoop = nil
        thread = nil
    }

    // MARK: Tap callback (tap thread)

    private func handle(type: CGEventType, event: CGEvent) -> Unmanaged<CGEvent>? {
        let pass = Unmanaged.passUnretained(event)
        let state = shared.snapshot()

        // Never forward synthetic events (our own injection, other automation)
        // — that would loop input back to the peer. They are also never
        // swallowed: the app that produced them expects local delivery.
        if type != .tapDisabledByTimeout, type != .tapDisabledByUserInput,
           event.getIntegerValueField(.eventSourceStateID)
               != Int64(CGEventSourceStateID.hidSystemState.rawValue)
        {
            return pass
        }

        switch type {
        case .tapDisabledByTimeout, .tapDisabledByUserInput:
            if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
            return pass

        case .keyDown, .keyUp:
            let code = UInt16(event.getIntegerValueField(.keyboardEventKeycode))
            guard let usage = KeyCodes.hid(fromCGKeyCode: code) else { return pass }
            let modifiers = KeyCodes.modifiers(from: event.flags)
            let isHotkey = usage == state.hotkeyUsage && modifiers == state.hotkeyModifiers
            let isRepeat = event.getIntegerValueField(.keyboardEventAutorepeat) != 0
            let down = type == .keyDown

            if isHotkey {
                // Forward once (press), suppress both press and release.
                if down && !isRepeat {
                    onInput?(makeKey(usage: usage, down: true, modifiers: modifiers))
                } else if !down {
                    onInput?(makeKey(usage: usage, down: false, modifiers: modifiers))
                }
                return nil
            }

            onInput?(makeKey(usage: usage, down: down, modifiers: modifiers))
            return state.forwarding ? nil : pass

        case .flagsChanged:
            let code = UInt16(event.getIntegerValueField(.keyboardEventKeycode))
            guard let usage = KeyCodes.hid(fromCGKeyCode: code) else { return pass }
            let down: Bool
            if pressedModifierUsages.contains(usage) {
                pressedModifierUsages.remove(usage)
                down = false
            } else {
                pressedModifierUsages.insert(usage)
                down = true
            }
            let modifiers = KeyCodes.modifiers(from: event.flags)
            onInput?(makeKey(usage: usage, down: down, modifiers: modifiers))
            return state.forwarding ? nil : pass

        case .mouseMoved, .leftMouseDragged, .rightMouseDragged, .otherMouseDragged:
            let point = state.metrics.normalized(event.location)
            let dx = Double(event.getIntegerValueField(.mouseEventDeltaX))
            let dy = Double(event.getIntegerValueField(.mouseEventDeltaY))
            onInput?(makeMove(x: point.x, y: point.y, dx: dx, dy: dy))
            return state.forwarding ? nil : pass

        case .leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp,
             .otherMouseDown, .otherMouseUp:
            let button: UInt16
            switch type {
            case .leftMouseDown, .leftMouseUp: button = 1
            case .rightMouseDown, .rightMouseUp: button = 2
            default: button = 4
            }
            let down = type == .leftMouseDown || type == .rightMouseDown || type == .otherMouseDown
            let modifiers = KeyCodes.modifiers(from: event.flags)
            onInput?(makeButton(button: button, down: down, modifiers: modifiers))
            return state.forwarding ? nil : pass

        case .scrollWheel:
            // Wire units: notches for discrete wheels (one per physical
            // tick), pixels for trackpads (`precise`). Positive dy = wheel up
            // (away from the user), matching kCGScrollWheelEventDeltaAxis1.
            let precise = event.getIntegerValueField(.scrollWheelEventIsContinuous) != 0
            let dy: Double
            let dx: Double
            if precise {
                dy = event.getDoubleValueField(.scrollWheelEventPointDeltaAxis1)
                dx = event.getDoubleValueField(.scrollWheelEventPointDeltaAxis2)
            } else {
                dy = event.getDoubleValueField(.scrollWheelEventDeltaAxis1)
                dx = event.getDoubleValueField(.scrollWheelEventDeltaAxis2)
            }
            onInput?(makeScroll(dx: dx, dy: dy, precise: precise))
            return state.forwarding ? nil : pass

        default:
            return pass
        }
    }

    // MARK: HostInput constructors

    private func makeKey(usage: UInt16, down: Bool, modifiers: UInt8) -> FlHostInput {
        FlHostInput(
            kind: FLHostKindC.key,
            usage: usage,
            down: down ? 1 : 0,
            modifiers: modifiers,
            x: 0, y: 0, dx: 0, dy: 0, flags: 0
        )
    }

    private func makeMove(x: Double, y: Double, dx: Double, dy: Double) -> FlHostInput {
        FlHostInput(
            kind: FLHostKindC.mouseMove,
            usage: 0,
            down: 0,
            modifiers: 0,
            x: x, y: y, dx: dx, dy: dy, flags: 0
        )
    }

    private func makeButton(button: UInt16, down: Bool, modifiers: UInt8) -> FlHostInput {
        FlHostInput(
            kind: FLHostKindC.mouseButton,
            usage: button,
            down: down ? 1 : 0,
            modifiers: modifiers,
            x: 0, y: 0, dx: 0, dy: 0, flags: 0
        )
    }

    private func makeScroll(dx: Double, dy: Double, precise: Bool) -> FlHostInput {
        FlHostInput(
            kind: FLHostKindC.scroll,
            usage: 0,
            down: 0,
            modifiers: 0,
            x: dx, y: dy, dx: 0, dy: 0,
            flags: precise ? 1 : 0
        )
    }
}
