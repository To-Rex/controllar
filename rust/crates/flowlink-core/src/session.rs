//! Session logic: TLS handshake, protocol handshake, authentication/pairing
//! and the message pump. One session == one TCP+TLS connection.

use std::sync::Arc;
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use tokio::io::{AsyncRead, AsyncWrite, AsyncWriteExt};
use tokio::sync::mpsc;
use tokio_util::sync::CancellationToken;

use crate::backoff::Backoff;
use crate::config::Config;
use crate::engine::Engine;
use crate::framing::{self, FrameReader};
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
/// How long a session may stay silent before it is declared lost.
///
/// Deliberately generous: on a Wi-Fi link, TCP retransmission backoff can
/// stall the data flow for many seconds without the connection being dead.
/// Real failures (app closed, cable pulled) are detected instantly by the
/// read error, so this timer only needs to catch genuinely silent deaths.
const DEAD_AFTER: Duration = Duration::from_secs(30);
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

    // Bind the configured port first (with short retries for the restart
    // race), then fall back to the next few ports when it is genuinely busy
    // — the actual port is announced via mDNS and carried by invite links,
    // so the peer never needs to know which one was used.
    let mut bound: Option<(tokio::net::TcpListener, u16)> = None;
    let mut last_error = None;
    'ports: for offset in 0..10u16 {
        let candidate = cfg.listen_port.saturating_add(offset);
        if candidate == 0 {
            continue;
        }
        let attempts = if offset == 0 { 15 } else { 2 };
        for attempt in 0..attempts {
            if cancel.is_cancelled() {
                return Ok(());
            }
            match transport::bind_listener(candidate).await {
                Ok(listener) => {
                    bound = Some((listener, candidate));
                    break 'ports;
                }
                Err(err) => {
                    last_error = Some(err);
                    if attempt + 1 < attempts {
                        tokio::time::sleep(Duration::from_millis(120)).await;
                    }
                }
            }
        }
    }
    let Some((listener, port)) = bound else {
        let err = last_error
            .map(|err| err.to_string())
            .unwrap_or_else(|| "no free port".to_string());
        engine.fail(format!("cannot listen on port {}: {err}", cfg.listen_port));
        return Ok(());
    };
    engine.set_actual_port(port);
    if port != cfg.listen_port {
        engine.notice(
            "network",
            &format!(
                "Port {} is busy — listening on {} instead (invite links and discovery use the new port automatically).",
                cfg.listen_port, port
            ),
        );
    }

    engine
        .listening
        .store(true, std::sync::atomic::Ordering::Relaxed);
    engine.ensure_pairing_code();
    engine.set_state(ConnState::Listening);
    engine.refresh_pairing_status();
    engine.notify();
    tracing::info!(port, "listening for clients");

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
    let stream = Stream::Server(
        tokio::time::timeout(HANDSHAKE_TIMEOUT, acceptor.accept(tcp))
            .await
            .context("TLS handshake timed out")?
            .context("TLS handshake failed")?,
    );
    // Read and write directions are handled by the same task but kept in
    // separate halves so the pump can select on a cancellation-safe
    // FrameReader while heartbeats and replies write out.
    let (read_half, write_half) = tokio::io::split(stream);
    let mut reader = FrameReader::new(read_half);
    let mut writer = write_half;

    // A fresh token per session: the loop-level `cancel` is shared by every
    // session, but a reconnect must be able to end just this one.
    let session_cancel = CancellationToken::new();
    let result = server_session_with(
        engine.clone(),
        &mut reader,
        &mut writer,
        peer,
        cancel,
        session_cancel,
    )
    .await;
    // Handshake-level failures never reach the pump, so explain them here:
    // the peer logs the reason instead of "peer closed connection without
    // sending TLS close_notify", which made every teardown look like a crash.
    if let Err(err) = &result {
        let _ = send(
            &mut writer,
            &Message::Bye {
                reason: format!("{err:#}"),
            },
        )
        .await;
    }
    let _ = writer.shutdown().await;
    result
}

