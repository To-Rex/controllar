//! End-to-end integration: two engines, real TCP + TLS on loopback,
//! pairing with a code, input relay, clipboard sync and reclaim.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use flowlink_core::engine::{Cmd, Engine};
use flowlink_core::host::Host;
use flowlink_core::types::{Edge, HostInput, InputEvent, Modifiers};
use serde_json::Value;
use tempfile::TempDir;

/// Host that records everything the engine pushes to it.
#[derive(Default)]
struct RecordingHost {
    statuses: Mutex<Vec<Value>>,
    inputs: Mutex<Vec<InputEvent>>,
    clipboards: Mutex<Vec<String>>,
    notices: Mutex<Vec<Value>>,
}

impl RecordingHost {
    fn latest_status(&self) -> Option<Value> {
        self.statuses.lock().unwrap().last().cloned()
    }

    fn state(&self) -> String {
        self.latest_status()
            .and_then(|s| s["state"].as_str().map(|v| v.to_string()))
            .unwrap_or_default()
    }

    fn remote_active(&self) -> bool {
        self.latest_status()
            .and_then(|s| s["remote_active"].as_bool())
            .unwrap_or(false)
    }

    fn focused(&self) -> bool {
        self.latest_status()
            .and_then(|s| s["focused"].as_bool())
            .unwrap_or(false)
    }

    fn forwarding(&self) -> bool {
        self.latest_status()
            .and_then(|s| s["forwarding"].as_bool())
            .unwrap_or(false)
    }

    fn inputs(&self) -> Vec<InputEvent> {
        self.inputs.lock().unwrap().clone()
    }

    fn clipboards(&self) -> Vec<String> {
        self.clipboards.lock().unwrap().clone()
    }
}

impl Host for RecordingHost {
    fn on_status(&self, status_json: &str) {
        if let Ok(value) = serde_json::from_str::<Value>(status_json) {
            self.statuses.lock().unwrap().push(value);
        }
    }
    fn on_input(&self, event: &InputEvent) {
        self.inputs.lock().unwrap().push(*event);
    }
    fn on_clipboard(&self, text: &str) {
        self.clipboards.lock().unwrap().push(text.to_string());
    }
    fn on_notice(&self, notice_json: &str) {
        if let Ok(value) = serde_json::from_str::<Value>(notice_json) {
            self.notices.lock().unwrap().push(value);
        }
    }
}

