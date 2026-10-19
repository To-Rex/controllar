//! Robustness scenarios added after real-world connection problems:
//!
//! * a busy port must not stop the server (it falls back and the real port
//!   reaches the peer through discovery and invite links);
//! * a pairing code must survive failed connection attempts (a transport
//!   error between attempts must not force the user to retype it).

use std::sync::Arc;
use std::time::Duration;

use flowlink_core::engine::{Cmd, Engine};
use flowlink_core::host::Host;
use flowlink_core::types::InputEvent;
use serde_json::Value;

/// Records callbacks so the scenarios can wait on visible state.
#[derive(Default)]
struct RecordingHost {
    statuses: std::sync::Mutex<Vec<Value>>,
    notices: std::sync::Mutex<Vec<Value>>,
}

impl RecordingHost {
    fn state(&self) -> String {
        self.statuses
            .lock()
            .unwrap()
            .last()
            .and_then(|s| s["state"].as_str().map(|v| v.to_string()))
            .unwrap_or_default()
    }

    fn latest(&self) -> Value {
        self.statuses
            .lock()
            .unwrap()
            .last()
            .cloned()
            .unwrap_or(Value::Null)
    }
}

impl Host for RecordingHost {
    fn on_status(&self, status_json: &str) {
        if let Ok(value) = serde_json::from_str::<Value>(status_json) {
            let mut statuses = self.statuses.lock().unwrap();
            statuses.push(value);
            if statuses.len() > 128 {
                statuses.remove(0);
            }
        }
    }
    fn on_input(&self, _event: &InputEvent) {}
    fn on_clipboard(&self, _text: &str) {}
    fn on_notice(&self, notice_json: &str) {
        if let Ok(value) = serde_json::from_str::<Value>(notice_json) {
            self.notices.lock().unwrap().push(value);
        }
    }
}

fn spawn_engine(host: Arc<RecordingHost>) -> (Arc<Engine>, tempfile::TempDir) {
    let dir = tempfile::TempDir::new().expect("tempdir");
    let engine = Engine::new(dir.path().to_path_buf(), host).expect("engine");
    (engine, dir)
}

async fn wait_until<F: Fn() -> bool>(what: &str, timeout: Duration, f: F) {
    let deadline = tokio::time::Instant::now() + timeout;
    loop {
        if f() {
            return;
        }
        if tokio::time::Instant::now() > deadline {
            panic!("timed out waiting for: {what}");
        }
        tokio::time::sleep(Duration::from_millis(40)).await;
    }
}

/// A port that is guaranteed to be occupied for the duration of the test.
fn busy_port() -> (std::net::TcpListener, u16) {
    let listener = std::net::TcpListener::bind("0.0.0.0:0").expect("blocker");
    let port = listener.local_addr().unwrap().port();
    (listener, port)
}

#[tokio::test(flavor = "multi_thread")]
async fn server_falls_back_to_next_port_when_busy() {
    let result = tokio::time::timeout(Duration::from_secs(30), async {
        let (blocker, busy) = busy_port();

        let host = Arc::new(RecordingHost::default());
        let (server, _dir) = spawn_engine(host.clone());
        server.execute(Cmd::SetListenPort(busy));
        server.execute(Cmd::Start);

        wait_until("fallback listening", Duration::from_secs(15), || {
            host.state() == "listening"
        })
        .await;

        let status = host.latest();
        let actual = status["local"]["port"].as_u64().expect("port") as u16;
        assert_ne!(actual, busy, "the busy port must not be reported as ours");
        assert!(
            (busy + 1..=busy + 9).contains(&actual),
            "fallback should stay close to the configured port (got {actual}, busy {busy})"
        );
        assert_eq!(status["listening"], true);

        // Invite links carry the actual port, so a peer pasting one connects
        // to the right place without knowing about the fallback.
        let link = server.invite_link();
        let parsed = flowlink_core::qr::ConnectLink::parse(&link).expect("link parses");
        assert_eq!(parsed.port, actual);
        assert!(
            parsed.code.is_some(),
            "a live code is embedded while listening"
        );

        // The user is told what happened.
        let noticed = host.notices.lock().unwrap().iter().any(|notice| {
            notice["message"]
                .as_str()
                .unwrap_or("")
                .contains("listening on")
        });
        assert!(noticed, "a notice should explain the port fallback");

        drop(blocker);
    })
    .await;
    result.expect("fallback scenario timed out");
}

#[tokio::test(flavor = "multi_thread")]
async fn pairing_code_survives_failed_attempts() {
    let result = tokio::time::timeout(Duration::from_secs(30), async {
        let (blocker, dead_port) = busy_port();
        // The blocker only proves the port is taken; connecting there will be
        // refused fast, which is exactly the "first attempt fails" situation.
        drop(blocker);

        let host = Arc::new(RecordingHost::default());
        let (client, _dir) = spawn_engine(host.clone());
        client.execute(Cmd::Connect {
            host: "127.0.0.1".to_string(),
            port: dead_port,
            code: Some("123456".to_string()),
            fingerprint: None,
        });

        wait_until("first attempt failed", Duration::from_secs(15), || {
            matches!(host.state().as_str(), "reconnecting" | "error")
        })
        .await;

        // The code must still be available for the next attempt.
        assert_eq!(
            client.pending_pair_code.lock().as_deref(),
            Some("123456"),
            "the pairing code must survive a transport failure"
        );
    })
    .await;
    result.expect("retention scenario timed out");
}
