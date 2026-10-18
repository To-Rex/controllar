//! Session logic: TLS handshake, protocol handshake, authentication/pairing
//! and the message pump. One session == one TCP+TLS connection.

use std::sync::Arc;
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

use crate::backoff::Backoff;
use crate::config::Config;
use crate::engine::Engine;
use crate::framing;
use crate::pairing::{self, PairVerdict};
use crate::protocol::{self, Hello, HelloAck, Message, PROTOCOL_VERSION};
use crate::status::ConnState;
use crate::transport::{self, Stream};
use crate::types::*;

/// How long TLS + Hello may take.
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(10);
/// How long the server waits for auth/pairing after Hello.
const AUTH_TIMEOUT: Duration = Duration::from_secs(180);
/// How long the client waits while the user fetches the pairing code.
const PAIR_WINDOW: Duration = Duration::from_secs(300);
/// No inbound frame for this long ⇒ connection considered dead.
const DEAD_AFTER: Duration = Duration::from_secs(6);
/// Heartbeat period.
const HEARTBEAT: Duration = Duration::from_millis(1000);

/// Where the client should connect, including whatever memory we have.
#[derive(Clone, Debug)]
pub struct ClientTarget {
    pub host: String,
    pub port: u16,
    pub token: Option<String>,
    pub fingerprint: Option<String>,
    pub code: Option<String>,
}

impl ClientTarget {
    pub fn from_config(cfg: &Config) -> Option<Self> {
        cfg.last_server.as_ref().map(|ls| ClientTarget {
            host: ls.host.clone(),
            port: ls.port,
            token: ls.token.clone(),
            fingerprint: ls.fingerprint.clone(),
            code: None,
        })
    }
}

/// How a session finished.
#[derive(Debug)]
pub enum SessionOutcome {
    /// Peer closed (or protocol-level end).
    Closed(Option<String>),
    /// Local shutdown requested.
    Cancelled,
}

// =========================================================================
// Server role
// =========================================================================

/// Server accept loop. Runs until cancelled or a fatal listener error.
pub async fn server_loop(engine: Arc<Engine>, cancel: CancellationToken) -> Result<()> {
    let cfg = engine.cfg.lock().clone();

    let acceptor = transport::tls_acceptor(&engine.identity).context("TLS setup")?;

    // Bind with retries: restarting the listener (mode/port change) may race
    // the old socket's release by a few milliseconds.
    let mut listener = None;
    for attempt in 0..25 {
        if cancel.is_cancelled() {
            return Ok(());
        }
        match transport::bind_listener(cfg.listen_port).await {
            Ok(bound) => {
                listener = Some(bound);
                break;
            }
            Err(err) => {
                if attempt == 24 {
                    engine.fail(format!("cannot listen on port {}: {err}", cfg.listen_port));
                    return Ok(());
                }
                tracing::debug!("port {} busy, retrying… ({err})", cfg.listen_port);
                tokio::time::sleep(Duration::from_millis(120)).await;
            }
        }
    }
    let Some(listener) = listener else {
        return Ok(());
    };

    engine
        .listening
        .store(true, std::sync::atomic::Ordering::Relaxed);
    engine.ensure_pairing_code();
    engine.set_state(ConnState::Listening);
    engine.refresh_pairing_status();
    engine.notify();
    tracing::info!(port = cfg.listen_port, "listening for clients");

    loop {
        tokio::select! {
            _ = cancel.cancelled() => break,
            result = listener.accept() => {
                match result {
                    Ok((tcp, peer)) => {
                        transport::configure_tcp(&tcp).ok();
                        let engine = engine.clone();
                        let cancel = cancel.clone();
                        let acceptor = acceptor.clone();
                        let runtime = engine.runtime.handle().clone();
                        runtime.spawn(async move {
                            if let Err(err) =
                                run_server_session(engine.clone(), tcp, peer, cancel, acceptor)
                                    .await
                            {
                                tracing::warn!(%peer, "server session ended: {err:#}");
                            }
                        });
                    }
                    Err(err) => {
                        tracing::warn!("accept failed: {err}");
                        tokio::time::sleep(Duration::from_millis(200)).await;
                    }
                }
            }
        }
    }

    engine
        .listening
        .store(false, std::sync::atomic::Ordering::Relaxed);
    Ok(())
}

