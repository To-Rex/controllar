//! The engine facade.
//!
//! One [`Engine`] instance owns the whole runtime: configuration, identity,
//! status, command queue, role task (listener/dialer), discovery, pairing
//! state, clipboard gate and the edge-crossing state machine.
//!
//! Native shells interact with it through:
//!
//! * [`Cmd`] values sent via [`Engine::execute`] (sync, non-blocking), and
//! * the [`Host`] callbacks it invokes (status / input / clipboard / notices).

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};
use parking_lot::Mutex;
use tokio::runtime::Runtime;
use tokio::sync::mpsc::{self, UnboundedReceiver, UnboundedSender};
use tokio_util::sync::CancellationToken;

use crate::clipboard::{ClipDecision, ClipboardGate};
use crate::coalesce::MoveCoalescer;
use crate::config::{self, Config};
use crate::crypto::{self, Identity};
use crate::edge::{self, EdgeTracker};
use crate::host::{self, Host};
use crate::pairing::{self, PairVerdict};
use crate::protocol::Message;
use crate::qr::{self, QrMatrix};
use crate::session;
use crate::status::{ConnState, Counters, LocalInfo, Metrics, PairingStatus, PeerStatus, Status};
use crate::types::*;
use crate::util;

/// Commands accepted from the host shell.
pub enum Cmd {
    /// Begin running in the configured mode.
    Start,
    /// Stop everything (listener/dialer included) but keep the engine alive.
    Stop,
    /// Change operating mode and (re)start.
    SetMode(Mode),
    /// Connect to a server (client role) with optional pre-shared info.
    Connect {
        host: String,
        port: u16,
        code: Option<String>,
        fingerprint: Option<String>,
    },
    /// Connect using a `flowlink://connect?...` link (QR content).
    ConnectLink(String),
    /// Stop the client loop and stay idle.
    Disconnect,
    /// Provide the pairing code typed by the user (client role).
    SubmitPairCode(String),
    /// Invalidate the current code and show a fresh one (server role).
    RegeneratePairCode,
    /// Remove a device from memory.
    ForgetDevice(String),
    /// Input captured by the native shell.
    HostInput(HostInput),
    /// Client asks for control to be returned to the server (edge push or
    /// hotkey on the client).
    RequestControl,
    /// Server: toggle driving the remote device (menu bar / UI button).
    ToggleControl,
    /// Local clipboard changed (text).
    SetClipboardText(String),
    SetDeviceName(String),
    SetEdge(Edge),
    SetClipboardEnabled(bool),
    SetHotkey(Hotkey),
    SetListenPort(u16),
    SetLocalScreen(ScreenInfo),
    /// Tear down runtime and exit the command loop.
    Shutdown,
}

/// The one live peer session's outbound channel.
pub struct SessionSlot {
    pub role: Mode,
    pub device_id: String,
    pub tx: UnboundedSender<Message>,
}

struct RunHandle {
    token: CancellationToken,
    handle: tokio::task::JoinHandle<()>,
}

/// See module docs.
pub struct Engine {
    pub cfg: Mutex<Config>,
    pub status: Mutex<Status>,
    pub host: Arc<dyn Host>,
    pub identity: Identity,
    pub config_dir: PathBuf,
    pub counters: Arc<Counters>,
    /// Devices currently visible on the LAN (discovery).
    pub devices: Mutex<HashMap<String, DeviceInfo>>,
    /// The active peer session, when any.
    pub session: Mutex<Option<SessionSlot>>,
    /// Server-side pairing code.
    pub pairing: Mutex<Option<pairing::PairingCode>>,
    pub clipboard_gate: Mutex<ClipboardGate>,
    pub move_coalescer: Mutex<MoveCoalescer>,
    pub edge_tracker: Mutex<EdgeTracker>,
    pub local_screen: Mutex<ScreenInfo>,
    /// True while this side owns the shared cursor (see [`Status::focused`]).
    pub focused: AtomicBool,
    /// Local edge the cursor must cross to hand focus to the peer. Derived
    /// from the last focus transfer (the edge the cursor came in through), so
    /// mismatched per-machine edge settings cannot break the return trip.
    pub focus_edge: Mutex<Option<Edge>>,
    /// Client role: a pairing code from the UI, waiting for the session to
    /// consume it (avoids any startup race with the session task).
    pub pending_pair_code: Mutex<Option<String>>,
    /// The engine's role task (listener / dialer).
    run: Mutex<Option<RunHandle>>,
    pub flusher: Mutex<Option<CancellationToken>>,
    pub runtime: Runtime,
    pub started_ms: u64,
    pub listening: AtomicBool,
    /// The port the listener is actually bound to (0 = not bound); differs
    /// from the configured port when that one was busy and we fell back.
    pub actual_port: Mutex<u16>,
    cmd_tx: UnboundedSender<Cmd>,
    _log_guard: Mutex<Option<tracing_appender::non_blocking::WorkerGuard>>,
}