fn spawn_engine(host: Arc<RecordingHost>) -> (Arc<Engine>, TempDir) {
    let dir = TempDir::new().expect("tempdir");
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

fn free_port() -> u16 {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind ephemeral");
    listener.local_addr().unwrap().port()
}

#[tokio::test(flavor = "multi_thread")]
async fn two_engines_pair_and_exchange_input() {
    let result = tokio::time::timeout(Duration::from_secs(45), async {
        // ---- Server engine -------------------------------------------------
        let server_host = Arc::new(RecordingHost::default());
        let (server, _server_dir) = spawn_engine(server_host.clone());

        let port = free_port();
        server.execute(Cmd::SetListenPort(port));
        server.execute(Cmd::SetLocalScreen(flowlink_core::types::ScreenInfo {
            width: 1920.0,
            height: 1080.0,
            scale: 2.0,
        }));
        server.execute(Cmd::Start);

        wait_until("server listening", Duration::from_secs(10), || {
            server_host.state() == "listening"
        })
        .await;

        let code = {
            let status: Value = serde_json::from_str(&server.status_json()).expect("status json");
            status["pairing"]["code"]
                .as_str()
                .expect("pairing code visible")
                .to_string()
        };
        assert_eq!(code.len(), 6);

        // ---- Client engine -------------------------------------------------
        let client_host = Arc::new(RecordingHost::default());
        let (client, _client_dir) = spawn_engine(client_host.clone());
        client.execute(Cmd::Connect {
            host: "127.0.0.1".to_string(),
            port,
            code: Some(code),
            fingerprint: None,
        });

        wait_until("both connected", Duration::from_secs(15), || {
            server_host.state() == "connected" && client_host.state() == "connected"
        })
        .await;

        // Server remembers the paired device (device memory).
        let server_cfg: Value = serde_json::from_str(&server.config_json()).expect("config json");
        assert_eq!(
            server_cfg["paired_devices"].as_array().unwrap().len(),
            1,
            "server should remember the paired device"
        );
        // Client now holds a token for silent reconnects.
        let client_cfg: Value = serde_json::from_str(&client.config_json()).expect("config json");
        assert!(
            client_cfg["last_server"]["token"].as_str().is_some(),
            "client should store the pairing token"
        );

        // The server's screen owns the shared cursor when the session starts;
        // the client begins by forwarding its input.
        wait_until("server starts focused", Duration::from_secs(5), || {
            server_host.focused()
        })
        .await;
        wait_until("client starts forwarding", Duration::from_secs(5), || {
            client_host.forwarding()
        })
        .await;

        // ---- Edge crossing hands the cursor to the client -------------------
        server.execute(Cmd::SetEdge(Edge::Right));
        server.execute(Cmd::HostInput(HostInput::MouseMove {
            x_px: 1920.0,
            y_px: 540.0,
            dx: 14.0,
            dy: 0.0,
        }));

        wait_until("client received Enter", Duration::from_secs(5), || {
            client_host
                .inputs()
                .iter()
                .any(|e| matches!(e, InputEvent::Enter { .. }))
        })
        .await;
        wait_until("client owns the cursor", Duration::from_secs(5), || {
            client_host.focused()
        })
        .await;
        wait_until(
            "server forwards input to the client",
            Duration::from_secs(5),
            || server_host.forwarding(),
        )
        .await;

        let enter = client_host
            .inputs()
            .into_iter()
            .find(|e| matches!(e, InputEvent::Enter { .. }))
            .expect("enter event");
        match enter {
            InputEvent::Enter { edge, along_norm } => {
                assert_eq!(edge, Edge::Right);
                assert!((along_norm - 0.5).abs() < 0.01, "along={along_norm}");
            }
            other => panic!("unexpected {other:?}"),
        }

        // ---- Key events are relayed while remote is active -----------------
        server.execute(Cmd::HostInput(HostInput::Key {
            usage: flowlink_core::hid::usage::A,
            down: true,
            modifiers: Modifiers::from_bits(Modifiers::SHIFT),
        }));
        server.execute(Cmd::HostInput(HostInput::Key {
            usage: flowlink_core::hid::usage::A,
            down: false,
            modifiers: Modifiers::from_bits(Modifiers::SHIFT),
        }));

        wait_until("client received key events", Duration::from_secs(5), || {
            client_host
                .inputs()
                .iter()
                .filter(|e| matches!(e, InputEvent::Key { .. }))
                .count()
                >= 2
        })
        .await;

        // ---- Relative mouse motion is coalesced and relayed ----------------
        for i in 0..30 {
            server.execute(Cmd::HostInput(HostInput::MouseMove {
                x_px: 1800.0 - i as f64,
                y_px: 500.0,
                dx: 2.0,
                dy: -1.0,
            }));
        }
        wait_until(
            "client received mouse motion",
            Duration::from_secs(5),
            || {
                client_host
                    .inputs()
                    .iter()
                    .any(|e| matches!(e, InputEvent::MouseMove { .. }))
            },
        )
        .await;

        // ---- Clipboard travels client → server -----------------------------
        client.execute(Cmd::SetClipboardText("salom dunyo 🌍".to_string()));
        wait_until("server received clipboard", Duration::from_secs(5), || {
            server_host
                .clipboards()
                .iter()
                .any(|t| t == "salom dunyo 🌍")
        })
        .await;

        // ---- Symmetric focus: the client hands the cursor back -------------
        // Deliberately not changing the client's configured edge here: the
        // return edge is derived from the edge the cursor entered through, so
        // mismatched per-machine settings must not break the trip back.
        client.execute(Cmd::ToggleControl);
        wait_until(
            "server owns the cursor again",
            Duration::from_secs(5),
            || server_host.focused(),
        )
        .await;
        wait_until("client forwards again", Duration::from_secs(5), || {
            client_host.forwarding()
        })
        .await;
        // The server is told to appear on the mirror of the edge the cursor
        // actually left through (the client's left edge → server's Enter{Left}).
        wait_until(
            "server received a mirrored Enter",
            Duration::from_secs(5),
            || {
                server_host.inputs().iter().any(|event| {
                    matches!(
                        event,
                        InputEvent::Enter {
                            edge: Edge::Left,
                            ..
                        }
                    )
                })
            },
        )
        .await;

        // One keyboard on either desk types into the focused screen: the
        // client's physical keys are now relayed to the server.
        client.execute(Cmd::HostInput(HostInput::Key {
            usage: flowlink_core::hid::usage::B,
            down: true,
            modifiers: Modifiers::NONE,
        }));
        wait_until(
            "server received the client's key",
            Duration::from_secs(5),
            || {
                server_host.inputs().iter().any(|event| {
                    matches!(
                        event,
                        InputEvent::Key { usage, down: true, .. }
                            if *usage == flowlink_core::hid::usage::B
                    )
                })
            },
        )
        .await;

        // ---- Reclaim from the unfocused side (hotkey on the client) --------
        client.execute(Cmd::RequestControl);
        wait_until(
            "client owns the cursor again",
            Duration::from_secs(5),
            || client_host.focused(),
        )
        .await;
        wait_until(
            "server let go of the cursor",
            Duration::from_secs(5),
            || !server_host.focused(),
        )
        .await;
        wait_until("server released held input", Duration::from_secs(5), || {
            server_host
                .inputs()
                .iter()
                .any(|e| matches!(e, InputEvent::ReleaseAll))
        })
        .await;

        // ---- Disconnect: server returns to listening ------------------------
        client.execute(Cmd::Disconnect);
        wait_until("server back to listening", Duration::from_secs(10), || {
            server_host.state() == "listening"
        })
        .await;
        assert!(!server.has_session());

        // ---- Reconnect uses the stored token (no code needed) ---------------
        client.execute(Cmd::Connect {
            host: "127.0.0.1".to_string(),
            port,
            code: None,
            fingerprint: None,
        });
        wait_until("token reconnect", Duration::from_secs(15), || {
            client_host.state() == "connected" && server_host.state() == "connected"
        })
        .await;
        // Clipboard now flows server → client as well.
        server.execute(Cmd::SetClipboardText("javob".to_string()));
        wait_until("client received clipboard", Duration::from_secs(5), || {
            client_host.clipboards().iter().any(|t| t == "javob")
        })
        .await;

        // Reconnects must refresh the remembered server, not duplicate it.
        let client_cfg: Value = serde_json::from_str(&client.config_json()).expect("config json");
        let remembered = client_cfg["paired_devices"].as_array().unwrap();
        let server_id = client_cfg["last_server"]["device_id"]
            .as_str()
            .expect("server id");
        let matching = remembered
            .iter()
            .filter(|device| device["id"] == server_id)
            .count();
        assert_eq!(matching, 1, "the server must be remembered exactly once");
    })
    .await;
    result.expect("integration scenario timed out");
}

/// A wrong pairing code must be rejected and control never granted.
#[tokio::test(flavor = "multi_thread")]
async fn wrong_pairing_code_is_rejected() {
    let result = tokio::time::timeout(Duration::from_secs(30), async {
        let server_host = Arc::new(RecordingHost::default());
        let (server, _d1) = spawn_engine(server_host.clone());
        let port = free_port();
        server.execute(Cmd::SetListenPort(port));
        server.execute(Cmd::Start);
        wait_until("listening", Duration::from_secs(10), || {
            server_host.state() == "listening"
        })
        .await;

        let client_host = Arc::new(RecordingHost::default());
        let (client, _d2) = spawn_engine(client_host.clone());
        client.execute(Cmd::Connect {
            host: "127.0.0.1".to_string(),
            port,
            code: Some("000000".to_string()),
            fingerprint: None,
        });

        // Expect the client to report a pairing notice and never connect.
        wait_until("pairing failure notice", Duration::from_secs(15), || {
            client_host
                .notices
                .lock()
                .unwrap()
                .iter()
                .any(|n| n["kind"] == "pairing")
        })
        .await;
        assert_ne!(client_host.state(), "connected");
        assert_ne!(server_host.state(), "connected");

        // The correct code still works afterwards.
        let code = {
            let status: Value = serde_json::from_str(&server.status_json()).unwrap();
            status["pairing"]["code"].as_str().unwrap().to_string()
        };
        client.execute(Cmd::SubmitPairCode(code));
        wait_until(
            "connected after correct code",
            Duration::from_secs(15),
            || client_host.state() == "connected" && server_host.state() == "connected",
        )
        .await;
    })
    .await;
    result.expect("reject scenario timed out");
}