async fn run_server_session(
    engine: Arc<Engine>,
    tcp: tokio::net::TcpStream,
    peer: std::net::SocketAddr,
    cancel: CancellationToken,
    acceptor: tokio_rustls::TlsAcceptor,
) -> Result<()> {
    let mut stream = Stream::Server(
        tokio::time::timeout(HANDSHAKE_TIMEOUT, acceptor.accept(tcp))
            .await
            .context("TLS handshake timed out")?
            .context("TLS handshake failed")?,
    );

    let hello = match read_message_timeout(&mut stream, HANDSHAKE_TIMEOUT).await {
        Ok(Message::Hello(hello)) => hello,
        Ok(other) => {
            let _ = send(
                &mut stream,
                &Message::Bye {
                    reason: "expected Hello".into(),
                },
            )
            .await;
            return Err(anyhow!(
                "protocol violation: first message was {}",
                other.name()
            ));
        }
        Err(err) => return Err(err),
    };

    if hello.protocol != PROTOCOL_VERSION {
        let _ = send(
            &mut stream,
            &Message::Bye {
                reason: format!("protocol version mismatch (server {PROTOCOL_VERSION})"),
            },
        )
        .await;
        return Err(anyhow!("protocol mismatch: client {}", hello.protocol));
    }

    // Single-client policy: politely refuse while busy.
    if engine.has_session() {
        let _ = send(
            &mut stream,
            &Message::Bye {
                reason: "server is busy with another client".into(),
            },
        )
        .await;
        tracing::info!(%peer, client = %hello.name, "refused connection: busy");
        return Ok(());
    }

    let paired = engine.cfg.lock().find_paired(&hello.device_id).cloned();
    let ack = Message::HelloAck(HelloAck {
        protocol: PROTOCOL_VERSION,
        device_id: engine.own_device_id(),
        name: engine.own_device_name(),
        version: crate::VERSION.to_string(),
        known_device: paired.is_some(),
    });
    send(&mut stream, &ack).await?;
    engine.set_connecting_peer(&hello, &peer.to_string());
    engine.notice(
        "peer",
        &format!(
            "{} connected{}",
            hello.name,
            if paired.is_some() {
                " (known device)"
            } else {
                " — pairing required"
            }
        ),
    );
    tracing::info!(%peer, client = %hello.name, known = paired.is_some(), "client connected");

    // ---- Auth / pairing phase -----------------------------------------
    let deadline = tokio::time::Instant::now() + AUTH_TIMEOUT;
    let mut paired_now = false;
    loop {
        tokio::select! {
            _ = cancel.cancelled() => return Ok(()),
            _ = tokio::time::sleep_until(deadline) => {
                let _ = send(&mut stream, &Message::Bye { reason: "pairing timed out".into() }).await;
                return Err(anyhow!("auth timed out for {}", hello.name));
            }
            msg = read_message(&mut stream) => {
                match msg? {
                    Message::Authenticate { device_id, token } => {
                        let device = engine.cfg.lock().find_paired(&device_id).cloned();
                        let ok = device_id == hello.device_id
                            && device
                                .as_ref()
                                .map(|d| pairing::token_matches(&d.token, &token))
                                .unwrap_or(false);
                        if ok {
                            send(&mut stream, &Message::AuthResult { ok: true, reason: None }).await?;
                            engine.touch_paired_device(&device_id, &peer.to_string());
                            tracing::info!(client = %hello.name, "client authenticated");
                            break;
                        }
                        send(
                            &mut stream,
                            &Message::AuthResult { ok: false, reason: Some("unknown device or invalid token".into()) },
                        )
                        .await?;
                        tracing::warn!(client = %hello.name, "client authentication failed");
                    }
                    Message::PairRequest { device_id, name, code } => {
                        engine.set_state(ConnState::Pairing);
                        let verdict = engine.verify_pair_code(&code);
                        match verdict {
                            PairVerdict::Accepted => {
                                let token = pairing::generate_token();
                                engine.remember_paired_device(&device_id, &name, &token, &peer.to_string());
                                send(
                                    &mut stream,
                                    &Message::PairResult { ok: true, reason: None, token: Some(token), attempts_left: None },
                                )
                                .await?;
                                paired_now = true;
                                tracing::info!(client = %name, "pairing accepted");
                                break;
                            }
                            PairVerdict::Wrong { attempts_left } => {
                                send(
                                    &mut stream,
                                    &Message::PairResult {
                                        ok: false,
                                        reason: Some("wrong pairing code".into()),
                                        token: None,
                                        attempts_left: Some(attempts_left),
                                    },
                                )
                                .await?;
                                tracing::warn!(client = %name, attempts_left, "wrong pairing code");
                            }
                            PairVerdict::Expired => {
                                send(
                                    &mut stream,
                                    &Message::PairResult {
                                        ok: false,
                                        reason: Some("pairing code expired — ask the server for a new code".into()),
                                        token: None,
                                        attempts_left: None,
                                    },
                                )
                                .await?;
                            }
                            PairVerdict::Exhausted => {
                                send(
                                    &mut stream,
                                    &Message::PairResult {
                                        ok: false,
                                        reason: Some("too many attempts — ask the server to generate a new code".into()),
                                        token: None,
                                        attempts_left: Some(0),
                                    },
                                )
                                .await?;
                            }
                        }
                    }
                    Message::Bye { .. } => return Ok(()),
                    other => {
                        tracing::debug!("ignoring {} during auth", other.name());
                    }
                }
            }
        }
    }

    // ---- Ready ---------------------------------------------------------
    let (tx, rx) = mpsc::unbounded_channel();
    if !engine.register_session(Mode::Server, &hello.device_id, tx) {
        let _ = send(
            &mut stream,
            &Message::Bye {
                reason: "busy".into(),
            },
        )
        .await;
        return Ok(());
    }
    let guard = SessionGuard::new(engine.clone());
    engine.set_connected_peer(
        &hello.device_id,
        &hello.name,
        None,
        &peer.to_string(),
        paired.is_some() || paired_now,
    );
    engine.set_state(ConnState::Connected);
    engine.refresh_pairing_status();
    engine.notice("connected", &format!("{} is connected", hello.name));

    let result = pump(engine.clone(), &mut stream, rx, cancel, Mode::Server).await;
    drop(guard);
    engine.on_server_session_ended();
    result.map(|_| ())
}