impl Engine {
    /// Create the engine, load configuration/identity, initialize logging and
    /// spawn the command loop and discovery.
    pub fn new(config_dir: PathBuf, host: Arc<dyn Host>) -> Result<Arc<Self>> {
        crypto::install_crypto_provider();
        config::ensure_dir(&config_dir)?;

        let (cfg, config_notice) = Config::load(&config_dir)?;
        let log_guard = crate::logging::init(&config_dir, &cfg.log_level);
        if let Some(notice) = &config_notice {
            tracing::warn!("{notice}");
        }
        let identity = crypto::load_or_create_identity(&config::identity_dir(&config_dir))
            .context("loading device identity")?;

        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .thread_name("flowlink-rt")
            .enable_all()
            .build()
            .context("creating tokio runtime")?;

        let (cmd_tx, cmd_rx) = mpsc::unbounded_channel();

        let initial_status = Self::build_initial_status(&cfg, &identity);
        let engine = Arc::new(Engine {
            status: Mutex::new(initial_status),
            clipboard_gate: Mutex::new(ClipboardGate::new(
                cfg.clipboard_enabled,
                cfg.clipboard_max_bytes,
            )),
            move_coalescer: Mutex::new(MoveCoalescer::default_profile()),
            edge_tracker: Mutex::new(EdgeTracker::new()),
            local_screen: Mutex::new(ScreenInfo::default()),
            focused: AtomicBool::new(false),
            focus_edge: Mutex::new(None),
            devices: Mutex::new(HashMap::new()),
            session: Mutex::new(None),
            pairing: Mutex::new(None),
            pending_pair_code: Mutex::new(None),
            run: Mutex::new(None),
            flusher: Mutex::new(None),
            started_ms: util::now_ms(),
            listening: AtomicBool::new(false),
            actual_port: Mutex::new(0),
            cmd_tx,
            identity,
            config_dir,
            cfg: Mutex::new(cfg),
            host,
            counters: Arc::new(Counters::default()),
            runtime,
            _log_guard: Mutex::new(log_guard),
        });

        let run_engine = engine.clone();
        engine.runtime.spawn(async move {
            run_engine.run(cmd_rx).await;
        });

        if let Some(notice) = config_notice {
            engine.notice("config", &notice);
        }
        tracing::info!(version = crate::VERSION, dir = %engine.config_dir.display(), "engine ready");
        Ok(engine)
    }

    fn build_initial_status(cfg: &Config, identity: &Identity) -> Status {
        Status {
            app: "FlowLink".to_string(),
            version: crate::VERSION.to_string(),
            mode: cfg.mode,
            state: ConnState::Off,
            error: None,
            reconnect_in_ms: None,
            local: LocalInfo {
                id: cfg.device_id.clone(),
                name: cfg.device_name.clone(),
                fingerprint: identity.fingerprint.clone(),
                addrs: util::local_addrs(),
                port: cfg.listen_port,
            },
            peer: None,
            pairing: None,
            remote_active: false,
            focused: false,
            forwarding: false,
            remote_edge: None,
            edge: cfg.edge,
            clipboard_enabled: cfg.clipboard_enabled,
            clipboard_max_bytes: cfg.clipboard_max_bytes,
            hotkey: cfg.hotkey,
            discovered: Vec::new(),
            paired_devices: cfg
                .paired_devices
                .iter()
                .map(PairedDevicePublic::from)
                .collect(),
            metrics: Metrics::default(),
            listening: false,
        }
    }

    // -----------------------------------------------------------------
    // Command surface
    // -----------------------------------------------------------------

    /// Queue a command (non-blocking, callable from any thread).
    pub fn execute(&self, cmd: Cmd) {
        let _ = self.cmd_tx.send(cmd);
    }

    /// Snapshot of the current status as JSON.
    pub fn status_json(&self) -> String {
        let mut status = self.status.lock();
        self.refresh_metrics(&mut status);
        status.to_json()
    }

    /// Snapshot of the configuration as JSON.
    pub fn config_json(&self) -> String {
        serde_json::to_string_pretty(&*self.cfg.lock()).unwrap_or_else(|_| "{}".to_string())
    }

    pub fn config_dir(&self) -> &PathBuf {
        &self.config_dir
    }

    /// The port the listener is actually bound to, falling back to the
    /// configured one when nothing is bound yet. Invite links, discovery and
    /// the status snapshot all use this value.
    pub fn effective_listen_port(&self) -> u16 {
        let actual = *self.actual_port.lock();
        if actual != 0 {
            actual
        } else {
            self.cfg.lock().listen_port
        }
    }

    pub fn set_actual_port(&self, port: u16) {
        *self.actual_port.lock() = port;
    }

    /// QR code for the current pairing session (invite link with address,
    /// fingerprint and code).
    pub fn pairing_qr(&self) -> Result<QrMatrix> {
        let port = self.effective_listen_port();
        let code = self
            .pairing
            .lock()
            .as_ref()
            .map(|p| p.code.clone())
            .filter(|c| !c.is_empty());
        let addr = util::preferred_lan_addr().unwrap_or_else(|| "127.0.0.1".to_string());
        let url = qr::invite_url(&addr, port, &self.identity.fingerprint, code.as_deref());
        qr::qr_matrix(&url)
    }

    /// Invite link text (same content as the QR).
    pub fn invite_link(&self) -> String {
        let port = self.effective_listen_port();
        let code = self
            .pairing
            .lock()
            .as_ref()
            .map(|p| p.code.clone())
            .filter(|c| !c.is_empty());
        let addr = util::preferred_lan_addr().unwrap_or_else(|| "127.0.0.1".to_string());
        qr::invite_url(&addr, port, &self.identity.fingerprint, code.as_deref())
    }

    // -----------------------------------------------------------------
    // Main loop
    // -----------------------------------------------------------------

    async fn run(self: Arc<Self>, mut rx: UnboundedReceiver<Cmd>) {
        // Discovery lives for the whole engine lifetime.
        let discovery_token = CancellationToken::new();
        self.runtime.spawn(crate::discovery::discovery_task(
            self.clone(),
            discovery_token.clone(),
        ));

        let mut tick = tokio::time::interval(Duration::from_millis(500));
        tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        let mut tick_count: u64 = 0;

        loop {
            tokio::select! {
                cmd = rx.recv() => {
                    match cmd {
                        Some(cmd) => {
                            if !self.handle_cmd(cmd).await {
                                break;
                            }
                        }
                        None => break,
                    }
                }
                _ = tick.tick() => {
                    tick_count += 1;
                    self.on_tick(tick_count).await;
                }
            }
        }

        self.stop_run();
        discovery_token.cancel();
        tracing::info!("engine command loop finished");
    }

