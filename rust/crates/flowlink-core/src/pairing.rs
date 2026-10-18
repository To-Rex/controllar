//! Pairing: 6-digit codes shown on the server, one-time tokens shared
//! afterwards.
//!
//! The pairing code is an *authentication ceremony*: it is displayed only on
//! the server's screen (or embedded in a QR code shown on that screen) and
//! must be typed on the client. It expires after [`DEFAULT_TTL_MS`] and dies
//! after [`MAX_ATTEMPTS`] wrong entries.

use subtle::ConstantTimeEq;

/// How long a generated code stays valid.
pub const DEFAULT_TTL_MS: u64 = 120_000;

/// Wrong attempts allowed before the code is invalidated.
pub const MAX_ATTEMPTS: u8 = 3;

/// A live pairing code.
#[derive(Clone, Debug)]
pub struct PairingCode {
    pub code: String,
    pub created_ms: u64,
    pub ttl_ms: u64,
    pub attempts_left: u8,
}

/// Result of verifying a user-entered code.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PairVerdict {
    Accepted,
    Wrong { attempts_left: u8 },
    Expired,
    Exhausted,
}

impl PairingCode {
    /// Generate a fresh code. First digit is non-zero so the code is always
    /// six digits when rendered.
    pub fn generate(ttl_ms: u64) -> Self {
        let n = rand::Rng::gen_range(&mut rand::thread_rng(), 100_000u32..=999_999u32);
        PairingCode {
            code: n.to_string(),
            created_ms: crate::util::now_ms(),
            ttl_ms,
            attempts_left: MAX_ATTEMPTS,
        }
    }

    pub fn generate_default() -> Self {
        Self::generate(DEFAULT_TTL_MS)
    }

    pub fn remaining_ms(&self, now_ms: u64) -> u64 {
        (self.created_ms + self.ttl_ms).saturating_sub(now_ms)
    }

    pub fn expired(&self, now_ms: u64) -> bool {
        self.remaining_ms(now_ms) == 0
    }

    /// Verify an entered code, consuming an attempt on failure.
    pub fn verify(&mut self, input: &str, now_ms: u64) -> PairVerdict {
        if self.expired(now_ms) {
            return PairVerdict::Expired;
        }
        if self.attempts_left == 0 {
            return PairVerdict::Exhausted;
        }
        let normalized = normalize_code(input);
        if constant_time_eq(self.code.as_bytes(), normalized.as_bytes()) {
            PairVerdict::Accepted
        } else {
            self.attempts_left = self.attempts_left.saturating_sub(1);
            PairVerdict::Wrong {
                attempts_left: self.attempts_left,
            }
        }
    }
}

/// Keep only the first six ASCII digits (users paste "123 456" or "123-456").
pub fn normalize_code(input: &str) -> String {
    input
        .chars()
        .filter(|c| c.is_ascii_digit())
        .take(6)
        .collect()
}

/// A 32-byte random shared secret, hex-encoded.
pub fn generate_token() -> String {
    let bytes: [u8; 32] = rand::random();
    hex::encode(bytes)
}

/// Constant-time comparison of two tokens.
pub fn token_matches(expected: &str, candidate: &str) -> bool {
    constant_time_eq(expected.as_bytes(), candidate.as_bytes())
}

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        // Still compare something constant-time-ish to avoid trivial timing
        // of length differences; length itself is not secret here.
        return false;
    }
    a.ct_eq(b).into()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn code_generation_shape() {
        for _ in 0..100 {
            let pc = PairingCode::generate_default();
            assert_eq!(pc.code.len(), 6);
            assert!(!pc.code.starts_with('0'));
            assert_eq!(pc.attempts_left, MAX_ATTEMPTS);
        }
    }

    #[test]
    fn verify_accepts_with_separators() {
        let mut pc = PairingCode::generate_default();
        let spaced = format!("{} {}", &pc.code[..3], &pc.code[3..]);
        assert_eq!(
            pc.verify(&spaced, crate::util::now_ms()),
            PairVerdict::Accepted
        );
    }

    #[test]
    fn wrong_attempts_decrement_and_exhaust() {
        let mut pc = PairingCode::generate_default();
        let now = crate::util::now_ms();
        assert_eq!(
            pc.verify("000000", now),
            PairVerdict::Wrong { attempts_left: 2 }
        );
        assert_eq!(
            pc.verify("000000", now),
            PairVerdict::Wrong { attempts_left: 1 }
        );
        assert_eq!(
            pc.verify("000000", now),
            PairVerdict::Wrong { attempts_left: 0 }
        );
        assert_eq!(pc.verify(&pc.code.clone(), now), PairVerdict::Exhausted);
    }

    #[test]
    fn expiry() {
        let mut pc = PairingCode::generate(1000);
        let now = pc.created_ms;
        assert!(!pc.expired(now + 999));
        assert!(pc.expired(now + 1000));
        assert_eq!(
            pc.verify(&pc.code.clone(), now + 5000),
            PairVerdict::Expired
        );
    }

    #[test]
    fn tokens_compare_correctly() {
        let token = generate_token();
        assert_eq!(token.len(), 64);
        assert!(token_matches(&token, &token));
        assert!(!token_matches(&token, "00"));
        assert!(!token_matches("abcd", "abce"));
    }
}
