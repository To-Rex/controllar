//! Edge-crossing state machine (server side) and cursor geometry helpers.
//!
//! The tracker is fed *every* mouse move captured by the server shell (in
//! pixel coordinates of the union screen frame). It activates when the cursor
//! sits on the configured edge **and** is still pushing outwards, with
//! hysteresis so returning control does not instantly re-activate.

use crate::types::{Edge, ScreenInfo};

/// How far (px) the cursor must be inside the screen before the edge re-arms.
const REARM_MARGIN_PX: f64 = 10.0;

/// Minimum outward push (px) to count as "still pushing".
const PUSH_EPSILON_PX: f64 = 0.4;

/// Server-side edge tracker.
#[derive(Debug)]
pub struct EdgeTracker {
    armed: bool,
}

impl Default for EdgeTracker {
    fn default() -> Self {
        Self::new()
    }
}

impl EdgeTracker {
    pub fn new() -> Self {
        EdgeTracker { armed: true }
    }

    /// Re-arm after control returns; the cursor is expected to be a few
    /// pixels away from the edge, so re-arming happens on the next move.
    pub fn hint_returned(&mut self) {
        self.armed = false;
    }

    pub fn armed(&self) -> bool {
        self.armed
    }

    /// Feed the current cursor position/deltas. Returns `true` once, when
    /// control should cross to the remote device.
    pub fn feed(
        &mut self,
        edge: Edge,
        screen: ScreenInfo,
        x_px: f64,
        y_px: f64,
        dx: f64,
        dy: f64,
    ) -> bool {
        if screen.width <= 0.0 || screen.height <= 0.0 {
            return false;
        }

        if !self.armed {
            // Re-arm once the cursor is comfortably away from the edge.
            let inside = match edge {
                Edge::Left => x_px > REARM_MARGIN_PX,
                Edge::Right => x_px < screen.width - REARM_MARGIN_PX,
                Edge::Top => y_px > REARM_MARGIN_PX,
                Edge::Bottom => y_px < screen.height - REARM_MARGIN_PX,
            };
            if inside {
                self.armed = true;
            } else {
                return false;
            }
        }

        let on_edge = match edge {
            Edge::Left => x_px <= 0.5,
            // The cursor's last pixel column is width-1, so the threshold is
            // width-1.0 (not width-0.5, which integer positions can't reach).
            Edge::Right => x_px >= screen.width - 1.0,
            Edge::Top => y_px <= 0.5,
            Edge::Bottom => y_px >= screen.height - 1.0,
        };
        let pushing = match edge {
            Edge::Left => dx < -PUSH_EPSILON_PX,
            Edge::Right => dx > PUSH_EPSILON_PX,
            Edge::Top => dy < -PUSH_EPSILON_PX,
            Edge::Bottom => dy > PUSH_EPSILON_PX,
        };

        if on_edge && pushing {
            self.armed = false;
            true
        } else {
            false
        }
    }
}

/// Position where the pointer should appear on the *client* when the cursor
/// exits through `server_edge` (the cursor continues in the same direction,
/// so it enters through the opposite local edge).
pub fn enter_position(server_edge: Edge, along_norm: f32, screen: ScreenInfo) -> (f64, f64) {
    let along = (along_norm as f64).clamp(0.0, 1.0);
    match server_edge {
        // Exited to the right → appears at the local left edge.
        Edge::Right => (0.0, along * screen.height),
        Edge::Left => (screen.width, along * screen.height),
        // Exited upwards → appears at the local bottom edge.
        Edge::Top => (along * screen.width, screen.height),
        Edge::Bottom => (along * screen.width, 0.0),
    }
}

/// The local (client) edge the user must push to give control back.
pub fn return_edge(server_edge: Edge) -> Edge {
    server_edge.opposite()
}

/// Along-the-edge normalized coordinate for an activation on `edge`.
pub fn along_norm(edge: Edge, screen: ScreenInfo, x_px: f64, y_px: f64) -> f32 {
    let (value, span) = if edge.is_horizontal() {
        (y_px, screen.height)
    } else {
        (x_px, screen.width)
    };
    if span <= 0.0 {
        0.5
    } else {
        (value / span).clamp(0.0, 1.0) as f32
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn screen() -> ScreenInfo {
        ScreenInfo {
            width: 1920.0,
            height: 1080.0,
            scale: 2.0,
        }
    }

    #[test]
    fn activates_only_when_pushing_out_on_edge() {
        let mut t = EdgeTracker::new();
        // Moving right in the middle: no.
        assert!(!t.feed(Edge::Right, screen(), 900.0, 500.0, 12.0, 0.0));
        // Touching the right edge but pushing left: no.
        assert!(!t.feed(Edge::Right, screen(), 1920.0, 500.0, -12.0, 0.0));
        // On the right edge pushing right: yes.
        assert!(t.feed(Edge::Right, screen(), 1920.0, 500.0, 12.0, 0.0));
    }

    #[test]
    fn hysteresis_prevents_instant_reactivation() {
        let mut t = EdgeTracker::new();
        assert!(t.feed(Edge::Right, screen(), 1920.0, 500.0, 12.0, 0.0));
        // Returns (warp lands 2 px inside the edge) and pushes out again...
        t.hint_returned();
        assert!(!t.feed(Edge::Right, screen(), 1918.0, 500.0, 1.0, 0.0));
        // ...still too close, not re-armed yet:
        assert!(!t.feed(Edge::Right, screen(), 1920.0, 500.0, 12.0, 0.0));
        // Cursor moves well inside → re-arms.
        assert!(!t.feed(Edge::Right, screen(), 1500.0, 500.0, -30.0, 0.0));
        // Now a deliberate push out activates again.
        assert!(t.feed(Edge::Right, screen(), 1920.0, 500.0, 15.0, 0.0));
    }

    #[test]
    fn edge_matching_uses_configured_edge_only() {
        let mut t = EdgeTracker::new();
        // On the left edge while configured for right: no activation.
        assert!(!t.feed(Edge::Right, screen(), 0.0, 500.0, -12.0, 0.0));
    }

    #[test]
    fn enter_positions_are_mirrored() {
        let s = screen();
        assert_eq!(enter_position(Edge::Right, 0.25, s), (0.0, 270.0));
        assert_eq!(enter_position(Edge::Left, 0.25, s), (1920.0, 270.0));
        assert_eq!(enter_position(Edge::Top, 0.25, s), (480.0, 1080.0));
        assert_eq!(enter_position(Edge::Bottom, 0.25, s), (480.0, 0.0));
    }

    #[test]
    fn return_edge_is_opposite() {
        assert_eq!(return_edge(Edge::Right), Edge::Left);
        assert_eq!(return_edge(Edge::Top), Edge::Bottom);
    }

    #[test]
    fn along_norm_uses_correct_axis() {
        let s = screen();
        // Right edge: along is y.
        let v = along_norm(Edge::Right, s, 1920.0, 540.0);
        assert!((v - 0.5).abs() < 1e-6);
        // Top edge: along is x.
        let v = along_norm(Edge::Top, s, 480.0, 0.0);
        assert!((v - 0.25).abs() < 1e-6);
    }
}