    async fn handle_cmd(self: &Arc<Self>, cmd: Cmd) -> bool {
        match cmd {
            Cmd::Start => {
                self.start_run();
            }
            Cmd::Stop => {
                self.stop_run();
            }
            Cmd::Shutdown => return false,
            Cmd::SetMode(mode) => {
                {
                    let mut cfg = self.cfg.lock();
                    if cfg.mode == mode {
                        drop(cfg);
                        // Restart anyway: idempotent and cheap.
                    } else {
                        cfg.mode = mode;
                        let cfg_snapshot = cfg.clone();
                        drop(cfg);
                        self.save_config(&cfg_snapshot);
                    }
                }
                if let Mode::Client = mode {
                    // Nothing else to do; target comes from last_server.
                }
                {
                    let mut status = self.status.lock();
                    status.mode = mode;
                }
                self.start_run();
            }
            Cmd::Connect {
                host,
                port,
                code,
                fingerprint,
            } => {
                self.stop_run();
                {
                    let mut cfg = self.cfg.lock();
                    cfg.mode = Mode::Client;
                    let reuse_token = cfg
                        .last_server
                        .as_ref()
                        .filter(|ls| ls.host == host && ls.port == port)
                        .and_then(|ls| ls.token.clone());
                    cfg.last_server = Some(LastServer {
                        host,
                        port,
                        device_id: None,
                        fingerprint,
                        token: reuse_token,
                    });
                    let cfg_snapshot = cfg.clone();
                    drop(cfg);
                    self.save_config(&cfg_snapshot);
                }
                self.notice("connect", "connecting…");
                self.start_run();
                if let Some(code) = code {
                    // Set *after* start_run: stop_run (inside) clears the
                    // mailbox, and the pairing flow polls it periodically.
                    *self.pending_pair_code.lock() = Some(code);
                }
            }
            Cmd::ConnectLink(link) => match qr::ConnectLink::parse(&link) {
                Ok(parsed) => {
                    self.execute(Cmd::Connect {
                        host: parsed.host,
                        port: parsed.port,
                        code: parsed.code,
                        fingerprint: parsed.fp,
                    });
                }
                Err(err) => {
                    self.notice("error", &format!("invalid link: {err}"));
                }
            },
            Cmd::Disconnect => {
                self.stop_run();
                self.notice("info", "disconnected");
            }
            Cmd::SubmitPairCode(code) => {
                // Park the code; the client pairing flow polls this mailbox.
                // The authoritative attempt count arrives with PairResult.
                *self.pending_pair_code.lock() = Some(code);
                if let Some(pairing) = self.status.lock().pairing.as_mut() {
                    pairing.awaiting_input = true;
                }
                self.notify();
            }
            Cmd::RegeneratePairCode => {
                *self.pairing.lock() = Some(pairing::PairingCode::generate_default());
                self.refresh_pairing_status();
                self.notify();
                self.notice("pairing", "new pairing code generated");
            }
            Cmd::ForgetDevice(id) => {
                let removed;
                {
                    let mut cfg = self.cfg.lock();
                    removed = cfg.forget_paired(&id);
                    let cfg_snapshot = cfg.clone();
                    drop(cfg);
                    self.save_config(&cfg_snapshot);
                }
                if removed {
                    let cfg = self.cfg.lock();
                    let mut status = self.status.lock();
                    status.paired_devices = cfg
                        .paired_devices
                        .iter()
                        .map(PairedDevicePublic::from)
                        .collect();
                    drop(cfg);
                }
                self.notify();
            }
            Cmd::HostInput(ev) => {
                self.handle_host_input(ev);
            }
            Cmd::RequestControl => {
                // "Take the cursor back" from the UI/hotkey on this side.
                self.take_focus();
            }
            Cmd::ToggleControl => {
                self.toggle_focus();
            }
            Cmd::SetClipboardText(text) => {
                self.handle_local_clipboard(&text);
            }
            Cmd::SetDeviceName(name) => {
                {
                    let mut cfg = self.cfg.lock();
                    cfg.device_name = name.clone();
                    let cfg_snapshot = cfg.clone();
                    drop(cfg);
                    self.save_config(&cfg_snapshot);
                }
                self.status.lock().local.name = name;
                self.notify();
            }
            Cmd::SetEdge(edge) => {
                {
                    let mut cfg = self.cfg.lock();
                    cfg.edge = edge;
                    let cfg_snapshot = cfg.clone();
                    drop(cfg);
                    self.save_config(&cfg_snapshot);
                }
                self.status.lock().edge = edge;
                self.notify();
            }
            Cmd::SetClipboardEnabled(enabled) => {
                {
                    let mut cfg = self.cfg.lock();
                    cfg.clipboard_enabled = enabled;
                    let cfg_snapshot = cfg.clone();
                    drop(cfg);
                    self.save_config(&cfg_snapshot);
                }
                self.clipboard_gate.lock().set_enabled(enabled);
                self.status.lock().clipboard_enabled = enabled;
                self.notify();
            }
            Cmd::SetHotkey(hotkey) => {
                {
                    let mut cfg = self.cfg.lock();
                    cfg.hotkey = hotkey;
                    let cfg_snapshot = cfg.clone();
                    drop(cfg);
                    self.save_config(&cfg_snapshot);
                }
                self.status.lock().hotkey = hotkey;
                self.notify();
            }
            Cmd::SetListenPort(port) => {
                {
                    let mut cfg = self.cfg.lock();
                    cfg.listen_port = port;
                    let cfg_snapshot = cfg.clone();
                    drop(cfg);
                    self.save_config(&cfg_snapshot);
                }
                // A new preference invalidates the fallback binding.
                self.set_actual_port(0);
                // Restart the listener if (and only if) it is already running.
                let running = self.run.lock().is_some();
                if running && self.cfg.lock().mode == Mode::Server {
                    self.start_run();
                }
                self.notify();
            }
            Cmd::SetLocalScreen(screen) => {
                *self.local_screen.lock() = screen;
            }
        }
        true
    }

