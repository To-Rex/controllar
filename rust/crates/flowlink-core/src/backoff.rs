//! Exponential backoff with jitter for automatic reconnects.

use std::time::Duration;

/// Exponential backoff: `base * 2^attempt`, capped at `max`, with 50–100%
/// jitter so multiple devices do not retry in lockstep.
#[derive(Debug, Clone)]
pub struct Backoff {
    base_ms: u64,
    max_ms: u64,
    attempt: u32,
}

impl Backoff {
    pub fn new(base_ms: u64, max_ms: u64) -> Self {
        Backoff {
            base_ms: base_ms.max(1),
            max_ms: max_ms.max(base_ms.max(1)),
            attempt: 0,
        }
    }

    /// Standard reconnect profile: 500 ms → 15 s.
    pub fn reconnect() -> Self {
        Self::new(500, 15_000)
    }

    pub fn attempt(&self) -> u32 {
        self.attempt
    }

    pub fn reset(&mut self) {
        self.attempt = 0;
    }

    /// Next delay, advancing the attempt counter.
    pub fn next_delay(&mut self) -> Duration {
        let rnd = rand::random::<f64>();
        Duration::from_millis(self.next_delay_with(rnd))
    }

    /// Same as [`Backoff::next_delay`] but with injected randomness in
    /// `[0, 1)` — deterministic for tests. The delay lands in
    /// `[raw/2, raw)` where `raw = min(max, base · 2^attempt)`.
    pub fn next_delay_with(&mut self, rnd: f64) -> u64 {
        let exp = self.attempt.min(20); // avoid overflow
        let raw = self.base_ms.saturating_mul(1u64 << exp).min(self.max_ms);
        self.attempt = self.attempt.saturating_add(1);
        let half = raw / 2;
        let span = raw - half;
        half + (rnd.clamp(0.0, 0.999_999) * span as f64) as u64
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn grows_exponentially_and_caps() {
        let mut b = Backoff::new(500, 8_000);
        // With rnd = 0 the delay is exactly half of the raw window.
        assert_eq!(b.next_delay_with(0.0), 250);
        assert_eq!(b.next_delay_with(0.0), 500);
        assert_eq!(b.next_delay_with(0.0), 1_000);
        assert_eq!(b.next_delay_with(0.0), 2_000);
        assert_eq!(b.next_delay_with(0.0), 4_000);
        assert_eq!(b.next_delay_with(0.0), 4_000);
        assert_eq!(b.attempt(), 6);
    }

    #[test]
    fn jitter_is_within_bounds() {
        for rnd in [0.0, 0.25, 0.5, 0.999] {
            let mut b = Backoff::new(1000, 60_000);
            let d = b.next_delay_with(rnd);
            assert!(d >= 500, "delay {d} below half of raw");
            assert!(d <= 1000, "delay {d} above raw");
        }
    }

    #[test]
    fn reset_starts_over() {
        let mut b = Backoff::new(500, 8_000);
        b.next_delay_with(1.0);
        b.next_delay_with(1.0);
        assert_eq!(b.attempt(), 2);
        b.reset();
        assert_eq!(b.attempt(), 0);
        assert_eq!(b.next_delay_with(0.0), 250);
    }
}
