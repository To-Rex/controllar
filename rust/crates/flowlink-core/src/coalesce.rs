//! Mouse-move coalescing.
//!
//! High-polling mice emit several hundred events per second. Sending each one
//! as a separate frame wastes bandwidth and adds jitter; instead we accumulate
//! deltas and flush at most once per `interval_ms` (default ≈ 4 ms ≈ 250 Hz),
//! keeping latency imperceptible while cutting frame count massively.

#[derive(Debug)]
pub struct MoveCoalescer {
    interval_ms: u64,
    pending_dx: f64,
    pending_dy: f64,
    has_pending: bool,
    last_sent_ms: u64,
}

/// Sub-millisecond motion is dropped — impossible to perceive and it keeps
/// idle hover noise from generating traffic.
const EPSILON: f64 = 0.05;

impl MoveCoalescer {
    pub fn new(interval_ms: u64) -> Self {
        MoveCoalescer {
            interval_ms: interval_ms.max(1),
            pending_dx: 0.0,
            pending_dy: 0.0,
            has_pending: false,
            last_sent_ms: 0,
        }
    }

    /// Default profile: flush at most every 4 ms.
    pub fn default_profile() -> Self {
        Self::new(4)
    }

    /// Accumulate a delta. Returns a flush when the interval has elapsed.
    pub fn push(&mut self, dx: f64, dy: f64, now_ms: u64) -> Option<(f32, f32)> {
        self.pending_dx += dx;
        self.pending_dy += dy;
        self.has_pending = true;
        if now_ms.saturating_sub(self.last_sent_ms) >= self.interval_ms {
            Some(self.take(now_ms))
        } else {
            None
        }
    }

    /// Flush accumulated motion during a periodic tick.
    pub fn flush_if_due(&mut self, now_ms: u64) -> Option<(f32, f32)> {
        if self.has_pending && now_ms.saturating_sub(self.last_sent_ms) >= self.interval_ms {
            Some(self.take(now_ms))
        } else {
            None
        }
    }

    /// Flush unconditionally (control switching, session teardown).
    pub fn flush(&mut self, now_ms: u64) -> Option<(f32, f32)> {
        if self.has_pending {
            Some(self.take(now_ms))
        } else {
            None
        }
    }

    pub fn has_pending(&self) -> bool {
        self.has_pending
    }

    /// Drop pending motion without sending (e.g. cursor returned to local).
    pub fn clear(&mut self) {
        self.pending_dx = 0.0;
        self.pending_dy = 0.0;
        self.has_pending = false;
    }

    fn take(&mut self, now_ms: u64) -> (f32, f32) {
        let dx = self.pending_dx;
        let dy = self.pending_dy;
        self.pending_dx = 0.0;
        self.pending_dy = 0.0;
        self.has_pending = false;
        self.last_sent_ms = now_ms;
        if dx.abs() < EPSILON && dy.abs() < EPSILON {
            (0.0, 0.0)
        } else {
            (dx as f32, dy as f32)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accumulates_within_interval_and_flushes_after() {
        let mut c = MoveCoalescer::new(4);
        // First event at t=4 flushes immediately (last_sent_ms initial 0).
        let first = c.push(1.0, 1.0, 4);
        assert_eq!(first, Some((1.0, 1.0)));

        // Accumulate for 3 ms — the interval has not elapsed.
        assert_eq!(c.push(0.5, 0.0, 5), None);
        assert_eq!(c.push(0.5, 0.0, 6), None);
        assert_eq!(c.push(0.5, 0.0, 7), None);
        assert!(c.has_pending());

        // 8 - 4 == 4 ≥ interval: the next push flushes everything so far.
        let flushed = c.push(0.5, 0.0, 8).expect("due at t=8");
        assert_eq!(flushed, (2.0, 0.0));
        assert!(!c.has_pending());
    }

    #[test]
    fn flush_returns_motion_when_pending() {
        let mut c = MoveCoalescer::new(100);
        assert_eq!(c.push(3.0, -2.0, 1), None);
        assert_eq!(c.flush(2), Some((3.0, -2.0)));
        assert_eq!(c.flush(3), None);
    }

    #[test]
    fn tiny_motion_is_suppressed() {
        let mut c = MoveCoalescer::new(4);
        // Below epsilon only.
        assert_eq!(c.push(0.01, -0.01, 10), Some((0.0, 0.0)));
    }

    #[test]
    fn clear_drops_pending() {
        let mut c = MoveCoalescer::new(100);
        c.push(10.0, 10.0, 1);
        c.clear();
        assert!(!c.has_pending());
        assert_eq!(c.flush(2), None);
    }
}