    async fn on_tick(self: &Arc<Self>, tick_count: u64) {
        // Refresh internal state twice per second; publish status to the UI
        // once per second (idle CPU stays negligible).
        {
            let mut status = self.status.lock();
            self.refresh_metrics(&mut status);
        }
        if tick_count % 2 == 0 {
            self.refresh_pairing_status();
            self.notify();
        }
        // Every 2 s: refresh LAN addresses and prune stale devices.
        if tick_count % 4 == 0 {
            let addrs = util::local_addrs();
            self.status.lock().local.addrs = addrs;
        }
        if tick_count % 2 == 0 {
            let now = util::now_ms();
            let mut devices = self.devices.lock();
            let before = devices.len();
            devices.retain(|_, d| now.saturating_sub(d.last_seen_ms) < 15_000);
            if devices.len() != before {
                self.refresh_devices_status();
            }
        }
    }

    fn refresh_metrics(&self, status: &mut Status) {
        self.counters.snapshot_into(&mut status.metrics);
        status.metrics.connected_since_ms = status.peer.as_ref().map(|p| p.since_ms);
        status.metrics.uptime_ms = util::now_ms().saturating_sub(self.started_ms);
        status.listening = self.listening.load(Ordering::Relaxed);
        status.clipboard_max_bytes = self.cfg.lock().clipboard_max_bytes;
        status.local.port = self.effective_listen_port();
    }

    pub fn refresh_devices_status(&self) {
        let mut list: Vec<DeviceInfo> = self.devices.lock().values().cloned().collect();
        list.sort_by(|a, b| b.last_seen_ms.cmp(&a.last_seen_ms));
        self.status.lock().discovered = list;
    }

    fn save_config(&self, cfg: &Config) {
        if let Err(err) = cfg.save(&self.config_dir) {
            tracing::error!("saving config failed: {err}");
            self.notice("error", &format!("could not save settings: {err}"));
        }
    }

    // -----------------------------------------------------------------
    // Run control
    // -----------------------------------------------------------------

    fn start_run(self: &Arc<Self>) {
        self.stop_run();
        let mode = self.cfg.lock().mode;
        let token = CancellationToken::new();
        let engine = self.clone();
        let t = token.clone();
        let handle = self.runtime.spawn(async move {
            match mode {
                Mode::Server => {
                    if let Err(err) = session::server_loop(engine.clone(), t).await {
                        engine.fail(err.to_string());
                    }
                }
                Mode::Client => {
                    session::client_loop(engine.clone(), t).await;
                }
            }
        });
        *self.run.lock() = Some(RunHandle { token, handle });
    }

    fn stop_run(&self) {
        self.stop_flusher();
        if let Some(run) = self.run.lock().take() {
            // Graceful first: cancellation lets the session send `Bye` and
            // close the socket cleanly; hard-abort only as a fallback.
            run.token.cancel();
            let mut handle = run.handle;
            self.runtime.spawn(async move {
                tokio::select! {
                    _ = &mut handle => {}
                    _ = tokio::time::sleep(Duration::from_millis(700)) => {
                        handle.abort();
                    }
                }
            });
        }
        self.deregister_session();
        *self.pending_pair_code.lock() = None;
        self.set_actual_port(0);
        {
            let mut status = self.status.lock();
            status.state = ConnState::Off;
            status.error = None;
            status.reconnect_in_ms = None;
            status.peer = None;
            status.pairing = None;
            status.remote_active = false;
            status.focused = false;
            status.forwarding = false;
            status.remote_edge = None;
            status.listening = false;
        }
        self.listening.store(false, Ordering::Relaxed);
        // A fresh run starts armed: no control is active at startup.
        *self.edge_tracker.lock() = EdgeTracker::new();
        self.move_coalescer.lock().clear();
        self.counters.reset();
        self.notify();
    }

    // -----------------------------------------------------------------
    // Input routing (symmetric "one body" focus model)
    // -----------------------------------------------------------------
    //
    // Exactly one side owns the shared cursor at a time (`focused`):
    //
    // * the focused side uses its local input locally and hands the cursor to
    //   the peer when the pointer crosses its configured edge;
    // * the other side captures all of its input and forwards it, while the
    //   focused side injects it — so either keyboard/mouse drives whichever
    //   screen currently shows the cursor;
    // * the hotkey on either side moves the cursor here/there, and a
    //   `Reclaim` from the peer asks the focused side to let go.