// =========================================================================
// Client role
// =========================================================================

/// Client reconnect loop. Runs until cancelled.
pub async fn client_loop(engine: Arc<Engine>, cancel: CancellationToken) {
    let mut backoff = Backoff::reconnect();

    loop {
        if cancel.is_cancelled() {
            return;
        }
        let Some(target) = ClientTarget::from_config(&engine.cfg.lock()) else {
            engine.set_state(ConnState::Off);
            engine.notice(
                "info",
                "no server configured — pick a discovered device, scan a QR code or enter an address",
            );
            return;
        };

        engine.set_state(ConnState::Connecting);
        match run_client_session(engine.clone(), &target, cancel.clone()).await {
            Ok(SessionOutcome::Closed(reason)) => {
                let reason = reason.unwrap_or_else(|| "peer closed the connection".to_string());
                tracing::info!("session closed: {reason}");
                engine.set_error(ConnState::Reconnecting, reason);
                backoff.reset();
            }
            Ok(SessionOutcome::Cancelled) => return,
            Err(err) => {
                tracing::warn!("connect attempt failed: {err:#}");
                engine.set_error(ConnState::Reconnecting, format!("{err:#}"));
            }
        }

        if cancel.is_cancelled() {
            return;
        }
        let delay = backoff.next_delay();
        engine.set_reconnect_delay(Some(delay.as_millis() as u64));
        engine.set_state(ConnState::Reconnecting);
        tokio::select! {
            _ = cancel.cancelled() => return,
            _ = tokio::time::sleep(delay) => {}
        }
        engine.set_reconnect_delay(None);
    }
}

