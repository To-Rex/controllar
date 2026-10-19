//! Injected-pointer edge return.
//!
//! While the shared cursor is on this screen, the physical mouse may be on
//! the *other* computer. That computer forwards relative motion and the local
//! shell injects it; the low-level hook deliberately ignores synthetic events,
//! so the only place that can notice the pointer being pushed past the
//! peer-facing edge is the injector. This module is the pure state machine
//! behind that decision: enter keyed by the `Enter` event, feed the intended
//! cursor position and deltas, and it returns the edge/along position once a
//! deliberate push has happened.
//!
//! Kept free of Win32 calls so the behaviour is unit-tested on every platform.

use flowlink_core::types::Edge;

/// Distance from the peer-facing edge the pointer must reach after `Enter`
/// before an outward push is accepted (the entry inset is 24 px).
pub const REARM_MARGIN: f64 = 64.0;
/// Outward distance accumulated at the edge that triggers the handoff.
pub const PUSH_THRESHOLD: f64 = 20.0;
/// The pointer must actually be this close to the facing edge before an
/// overshoot counts; a single fast flick from mid-screen must not hand off.
pub const CONTACT_MARGIN: f64 = 4.0;
/// If a handoff never produced a new `Enter` (the session died), allow edge
/// detection again after this long.
pub const COOLDOWN_MS: u64 = 1_000;

#[derive(Debug, Default)]
pub struct EdgePushTracker {
    peer_edge: Option<Edge>,
    armed: bool,
    overshoot: f64,
    handed_off_at_ms: Option<u64>,
}

#[allow(dead_code)]
impl EdgePushTracker {
    pub fn new() -> Self {
        Self::default()
    }

    /// A new cursor entered from the peer through `remote_edge` (the edge the
    /// *peer* exited through). The local facing edge is its mirror.
    pub fn enter(&mut self, remote_edge: Edge) {
        self.peer_edge = Some(remote_edge.opposite());
        self.armed = false;
        self.overshoot = 0.0;
        self.handed_off_at_ms = None;
    }

    /// The peer-facing edge currently known, if any.
    pub fn peer_edge(&self) -> Option<Edge> {
        self.peer_edge
    }

    /// Learn the peer-facing edge without an `Enter` (the focuses can be
    /// established by the session itself, e.g. the server owns the cursor at
    /// startup). Only a changed edge resets the gesture state.
    pub fn set_peer_edge(&mut self, edge: Option<Edge>) {
        if self.peer_edge == edge {
            return;
        }
        self.peer_edge = edge;
        self.armed = false;
        self.overshoot = 0.0;
        self.handed_off_at_ms = None;
    }

    /// Forget the edge (session ended while this side owned the cursor).
    pub fn clear(&mut self) {
        self.peer_edge = None;
        self.armed = false;
        self.overshoot = 0.0;
        self.handed_off_at_ms = None;
    }