    fn handle_host_input(self: &Arc<Self>, ev: HostInput) {
        // Input only means something while a peer session is live.
        if !self.has_session() || self.status.lock().state != ConnState::Connected {
            return;
        }

        // Hotkey first, on both sides.
        if let HostInput::Key {
            usage,
            down: true,
            modifiers,
        } = ev
        {
            let hotkey = self.cfg.lock().hotkey;
            if hotkey.matches(usage, modifiers) {
                self.toggle_focus();
                return;
            }
        }

        if self.focused.load(Ordering::Relaxed) {
            // Cursor is here: local input is applied by the OS (the shell
            // passes events through) and only edge crossings matter.
            if let HostInput::MouseMove { x_px, y_px, dx, dy } = ev {
                // Prefer the edge this focus arrived through (self-aligning
                // across machines); fall back to the configured edge.
                let edge = self
                    .focus_edge
                    .lock()
                    .unwrap_or_else(|| self.cfg.lock().edge);
                let screen = *self.local_screen.lock();
                if self
                    .edge_tracker
                    .lock()
                    .feed(edge, screen, x_px, y_px, dx, dy)
                {
                    let along = edge::along_norm(edge, screen, x_px, y_px);
                    self.give_focus(edge, along);
                }
            }
            return;
        }

        // Cursor is on the peer: capture and forward everything.
        match ev {
            HostInput::MouseMove { dx, dy, .. } => {
                let now = util::now_ms();
                let flush = self.move_coalescer.lock().push(dx, dy, now);
                if let Some((dx, dy)) = flush {
                    if dx != 0.0 || dy != 0.0 {
                        self.push_to_peer(Message::Input(InputEvent::MouseMove { dx, dy }));
                    }
                }
            }
            HostInput::Key {
                usage,
                down,
                modifiers,
            } => {
                self.push_to_peer(Message::Input(InputEvent::Key {
                    usage,
                    down,
                    modifiers,
                }));
            }
            HostInput::MouseButton {
                button,
                down,
                modifiers,
            } => {
                self.push_to_peer(Message::Input(InputEvent::MouseButton {
                    button,
                    down,
                    modifiers,
                }));
            }
            HostInput::Scroll { dx, dy, precise } => {
                self.push_to_peer(Message::Input(InputEvent::Scroll {
                    dx: dx as f32,
                    dy: dy as f32,
                    precise,
                }));
            }
        }
    }

    /// Hand the shared cursor to the peer through `edge` (the side losing it).
    fn give_focus(self: &Arc<Self>, edge: Edge, along_norm: f32) {
        if !self.focused.load(Ordering::Relaxed) {
            return;
        }
        self.push_to_peer(Message::Input(InputEvent::Enter { edge, along_norm }));
        self.set_focused(false);
        self.notice("focus", "the cursor is now on the other computer");
        tracing::info!(edge = edge.as_str(), "gave focus to the peer");
    }

    /// Take the shared cursor onto this screen (hotkey on the unfocused side).
    fn take_focus(self: &Arc<Self>) {
        if self.focused.load(Ordering::Relaxed) || !self.has_session() {
            return;
        }
        // The side that currently shows the cursor must stop showing it.
        self.push_to_peer(Message::Reclaim);
        self.set_focused(true);
        // Appear at the edge facing the peer: the host mirrors `Enter.edge`,
        // so passing the opposite of our own edge lands on our own edge.
        let edge = self.cfg.lock().edge;
        *self.focus_edge.lock() = Some(edge);
        self.host.on_input(&InputEvent::Enter {
            edge: edge.opposite(),
            along_norm: 0.5,
        });
        self.notice("focus", "the cursor is now on this computer");
        tracing::info!(edge = edge.as_str(), "took the cursor back");
    }

    /// Hotkey on either side: move the cursor there, or bring it here.
    fn toggle_focus(self: &Arc<Self>) {
        if self.status.lock().state != ConnState::Connected {
            self.notice("info", "not connected — nothing to toggle");
            return;
        }
        if self.focused.load(Ordering::Relaxed) {
            // Leave through the edge this focus arrived from (falls back to
            // the configured edge), so the other side always lands at the
            // mirror position.
            let edge = self
                .focus_edge
                .lock()
                .unwrap_or_else(|| self.cfg.lock().edge);
            self.give_focus(edge, 0.5);
        } else {
            self.take_focus();
        }
    }

    /// The peer pushed its pointer onto this screen (it sent `Enter`).
    pub fn on_focus_granted(self: &Arc<Self>, edge: Edge) {
        if self.focused.load(Ordering::Relaxed) {
            return;
        }
        // The cursor appears on the mirrored side; pushing back through that
        // same local edge returns focus. Deriving it here means the two
        // machines do not need perfectly mirrored edge settings.
        *self.focus_edge.lock() = Some(edge.opposite());
        self.set_focused(true);
        tracing::info!(edge = edge.as_str(), "received the cursor from the peer");
    }

    /// The peer asked for the cursor back (hotkey pressed on its side).
    pub fn on_reclaim(self: &Arc<Self>) {
        if !self.focused.load(Ordering::Relaxed) {
            return;
        }
        self.set_focused(false);
        tracing::info!("peer reclaimed the cursor");
    }

    fn set_focused(self: &Arc<Self>, focused: bool) {
        let was = self.focused.swap(focused, Ordering::Relaxed);
        {
            let mut status = self.status.lock();
            status.focused = focused;
            status.forwarding = !focused && self.has_session();
            // Compatibility alias for shells built before the focus model:
            // "this machine's input is captured for the peer".
            status.remote_active = status.forwarding;
            status.remote_edge = None;
        }
        if was != focused {
            // No key/button may stay held across a switch.
            self.host.on_input(&InputEvent::ReleaseAll);
            self.move_coalescer.lock().clear();
            self.edge_tracker.lock().hint_returned();
            if !focused && self.has_session() {
                self.start_flusher();
            } else {
                self.stop_flusher();
            }
        }
        self.notify();
    }

    fn start_flusher(self: &Arc<Self>) {
        self.stop_flusher();
        let token = CancellationToken::new();
        *self.flusher.lock() = Some(token.clone());
        let engine = self.clone();
        self.runtime.spawn(async move {
            let mut tick = tokio::time::interval(Duration::from_millis(8));
            tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
            loop {
                tokio::select! {
                    _ = token.cancelled() => break,
                    _ = tick.tick() => {
                        if !engine.status.lock().forwarding {
                            continue;
                        }
                        let now = util::now_ms();
                        if let Some((dx, dy)) = engine.move_coalescer.lock().flush_if_due(now) {
                            if dx != 0.0 || dy != 0.0 {
                                engine.push_to_peer(Message::Input(InputEvent::MouseMove { dx, dy }));
                            }
                        }
                    }
                }
            }
        });
    }

