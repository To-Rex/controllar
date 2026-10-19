import Foundation

/// Pure state machine deciding when an *injected* pointer being pushed past
/// the peer-facing edge is a deliberate return gesture.
///
/// While this Mac owns the shared cursor the event tap ignores synthetic
/// events (that is what prevents input loops), so a cursor driven by the
/// other computer's mouse is invisible to it. The injector feeds every
/// injected move here; the tracker mirrors the `Enter` geometry, requires the
/// pointer to travel inside first (no bounce on the tail of the entry move)
/// and accumulates outward overshoot at the edge until the gesture is clear.
///
/// Kept free of CoreGraphics so it can be unit-tested deterministically.
struct EdgePushTracker {
    /// Distance from the peer-facing edge the pointer must reach after
    /// `Enter` before an outward push is accepted. The entry lands 24 px
    /// inside, so the user must move ~40 px into the screen first; a twitch
    /// at the seam cannot hand focus back.
    static let rearmMargin: Double = 64
    /// Outward distance accumulated while the pointer is **at** the edge.
    static let pushThreshold: Double = 20
    /// The pointer must actually be this close to the facing edge before an
    /// overshoot counts. Without it a single fast flick from mid-screen could
    /// jump past the edge in one event and hand focus back unexpectedly.
    static let contactMargin: Double = 4
    /// A handoff that never produced a new `Enter` (the session died) must
    /// not disable edge detection forever.
    static let cooldownNanos: UInt64 = 1_000_000_000

    private(set) var peerEdge: FLEdge?
    private var armed = false
    private var overshoot: Double = 0
    private var handedOffAt: UInt64?

    /// A new cursor arrived through `remoteEdge` (the edge the *peer* exited
    /// through). The local facing edge is its mirror.
    mutating func enter(remoteEdge: FLEdge) {
        peerEdge = remoteEdge.opposite
        armed = false
        overshoot = 0
        handedOffAt = nil
    }

    /// Learn the peer-facing edge without an `Enter`. The focus may be
    /// established by the session itself (the server owns the cursor at
    /// startup, so no `Enter` arrives before synthetic input does). Only a
    /// changed edge resets the gesture state.
    mutating func setPeerEdge(_ edge: FLEdge?) {
        guard peerEdge != edge else { return }
        peerEdge = edge
        armed = false
        overshoot = 0
        handedOffAt = nil
    }

    mutating func clear() {
        peerEdge = nil
        armed = false
        overshoot = 0
        handedOffAt = nil
    }

    /// Feed the current *local* cursor position (origin at the union's
    /// top-left), the intended delta of this injected move, the union screen
    /// size and a monotonic timestamp. Returns the edge and along-edge
    /// position (0..1) once a deliberate outward push should hand focus back.
    mutating func feed(
        x: Double,
        y: Double,
        dx: Double,
        dy: Double,
        width: Double,
        height: Double,
        nowNanos: UInt64
    ) -> (FLEdge, Double)? {
        if let at = handedOffAt {
            if nowNanos &- at < Self.cooldownNanos {
                return nil
            }
            handedOffAt = nil
            armed = false
            overshoot = 0
        }
        guard let edge = peerEdge, width > 1, height > 1 else { return nil }
        let maxX = width - 1
        let maxY = height - 1

        let inward: Double
        switch edge {
        case .left: inward = x
        case .right: inward = maxX - x
        case .top: inward = y
        case .bottom: inward = maxY - y
        }
        if inward >= Self.rearmMargin {
            armed = true
            overshoot = 0
        }

        // Only a push that starts *at* the seam counts. A fast movement from
        // the middle of the screen may ask to go past the edge in a single
        // event; the hardware cursor only reaches the edge, and the next
        // event (still at the edge and still outward) can hand off.
        let atEdge: Bool
        switch edge {
        case .left: atEdge = x <= Self.contactMargin
        case .right: atEdge = x >= maxX - Self.contactMargin
        case .top: atEdge = y <= Self.contactMargin
        case .bottom: atEdge = y >= maxY - Self.contactMargin
        }
        guard atEdge else {
            overshoot = 0
            return nil
        }

        let desiredX = x + dx
        let desiredY = y + dy
        let over: Double
        switch edge {
        case .left: over = max(0, -desiredX)
        case .right: over = max(0, desiredX - maxX)
        case .top: over = max(0, -desiredY)
        case .bottom: over = max(0, desiredY - maxY)
        }
        guard over > 0 else {
            // Moving away from the edge: forget a partial push.
            if inward > 2 { overshoot = 0 }
            return nil
        }
        guard armed else { return nil }
        overshoot += over
        guard overshoot >= Self.pushThreshold else { return nil }

        let along: Double
        if edge == .left || edge == .right {
            along = y / height
        } else {
            along = x / width
        }
        handedOffAt = nowNanos
        overshoot = 0
        return (edge, max(0, min(1, along)))
    }
}
