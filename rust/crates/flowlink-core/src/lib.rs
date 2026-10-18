//! # FlowLink Core
//!
//! Platform-agnostic engine behind the FlowLink client/server apps.
//!
//! The core owns everything that is not native UI: the wire protocol, the
//! encrypted transport, pairing, device memory, local-network discovery,
//! clipboard relay, edge-crossing state and connection lifecycle
//! (reconnect, heartbeat, metrics).
//!
//! Native shells (Swift on macOS, Win32 on Windows) are responsible for
//! capturing and injecting input and for reading/writing the clipboard;
//! they talk to the core through the [`host::Host`] callback trait
//! (locally) or the C ABI in `flowlink-ffi`.
//!
//! ## Layering
//!
//! ```text
//!  +--------------------- platform shells ---------------------+
//!  |  Swift/SwiftUI (macOS)          Rust + Win32 (Windows)    |
//!  +--------------------------+--------------------------------+
//!                             |  host callbacks / commands
//!  +-------------------------- v ------------------------------+
//!  |                        engine                             |
//!  |  roles, commands, state machine, clipboard gate, status   |
//!  +----+--------------+---------------+----------------------+
//!       |              |               |
//!   session.rs     discovery.rs     config/crypto
//!   (TLS + wire)   (mDNS)           (identity, pairing, storage)
//! ```
//!
//! Nothing in this crate depends on the platform UI; it builds and tests
//! on macOS, Windows and Linux.

pub mod backoff;
pub mod clipboard;
pub mod coalesce;
pub mod config;
pub mod crypto;
pub mod discovery;
pub mod edge;
pub mod engine;
pub mod framing;
pub mod hid;
pub mod host;
pub mod logging;
pub mod pairing;
pub mod protocol;
pub mod qr;
pub mod session;
pub mod status;
pub mod transport;
pub mod types;
pub mod util;

pub use types::*;

/// Crate version, reported to peers and shown in the UI.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