    fn stop_flusher(&self) {
        if let Some(token) = self.flusher.lock().take() {
            token.cancel();
        }
    }

    // -----------------------------------------------------------------
    // Clipboard
    // -----------------------------------------------------------------

    fn handle_local_clipboard(&self, text: &str) {
        let decision = self.clipboard_gate.lock().local_change(text);
        match decision {
            ClipDecision::Send => {
                let bytes = text.len();
                if self.push_to_peer(Message::Clipboard {
                    text: text.to_string(),
                }) {
                    tracing::debug!(bytes, "clipboard sent to peer");
                } else {
                    tracing::trace!(bytes, "clipboard changed but no peer connected");
                }
            }
            ClipDecision::TooLarge => {
                tracing::warn!(
                    bytes = text.len(),
                    "clipboard content exceeds size limit — not synced"
                );
            }
            _ => {}
        }
    }

    /// Called from the session when peer clipboard text arrives.
    pub fn handle_incoming_clipboard(&self, text: &str) {
        match self.clipboard_gate.lock().incoming(text) {
            ClipDecision::Applied => {
                tracing::debug!(bytes = text.len(), "applying peer clipboard");
                self.host.on_clipboard(text);
            }
            ClipDecision::TooLarge => {
                tracing::warn!(
                    bytes = text.len(),
                    "peer clipboard content exceeds size limit"
                );
            }
            _ => {}
        }
    }

    // -----------------------------------------------------------------
    // Session plumbing (called from session tasks)
    // -----------------------------------------------------------------

    /// Register the outbound channel of a ready session. Returns false when
    /// another session is already live (server is single-client by design).
    pub fn register_session(
        &self,
        role: Mode,
        device_id: &str,
        tx: UnboundedSender<Message>,
    ) -> bool {
        let mut slot = self.session.lock();
        if slot.is_some() {
            return false;
        }
        *slot = Some(SessionSlot {
            role,
            device_id: device_id.to_string(),
            tx,
        });
        true
    }

    pub fn deregister_session(&self) {
        *self.session.lock() = None;
    }

    pub fn has_session(&self) -> bool {
        self.session.lock().is_some()
    }

    /// Send a message to the live session (no-op without one).
    pub fn push_to_peer(&self, msg: Message) -> bool {
        let slot = self.session.lock();
        let Some(slot) = slot.as_ref() else {
            return false;
        };
        if matches!(msg, Message::Input(_)) {
            self.counters.events_out.fetch_add(1, Ordering::Relaxed);
        }
        slot.tx.send(msg).is_ok()
    }

    /// Called by the session when peer input arrives (both roles).
    pub fn handle_incoming_input(self: &Arc<Self>, event: InputEvent) {
        self.counters.events_in.fetch_add(1, Ordering::Relaxed);
        match event {
            InputEvent::Enter { edge, .. } => {
                tracing::info!(
                    edge = edge.as_str(),
                    "received the shared cursor from the peer"
                );
                self.on_focus_granted(edge);
                self.host.on_input(&event);
            }
            InputEvent::ReleaseAll => {
                if self.focused.load(Ordering::Relaxed) {
                    self.host.on_input(&event);
                }
            }
            _ => {
                if self.focused.load(Ordering::Relaxed) {
                    self.host.on_input(&event);
                } else {
                    tracing::debug!("dropping input event while the cursor is elsewhere");
                }
            }
        }
    }

    /// Called once a session is fully ready. The server's screen owns the
    /// cursor at the start; the client begins by forwarding its input.
    pub fn on_session_ready(self: &Arc<Self>, role: Mode) {
        self.set_focused(role == Mode::Server);
        if role == Mode::Server {
            // Fresh session with the pointer at a normal position: the edge
            // tracker may fire immediately (no warp happened). The configured
            // edge is the boundary towards the peer.
            *self.edge_tracker.lock() = EdgeTracker::new();
            *self.focus_edge.lock() = Some(self.cfg.lock().edge);
        } else {
            // The client starts in the forwarding state without a focus
            // transition, so start the move flusher explicitly.
            *self.focus_edge.lock() = None;
            self.start_flusher();
        }
        self.notify();
    }

    /// Server-side: verify a submitted pairing code.
    pub fn verify_pair_code(&self, code: &str) -> PairVerdict {
        let now = util::now_ms();
        let verdict;
        let regenerated;
        {
            let mut pairing = self.pairing.lock();
            match pairing.as_mut() {
                Some(pc) => {
                    verdict = pc.verify(code, now);
                    match verdict {
                        PairVerdict::Accepted => {
                            // One-time use.
                            *pairing = None;
                            regenerated = false;
                        }
                        PairVerdict::Exhausted | PairVerdict::Expired => {
                            *pairing = Some(pairing::PairingCode::generate_default());
                            regenerated = true;
                        }
                        PairVerdict::Wrong { .. } => {
                            regenerated = false;
                        }
                    }
                }
                None => {
                    verdict = PairVerdict::Expired;
                    regenerated = false;
                }
            }
        }
        if regenerated {
            self.notice("pairing", "pairing code invalidated — a new code is shown");
            self.refresh_pairing_status();
        }
        verdict
    }

    pub fn ensure_pairing_code(&self) {
        let mut pairing = self.pairing.lock();
        if pairing.is_none() {
            *pairing = Some(pairing::PairingCode::generate_default());
        }
    }