async fn run_client_session(
    engine: Arc<Engine>,
    target: &ClientTarget,
    cancel: CancellationToken,
) -> Result<SessionOutcome> {
    let connector = transport::tls_connector();
    let mut stream =
        transport::connect_tls(&connector, &target.host, target.port, HANDSHAKE_TIMEOUT)
            .await
            .with_context(|| format!("connecting to {}:{}", target.host, target.port))?;

    // ---- Identity check (pin or TOFU) ----------------------------------
    let presented = transport::stream_peer_fingerprint(&stream);
    if let Some(expected) = &target.fingerprint {
        match &presented {
            Some(fp) if crate::crypto::fingerprints_match(expected, fp) => {}
            Some(fp) => {
                return Err(anyhow!(
                    "server identity changed! expected {}, got {} — refusing to connect",
                    crate::crypto::format_fingerprint(expected),
                    crate::crypto::format_fingerprint(fp)
                ));
            }
            None => return Err(anyhow!("server presented no certificate")),
        }
    }

    engine.set_state(ConnState::Handshake);

    // ---- Protocol handshake -------------------------------------------
    let cfg = engine.cfg.lock().clone();
    let local_screen = *engine.local_screen.lock();
    send(
        &mut stream,
        &Message::Hello(Hello {
            protocol: PROTOCOL_VERSION,
            device_id: cfg.device_id.clone(),
            name: cfg.device_name.clone(),
            version: crate::VERSION.to_string(),
            mode: Mode::Client,
            screen: local_screen,
        }),
    )
    .await?;

    let ack = match read_message_timeout(&mut stream, HANDSHAKE_TIMEOUT).await? {
        Message::HelloAck(ack) => ack,
        Message::Bye { reason } => return Ok(SessionOutcome::Closed(Some(reason))),
        other => return Err(anyhow!("expected HelloAck, got {}", other.name())),
    };
    if ack.protocol != PROTOCOL_VERSION {
        return Err(anyhow!(
            "protocol mismatch: server speaks {}, this build speaks {PROTOCOL_VERSION}",
            ack.protocol
        ));
    }

    let addr_string = format!("{}:{}", target.host, target.port);
    engine.remember_server(
        &target.host,
        target.port,
        Some(ack.device_id.clone()),
        presented.clone(),
        target.token.clone(),
    );
    engine.set_connected_peer(
        &ack.device_id,
        &ack.name,
        presented.clone(),
        &addr_string,
        target.token.is_some(),
    );
    engine.notice("peer", &format!("connected to {}", ack.name));
    tracing::info!(server = %ack.name, addr = %addr_string, "connected (TLS established)");

    // ---- Authentication -------------------------------------------------
    let mut authenticated = false;
    let mut token_used = target.token.clone();
    if let Some(token) = &token_used {
        send(
            &mut stream,
            &Message::Authenticate {
                device_id: cfg.device_id.clone(),
                token: token.clone(),
            },
        )
        .await?;
        match read_message_timeout(&mut stream, HANDSHAKE_TIMEOUT).await? {
            Message::AuthResult { ok: true, .. } => {
                authenticated = true;
                tracing::info!("authenticated with stored token");
            }
            Message::AuthResult { ok: false, reason } => {
                tracing::warn!(reason = ?reason, "stored token rejected — pairing required");
                token_used = None;
                engine.notice("pairing", "pairing is required on this server");
            }
            Message::Bye { reason } => return Ok(SessionOutcome::Closed(Some(reason))),
            other => return Err(anyhow!("unexpected {} during auth", other.name())),
        }
    }

    // ---- Pairing if needed ---------------------------------------------
    if !authenticated {
        let token = client_pairing_flow(&engine, &mut stream, target, &cancel).await?;
        token_used = Some(token);
    }

    // ---- Ready -----------------------------------------------------------
    let (tx, rx) = mpsc::unbounded_channel();
    if !engine.register_session(Mode::Client, &ack.device_id, tx) {
        return Err(anyhow!("another session is already active"));
    }
    let guard = SessionGuard::new(engine.clone());
    if let Some(token) = &token_used {
        engine.save_server_token(&ack.device_id, &ack.name, token, &target.host);
    }
    engine.set_connected_peer(&ack.device_id, &ack.name, presented, &addr_string, true);
    engine.set_state(ConnState::Connected);
    engine.refresh_pairing_status();
    engine.notice("connected", &format!("connected to {}", ack.name));

    let result = pump(engine.clone(), &mut stream, rx, cancel, Mode::Client).await;
    drop(guard);
    engine.on_client_session_ended();
    result
}

