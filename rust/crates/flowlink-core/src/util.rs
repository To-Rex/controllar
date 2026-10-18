//! Small shared utilities: time, hostname, local addresses.

use std::net::IpAddr;
use std::time::{SystemTime, UNIX_EPOCH};

/// Milliseconds since the Unix epoch.
pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// Best-effort machine name.
pub fn hostname() -> String {
    hostname::get()
        .ok()
        .and_then(|h| h.into_string().ok())
        .filter(|h| !h.trim().is_empty())
        .unwrap_or_else(|| "FlowLink device".to_string())
}

/// All usable local IPv4/v6 addresses; private LAN addresses first,
/// loopback last. Used for status display and QR invite links.
pub fn local_addrs() -> Vec<String> {
    let mut lan = Vec::new();
    let mut other = Vec::new();
    let mut loopback = Vec::new();

    if let Ok(ifaces) = if_addrs::get_if_addrs() {
        for iface in ifaces {
            let ip = iface.ip();
            // Ignore link-local and virtual/vpn-ish noise where possible.
            match ip {
                IpAddr::V4(v4) => {
                    if v4.is_loopback() {
                        loopback.push(v4.to_string());
                    } else if v4.is_private() {
                        lan.push(v4.to_string());
                    } else if !v4.is_link_local() {
                        other.push(v4.to_string());
                    }
                }
                IpAddr::V6(v6) => {
                    if v6.is_loopback() {
                        loopback.push(v6.to_string());
                    } else if !v6.is_unicast_link_local() {
                        other.push(v6.to_string());
                    }
                }
            }
        }
    }

    lan.extend(other);
    lan.extend(loopback);
    lan.dedup();
    lan
}

/// The most likely address for LAN connections (used in QR invite links).
pub fn preferred_lan_addr() -> Option<String> {
    local_addrs().into_iter().next()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn now_ms_is_reasonable() {
        // After 2023-11-14.
        assert!(now_ms() > 1_700_000_000_000);
    }

    #[test]
    fn hostname_is_not_empty() {
        assert!(!hostname().trim().is_empty());
    }
}