    pub fn refresh_pairing_status(&self) {
        let mode = self.cfg.lock().mode;
        let mut status = self.status.lock();
        if mode == Mode::Server {
            let pairing = self.pairing.lock();
            status.pairing = pairing.as_ref().map(|pc| PairingStatus {
                code: Some(pc.code.clone()),
                attempts_left: pc.attempts_left,
                expires_in_ms: pc.remaining_ms(util::now_ms()),
                awaiting_input: false,
            });
        } else if status.state == ConnState::AwaitingPair {
            if let Some(p) = status.pairing.as_mut() {
                p.awaiting_input = true;
            } else {
                status.pairing = Some(PairingStatus {
                    code: None,
                    attempts_left: pairing::MAX_ATTEMPTS,
                    expires_in_ms: 0,
                    awaiting_input: true,
                });
            }
        } else {
            status.pairing = None;
        }
    }

    /// Server-side: remember a newly paired device.
    pub fn remember_paired_device(&self, device_id: &str, name: &str, token: &str, addr: &str) {
        let cfg_snapshot = {
            let mut cfg = self.cfg.lock();
            cfg.upsert_paired(PairedDevice {
                id: device_id.to_string(),
                name: name.to_string(),
                token: token.to_string(),
                fingerprint: String::new(),
                last_addr: Some(addr.to_string()),
                last_seen_ms: util::now_ms(),
            });
            cfg.clone()
        };
        self.save_config(&cfg_snapshot);
        self.status.lock().paired_devices = cfg_snapshot
            .paired_devices
            .iter()
            .map(PairedDevicePublic::from)
            .collect();
    }

    /// Server-side: refresh last-seen for a reconnecting paired device.
    pub fn touch_paired_device(&self, device_id: &str, addr: &str) {
        let cfg_snapshot = {
            let mut cfg = self.cfg.lock();
            if let Some(device) = cfg.paired_devices.iter_mut().find(|d| d.id == device_id) {
                device.last_addr = Some(addr.to_string());
                device.last_seen_ms = util::now_ms();
            }
            cfg.clone()
        };
        self.save_config(&cfg_snapshot);
    }

    /// Client-side: persist server memory after a successful connection.
    pub fn remember_server(
        &self,
        addr: &str,
        port: u16,
        device_id: Option<String>,
        fingerprint: Option<String>,
        token: Option<String>,
    ) {
        let cfg_snapshot = {
            let mut cfg = self.cfg.lock();
            cfg.last_server = Some(LastServer {
                host: addr.to_string(),
                port,
                device_id,
                fingerprint,
                token,
            });
            cfg.clone()
        };
        self.save_config(&cfg_snapshot);
    }

    /// Client-side: store the pairing token for silent reconnects.
    pub fn save_server_token(&self, server_id: &str, server_name: &str, token: &str, addr: &str) {
        let cfg_snapshot = {
            let mut cfg = self.cfg.lock();
            let fp = cfg
                .last_server
                .as_ref()
                .and_then(|ls| ls.fingerprint.clone());
            if let Some(ls) = cfg.last_server.as_mut() {
                ls.token = Some(token.to_string());
                ls.device_id = Some(server_id.to_string());
            }
            // Upsert: reconnects must refresh the entry, not duplicate it.
            cfg.upsert_paired(PairedDevice {
                id: server_id.to_string(),
                name: server_name.to_string(),
                token: token.to_string(),
                fingerprint: fp.unwrap_or_default(),
                last_addr: Some(addr.to_string()),
                last_seen_ms: util::now_ms(),
            });
            cfg.clone()
        };
        self.save_config(&cfg_snapshot);
        self.status.lock().paired_devices = cfg_snapshot
            .paired_devices
            .iter()
            .map(PairedDevicePublic::from)
            .collect();
    }

    // -----------------------------------------------------------------
    // Status updates
    // -----------------------------------------------------------------

    pub fn set_state(&self, state: ConnState) {
        {
            let mut status = self.status.lock();
            status.state = state;
            if state != ConnState::Error {
                status.error = None;
            }
            if state != ConnState::Reconnecting {
                status.reconnect_in_ms = None;
            }
        }
        self.notify();
    }

    pub fn fail(&self, message: String) {
        tracing::error!("{message}");
        {
            let mut status = self.status.lock();
            status.state = ConnState::Error;
            status.error = Some(message.clone());
        }
        self.notify();
        self.notice("error", &message);
    }

    pub fn set_error(&self, state: ConnState, message: String) {
        {
            let mut status = self.status.lock();
            status.state = state;
            status.error = Some(message.clone());
        }
        self.notify();
    }

    pub fn set_reconnect_delay(&self, delay_ms: Option<u64>) {
        self.status.lock().reconnect_in_ms = delay_ms;
        self.notify();
    }

    pub fn set_connecting_peer(&self, hello: &crate::protocol::Hello, addr: &str) {
        {
            let mut status = self.status.lock();
            status.peer = Some(PeerStatus {
                id: hello.device_id.clone(),
                name: hello.name.clone(),
                version: Some(hello.version.clone()),
                fingerprint: None,
                addr: addr.to_string(),
                paired: false,
                since_ms: util::now_ms(),
            });
        }
        self.notify();
    }

    pub fn set_connected_peer(
        &self,
        id: &str,
        name: &str,
        version: Option<String>,
        fingerprint: Option<String>,
        addr: &str,
        paired: bool,
    ) {
        {
            let mut status = self.status.lock();
            status.peer = Some(PeerStatus {
                id: id.to_string(),
                name: name.to_string(),
                version,
                fingerprint,
                addr: addr.to_string(),
                paired,
                since_ms: util::now_ms(),
            });
        }
        self.notify();
    }

    /// Server role: session ended — return to listening.
    pub fn on_server_session_ended(self: &Arc<Self>) {
        self.set_focused(false);
        *self.focus_edge.lock() = None;
        {
            let mut status = self.status.lock();
            status.state = ConnState::Listening;
            status.peer = None;
            status.forwarding = false;
            status.focused = false;
            status.remote_active = false;
        }
        self.ensure_pairing_code();
        self.refresh_pairing_status();
        self.notify();
    }

