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

        // Compat: older shells read `remote_active`; it must always mirror
        // `forwarding` so both directions stay in agreement.
        assert_eq!(
            server_host.remote_active(),
            server_host.forwarding(),
            "remote_active must mirror forwarding on the server"
        );
        assert_eq!(
            client_host.remote_active(),
            client_host.forwarding(),
            "remote_active must mirror forwarding on the client"
        );

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
        // The exit edge is each machine's *configured* edge, every time (the
        // self-aligning edge was removed: it flipped the return trip against
        // the user's physical layout after a transition or reconnect).
        client.execute(Cmd::SetEdge(Edge::Left));
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
        // The client leaves through its configured (left) edge → the server
        // receives Enter{Left}.
        wait_until(
            "server received the client's Enter",
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

/// The shared cursor must round-trip repeatedly in both directions: the exit
/// edge is each machine's *configured* edge, every time — it must never flip
/// or get stuck after a transition (the bug that made one direction stop
/// working after the other direction had been used).
#[tokio::test(flavor = "multi_thread")]
async fn cursor_round_trips_repeatedly() {
    let result = tokio::time::timeout(Duration::from_secs(60), async {
        let server_host = Arc::new(RecordingHost::default());
        let (server, _server_dir) = spawn_engine(server_host.clone());
        let port = free_port();
        server.execute(Cmd::SetListenPort(port));
        server.execute(Cmd::Start);

        wait_until("server listening", Duration::from_secs(10), || {
            server_host.state() == "listening"
        })
        .await;

        let code = {
            let status: Value = serde_json::from_str(&server.status_json()).expect("status json");
            status["pairing"]["code"].as_str().unwrap().to_string()
        };

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

        wait_until("server starts focused", Duration::from_secs(5), || {
            server_host.focused()
        })
        .await;

        // Server exits to the right, client exits to the left (consistent
        // mirrored configs, like two adjacent screens).
        server.execute(Cmd::SetEdge(Edge::Right));
        client.execute(Cmd::SetEdge(Edge::Left));

        for round in 0..3 {
            // 1. Server pushes the cursor out through its right edge.
            server.execute(Cmd::HostInput(HostInput::MouseMove {
                x_px: 1700.0,
                y_px: 540.0,
                dx: -10.0,
                dy: 0.0,
            }));
            server.execute(Cmd::HostInput(HostInput::MouseMove {
                x_px: 1920.0,
                y_px: 540.0,
                dx: 20.0,
                dy: 0.0,
            }));
            wait_until(
                &format!("round {round}: client owns the cursor"),
                Duration::from_secs(5),
                || client_host.focused(),
            )
            .await;

            // 2. The cursor entered near the client's left edge: re-arm by
            //    moving inward, then push out through the left edge.
            client.execute(Cmd::HostInput(HostInput::MouseMove {
                x_px: 200.0,
                y_px: 540.0,
                dx: 40.0,
                dy: 0.0,
            }));
            client.execute(Cmd::HostInput(HostInput::MouseMove {
                x_px: 0.0,
                y_px: 540.0,
                dx: -40.0,
                dy: 0.0,
            }));
            wait_until(
                &format!("round {round}: server owns the cursor again"),
                Duration::from_secs(5),
                || server_host.focused(),
            )
            .await;
        }
    })
    .await;
    result.expect("round-trip scenario timed out");
}

/// Helper: start a paired server/client pair with the given edges.
async fn paired_pair(
    server_edge: Edge,
    client_edge: Edge,
) -> (
    Arc<Engine>,
    Arc<RecordingHost>,
    Arc<Engine>,
    Arc<RecordingHost>,
    TempDir,
    TempDir,
) {
    let server_host = Arc::new(RecordingHost::default());
    let (server, server_dir) = spawn_engine(server_host.clone());
    let port = free_port();
    server.execute(Cmd::SetListenPort(port));
    server.execute(Cmd::SetEdge(server_edge));
    server.execute(Cmd::Start);
    wait_until("server listening", Duration::from_secs(10), || {
        server_host.state() == "listening"
    })
    .await;
    let code = {
        let status: Value = serde_json::from_str(&server.status_json()).expect("status json");
        status["pairing"]["code"].as_str().unwrap().to_string()
    };

    let client_host = Arc::new(RecordingHost::default());
    let (client, client_dir) = spawn_engine(client_host.clone());
    // Edge set *before* connecting, like a real installation with mismatched
    // per-machine settings.
    client.execute(Cmd::SetEdge(client_edge));
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
    (
        server,
        server_host,
        client,
        client_host,
        server_dir,
        client_dir,
    )
}

/// The client's own edge setting may disagree with the server's (a very common
/// real-world setup: both machines left at "right"). The server announces the
/// topology, the client mirrors it, and the cursor must still return through
/// the correct edge.
#[tokio::test(flavor = "multi_thread")]
async fn mismatched_client_edge_is_mirrored_from_the_server() {
    let result = tokio::time::timeout(Duration::from_secs(45), async {
        // Server's peer is on its right; the client (wrongly) also says right.
        let (server, server_host, client, client_host, _sd, _cd) =
            paired_pair(Edge::Right, Edge::Right).await;

        // The server owns the cursor at session start. Its announced topology
        // must have corrected the client to the mirrored (left) edge.
        wait_until(
            "client mirrors the server edge",
            Duration::from_secs(5),
            || {
                client_host
                    .latest_status()
                    .and_then(|s| s["peer_edge"].as_str().map(|v| v.to_string()))
                    .as_deref()
                    == Some("left")
            },
        )
        .await;
        let client_cfg: Value = serde_json::from_str(&client.config_json()).unwrap();
        assert_eq!(client_cfg["edge"], "left", "mirrored edge is persisted");

        // Server pushes out through its right edge.
        server.execute(Cmd::HostInput(HostInput::MouseMove {
            x_px: 1919.0,
            y_px: 540.0,
            dx: 18.0,
            dy: 0.0,
        }));
        wait_until("client owns the cursor", Duration::from_secs(5), || {
            client_host.focused()
        })
        .await;

        // Client re-arms by moving inward, then pushes out through the
        // *mirrored* left edge — even though its own setting said right.
        client.execute(Cmd::HostInput(HostInput::MouseMove {
            x_px: 300.0,
            y_px: 540.0,
            dx: 30.0,
            dy: 0.0,
        }));
        client.execute(Cmd::HostInput(HostInput::MouseMove {
            x_px: 0.0,
            y_px: 540.0,
            dx: -30.0,
            dy: 0.0,
        }));
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
        assert!(
            server_host.inputs().iter().any(|event| matches!(
                event,
                InputEvent::Enter {
                    edge: Edge::Left,
                    ..
                }
            )),
            "the server must receive the mirrored return edge"
        );
    })
    .await;
    result.expect("mismatched-edge scenario timed out");
}

/// While the cursor is being driven by the *other* computer's mouse, the
/// synthetic pointer hitting the edge must hand focus back — this is the path
/// the local event tap cannot see. `Cmd::HandOff` models the injector.
#[tokio::test(flavor = "multi_thread")]
async fn injected_edge_push_hands_focus_back() {
    let result = tokio::time::timeout(Duration::from_secs(45), async {
        let (server, server_host, client, client_host, _sd, _cd) =
            paired_pair(Edge::Right, Edge::Left).await;

        // Server → client: client now owns the cursor and its injector knows
        // the peer-facing edge is left.
        server.execute(Cmd::HostInput(HostInput::MouseMove {
            x_px: 1919.0,
            y_px: 540.0,
            dx: 18.0,
            dy: 0.0,
        }));
        wait_until("client owns the cursor", Duration::from_secs(5), || {
            client_host.focused()
        })
        .await;

        // A push on the wrong edge must be ignored (never steal focus).
        client.execute(Cmd::HandOff {
            edge: Edge::Right,
            along_norm: 0.5,
        });
        tokio::time::sleep(Duration::from_millis(150)).await;
        assert!(client_host.focused(), "wrong-edge handoff must not fire");

        // A push on the peer-facing edge returns the cursor.
        client.execute(Cmd::HandOff {
            edge: Edge::Left,
            along_norm: 0.25,
        });
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
        wait_until(
            "server received Enter{Left,0.25}",
            Duration::from_secs(5),
            || {
                server_host.inputs().iter().any(|event| {
                    matches!(
                        event,
                        InputEvent::Enter { edge: Edge::Left, along_norm }
                            if (*along_norm - 0.25).abs() < 0.001
                    )
                })
            },
        )
        .await;
    })
    .await;
    result.expect("injected-edge scenario timed out");
}

/// Changing the edge on one computer must move the seam on both, immediately
/// and persistently, without reconnecting.
#[tokio::test(flavor = "multi_thread")]
async fn topology_changes_sync_while_connected() {
    let result = tokio::time::timeout(Duration::from_secs(45), async {
        let (server, server_host, client, client_host, _sd, _cd) =
            paired_pair(Edge::Right, Edge::Left).await;
        wait_until("initial topology", Duration::from_secs(5), || {
            client_host
                .latest_status()
                .and_then(|s| s["peer_edge"].as_str().map(|v| v.to_string()))
                .as_deref()
                == Some("left")
        })
        .await;

        // Move the seam to the top/bottom axis from the server side.
        server.execute(Cmd::SetEdge(Edge::Top));
        wait_until(
            "client mirrored the new edge",
            Duration::from_secs(5),
            || {
                client_host
                    .latest_status()
                    .and_then(|s| s["peer_edge"].as_str().map(|v| v.to_string()))
                    .as_deref()
                    == Some("bottom")
            },
        )
        .await;
        let server_cfg: Value = serde_json::from_str(&server.config_json()).unwrap();
        let client_cfg: Value = serde_json::from_str(&client.config_json()).unwrap();
        assert_eq!(server_cfg["edge"], "top");
        assert_eq!(client_cfg["edge"], "bottom", "both configs stay mirrored");

        // A hotkey handoff now leaves through the new edge.
        server.execute(Cmd::ToggleControl);
        wait_until("client owns the cursor", Duration::from_secs(5), || {
            client_host.focused()
        })
        .await;
        client.execute(Cmd::ToggleControl);
        wait_until(
            "server owns the cursor again",
            Duration::from_secs(5),
            || server_host.focused(),
        )
        .await;
        assert!(
            server_host.inputs().iter().any(|event| matches!(
                event,
                InputEvent::Enter {
                    edge: Edge::Bottom,
                    ..
                }
            )),
            "the return must use the mirrored bottom edge"
        );
    })
    .await;
    result.expect("topology-sync scenario timed out");
}