/// The pairing conversation (client side): user-visible code in, token out.
///
/// The code arrives either with [`ClientTarget`] (QR scan / link) or through
/// [`Engine::pending_pair_code`] (typed in the UI). The mailbox is polled at
/// most every 250 ms, so the flow cannot miss a code regardless of timing.
async fn client_pairing_flow(
    engine: &Arc<Engine>,
    stream: &mut Stream,
    target: &ClientTarget,
    cancel: &CancellationToken,
) -> Result<String> {
    let cfg = engine.cfg.lock().clone();
    let mut pending_code = target.code.clone();
    let deadline = tokio::time::Instant::now() + PAIR_WINDOW;
    let mut sent_any = false;

    loop {
        if pending_code.is_none() {
            pending_code = engine.pending_pair_code.lock().take();
        }
        if let Some(code) = pending_code.take() {
            send(
                stream,
                &Message::PairRequest {
                    device_id: cfg.device_id.clone(),
                    name: cfg.device_name.clone(),
                    code,
                },
            )
            .await?;
            sent_any = true;
            engine.set_state(ConnState::Pairing);
        } else if !sent_any {
            engine.set_state(ConnState::AwaitingPair);
            engine.refresh_pairing_status();
            engine.notify();
        }

        tokio::select! {
            _ = cancel.cancelled() => return Err(anyhow!("cancelled during pairing")),
            _ = tokio::time::sleep_until(deadline) => {
                return Err(anyhow!("pairing timed out — regenerate the code and try again"));
            }
            msg = read_message(stream) => {
                match msg? {
                    Message::PairResult { ok, reason, token, attempts_left } => {
                        if ok {
                            let token = token.ok_or_else(|| anyhow!("pairing succeeded without token"))?;
                            tracing::info!("pairing complete");
                            return Ok(token);
                        }
                        let reason = reason.unwrap_or_else(|| "pairing rejected".to_string());
                        tracing::warn!(%reason, "pairing rejected");
                        engine.notice("pairing", &reason);
                        {
                            let mut status = engine.status.lock();
                            if let Some(pairing) = status.pairing.as_mut() {
                                if let Some(left) = attempts_left {
                                    pairing.attempts_left = left;
                                }
                                pairing.awaiting_input = true;
                            }
                        }
                        engine.set_state(ConnState::AwaitingPair);
                        engine.refresh_pairing_status();
                        engine.notify();
                    }
                    Message::Bye { reason } => return Err(anyhow!("server closed: {reason}")),
                    other => tracing::debug!("ignoring {} while pairing", other.name()),
                }
            }
            // Wake up regularly to re-check the pending-code mailbox.
            _ = tokio::time::sleep(Duration::from_millis(250)) => {}
        }
    }
}

// =========================================================================
// Shared pump
// =========================================================================