    /// Client role: session ended — clear local focus state.
    pub fn on_client_session_ended(self: &Arc<Self>) {
        self.set_focused(false);
        *self.focus_edge.lock() = None;
        {
            let mut status = self.status.lock();
            status.peer = None;
            status.forwarding = false;
            status.focused = false;
            status.remote_active = false;
        }
        self.notify();
    }

    pub fn notice(&self, kind: &str, message: &str) {
        self.host.on_notice(&host::notice_json(kind, message));
    }

    /// Push the full status snapshot to the host shell.
    pub fn notify(&self) {
        let mut status = self.status.lock();
        self.refresh_metrics(&mut status);
        let json = status.to_json();
        drop(status);
        self.host.on_status(&json);
    }

    pub fn own_device_id(&self) -> String {
        self.cfg.lock().device_id.clone()
    }

    pub fn own_device_name(&self) -> String {
        self.cfg.lock().device_name.clone()
    }
}

// =========================================================================
// Tests
// =========================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use crate::host::NullHost;
    use std::sync::atomic::AtomicUsize;
    use tempfile::TempDir;

    /// Host that records callbacks for assertions.
    #[derive(Default)]
    struct RecordingHost {
        statuses: Mutex<Vec<String>>,
        inputs: Mutex<Vec<InputEvent>>,
        clipboards: Mutex<Vec<String>>,
        notices: Mutex<Vec<String>>,
        count: AtomicUsize,
    }

    impl Host for RecordingHost {
        fn on_status(&self, status_json: &str) {
            self.count.fetch_add(1, Ordering::Relaxed);
            let mut statuses = self.statuses.lock();
            statuses.push(status_json.to_string());
            if statuses.len() > 64 {
                statuses.remove(0);
            }
        }
        fn on_input(&self, event: &InputEvent) {
            self.inputs.lock().push(event.clone());
        }
        fn on_clipboard(&self, text: &str) {
            self.clipboards.lock().push(text.to_string());
        }
        fn on_notice(&self, notice_json: &str) {
            self.notices.lock().push(notice_json.to_string());
        }
    }

    fn make_engine(host: Arc<dyn Host>) -> (Arc<Engine>, TempDir) {
        let dir = TempDir::new().unwrap();
        let engine = Engine::new(dir.path().to_path_buf(), host).unwrap();
        (engine, dir)
    }

    #[test]
    fn engine_starts_in_off_state() {
        let (engine, _dir) = make_engine(Arc::new(NullHost));
        let status: serde_json::Value = serde_json::from_str(&engine.status_json()).unwrap();
        assert_eq!(status["state"], "off");
        assert_eq!(status["app"], "FlowLink");
        assert!(status["local"]["fingerprint"].as_str().unwrap().len() == 64);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn config_and_hotkey_commands_persist() {
        let host = Arc::new(RecordingHost::default());
        let (engine, dir) = make_engine(host);

        engine.execute(Cmd::SetEdge(Edge::Left));
        engine.execute(Cmd::SetClipboardEnabled(false));
        engine.execute(Cmd::SetHotkey(Hotkey {
            modifiers: Modifiers::META,
            usage: crate::hid::usage::K,
        }));
        engine.execute(Cmd::SetDeviceName("TestBook".into()));

        // Wait for the command loop to drain.
        for _ in 0..50 {
            tokio::time::sleep(Duration::from_millis(20)).await;
            let cfg = engine.cfg.lock();
            if cfg.edge == Edge::Left
                && !cfg.clipboard_enabled
                && cfg.device_name == "TestBook"
                && cfg.hotkey.usage == crate::hid::usage::K
            {
                break;
            }
        }

        let (reloaded, _) = Config::load(dir.path()).unwrap();
        assert_eq!(reloaded.edge, Edge::Left);
        assert!(!reloaded.clipboard_enabled);
        assert_eq!(reloaded.device_name, "TestBook");
        assert_eq!(reloaded.hotkey.usage, crate::hid::usage::K);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn local_clipboard_without_peer_is_safe() {
        let (engine, _dir) = make_engine(Arc::new(NullHost));
        engine.execute(Cmd::SetClipboardText("hello".into()));
        tokio::time::sleep(Duration::from_millis(100)).await;
        // No panic, no session required.
        assert!(!engine.has_session());
    }

    #[test]
    fn pairing_qr_contains_link() {
        let (engine, _dir) = make_engine(Arc::new(NullHost));
        engine.ensure_pairing_code();
        let link = engine.invite_link();
        assert!(link.starts_with("flowlink://connect?"));
        let parsed = qr::ConnectLink::parse(&link).unwrap();
        assert_eq!(parsed.port, engine.cfg.lock().listen_port);
        assert_eq!(
            parsed.fp.as_deref(),
            Some(engine.identity.fingerprint.as_str())
        );
        assert!(parsed.code.is_some());
        let matrix = engine.pairing_qr().unwrap();
        assert!(matrix.size > 21);
    }

    #[test]
    fn server_pairing_verification_workflow() {
        let (engine, _dir) = make_engine(Arc::new(NullHost));
        engine.ensure_pairing_code();
        let code = engine.pairing.lock().as_ref().unwrap().code.clone();

        // Wrong attempt.
        match engine.verify_pair_code("000000") {
            PairVerdict::Wrong { attempts_left } => {
                assert_eq!(attempts_left, pairing::MAX_ATTEMPTS - 1)
            }
            other => panic!("unexpected verdict: {other:?}"),
        }
        // Correct attempt.
        assert_eq!(engine.verify_pair_code(&code), PairVerdict::Accepted);
        // Code is one-time: a second use fails.
        assert!(matches!(
            engine.verify_pair_code(&code),
            PairVerdict::Expired | PairVerdict::Wrong { .. }
        ));
    }
}
