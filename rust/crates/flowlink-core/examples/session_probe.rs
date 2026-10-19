//! Session probe — connects to the configured server exactly like the app
//! does and prints the session lifecycle (state changes, received input,
//! clipboard, notices, engine logs) to stderr with timestamps.
//!
//! Useful to reproduce and diagnose connection flapping on a real network
//! without the UI in the way:
//!
//! ```text
//! cargo run -p flowlink-core --example session_probe -- <config-dir> [seconds]
//! ```
//!
//! Use a *copy* of a real config directory to reuse its identity and token
//! (the peer remembers that device), e.g.:
//!
//! ```text
//! cp -R "$HOME/Library/Application Support/FlowLink" /tmp/probe-config
//! cargo run -p flowlink-core --example session_probe -- /tmp/probe-config 120
//! ```

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use flowlink_core::engine::{Cmd, Engine};
use flowlink_core::host::Host;
use flowlink_core::types::{InputEvent, Mode};

struct ProbeHost {
    start: Instant,
    last_state: Mutex<String>,
}

fn stamp(start: Instant) -> String {
    format!("+{:8.3}s", start.elapsed().as_millis() as f64 / 1000.0)
}

impl Host for ProbeHost {
    fn on_status(&self, status_json: &str) {
        let Ok(value) = serde_json::from_str::<serde_json::Value>(status_json) else {
            return;
        };
        let state = value["state"].as_str().unwrap_or("").to_string();
        let mut last = self.last_state.lock().unwrap();
        if *last != state {
            eprintln!(
                "{} STATE -> {state:<12} remote_active={} events_in={} rtt_ms={}",
                stamp(self.start),
                value["remote_active"],
                value["metrics"]["events_in"],
                value["metrics"]["rtt_ms"]
            );
            *last = state;
        }
    }

    fn on_input(&self, event: &InputEvent) {
        eprintln!("{} INPUT {:?}", stamp(self.start), event);
    }

    fn on_clipboard(&self, text: &str) {
        eprintln!("{} CLIPBOARD {} bytes", stamp(self.start), text.len());
    }

    fn on_notice(&self, notice_json: &str) {
        eprintln!("{} NOTICE {notice_json}", stamp(self.start));
    }
}

fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            std::env::var("RUST_LOG")
                .unwrap_or_else(|_| "flowlink_core=debug,flowlink=debug".to_string()),
        )
        .with_target(false)
        .init();

    let mut args = std::env::args().skip(1);
    let dir = args
        .next()
        .expect("usage: session_probe <config-dir> [seconds]");
    let seconds: u64 = args.next().and_then(|s| s.parse().ok()).unwrap_or(120);

    let start = Instant::now();
    let host = Arc::new(ProbeHost {
        start,
        last_state: Mutex::new(String::new()),
    });
    let engine = Engine::new(dir.into(), host).expect("engine");
    eprintln!("{} engine ready", stamp(start));

    engine.execute(Cmd::SetMode(Mode::Client));
    engine.execute(Cmd::Start);
    eprintln!("{} connecting (probing for {seconds}s)", stamp(start));

    std::thread::sleep(Duration::from_secs(seconds));
    eprintln!("{} done", stamp(start));
}