async fn pump(
    engine: Arc<Engine>,
    stream: &mut Stream,
    mut rx: mpsc::UnboundedReceiver<Message>,
    cancel: CancellationToken,
    role: Mode,
) -> Result<SessionOutcome> {
    let mut heartbeat = tokio::time::interval(HEARTBEAT);
    heartbeat.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    let mut seq: u64 = 0;
    let mut pending_pongs: std::collections::VecDeque<(u64, std::time::Instant)> =
        std::collections::VecDeque::new();
    let mut last_inbound = std::time::Instant::now();
    let mut first_tick = true;

    loop {
        tokio::select! {
            _ = cancel.cancelled() => {
                let _ = send(stream, &Message::Bye { reason: "shutting down".into() }).await;
                return Ok(SessionOutcome::Cancelled);
            }
            frame = framing::read_frame(stream) => {
                let payload = match frame {
                    Ok(Some(payload)) => payload,
                    Ok(None) => return Ok(SessionOutcome::Closed(None)),
                    Err(err) => return Err(err.into()),
                };
                last_inbound = std::time::Instant::now();
                engine.counters.rx_bytes.fetch_add(payload.len() as u64 + 4, std::sync::atomic::Ordering::Relaxed);
                let msg = protocol::decode(&payload)?;
                match msg {
                    Message::Heartbeat { seq } => {
                        send(stream, &Message::Pong { seq }).await?;
                    }
                    Message::Pong { seq } => {
                        if let Some(index) = pending_pongs.iter().position(|(s, _)| *s == seq) {
                            if let Some((_, sent_at)) = pending_pongs.remove(index) {
                                let micros = sent_at.elapsed().as_micros() as u64;
                                engine.counters.rtt_micros.store(micros, std::sync::atomic::Ordering::Relaxed);
                            }
                        }
                    }
                    Message::Bye { reason } => {
                        return Ok(SessionOutcome::Closed(Some(reason)));
                    }
                    other => {
                        on_session_message(&engine, role, other).await?;
                    }
                }
            }
            msg = rx.recv() => {
                let Some(msg) = msg else {
                    return Ok(SessionOutcome::Cancelled);
                };
                let payload = protocol::encode(&msg)?;
                framing::write_frame(stream, &payload).await?;
                engine.counters.tx_bytes.fetch_add(payload.len() as u64 + 4, std::sync::atomic::Ordering::Relaxed);
            }
            _ = heartbeat.tick() => {
                if first_tick {
                    first_tick = false;
                    continue;
                }
                if last_inbound.elapsed() > DEAD_AFTER {
                    return Err(anyhow!("connection lost (no data for {}s)", DEAD_AFTER.as_secs()));
                }
                seq += 1;
                let payload = protocol::encode(&Message::Heartbeat { seq })?;
                framing::write_frame(stream, &payload).await?;
                engine.counters.tx_bytes.fetch_add(payload.len() as u64 + 4, std::sync::atomic::Ordering::Relaxed);
                pending_pongs.push_back((seq, std::time::Instant::now()));
                while pending_pongs.len() > 8 {
                    pending_pongs.pop_front();
                }
            }
        }
    }
}

async fn on_session_message(engine: &Arc<Engine>, role: Mode, msg: Message) -> Result<()> {
    match (role, msg) {
        // Client receives injected input.
        (Mode::Client, Message::Input(event)) => {
            engine.handle_incoming_input(event);
        }
        // Server receives reclaim requests.
        (Mode::Server, Message::Reclaim) => {
            engine.on_peer_reclaimed();
        }
        // Clipboard flows both ways.
        (_, Message::Clipboard { text }) => {
            engine.handle_incoming_clipboard(&text);
        }
        (_, other) => {
            tracing::trace!("ignoring {} in role {}", other.name(), role.as_str());
        }
    }
    Ok(())
}

// =========================================================================
// Helpers
// =========================================================================

async fn read_message(stream: &mut Stream) -> Result<Message> {
    match framing::read_frame(stream).await? {
        Some(payload) => Ok(protocol::decode(&payload)?),
        None => Err(anyhow!("connection closed by peer")),
    }
}

async fn read_message_timeout(stream: &mut Stream, timeout: Duration) -> Result<Message> {
    tokio::time::timeout(timeout, read_message(stream))
        .await
        .context("timed out waiting for message")?
}

async fn send(stream: &mut Stream, msg: &Message) -> Result<()> {
    let payload = protocol::encode(msg)?;
    framing::write_frame(stream, &payload).await?;
    Ok(())
}

/// Ensures the session slot is released however the session task exits.
struct SessionGuard {
    engine: Arc<Engine>,
}

impl SessionGuard {
    fn new(engine: Arc<Engine>) -> Self {
        SessionGuard { engine }
    }
}

impl Drop for SessionGuard {
    fn drop(&mut self) {
        self.engine.deregister_session();
    }
}