async fn server_session_with<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(
    engine: Arc<Engine>,
    reader: &mut FrameReader<R>,
    writer: &mut W,
    peer: std::net::SocketAddr,
    cancel: CancellationToken,
    session_cancel: CancellationToken,
) -> Result<()> {
    let hello = match read_message_timeout(reader, HANDSHAKE_TIMEOUT).await {
        Ok(Message::Hello(hello)) => hello,
        Ok(other) => {
            let _ = send(
                writer,
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
            writer,
            &Message::Bye {
                reason: format!(
                    "protocol version mismatch: this server speaks {PROTOCOL_VERSION}, the client speaks {} — update FlowLink on both computers",
                    hello.protocol
                ),
            },
        )
        .await;
        return Err(anyhow!(
            "protocol mismatch: client speaks {}, this build speaks {PROTOCOL_VERSION}",
            hello.protocol
        ));
    }

    // Single-client policy: politely refuse a *different* client while busy.
    // A reconnect of the same device must never be refused: its previous
    // socket can linger for the whole dead-man window after a Wi-Fi dropout,
    // and rejecting it used to add tens of seconds to every recovery.
    if let Some(existing) = engine.session_device_id() {
        if existing != hello.device_id {
            let _ = send(
                writer,
                &Message::Bye {
                    reason: "server is busy with another client".into(),
                },
            )
            .await;
            tracing::info!(%peer, client = %hello.name, "refused connection: busy");
            return Ok(());
        }
        tracing::info!(
            %peer,
            client = %hello.name,
            "same device reconnecting — replacing its lingering session"
        );
    }

    let paired = engine.cfg.lock().find_paired(&hello.device_id).cloned();
    // Remember whether this peer understands the additive Topology message.
    // Older 0.2.x builds decode unknown variants as errors, so they must
    // never receive one; they fall back to geometric self-alignment on Enter.
    let topology = protocol::supports_topology(&hello.version);
    engine.set_peer_topology_support(topology);
    if !topology {
        engine.notice(
            "version",
            &format!(
                "{} runs an older FlowLink ({}) — update both computers to get automatic edge sync",
                hello.name, hello.version
            ),
        );
    }
    let ack = Message::HelloAck(HelloAck {
        protocol: PROTOCOL_VERSION,
        device_id: engine.own_device_id(),
        name: engine.own_device_name(),
        version: crate::VERSION.to_string(),
        known_device: paired.is_some(),
    });
    send(writer, &ack).await?;
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
    tracing::info!(
        %peer,
        client = %hello.name,
        version = %hello.version,
        protocol = hello.protocol,
        known = paired.is_some(),
        "client connected"
    );

    // ---- Auth / pairing phase -----------------------------------------
    let deadline = tokio::time::Instant::now() + AUTH_TIMEOUT;
    let mut paired_now = false;
    loop {
        tokio::select! {
            _ = cancel.cancelled() => return Ok(()),
            _ = tokio::time::sleep_until(deadline) => {
                let _ = send(writer, &Message::Bye { reason: "pairing timed out".into() }).await;
                return Err(anyhow!("auth timed out for {}", hello.name));
            }
            msg = read_message(reader) => {
                match msg? {
                    Message::Authenticate { device_id, token } => {
                        let device = engine.cfg.lock().find_paired(&device_id).cloned();
                        let ok = device_id == hello.device_id
                            && device
                                .as_ref()
                                .map(|d| pairing::token_matches(&d.token, &token))
                                .unwrap_or(false);
                        if ok {
                            send(writer, &Message::AuthResult { ok: true, reason: None }).await?;
                            engine.touch_paired_device(&device_id, &peer.to_string());
                            tracing::info!(client = %hello.name, "client authenticated");
                            break;
                        }
                        send(
                            writer,
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
                                    writer,
                                    &Message::PairResult { ok: true, reason: None, token: Some(token), attempts_left: None },
                                )
                                .await?;
                                paired_now = true;
                                tracing::info!(client = %name, "pairing accepted");
                                break;
                            }
                            PairVerdict::Wrong { attempts_left } => {
                                send(
                                    writer,
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
                                    writer,
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
                                    writer,
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
    // A client may have held this connection through a long pairing window
    // while another session registered; re-check the single-client rule now.
    if let Some(existing) = engine.session_device_id() {
        if existing != hello.device_id {
            let _ = send(
                writer,
                &Message::Bye {
                    reason: "server is busy with another client".into(),
                },
            )
            .await;
            tracing::info!(%peer, client = %hello.name, "refused connection: busy");
            return Ok(());
        }
    }
    let (tx, rx) = mpsc::unbounded_channel();
    // Registers unconditionally for the same device: a reconnect arriving
    // while the previous session is still tearing down replaces it (and
    // cancels it) instead of waiting for the dead-man timer.
    let session_id =
        engine.register_session(Mode::Server, &hello.device_id, tx, session_cancel.clone());
    let guard = SessionGuard::new(engine.clone(), session_id);
    engine.set_connected_peer(
        &hello.device_id,
        &hello.name,
        Some(hello.version.clone()),
        None,
        &peer.to_string(),
        paired.is_some() || paired_now,
    );
    engine.set_state(ConnState::Connected);
    engine.refresh_pairing_status();
    engine.notice("connected", &format!("{} is connected", hello.name));
    engine.on_session_ready(Mode::Server);
    // Authoritative geometry: whichever way this screen faces the peer is
    // mirrored on the client, so both machines agree on the return edge even
    // when their local settings were configured inconsistently.
    engine.announce_topology();

    let result = pump(
        engine.clone(),
        reader,
        writer,
        rx,
        cancel,
        session_cancel,
        Mode::Server,
    )
    .await;
    drop(guard);
    engine.on_server_session_ended(session_id);
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
                let mut message = format!("{err:#}");
                if let Some(hint) =
                    crate::util::subnet_mismatch_hint(&target.host, &crate::util::local_addrs())
                {
                    message = format!("{message} — {hint}");
                }
                tracing::warn!("connect attempt failed: {message}");
                engine.set_error(ConnState::Reconnecting, message);
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
    let stream = transport::connect_tls(&connector, &target.host, target.port, HANDSHAKE_TIMEOUT)
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

    // Split the stream before the handshake: reads must not lose bytes when
    // a select branch (heartbeat, outbound message) wins a race mid-frame.
    let (read_half, write_half) = tokio::io::split(stream);
    let mut reader = FrameReader::new(read_half);
    let mut writer = write_half;

    // A fresh token per session (see the server side for why the loop-level
    // token cannot double as a per-session kill switch).
    let session_cancel = CancellationToken::new();
    let result = client_session_with(
        engine.clone(),
        &mut reader,
        &mut writer,
        target,
        presented,
        cancel,
        session_cancel,
    )
    .await;
    // Handshake-level failures never reach the pump: tell the server why we
    // are dropping instead of leaving it with an unexplained EOF.
    if let Err(err) = &result {
        let _ = send(
            &mut writer,
            &Message::Bye {
                reason: format!("{err:#}"),
            },
        )
        .await;
    }
    let _ = writer.shutdown().await;
    result
}

async fn client_session_with<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(
    engine: Arc<Engine>,
    reader: &mut FrameReader<R>,
    writer: &mut W,
    target: &ClientTarget,
    presented: Option<String>,
    cancel: CancellationToken,
    session_cancel: CancellationToken,
) -> Result<SessionOutcome> {
    engine.set_state(ConnState::Handshake);

    // ---- Protocol handshake -------------------------------------------
    let cfg = engine.cfg.lock().clone();
    let local_screen = *engine.local_screen.lock();
    send(
        writer,
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

    let ack = match read_message_timeout(reader, HANDSHAKE_TIMEOUT).await? {
        Message::HelloAck(ack) => ack,
        Message::Bye { reason } => return Ok(SessionOutcome::Closed(Some(reason))),
        other => return Err(anyhow!("expected HelloAck, got {}", other.name())),
    };
    if ack.protocol != PROTOCOL_VERSION {
        return Err(anyhow!(
            "protocol mismatch: the server speaks {}, this build speaks {PROTOCOL_VERSION} — update FlowLink on both computers",
            ack.protocol
        ));
    }
    engine.set_peer_topology_support(protocol::supports_topology(&ack.version));
    if !engine.peer_supports_topology() {
        engine.notice(
            "version",
            &format!(
                "{} runs an older FlowLink ({}) — update both computers to get automatic edge sync",
                ack.name, ack.version
            ),
        );
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
        Some(ack.version.clone()),
        presented.clone(),
        &addr_string,
        target.token.is_some(),
    );
    engine.notice("peer", &format!("connected to {}", ack.name));
    tracing::info!(
        server = %ack.name,
        version = %ack.version,
        protocol = ack.protocol,
        addr = %addr_string,
        "connected (TLS established)"
    );

    // ---- Authentication -------------------------------------------------
    let mut authenticated = false;
    let mut token_used = target.token.clone();
    if let Some(token) = &token_used {
        send(
            writer,
            &Message::Authenticate {
                device_id: cfg.device_id.clone(),
                token: token.clone(),
            },
        )
        .await?;
        match read_message_timeout(reader, HANDSHAKE_TIMEOUT).await? {
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
        let token = client_pairing_flow(&engine, reader, writer, target, &cancel).await?;
        token_used = Some(token);
    }

    // ---- Ready -----------------------------------------------------------
    let (tx, rx) = mpsc::unbounded_channel();
    let session_id =
        engine.register_session(Mode::Client, &ack.device_id, tx, session_cancel.clone());
    let guard = SessionGuard::new(engine.clone(), session_id);
    if let Some(token) = &token_used {
        engine.save_server_token(&ack.device_id, &ack.name, token, &target.host);
    }
    engine.set_connected_peer(
        &ack.device_id,
        &ack.name,
        Some(ack.version.clone()),
        presented,
        &addr_string,
        true,
    );
    engine.set_state(ConnState::Connected);
    engine.refresh_pairing_status();
    engine.notice("connected", &format!("connected to {}", ack.name));
    engine.on_session_ready(Mode::Client);

    let result = pump(
        engine.clone(),
        reader,
        writer,
        rx,
        cancel,
        session_cancel,
        Mode::Client,
    )
    .await;
    drop(guard);
    engine.on_client_session_ended(session_id);
    result
}

/// The pairing conversation (client side): user-visible code in, token out.
///
/// The code arrives either with [`ClientTarget`] (QR scan / link) or through
/// [`Engine::pending_pair_code`] (typed in the UI). The mailbox is polled at
/// most every 250 ms, so the flow cannot miss a code regardless of timing.
async fn client_pairing_flow<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(
    engine: &Arc<Engine>,
    reader: &mut FrameReader<R>,
    writer: &mut W,
    target: &ClientTarget,
    cancel: &CancellationToken,
) -> Result<String> {
    let cfg = engine.cfg.lock().clone();
    // The code may come with this attempt (link/QR) or from the mailbox
    // (typed earlier). The mailbox copy is *not* consumed here so a transport
    // failure between attempts does not lose it.
    let mut pending_code = target
        .code
        .clone()
        .or_else(|| engine.pending_pair_code.lock().clone());
    let deadline = tokio::time::Instant::now() + PAIR_WINDOW;
    let mut sent_any = false;

    loop {
        if pending_code.is_none() {
            pending_code = engine.pending_pair_code.lock().clone();
        }
        if let Some(code) = pending_code.take() {
            send(
                writer,
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
            msg = read_message(reader) => {
                match msg? {
                    Message::PairResult { ok, reason, token, attempts_left } => {
                        if ok {
                            let token = token.ok_or_else(|| anyhow!("pairing succeeded without token"))?;
                            // One-time code: never reuse it.
                            *engine.pending_pair_code.lock() = None;
                            tracing::info!("pairing complete");
                            return Ok(token);
                        }
                        // The server rejected this code (wrong or expired):
                        // drop it so we do not burn attempts on a dead code.
                        *engine.pending_pair_code.lock() = None;
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

async fn pump<R, W>(
    engine: Arc<Engine>,
    reader: &mut FrameReader<R>,
    writer: &mut W,
    mut rx: mpsc::UnboundedReceiver<Message>,
    cancel: CancellationToken,
    session_cancel: CancellationToken,
    role: Mode,
) -> Result<SessionOutcome>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    // Session messages (input, clipboard, focus) are applied on an ordered
    // side task. The pump loop must never block on a handler: a stalled loop
    // stops heartbeats, and the peer kills a silent connection after
    // DEAD_AFTER — which used to reset focus and snap the cursor back.
    let (handler_tx, mut handler_rx) = mpsc::unbounded_channel::<Message>();
    let handler_engine = engine.clone();
    let handler = tokio::spawn(async move {
        while let Some(msg) = handler_rx.recv().await {
            if let Err(err) = on_session_message(&handler_engine, role, msg).await {
                tracing::warn!("failed to apply a session message: {err:#}");
            }
        }
    });

    let result = pump_loop(
        &engine,
        reader,
        writer,
        &mut rx,
        &cancel,
        &session_cancel,
        &handler_tx,
    )
    .await;
    drop(handler_tx);
    handler.abort();

    // Polite close: explain ourselves and shut TLS down cleanly so the peer
    // logs a reason instead of "peer closed connection without sending TLS
    // close_notify", which made every teardown look like a crash.
    let bye_reason = match &result {
        Ok(SessionOutcome::Cancelled) => Some("shutting down".to_string()),
        Ok(SessionOutcome::Closed(_)) => None, // the peer already explained itself
        Err(err) => Some(format!("{err:#}")),
    };
    if let Some(reason) = bye_reason {
        let _ = send(writer, &Message::Bye { reason }).await;
    }
    let _ = writer.shutdown().await;
    result
}

async fn pump_loop<R, W>(
    engine: &Arc<Engine>,
    reader: &mut FrameReader<R>,
    writer: &mut W,
    rx: &mut mpsc::UnboundedReceiver<Message>,
    cancel: &CancellationToken,
    session_cancel: &CancellationToken,
    handler_tx: &mpsc::UnboundedSender<Message>,
) -> Result<SessionOutcome>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
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
                return Ok(SessionOutcome::Cancelled);
            }
            // This session was replaced by a newer connection (reconnect
            // arrived before the teardown finished): exit without touching
            // the shared state the newcomer now owns.
            _ = session_cancel.cancelled() => {
                return Ok(SessionOutcome::Cancelled);
            }
            // FrameReader keeps partial reads in the reader, so this branch
            // can be dropped mid-frame by a heartbeat or an outbound message
            // winning the race without losing a single byte.
            frame = reader.read_frame() => {
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
                        send(writer, &Message::Pong { seq }).await?;
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
                        if handler_tx.send(other).is_err() {
                            return Err(anyhow!("session message handler stopped unexpectedly"));
                        }
                    }
                }
            }
            msg = rx.recv() => {
                let Some(msg) = msg else {
                    return Ok(SessionOutcome::Cancelled);
                };
                engine.counters.tx_bytes.fetch_add(write_outbound(writer, &msg).await?, std::sync::atomic::Ordering::Relaxed);
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
                engine.counters.tx_bytes.fetch_add(
                    write_outbound(writer, &Message::Heartbeat { seq }).await?,
                    std::sync::atomic::Ordering::Relaxed,
                );
                pending_pongs.push_back((seq, std::time::Instant::now()));
                while pending_pongs.len() > 8 {
                    pending_pongs.pop_front();
                }
            }
        }
    }
}

async fn on_session_message(engine: &Arc<Engine>, role: Mode, msg: Message) -> Result<()> {
    match msg {
        // Input flows both ways now: whichever side does not own the cursor
        // forwards it, and the focused side injects it.
        Message::Input(event) => {
            engine.handle_incoming_input(event);
        }
        // The peer asked for the cursor (hotkey on its side).
        Message::Reclaim => {
            engine.on_reclaim();
        }
        // The peer moved the shared seam (the screen edge facing the other
        // computer). Mirror it and persist it locally.
        Message::Topology { edge } => {
            engine.apply_remote_topology(edge);
        }
        // Clipboard flows both ways.
        Message::Clipboard { text } => {
            engine.handle_incoming_clipboard(&text);
        }
        other => {
            tracing::trace!("ignoring {} in role {}", other.name(), role.as_str());
        }
    }
    Ok(())
}

// =========================================================================
// Helpers
// =========================================================================

async fn read_message<R: AsyncRead + Unpin>(reader: &mut FrameReader<R>) -> Result<Message> {
    match reader.read_frame().await? {
        Some(payload) => Ok(protocol::decode(&payload)?),
        None => Err(anyhow!("connection closed by peer")),
    }
}

async fn read_message_timeout<R: AsyncRead + Unpin>(
    reader: &mut FrameReader<R>,
    timeout: Duration,
) -> Result<Message> {
    tokio::time::timeout(timeout, read_message(reader))
        .await
        .context("timed out waiting for message")?
}

async fn send<W: AsyncWrite + Unpin>(writer: &mut W, msg: &Message) -> Result<()> {
    let payload = protocol::encode(msg)?;
    framing::write_frame(writer, &payload).await?;
    Ok(())
}

/// Encode and write one outbound session message; returns the bytes pushed
/// to the wire (0 when the message was skipped).
///
/// An oversized message is dropped with a warning instead of failing the
/// write: losing one clipboard blob must never take the whole session down.
async fn write_outbound<W: AsyncWrite + Unpin>(writer: &mut W, msg: &Message) -> Result<u64> {
    // The only message that can grow without bound: encoding runs under a
    // MAX_FRAME_BYTES limit, so decide on the text length first — encoding a
    // rejected payload would otherwise fail the whole session (bincode adds
    // at most ~12 bytes of overhead for this variant).
    if let Message::Clipboard { text } = msg {
        if text.len() + 64 > protocol::MAX_FRAME_BYTES {
            tracing::warn!(
                bytes = text.len() + 64,
                max = protocol::MAX_FRAME_BYTES,
                "outbound clipboard too large — dropped, session kept alive"
            );
            return Ok(0);
        }
    }
    let payload = protocol::encode(msg)?;
    if payload.len() > protocol::MAX_FRAME_BYTES {
        tracing::warn!(
            name = msg.name(),
            bytes = payload.len(),
            max = protocol::MAX_FRAME_BYTES,
            "outbound message too large — dropped, session kept alive"
        );
        return Ok(0);
    }
    framing::write_frame(writer, &payload).await?;
    Ok(payload.len() as u64 + 4)
}

/// Ensures the session slot is released however the session task exits.
struct SessionGuard {
    engine: Arc<Engine>,
    id: u64,
}

impl SessionGuard {
    fn new(engine: Arc<Engine>, id: u64) -> Self {
        SessionGuard { engine, id }
    }
}

impl Drop for SessionGuard {
    fn drop(&mut self) {
        self.engine.deregister_session(self.id);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::duplex;

    /// An oversized outbound message must be skipped (with a warning) rather
    /// than written as a partial frame or allowed to kill the session — the
    /// stream has to stay framed for every message after it.
    #[tokio::test]
    async fn oversized_outbound_is_skipped_and_stream_stays_framed() {
        let (mut a, b) = duplex(1024);
        let mut reader = FrameReader::new(b);

        let huge = Message::Clipboard {
            text: "x".repeat(protocol::MAX_FRAME_BYTES + 64),
        };
        let written = write_outbound(&mut a, &huge).await.unwrap();
        assert_eq!(written, 0, "oversized message must not hit the wire");

        // A normal message afterwards must still frame cleanly.
        write_outbound(&mut a, &Message::Heartbeat { seq: 7 })
            .await
            .unwrap();
        let frame = reader
            .read_frame()
            .await
            .unwrap()
            .expect("the session stream must still be aligned");
        match protocol::decode(&frame).unwrap() {
            Message::Heartbeat { seq } => assert_eq!(seq, 7),
            other => panic!("unexpected message {}", other.name()),
        }
    }
}