    /// Feed the current *local* cursor position (origin at the union's
    /// top-left), the intended delta of this injected move, and the local
    /// union screen size. Returns the edge and along-edge position (0..1) once
    /// a deliberate outward push should hand focus back.
    pub fn feed(
        &mut self,
        x: f64,
        y: f64,
        dx: f64,
        dy: f64,
        width: f64,
        height: f64,
        now_ms: u64,
    ) -> Option<(Edge, f32)> {
        if let Some(at) = self.handed_off_at_ms {
            if now_ms.saturating_sub(at) < COOLDOWN_MS {
                return None;
            }
            self.handed_off_at_ms = None;
            self.armed = false;
            self.overshoot = 0.0;
        }
        let edge = self.peer_edge?;
        if width <= 1.0 || height <= 1.0 {
            return None;
        }
        let max_x = width - 1.0;
        let max_y = height - 1.0;

        // The pointer lands 24 px inside on Enter; require real inward travel
        // so the tail of the crossing movement cannot bounce straight back.
        let inward = match edge {
            Edge::Left => x,
            Edge::Right => max_x - x,
            Edge::Top => y,
            Edge::Bottom => max_y - y,
        };
        if inward >= REARM_MARGIN {
            self.armed = true;
            self.overshoot = 0.0;
        }

        // Only a push that starts *at* the seam counts. A fast flick from
        // mid-screen can request a position past the edge in one event; the
        // hardware cursor only reaches the edge, and a following event at the
        // edge (still outward) may hand off.
        let at_edge = match edge {
            Edge::Left => x <= CONTACT_MARGIN,
            Edge::Right => x >= max_x - CONTACT_MARGIN,
            Edge::Top => y <= CONTACT_MARGIN,
            Edge::Bottom => y >= max_y - CONTACT_MARGIN,
        };
        if !at_edge {
            self.overshoot = 0.0;
            return None;
        }

        let desired_x = x + dx;
        let desired_y = y + dy;
        let overshoot = match edge {
            Edge::Left => (-desired_x).max(0.0),
            Edge::Right => (desired_x - max_x).max(0.0),
            Edge::Top => (-desired_y).max(0.0),
            Edge::Bottom => (desired_y - max_y).max(0.0),
        };
        if overshoot <= 0.0 {
            // Moving away: forget a partial push unless still touching.
            if inward > 2.0 {
                self.overshoot = 0.0;
            }
            return None;
        }
        if !self.armed {
            return None;
        }
        self.overshoot += overshoot;
        if self.overshoot < PUSH_THRESHOLD {
            return None;
        }
        let along = if matches!(edge, Edge::Left | Edge::Right) {
            (y / height).clamp(0.0, 1.0)
        } else {
            (x / width).clamp(0.0, 1.0)
        };
        self.handed_off_at_ms = Some(now_ms);
        self.overshoot = 0.0;
        Some((edge, along as f32))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const W: f64 = 1920.0;
    const H: f64 = 1080.0;

    fn armed_tracker(remote_edge: Edge) -> EdgePushTracker {
        let mut t = EdgePushTracker::new();
        t.enter(remote_edge);
        // Enter placed the pointer 24 px inside; move well inside to arm.
        match t.peer_edge().unwrap() {
            Edge::Left => assert_eq!(t.feed(300.0, 500.0, 10.0, 0.0, W, H, 1), None),
            Edge::Right => assert_eq!(t.feed(W - 300.0, 500.0, -10.0, 0.0, W, H, 1), None),
            Edge::Top => assert_eq!(t.feed(500.0, 300.0, 0.0, 0.0, W, H, 1), None),
            Edge::Bottom => assert_eq!(t.feed(500.0, H - 300.0, 0.0, 0.0, W, H, 1), None),
        }
        t
    }

    #[test]
    fn enter_mirrors_the_remote_edge() {
        let mut t = EdgePushTracker::new();
        t.enter(Edge::Right);
        assert_eq!(t.peer_edge(), Some(Edge::Left));
        t.enter(Edge::Top);
        assert_eq!(t.peer_edge(), Some(Edge::Bottom));
    }

    #[test]
    fn push_at_the_facing_edge_hands_off() {
        let mut t = armed_tracker(Edge::Right); // local edge = Left
                                                // Small pushes accumulate, then the threshold is crossed.
        assert_eq!(t.feed(0.0, 540.0, -10.0, 0.0, W, H, 2), None);
        let (edge, along) = t
            .feed(0.0, 540.0, -20.0, 0.0, W, H, 3)
            .expect("threshold crossed");
        assert_eq!(edge, Edge::Left);
        assert!((along - 0.5).abs() < 0.01);
    }

    #[test]
    fn the_tail_of_the_entry_move_does_not_bounce_back() {
        let mut t = EdgePushTracker::new();
        t.enter(Edge::Right); // entered at x=24, local facing edge Left
                              // Still within the inset: an outward twitch must not fire.
        assert_eq!(t.feed(24.0, 540.0, -24.0, 0.0, W, H, 1), None);
        assert_eq!(t.feed(0.0, 540.0, -30.0, 0.0, W, H, 2), None);
    }

    #[test]
    fn inward_travel_rearms() {
        let mut t = EdgePushTracker::new();
        t.enter(Edge::Right);
        assert_eq!(t.feed(0.0, 540.0, -30.0, 0.0, W, H, 1), None);
        // Move inward, then push out again.
        assert_eq!(t.feed(100.0, 540.0, 100.0, 0.0, W, H, 2), None);
        assert!(t.feed(0.0, 540.0, -30.0, 0.0, W, H, 3).is_some());
    }

    #[test]
    fn cooldown_prevents_a_burst_of_handoffs() {
        let mut t = armed_tracker(Edge::Right);
        assert!(t.feed(0.0, 540.0, -30.0, 0.0, W, H, 10).is_some());
        assert_eq!(t.feed(0.0, 540.0, -30.0, 0.0, W, H, 11), None);
        // A rejected handoff (edge mismatch / no session) must recover.
        assert_eq!(
            t.feed(500.0, 540.0, 40.0, 0.0, W, H, 11 + COOLDOWN_MS),
            None
        );
        assert!(t
            .feed(0.0, 540.0, -30.0, 0.0, W, H, 12 + COOLDOWN_MS)
            .is_some());
    }

    #[test]
    fn edge_can_be_learned_without_an_enter() {
        // Server startup: focused, no Enter has arrived yet, but the status
        // reports the peer-facing edge. The injector must still detect pushes.
        let mut t = EdgePushTracker::new();
        t.set_peer_edge(Some(Edge::Left));
        assert_eq!(t.peer_edge(), Some(Edge::Left));
        assert_eq!(t.feed(300.0, 500.0, 10.0, 0.0, W, H, 1), None);
        assert!(t.feed(0.0, 500.0, -30.0, 0.0, W, H, 2).is_some());

        // A topology change resets the gesture state.
        t.set_peer_edge(Some(Edge::Right));
        assert_eq!(t.feed(0.0, 500.0, -30.0, 0.0, W, H, 3), None);
        assert_eq!(t.feed(W - 300.0, 500.0, -10.0, 0.0, W, H, 4), None);
        assert!(t.feed(W - 1.0, 500.0, 30.0, 0.0, W, H, 5).is_some());
    }

    #[test]
    fn a_fast_flick_from_mid_screen_does_not_hand_off() {
        let mut t = armed_tracker(Edge::Right); // local facing edge = Left
                                                // One event asks to travel far past the edge while the hardware
                                                // cursor is still at x=300; that must not count.
        assert_eq!(t.feed(300.0, 500.0, -2000.0, 0.0, W, H, 2), None);
        // The cursor is now at the edge; a following outward event counts.
        assert!(t.feed(0.0, 500.0, -30.0, 0.0, W, H, 3).is_some());
    }

    #[test]
    fn pushes_away_from_the_facing_edge_are_ignored() {
        let mut t = armed_tracker(Edge::Right); // local edge Left
        assert_eq!(t.feed(W - 1.0, 540.0, 30.0, 0.0, W, H, 2), None);
        assert_eq!(t.feed(900.0, 0.0, 0.0, -30.0, W, H, 3), None);
    }

    #[test]
    fn vertical_edges_work_too() {
        let mut t = armed_tracker(Edge::Bottom); // local edge = Top
        let (edge, along) = t
            .feed(800.0, 0.0, 0.0, -30.0, W, H, 2)
            .expect("top edge push");
        assert_eq!(edge, Edge::Top);
        assert!(((along as f64) - 800.0 / W).abs() < 0.01);
    }
}
