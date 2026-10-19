//! The eframe/egui application: onboarding, dashboards, settings and the
//! glue between the engine, the platform threads and the UI.
//!
//! Threading model:
//!
//! * The engine pushes status/notices/clipboard/input callbacks from its own
//!   runtime threads; [`AppShared`] is the (lock-based) meeting point. UI state
//!   is never touched from there — only snapshots are stored and a repaint is
//!   requested.
//! * The hook thread ([`crate::platform::input`]) reads the small
//!   [`crate::platform::input::CaptureState`] snapshot to know whether input
//!   belongs to this machine or to the peer.
//! * The UI thread owns everything visual, the tray icon and the text edit
//!   buffers.

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use crossbeam_channel::{Receiver, Sender};
use eframe::egui;
use flowlink_core::engine::{Cmd, Engine};
use flowlink_core::host::Host;
use flowlink_core::types::{Edge, Hotkey, InputEvent, Mode, Modifiers};
use parking_lot::{Mutex, RwLock};
use serde::Deserialize;
use tracing::{info, warn};

use crate::platform::{autostart, clipboard, firewall, input};
use crate::theme::{self, Theme};
use crate::tray::{self, TrayCommand};

/// Name of the marker file that records "onboarding finished".
const ONBOARDED_MARKER: &str = ".onboarded";

/// Listen port used by preview/screenshot runs (kept away from the real 24800
/// so a running FlowLink instance never collides with a preview).
const PREVIEW_PORT: u16 = 24810;

/// How often the pairing QR code is regenerated while the server page is open.
const QR_REFRESH: Duration = Duration::from_secs(2);

/// How many notices are kept for the notice bar.
const NOTICE_HISTORY: usize = 20;

/// How long a non-error notice stays visible in the footer.
const NOTICE_LIFETIME: Duration = Duration::from_secs(20);

// ---------------------------------------------------------------------------
// Status mirror
// ---------------------------------------------------------------------------

/// Connection state, mirroring `flowlink_core::status::ConnState` (which is
/// serialization-only).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConnStateMirror {
    #[default]
    Off,
    Listening,
    Connecting,
    Handshake,
    AwaitingPair,
    Pairing,
    Connected,
    Reconnecting,
    Error,
}

/// This device's identity, as reported by the engine.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct LocalInfoMirror {
    pub id: String,
    pub name: String,
    pub fingerprint: String,
    pub addrs: Vec<String>,
    pub port: u16,
}

/// The connected peer.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct PeerMirror {
    pub id: String,
    pub name: String,
    pub version: Option<String>,
    pub fingerprint: Option<String>,
    pub addr: String,
    pub paired: bool,
    pub since_ms: u64,
}

/// Pairing state (server: code; client: code entry pending).
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct PairingMirror {
    pub code: Option<String>,
    pub attempts_left: u8,
    pub expires_in_ms: u64,
    pub awaiting_input: bool,
}

/// A device seen on the LAN.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct DeviceMirror {
    pub id: String,
    pub name: String,
    pub fingerprint: String,
    pub addr: Option<String>,
    pub port: u16,
    pub role: Option<Mode>,
    pub version: Option<String>,
    pub last_seen_ms: u64,
}

/// A device we have paired with before.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct PairedDeviceMirror {
    pub id: String,
    pub name: String,
    pub fingerprint: String,
    pub last_addr: Option<String>,
    pub last_seen_ms: u64,
}

/// Traffic/latency counters.
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct MetricsMirror {
    pub rtt_ms: f32,
    pub rx_bytes: u64,
    pub tx_bytes: u64,
    pub events_out: u64,
    pub events_in: u64,
    pub connected_since_ms: Option<u64>,
    pub uptime_ms: u64,
}

/// Parsed copy of the status JSON the engine pushes to
/// [`Host::on_status`](flowlink_core::host::Host::on_status).
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(default)]
pub struct StatusMirror {
    pub app: String,
    pub version: String,
    pub mode: Option<Mode>,
    pub state: ConnStateMirror,
    pub error: Option<String>,
    pub reconnect_in_ms: Option<u64>,
    pub local: LocalInfoMirror,
    pub peer: Option<PeerMirror>,
    pub pairing: Option<PairingMirror>,
    pub remote_active: bool,
    /// This side owns the shared cursor.
    pub focused: bool,
    /// This side's input is forwarded to the peer.
    pub forwarding: bool,
    pub remote_edge: Option<Edge>,
    pub edge: Option<Edge>,
    /// Effective edge facing the peer for this session (synced topology).
    pub peer_edge: Option<Edge>,
    pub clipboard_enabled: bool,
    pub hotkey: Hotkey,
    pub discovered: Vec<DeviceMirror>,
    pub paired_devices: Vec<PairedDeviceMirror>,
    pub metrics: MetricsMirror,
    pub listening: bool,
}

impl StatusMirror {
    fn is_server(&self) -> bool {
        self.mode == Some(Mode::Server)
    }

    fn is_running(&self) -> bool {
        self.state != ConnStateMirror::Off
    }

    fn edge(&self) -> Edge {
        self.edge.unwrap_or(Edge::Right)
    }
}

/// A message the engine sent through
/// [`Host::on_notice`](flowlink_core::host::Host::on_notice).
#[derive(Clone, Debug)]
pub struct Notice {
    pub kind: String,
    pub message: String,
    /// When the notice arrived (local clock, not part of the wire format).
    pub at: Instant,
}

/// Wire shape of a notice (`{"kind": "...", "message": "..."}`).
#[derive(Clone, Debug, Deserialize)]
struct NoticeWire {
    kind: String,
    message: String,
}

// ---------------------------------------------------------------------------
// Shared state
// ---------------------------------------------------------------------------

/// State shared between the engine threads, the platform threads and the UI
/// thread. Implements [`Host`].
pub struct AppShared {
    /// Set right after `Engine::new` returns (the engine needs the host, so it
    /// cannot exist before it).
    engine: OnceLock<Arc<Engine>>,
    status: RwLock<StatusMirror>,
    notices: Mutex<VecDeque<Notice>>,
    /// Snapshot consumed by the hook thread and the clipboard poller.
    capture: Arc<RwLock<input::CaptureState>>,
    /// Echo suppression for clipboard sync.
    fingerprint: Arc<clipboard::Fingerprint>,
    /// Wakes the UI; available once the window exists.
    ctx: Mutex<Option<egui::Context>>,
}

impl AppShared {
    fn new() -> Arc<Self> {
        Arc::new(AppShared {
            engine: OnceLock::new(),
            status: RwLock::new(StatusMirror::default()),
            notices: Mutex::new(VecDeque::new()),
            capture: Arc::new(RwLock::new(input::CaptureState::default())),
            fingerprint: clipboard::Fingerprint::new(),
            ctx: Mutex::new(None),
        })
    }

    fn set_engine(&self, engine: Arc<Engine>) {
        let _ = self.engine.set(engine);
    }

    fn capture_state(&self) -> Arc<RwLock<input::CaptureState>> {
        Arc::clone(&self.capture)
    }

    fn fingerprint(&self) -> Arc<clipboard::Fingerprint> {
        Arc::clone(&self.fingerprint)
    }

    fn set_ctx(&self, ctx: egui::Context) {
        *self.ctx.lock() = Some(ctx);
    }

    /// Ask the UI thread for a new frame (no-op before the window exists).
    pub fn request_repaint(&self) {
        if let Some(ctx) = self.ctx.lock().as_ref() {
            ctx.request_repaint();
        }
    }

    /// Restore and focus the main window.
    ///
    /// Safe to call from any thread (the tray posts it when the window is
    /// hidden and the UI thread may not be running frames yet).
    #[cfg_attr(not(windows), allow(dead_code))] // only the Windows tray calls this
    pub fn show_window(&self) {
        if let Some(ctx) = self.ctx.lock().as_ref() {
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(true));
            ctx.send_viewport_cmd(egui::ViewportCommand::Focus);
            ctx.request_repaint();
        }
    }

    fn snapshot_status(&self) -> StatusMirror {
        self.status.read().clone()
    }

    fn notices(&self) -> Vec<Notice> {
        self.notices.lock().iter().cloned().collect()
    }

    fn clear_notices(&self) {
        self.notices.lock().clear();
    }

    /// Store a status snapshot and propagate the parts the platform threads
    /// need (forwarding state, clipboard, hotkey).
    fn apply_status(&self, status: StatusMirror) {
        let forwarding = status.forwarding;
        let peer_edge = status.peer_edge;
        {
            let mut capture = self.capture.write();
            capture.forwarding = forwarding;
            capture.clipboard_enabled = status.clipboard_enabled;
            capture.remote_edge = status.remote_edge;
            capture.hotkey_mods = status.hotkey.modifiers;
            capture.hotkey_usage = status.hotkey.usage;
        }
        *self.status.write() = status;
        // The injector needs the facing edge even before the first `Enter`
        // (the server owns the cursor at session start, so it may never see
        // one before synthetic input arrives).
        input::set_peer_edge(peer_edge);
        // Idempotent: keeps the hook and the cursor in sync with every status,
        // even if a previous update was missed.
        input::update_forwarding(forwarding);
    }
}

impl Host for AppShared {
    fn on_status(&self, status_json: &str) {
        match serde_json::from_str::<StatusMirror>(status_json) {
            Ok(status) => {
                self.apply_status(status);
                self.request_repaint();
            }
            // The engine and this shell ship as one binary, so this can only
            // happen on a protocol bug; keep the previous snapshot.
            Err(err) => warn!("could not parse the status snapshot: {err}"),
        }
    }

    fn on_input(&self, event: &InputEvent) {
        input::inject(event);
        // Input flows at pointer rate; only state-changing events warrant a
        // repaint (`remote_active` changes also arrive through `on_status`).
        if matches!(event, InputEvent::Enter { .. } | InputEvent::ReleaseAll) {
            self.request_repaint();
        }
    }

    fn on_clipboard(&self, text: &str) {
        clipboard::set_from_peer(&self.fingerprint, text);
    }

    fn on_notice(&self, notice_json: &str) {
        let wire = serde_json::from_str::<NoticeWire>(notice_json).unwrap_or_else(|_| NoticeWire {
            kind: "info".to_string(),
            message: notice_json.to_string(),
        });
        let notice = Notice {
            kind: wire.kind,
            message: wire.message,
            at: Instant::now(),
        };
        let mut notices = self.notices.lock();
        notices.push_back(notice);
        while notices.len() > NOTICE_HISTORY {
            notices.pop_front();
        }
        drop(notices);
        self.request_repaint();
    }
}

// ---------------------------------------------------------------------------
// Application
// ---------------------------------------------------------------------------

/// Pages of the main window.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Page {
    Server,
    Client,
    Settings,
}

/// The hotkey being edited on the settings page.
#[derive(Clone, Copy, Debug, Default)]
struct HotkeyDraft {
    ctrl: bool,
    alt: bool,
    shift: bool,
    meta: bool,
    usage: Option<u16>,
    /// True while waiting for the user to press the key.
    listening: bool,
}

impl HotkeyDraft {
    fn from_hotkey(hotkey: Hotkey) -> Self {
        HotkeyDraft {
            ctrl: hotkey.modifiers & Modifiers::CTRL != 0,
            alt: hotkey.modifiers & Modifiers::ALT != 0,
            shift: hotkey.modifiers & Modifiers::SHIFT != 0,
            meta: hotkey.modifiers & Modifiers::META != 0,
            usage: Some(hotkey.usage),
            listening: false,
        }
    }

    fn modifiers(self) -> u8 {
        let mut bits = 0u8;
        if self.ctrl {
            bits |= Modifiers::CTRL;
        }
        if self.alt {
            bits |= Modifiers::ALT;
        }
        if self.shift {
            bits |= Modifiers::SHIFT;
        }
        if self.meta {
            bits |= Modifiers::META;
        }
        bits
    }

    /// A hotkey needs at least one modifier and a key.
    fn is_complete(self) -> bool {
        self.modifiers() != 0 && self.usage.is_some()
    }
}

/// Shared state of a running firewall elevation job (UI ↔ worker thread).
#[derive(Default)]
struct FirewallJob {
    busy: bool,
    /// `Some((ok, message))` once finished.
    result: Option<(bool, String)>,
}

/// The eframe application.
struct WinApp {
    shared: Arc<AppShared>,
    engine: Arc<Engine>,
    capture: Option<input::CaptureHandle>,
    tray: Option<tray::TrayHandle>,
    tray_rx: Receiver<TrayCommand>,
    config_dir: PathBuf,

    // --- window / navigation ---------------------------------------------
    page: Page,
    onboarding: bool,
    quitting: bool,
    cleaned_up: bool,
    /// Set when the window starts hidden but there is no tray to restore it.
    force_show: bool,
    dark_mode: bool,

    // --- onboarding -------------------------------------------------------
    pending_mode: Option<Mode>,
    device_name_edit: String,

    // --- server / client --------------------------------------------------
    qr: Option<flowlink_core::qr::QrMatrix>,
    qr_at: Option<Instant>,
    qr_code: Option<String>,
    manual_addr: String,
    link_edit: String,
    pair_code_edit: String,

    // --- settings ---------------------------------------------------------
    port_edit: String,
    hotkey_draft: HotkeyDraft,
    autostart_state: bool,

    /// Short-lived confirmation shown in the footer ("Saved").
    flash: Option<(String, Instant)>,
    /// Last values pushed into the tray menu (avoid per-frame churn).
    last_tray_state: Option<(bool, bool)>,

    /// Cached Windows Firewall state (checked lazily on the server page).
    firewall_state: Option<firewall::RuleState>,
    /// Progress of a running "allow through firewall" elevation job.
    firewall_job: Arc<Mutex<FirewallJob>>,

    // --- preview / screenshot mode (cross-platform design QA) -------------
    /// When set, the app keeps repainting, captures one frame and quits.
    screenshot: Option<PathBuf>,
    screenshot_requested: bool,
    screenshot_started_at: Option<Instant>,
}

impl WinApp {
    #[allow(clippy::too_many_arguments)]
    fn new(
        cc: &eframe::CreationContext<'_>,
        shared: Arc<AppShared>,
        engine: Arc<Engine>,
        capture: input::CaptureHandle,
        tray_rx: Receiver<TrayCommand>,
        tray_tx: Sender<TrayCommand>,
        config_dir: PathBuf,
        onboarded: bool,
        start_hidden: bool,
        initial_page: Option<Page>,
        screenshot: Option<PathBuf>,
        preview_theme: Option<bool>,
    ) -> Result<Self> {
        // Deterministic theme: an explicit preview theme wins, otherwise
        // follow egui's current (system) mode.
        let dark = preview_theme.unwrap_or_else(|| cc.egui_ctx.style().visuals.dark_mode);
        theme::apply(&cc.egui_ctx, dark);
        shared.set_ctx(cc.egui_ctx.clone());

        // The first status push has not happened yet, so the initial UI state
        // comes straight from the engine's configuration (the same values the
        // status will report moments later).
        let (config_mode, config_port, config_name, config_hotkey) = {
            let cfg = engine.cfg.lock();
            (
                cfg.mode,
                cfg.listen_port,
                cfg.device_name.clone(),
                cfg.hotkey,
            )
        };
        let mode_is_server = config_mode == Mode::Server;
        let tray = match tray::install(&shared, tray_tx, mode_is_server, false) {
            Ok(tray) => Some(tray),
            Err(err) => {
                warn!("system tray unavailable: {err:#}");
                None
            }
        };
        let has_tray = tray.is_some();

        let page = initial_page.unwrap_or(if mode_is_server {
            Page::Server
        } else {
            Page::Client
        });

        Ok(WinApp {
            shared,
            engine,
            capture: Some(capture),
            tray,
            tray_rx,
            config_dir,
            page,
            onboarding: !onboarded,
            quitting: false,
            cleaned_up: false,
            force_show: start_hidden && !has_tray,
            dark_mode: dark,
            pending_mode: if onboarded { None } else { Some(config_mode) },
            device_name_edit: config_name,
            qr: None,
            qr_at: None,
            qr_code: None,
            manual_addr: String::new(),
            link_edit: String::new(),
            pair_code_edit: String::new(),
            port_edit: config_port.to_string(),
            hotkey_draft: HotkeyDraft::from_hotkey(config_hotkey),
            autostart_state: autostart::is_enabled(),
            flash: None,
            last_tray_state: None,
            firewall_state: None,
            firewall_job: Arc::new(Mutex::new(FirewallJob::default())),
            screenshot,
            screenshot_requested: false,
            screenshot_started_at: None,
        })
    }

    // ---------------------------------------------------------------------
    // Small helpers
    // ---------------------------------------------------------------------

    fn flash(&mut self, message: impl Into<String>) {
        self.flash = Some((message.into(), Instant::now()));
    }

    fn marker_path(&self) -> PathBuf {
        self.config_dir.join(ONBOARDED_MARKER)
    }

    /// Tell the core about the real screen geometry, then start.
    ///
    /// `ScreenInfo` defaults to 1920×1080, so this must happen before (or at
    /// the same time as) every start for the edge math to line up.
    fn start_engine(&self) {
        self.engine
            .execute(Cmd::SetLocalScreen(input::local_screen()));
        self.engine.execute(Cmd::Start);
        // Defensive: the hooks are always installed, but "Stop" pauses them.
        input::set_capture_enabled(true);
    }

    /// Full shutdown: engine, hooks, clipboard poller, tray.
    fn cleanup(&mut self) {
        if self.cleaned_up {
            return;
        }
        self.cleaned_up = true;
        info!("shutting down");
        self.engine.execute(Cmd::Shutdown);
        input::shutdown();
        clipboard::stop();
        if let Some(capture) = self.capture.take() {
            capture.stop();
        }
    }

    fn quit(&mut self, ctx: &egui::Context) {
        self.quitting = true;
        self.cleanup();
        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        ctx.request_repaint();
    }

    fn show_window(&self, ctx: &egui::Context) {
        ctx.send_viewport_cmd(egui::ViewportCommand::Visible(true));
        ctx.send_viewport_cmd(egui::ViewportCommand::Focus);
        ctx.request_repaint();
    }

    /// Drain tray commands (posted from the tray's event thread).
    fn pump_tray(&mut self, ctx: &egui::Context) {
        while let Ok(command) = self.tray_rx.try_recv() {
            match command {
                TrayCommand::Show => self.show_window(ctx),
                TrayCommand::ModeServer | TrayCommand::ModeClient => {
                    let mode = if command == TrayCommand::ModeServer {
                        Mode::Server
                    } else {
                        Mode::Client
                    };
                    self.engine.execute(Cmd::SetMode(mode));
                    self.start_engine();
                    self.page = if mode == Mode::Server {
                        Page::Server
                    } else {
                        Page::Client
                    };
                    if let Some(tray) = &self.tray {
                        tray.set_mode(mode == Mode::Server);
                        tray.set_running(true);
                    }
                }
                TrayCommand::Start => {
                    self.start_engine();
                    if let Some(tray) = &self.tray {
                        tray.set_running(true);
                    }
                }
                TrayCommand::Stop => {
                    self.engine.execute(Cmd::Stop);
                    input::set_capture_enabled(false);
                    if let Some(tray) = &self.tray {
                        tray.set_running(false);
                    }
                }
                TrayCommand::Quit => {
                    self.quit(ctx);
                    return;
                }
            }
        }
    }

    /// Closing the window hides it in the tray; only "Quit" really exits.
    fn handle_close_request(&mut self, ctx: &egui::Context) {
        if !ctx.input(|i| i.viewport().close_requested()) {
            return;
        }
        if self.quitting || self.tray.is_none() {
            // Nothing to hide into — let the app end, but clean up first.
            self.cleanup();
            return;
        }
        ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
        ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
    }

    /// Regenerate the pairing QR when the code changed or the cache is stale.
    fn refresh_qr(&mut self, status: &StatusMirror) {
        let visible = !self.onboarding && self.page == Page::Server && status.is_server();
        if !visible {
            return;
        }
        let code = status
            .pairing
            .as_ref()
            .and_then(|pairing| pairing.code.clone());
        let due = self.qr_at.map_or(true, |at| at.elapsed() >= QR_REFRESH);
        if !due && code == self.qr_code {
            return;
        }
        self.qr = self.engine.pairing_qr().ok();
        self.qr_code = code;
        self.qr_at = Some(Instant::now());
    }

    /// Keep the edit buffers aligned with the engine (unless the user is
    /// mid-edit: the buffers are only filled while they are untouched).
    fn sync_edit_buffers(&mut self, status: &StatusMirror) {
        if self.device_name_edit.is_empty() && !status.local.name.is_empty() {
            self.device_name_edit = status.local.name.clone();
        }
        if self.port_edit.is_empty() && status.local.port != 0 {
            self.port_edit = status.local.port.to_string();
        }
    }

    /// Capture the next key press while the hotkey recorder is armed.
    fn handle_hotkey_recorder(&mut self, ctx: &egui::Context) {
        if !self.hotkey_draft.listening {
            return;
        }
        let pressed = ctx.input(|input| {
            input.events.iter().find_map(|event| match event {
                egui::Event::Key {
                    key, pressed: true, ..
                } => Some(*key),
                _ => None,
            })
        });
        if let Some(key) = pressed {
            if let Some(usage) = egui_key_to_hid(key) {
                self.hotkey_draft.usage = Some(usage);
                self.hotkey_draft.listening = false;
            }
        }
    }

    // ---------------------------------------------------------------------
    // Header / footer
    // ---------------------------------------------------------------------

    fn header(&mut self, ui: &mut egui::Ui, theme: &Theme, status: &StatusMirror) {
        ui.horizontal(|ui| {
            brand_glyph(ui, theme);
            ui.add_space(2.0);
            ui.label(
                egui::RichText::new("FlowLink")
                    .size(17.0)
                    .strong()
                    .color(theme.text),
            );
            ui.label(
                egui::RichText::new(format!("v{}", status.version))
                    .size(10.5)
                    .color(theme.text_dim),
            );
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                self.state_pill(ui, theme, status);
            });
        });

        if self.onboarding {
            return;
        }

        ui.add_space(8.0);
        egui::Frame::default()
            .fill(theme.card)
            .stroke(egui::Stroke::new(1.0_f32, theme.border))
            .corner_radius(egui::CornerRadius::same(9))
            .inner_margin(egui::Margin::symmetric(3, 3))
            .show(ui, |ui| {
                ui.spacing_mut().item_spacing.x = 2.0;
                ui.horizontal(|ui| {
                    for (page, label) in [
                        (Page::Server, "Server"),
                        (Page::Client, "Client"),
                        (Page::Settings, "Settings"),
                    ] {
                        if tab_button(ui, theme, self.page == page, label).clicked() {
                            self.page = page;
                        }
                    }
                });
            });
    }

    fn state_pill(&self, ui: &mut egui::Ui, theme: &Theme, status: &StatusMirror) {
        let color = state_color(theme, status.state);
        let label = match status.state {
            ConnStateMirror::Reconnecting => match status.reconnect_in_ms {
                Some(ms) => format!("Reconnecting in {}s", ms / 1000 + 1),
                None => "Reconnecting…".to_string(),
            },
            ConnStateMirror::Connected => match &status.peer {
                Some(peer) => format!("Connected to {}", peer.name),
                None => "Connected".to_string(),
            },
            other => state_label(other).to_string(),
        };
        egui::Frame::default()
            .fill(color.gamma_multiply(0.16))
            .corner_radius(egui::CornerRadius::same(9))
            .inner_margin(egui::Margin::symmetric(10, 4))
            .show(ui, |ui| {
                ui.horizontal(|ui| {
                    let (rect, _) =
                        ui.allocate_exact_size(egui::vec2(8.0, 8.0), egui::Sense::hover());
                    ui.painter().rect_filled(rect, 4.0_f32, color);
                    ui.label(egui::RichText::new(label).size(12.0).color(color));
                });
            });
    }

    fn footer(&mut self, ui: &mut egui::Ui, theme: &Theme) {
        let notices = self.shared.notices();
        let flash = self
            .flash
            .as_ref()
            .map(|(message, at)| (message.clone(), *at));
        if let Some((message, at)) = flash {
            if at.elapsed() < Duration::from_secs(4) {
                ui.colored_label(theme.accent, message);
                return;
            }
            self.flash = None;
        }
        // Transient notices fade after a while; errors stay until dismissed.
        let latest = notices
            .last()
            .filter(|notice| notice.kind == "error" || notice.at.elapsed() < NOTICE_LIFETIME);
        let Some(latest) = latest else {
            return;
        };
        ui.horizontal(|ui| {
            ui.colored_label(
                notice_color(theme, &latest.kind),
                format!("{}: {}", latest.kind, latest.message),
            );
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.small_button("Dismiss").clicked() {
                    self.shared.clear_notices();
                }
            });
        });
    }

    // ---------------------------------------------------------------------
    // Onboarding
    // ---------------------------------------------------------------------

    fn onboarding_page(&mut self, ui: &mut egui::Ui, theme: &Theme) {
        ui.add_space(6.0);
        ui.label(
            egui::RichText::new("Welcome to FlowLink")
                .size(24.0)
                .strong(),
        );
        ui.label(
            egui::RichText::new(
                "Share one keyboard, mouse and clipboard between this PC and your Mac.",
            )
            .color(theme.text_dim),
        );
        ui.add_space(10.0);

        card_title(ui, theme, "What role does this computer play?");
        ui.add_space(4.0);

        let card_width = (ui.available_width() - 10.0) / 2.0;
        let card_height = 128.0;
        let mut chosen = self.pending_mode;
        ui.horizontal(|ui| {
            if mode_card(
                ui,
                theme,
                card_width,
                card_height,
                "🖥",
                "This computer drives",
                "Server — its keyboard and mouse move to the Mac when the pointer crosses the screen edge.",
                self.pending_mode == Some(Mode::Server),
            )
            .clicked()
            {
                chosen = Some(Mode::Server);
            }
            if mode_card(
                ui,
                theme,
                card_width,
                card_height,
                "💻",
                "This computer is driven",
                "Client — the Mac takes over this screen; connect to it afterwards.",
                self.pending_mode == Some(Mode::Client),
            )
            .clicked()
            {
                chosen = Some(Mode::Client);
            }
        });
        self.pending_mode = chosen;

        ui.add_space(12.0);
        card(ui, theme, |ui| {
            card_title(ui, theme, "Computer name");
            ui.label(
                egui::RichText::new("How this computer appears on the other machine.")
                    .size(11.0)
                    .color(theme.text_dim),
            );
            ui.add_space(4.0);
            ui.add(
                egui::TextEdit::singleline(&mut self.device_name_edit)
                    .hint_text("e.g. Office PC")
                    .desired_width(f32::INFINITY),
            );
        });

        ui.add_space(10.0);
        let ready = self.pending_mode.is_some() && !self.device_name_edit.trim().is_empty();
        ui.add_enabled_ui(ready, |ui| {
            let button = egui::Button::new(
                egui::RichText::new("Start")
                    .strong()
                    .color(egui::Color32::WHITE),
            )
            .fill(theme.accent)
            .corner_radius(egui::CornerRadius::same(10))
            .min_size(egui::vec2(ui.available_width(), 34.0));
            if ui.add(button).clicked() {
                self.finish_onboarding();
            }
        });
        if !ready {
            ui.label(
                egui::RichText::new("Pick a role and give this computer a name to continue.")
                    .size(11.0)
                    .color(theme.text_dim),
            );
        }
    }

    fn finish_onboarding(&mut self) {
        let Some(mode) = self.pending_mode else {
            return;
        };
        let name = self.device_name_edit.trim().to_string();
        if !name.is_empty() {
            self.engine.execute(Cmd::SetDeviceName(name));
        }
        self.engine.execute(Cmd::SetMode(mode));
        self.start_engine();
        // Remember that onboarding is done (a plain marker file; the core's
        // config has no onboarding flag of its own).
        if let Err(err) = std::fs::write(self.marker_path(), b"1") {
            warn!("could not write {}: {err}", self.marker_path().display());
        }
        self.onboarding = false;
        self.page = if mode == Mode::Server {
            Page::Server
        } else {
            Page::Client
        };
        if let Some(tray) = &self.tray {
            tray.set_mode(mode == Mode::Server);
            tray.set_running(true);
        }
        self.flash("Ready — FlowLink is now running.");
    }

    // ---------------------------------------------------------------------
    // Server dashboard
    // ---------------------------------------------------------------------

    fn server_page(&mut self, ui: &mut egui::Ui, theme: &Theme, status: &StatusMirror) {
        // --- engine state banners -----------------------------------------
        if let Some(error) = &status.error {
            egui::Frame::default()
                .fill(theme.err.gamma_multiply(0.12))
                .stroke(egui::Stroke::new(1.0_f32, theme.err.gamma_multiply(0.5)))
                .corner_radius(egui::CornerRadius::same(theme::CARD_RADIUS))
                .inner_margin(egui::Margin::same(12))
                .show(ui, |ui| {
                    ui.label(
                        egui::RichText::new("FlowLink could not start listening")
                            .strong()
                            .color(theme.err),
                    );
                    ui.label(
                        egui::RichText::new(error)
                            .size(11.0)
                            .color(theme.text_dim),
                    );
                    ui.label(
                        egui::RichText::new(
                            "If the port is already in use, quit any other FlowLink copy (tray → Quit) and press Start.",
                        )
                        .size(10.5)
                        .color(theme.text_dim),
                    );
                    if ui.button("Start again").clicked() {
                        self.engine.execute(Cmd::Start);
                    }
                });
            ui.add_space(10.0);
        } else if !status.is_running() {
            card(ui, theme, |ui| {
                card_title(ui, theme, "Engine stopped");
                ui.label(
                    egui::RichText::new(
                        "FlowLink is not running, so no pairing code is shown yet.",
                    )
                    .size(11.0)
                    .color(theme.text_dim),
                );
                if ui.button("Start").clicked() {
                    self.start_engine();
                }
            });
            ui.add_space(10.0);
        }

        // --- pairing ------------------------------------------------------
        card(ui, theme, |ui| {
            card_title(ui, theme, "Pairing");
            let code = status
                .pairing
                .as_ref()
                .and_then(|pairing| pairing.code.as_deref());
            match code {
                Some(code) => {
                    ui.horizontal(|ui| {
                        ui.label(
                            egui::RichText::new(format_code(code))
                                .monospace()
                                .size(30.0)
                                .color(theme.accent),
                        );
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            if ui.button("New code").clicked() {
                                self.engine.execute(Cmd::RegeneratePairCode);
                                self.qr_at = None;
                            }
                        });
                    });
                    let mut details = Vec::new();
                    if let Some(pairing) = &status.pairing {
                        if pairing.expires_in_ms > 0 {
                            details
                                .push(format!("expires in {}s", pairing.expires_in_ms / 1000 + 1));
                        }
                        details.push(format!("{} attempts left", pairing.attempts_left));
                    }
                    if !details.is_empty() {
                        ui.label(
                            egui::RichText::new(details.join(" · "))
                                .size(11.0)
                                .color(theme.text_dim),
                        );
                    }
                }
                None => {
                    ui.label(egui::RichText::new("No pairing code yet.").color(theme.text_dim));
                    if ui.button("Generate code").clicked() {
                        self.engine.execute(Cmd::RegeneratePairCode);
                        self.qr_at = None;
                    }
                }
            }

            if let Some(qr) = &self.qr {
                ui.add_space(6.0);
                draw_qr(ui, qr);
            }

            ui.add_space(6.0);
            ui.label(
                egui::RichText::new("Invite link (scan the QR or paste this on the Mac)")
                    .size(11.0)
                    .color(theme.text_dim),
            );
            ui.horizontal(|ui| {
                let link = self.engine.invite_link();
                let text = egui::RichText::new(&link)
                    .size(10.5)
                    .monospace()
                    .color(theme.text_dim);
                ui.add(egui::Label::new(text).truncate());
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if ui.small_button("Copy").clicked() {
                        ui.ctx().copy_text(link);
                        self.flash("Invite link copied");
                    }
                });
            });
        });

        ui.add_space(10.0);

        self.network_card(ui, theme, status);

        // --- edge ---------------------------------------------------------
        card(ui, theme, |ui| {
            card_title(ui, theme, "Screen edge");
            ui.label(
                egui::RichText::new(
                    "The edge of this screen that faces the other computer — crossing it moves the shared cursor there. The setting is mirrored on the other computer automatically (0.3+).",
                )
                .size(11.0)
                .color(theme.text_dim),
            );
            ui.add_space(6.0);
            let current = status.edge();
            ui.horizontal(|ui| {
                for edge in [Edge::Left, Edge::Right, Edge::Top, Edge::Bottom] {
                    let selected = current == edge;
                    if tab_button(ui, theme, selected, edge_title(edge)).clicked() && !selected {
                        self.engine.execute(Cmd::SetEdge(edge));
                    }
                }
            });
        });

        ui.add_space(10.0);

        // --- peer / control ----------------------------------------------
        card(ui, theme, |ui| {
            card_title(ui, theme, "Connected device");
            match &status.peer {
                Some(peer) => {
                    ui.horizontal(|ui| {
                        ui.label(egui::RichText::new(&peer.name).size(15.0).strong());
                        if let Some(version) = &peer.version {
                            ui.label(
                                egui::RichText::new(format!("v{version}"))
                                    .size(10.5)
                                    .color(theme.text_dim),
                            );
                        }
                        ui.label(
                            egui::RichText::new(if peer.paired { "paired" } else { "unverified" })
                                .size(11.0)
                                .color(if peer.paired { theme.ok } else { theme.warn }),
                        );
                    });
                    ui.label(
                        egui::RichText::new(format!(
                            "{} · RTT {:.1} ms · since {}",
                            peer.addr,
                            status.metrics.rtt_ms,
                            human_duration(
                                flowlink_core::util::now_ms().saturating_sub(peer.since_ms)
                            )
                        ))
                        .size(11.0)
                        .color(theme.text_dim),
                    );
                }
                None => {
                    let message = if status.listening {
                        "Listening for a client on the local network…"
                    } else {
                        "Not connected."
                    };
                    ui.label(egui::RichText::new(message).color(theme.text_dim));
                }
            }

            ui.add_space(6.0);
            let mut focused = status.focused;
            if ui
                .checkbox(&mut focused, "The cursor is on this computer")
                .changed()
            {
                self.toggle_remote_control();
            }
            let hint = if status.focused {
                format!(
                    "The cursor is here — push it through the {} edge or press {} to send it to the other computer",
                    edge_title(status.edge()),
                    hotkey_label(status.hotkey)
                )
            } else {
                format!(
                    "The cursor is on the other computer — press {} to bring it back here",
                    hotkey_label(status.hotkey)
                )
            };
            ui.label(egui::RichText::new(hint).size(11.0).color(theme.text_dim));
        });

        ui.add_space(10.0);
        metrics_card(ui, theme, status);
    }

    // ---------------------------------------------------------------------
    // Client dashboard
    // ---------------------------------------------------------------------

    /// The "Network" card on the server page: manual address + Windows
    /// Firewall status and remediation. Inbound connections are the usual
    /// failure point on Windows, so this is deliberately prominent.
    fn network_card(&mut self, ui: &mut egui::Ui, theme: &Theme, status: &StatusMirror) {
        card(ui, theme, |ui| {
            card_title(ui, theme, "Network");
            let addr = status
                .local
                .addrs
                .first()
                .cloned()
                .unwrap_or_else(|| "—".to_string());
            let port = status.local.port;
            let address = format!("{addr}:{port}");
            ui.horizontal(|ui| {
                ui.label(
                    egui::RichText::new("Manual connect address")
                        .size(11.0)
                        .color(theme.text_dim),
                );
                ui.add(
                    egui::Label::new(
                        egui::RichText::new(&address)
                            .monospace()
                            .size(12.5)
                            .color(theme.text),
                    )
                    .truncate(),
                );
                if ui.small_button("Copy").clicked() {
                    ui.ctx().copy_text(address.clone());
                    self.flash("Address copied");
                }
            });
            ui.add_space(6.0);

            // Lazy check: `netsh` costs ~100 ms, so only once (and after the
            // user asks for a re-check).
            if self.firewall_state.is_none() {
                self.firewall_state = Some(firewall::check(port as u16));
            }

            // Poll a running elevation job.
            let finished = {
                let mut job = self.firewall_job.lock();
                match job.result.take() {
                    Some(result) => {
                        job.busy = false;
                        Some(result)
                    }
                    None => None,
                }
            };
            if let Some((ok, message)) = finished {
                if ok {
                    self.firewall_state = Some(firewall::RuleState::Present);
                }
                self.flash(message);
            }
            let busy = self.firewall_job.lock().busy;

            match self.firewall_state {
                Some(firewall::RuleState::Present) => {
                    ui.horizontal(|ui| {
                        ui.label(egui::RichText::new("✓").color(theme.ok).strong());
                        ui.label(
                            egui::RichText::new("Windows Firewall allows FlowLink")
                                .size(11.0)
                                .color(theme.text_dim),
                        );
                        if ui.small_button("Re-check").clicked() {
                            self.firewall_state = None;
                        }
                    });
                }
                Some(firewall::RuleState::Missing) => {
                    ui.label(
                        egui::RichText::new(
                            "Windows Firewall is probably blocking connections from the other computer.",
                        )
                        .size(11.0)
                        .color(theme.warn),
                    );
                    ui.add_space(4.0);
                    ui.horizontal_wrapped(|ui| {
                        let allow = egui::Button::new(
                            egui::RichText::new("Allow through firewall…")
                                .strong()
                                .color(egui::Color32::WHITE),
                        )
                        .fill(theme.accent)
                        .corner_radius(egui::CornerRadius::same(8));
                        if ui.add_enabled(!busy, allow).clicked() {
                            self.start_firewall_job(port as u16, ui.ctx().clone());
                        }
                        if busy {
                            ui.spinner();
                            ui.label(
                                egui::RichText::new("waiting for the admin prompt…")
                                    .size(10.5)
                                    .color(theme.text_dim),
                            );
                        }
                        if ui.button("Firewall settings").clicked() {
                            firewall::open_settings();
                        }
                    });
                    if let Ok(exe) = std::env::current_exe() {
                        let commands = firewall::commands(port as u16, &exe);
                        if let Some(first) = commands.first() {
                            ui.horizontal(|ui| {
                                ui.add(
                                    egui::Label::new(
                                        egui::RichText::new(first)
                                            .monospace()
                                            .size(9.5)
                                            .color(theme.text_dim),
                                    )
                                    .truncate(),
                                )
                                .on_hover_text(commands.join("\n"));
                                if ui.small_button("Copy commands").clicked() {
                                    ui.ctx().copy_text(commands.join("\n"));
                                    self.flash("Commands copied — run them in an admin terminal");
                                }
                            });
                        }
                    }
                }
                _ => {
                    ui.label(
                        egui::RichText::new(format!(
                            "If the other computer cannot connect, allow inbound TCP {port} and UDP 5353 in Windows Firewall.",
                        ))
                        .size(11.0)
                        .color(theme.text_dim),
                    );
                    if ui.button("Open firewall settings").clicked() {
                        firewall::open_settings();
                    }
                }
            }
        });
        ui.add_space(10.0);
    }

    /// Run the elevation prompt off the UI thread; the result is picked up by
    /// [`Self::network_card`] on the next frame.
    fn start_firewall_job(&self, port: u16, ctx: egui::Context) {
        {
            let mut job = self.firewall_job.lock();
            if job.busy {
                return;
            }
            job.busy = true;
            job.result = None;
        }
        let exe = match std::env::current_exe() {
            Ok(exe) => exe,
            Err(err) => {
                let mut job = self.firewall_job.lock();
                job.busy = false;
                job.result = Some((false, format!("Cannot locate the executable: {err}")));
                return;
            }
        };
        let job = Arc::clone(&self.firewall_job);
        let spawned = std::thread::Builder::new()
            .name("flowlink-firewall".to_string())
            .spawn(move || {
                let outcome = firewall::allow_elevated(port, &exe);
                let mut guard = job.lock();
                guard.result = Some(match outcome {
                    Ok(()) => (
                        true,
                        "Firewall rules added — the other computer can connect now".to_string(),
                    ),
                    Err(err) => (false, format!("Firewall change failed: {err}")),
                });
                guard.busy = false;
                ctx.request_repaint();
            });
        if spawned.is_err() {
            let mut job = self.firewall_job.lock();
            job.busy = false;
            job.result = Some((false, "Could not start the firewall worker".to_string()));
        }
    }

    /// Client dashboard: connection state, discovered servers, manual entry.
    fn client_page(&mut self, ui: &mut egui::Ui, theme: &Theme, status: &StatusMirror) {
        // --- connection state --------------------------------------------
        card(ui, theme, |ui| {
            card_title(ui, theme, "Connection");
            match &status.peer {
                Some(peer) if status.state == ConnStateMirror::Connected => {
                    ui.horizontal(|ui| {
                        ui.label(egui::RichText::new(&peer.name).size(15.0).strong());
                        if let Some(version) = &peer.version {
                            ui.label(
                                egui::RichText::new(format!("v{version}"))
                                    .size(10.5)
                                    .color(theme.text_dim),
                            );
                        }
                        ui.label(
                            egui::RichText::new(peer.addr.clone())
                                .size(11.0)
                                .color(theme.text_dim),
                        );
                    });
                }
                _ => {
                    ui.label(
                        egui::RichText::new(state_label(status.state))
                            .color(state_color(theme, status.state)),
                    );
                }
            }
            if let Some(error) = &status.error {
                ui.label(egui::RichText::new(error).size(11.0).color(theme.err));
            }
            if let Some(ms) = status.reconnect_in_ms {
                ui.label(
                    egui::RichText::new(format!("Retrying in {}s", ms / 1000 + 1))
                        .size(11.0)
                        .color(theme.text_dim),
                );
            }
            if matches!(
                status.state,
                ConnStateMirror::Reconnecting | ConnStateMirror::Error
            ) {
                ui.add_space(4.0);
                ui.label(
                    egui::RichText::new(
                        "No connection yet. Both computers must be on the same network, and the server must allow inbound TCP 24800 — on Windows use “Allow through firewall…” on the server page (one admin prompt).",
                    )
                    .size(10.5)
                    .color(theme.text_dim),
                );
            }
            if status.forwarding {
                ui.add_space(4.0);
                ui.label(
                    egui::RichText::new(format!(
                        "The cursor is on the other computer — your keyboard and mouse drive it there. Push the cursor back across the edge facing this screen or press {} to bring it here.",
                        hotkey_label(status.hotkey)
                    ))
                    .size(11.0)
                    .color(theme.text_dim),
                );
                if ui.button("Bring the cursor here").clicked() {
                    self.engine.execute(Cmd::ToggleControl);
                }
            }
            if status.is_running() && ui.button("Disconnect").clicked() {
                self.engine.execute(Cmd::Disconnect);
            }
        });

        ui.add_space(10.0);

        // --- pairing code entry ------------------------------------------
        let awaiting = status
            .pairing
            .as_ref()
            .map(|pairing| pairing.awaiting_input)
            .unwrap_or(false)
            || status.state == ConnStateMirror::AwaitingPair;
        if awaiting {
            card(ui, theme, |ui| {
                card_title(ui, theme, "Enter the pairing code");
                ui.label(
                    egui::RichText::new("The code is shown on the server's screen.")
                        .size(11.0)
                        .color(theme.text_dim),
                );
                ui.horizontal(|ui| {
                    let entry = ui.add(
                        egui::TextEdit::singleline(&mut self.pair_code_edit)
                            .hint_text("123456")
                            .desired_width(110.0),
                    );
                    // Enter submits too — typing 6 digits and hitting Enter is
                    // the natural flow.
                    let submitted =
                        entry.lost_focus() && ui.input(|input| input.key_pressed(egui::Key::Enter));
                    if ui.button("Pair").clicked() || submitted {
                        let code = self.pair_code_edit.trim().to_string();
                        if !code.is_empty() {
                            self.engine.execute(Cmd::SubmitPairCode(code));
                            self.pair_code_edit.clear();
                        }
                    }
                });
            });
            ui.add_space(10.0);
        }

        // --- discovered servers ------------------------------------------
        card(ui, theme, |ui| {
            card_title(ui, theme, "Servers on this network");
            let servers: Vec<&DeviceMirror> = status
                .discovered
                .iter()
                .filter(|device| matches!(device.role, Some(Mode::Server) | None))
                .collect();
            if servers.is_empty() {
                ui.label(
                    egui::RichText::new("Searching… make sure the server is running.")
                        .size(11.0)
                        .color(theme.text_dim),
                );
                ui.label(
                    egui::RichText::new(
                        "Discovery can be blocked by Windows Firewall or by “client isolation” on the router — the address field below always works.",
                    )
                    .size(10.5)
                    .color(theme.text_dim),
                );
            }
            for device in servers {
                ui.horizontal(|ui| {
                    ui.vertical(|ui| {
                        ui.label(egui::RichText::new(&device.name).strong());
                        let addr = device
                            .addr
                            .clone()
                            .unwrap_or_else(|| "unknown address".to_string());
                        let version = device.version.clone().unwrap_or_else(|| "?".to_string());
                        ui.label(
                            egui::RichText::new(format!("{addr} · v{version}"))
                                .size(11.0)
                                .color(theme.text_dim),
                        );
                    });
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if ui.button("Connect").clicked() {
                            self.connect_to(
                                device.addr.as_deref(),
                                device.port,
                                Some(device.fingerprint.as_str()),
                            );
                        }
                    });
                });
            }
        });

        ui.add_space(10.0);

        // --- manual address / link ---------------------------------------
        card(ui, theme, |ui| {
            card_title(ui, theme, "Connect manually");
            ui.horizontal(|ui| {
                let field_width = (ui.available_width() - 104.0).max(120.0);
                ui.add(
                    egui::TextEdit::singleline(&mut self.manual_addr)
                        .hint_text("192.168.1.42[:24800]")
                        .desired_width(field_width),
                );
                if ui.button("Connect").clicked() {
                    match parse_host_port(&self.manual_addr) {
                        Some((host, port)) => {
                            self.engine.execute(Cmd::Connect {
                                host,
                                port,
                                code: None,
                                fingerprint: None,
                            });
                            self.flash("Connecting…");
                        }
                        None => self.flash("Enter an address like 192.168.1.42:24800"),
                    }
                }
            });
            ui.add_space(6.0);
            ui.label(
                egui::RichText::new("Or paste a flowlink:// invite link")
                    .size(11.0)
                    .color(theme.text_dim),
            );
            ui.horizontal(|ui| {
                let field_width = (ui.available_width() - 80.0).max(120.0);
                ui.add(
                    egui::TextEdit::singleline(&mut self.link_edit)
                        .hint_text("flowlink://connect?h=…")
                        .desired_width(field_width),
                );
                if ui.button("Open").clicked() {
                    let link = self.link_edit.trim().to_string();
                    if !link.is_empty() {
                        self.engine.execute(Cmd::ConnectLink(link));
                        self.link_edit.clear();
                        self.flash("Connecting…");
                    }
                }
            });
        });

        ui.add_space(10.0);

        // --- remembered servers ------------------------------------------
        card(ui, theme, |ui| {
            card_title(ui, theme, "Remembered servers");
            if status.paired_devices.is_empty() {
                ui.label(
                    egui::RichText::new("Nothing remembered yet.")
                        .size(11.0)
                        .color(theme.text_dim),
                );
            }
            for device in &status.paired_devices {
                ui.horizontal(|ui| {
                    ui.vertical(|ui| {
                        ui.label(egui::RichText::new(&device.name).strong());
                        ui.label(
                            egui::RichText::new(
                                device.last_addr.clone().unwrap_or_else(|| "—".to_string()),
                            )
                            .size(11.0)
                            .color(theme.text_dim),
                        );
                    });
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if ui.button("Forget").clicked() {
                            self.engine.execute(Cmd::ForgetDevice(device.id.clone()));
                        }
                        ui.add_enabled_ui(device.last_addr.is_some(), |ui| {
                            if ui.button("Connect").clicked() {
                                self.connect_to(
                                    device.last_addr.as_deref(),
                                    flowlink_core::config::DEFAULT_PORT,
                                    Some(device.fingerprint.as_str()),
                                );
                            }
                        });
                    });
                });
            }
        });

        ui.add_space(10.0);
        metrics_card(ui, theme, status);
    }

    /// Ask the engine to move the shared cursor here/there. The core owns the
    /// decision, so both the UI button and the hotkey take the same path.
    fn toggle_remote_control(&self) {
        self.engine.execute(Cmd::ToggleControl);
    }

    /// Issue a `Connect` command for a discovered or remembered device.
    ///
    /// `address` is whatever the device announced (`host` or `host:port`); the
    /// announced `fallback_port` is used when it does not carry one.
    fn connect_to(&mut self, address: Option<&str>, fallback_port: u16, fingerprint: Option<&str>) {
        let Some(address) = address else {
            self.flash("No address known for that device — use manual connect");
            return;
        };
        let (host, port) = split_address(address, fallback_port);
        self.engine.execute(Cmd::Connect {
            host,
            port,
            code: None,
            fingerprint: fingerprint.map(|value| value.to_string()),
        });
        self.flash("Connecting…");
    }

    // ---------------------------------------------------------------------
    // Settings
    // ---------------------------------------------------------------------

    fn settings_page(&mut self, ui: &mut egui::Ui, theme: &Theme, status: &StatusMirror) {
        card(ui, theme, |ui| {
            card_title(ui, theme, "This device");
            egui::Grid::new("device_settings")
                .num_columns(2)
                .spacing([12.0, 10.0])
                .show(ui, |ui| {
                    ui.label("Device name");
                    ui.horizontal(|ui| {
                        ui.add(
                            egui::TextEdit::singleline(&mut self.device_name_edit)
                                .desired_width(180.0),
                        );
                        if ui.button("Save").clicked() {
                            let name = self.device_name_edit.trim().to_string();
                            if name.is_empty() {
                                self.flash("The device name must not be empty");
                            } else {
                                self.engine.execute(Cmd::SetDeviceName(name));
                                self.flash("Device name saved");
                            }
                        }
                    });
                    ui.end_row();

                    ui.label("Listening port");
                    ui.horizontal(|ui| {
                        ui.add(egui::TextEdit::singleline(&mut self.port_edit).desired_width(80.0));
                        if ui.button("Save").clicked() {
                            match self.port_edit.trim().parse::<u16>() {
                                Ok(port) if port > 0 => {
                                    self.engine.execute(Cmd::SetListenPort(port));
                                    // The firewall rule is per port: re-check.
                                    self.firewall_state = None;
                                    self.flash("Port saved");
                                }
                                _ => self.flash("Enter a port between 1 and 65535"),
                            }
                        }
                    });
                    ui.end_row();

                    ui.label("Clipboard");
                    let mut clipboard_enabled = status.clipboard_enabled;
                    if ui
                        .checkbox(&mut clipboard_enabled, "Sync clipboard text")
                        .changed()
                    {
                        self.engine
                            .execute(Cmd::SetClipboardEnabled(clipboard_enabled));
                        if clipboard_enabled {
                            // Send whatever is on the clipboard from now on,
                            // but do not resend what was already synced.
                            self.shared.fingerprint().clear();
                        }
                    }
                    ui.end_row();

                    ui.label("Start with Windows");
                    let mut autostart_enabled = self.autostart_state;
                    if ui
                        .checkbox(&mut autostart_enabled, "Launch at sign-in (tray)")
                        .changed()
                    {
                        match std::env::current_exe() {
                            Ok(exe) => {
                                let result = if autostart_enabled {
                                    autostart::enable(&exe)
                                } else {
                                    autostart::disable()
                                };
                                match result {
                                    Ok(()) => {
                                        self.autostart_state = autostart_enabled;
                                        self.flash(if autostart_enabled {
                                            "FlowLink will start with Windows"
                                        } else {
                                            "Autostart disabled"
                                        });
                                    }
                                    Err(err) => {
                                        warn!("autostart change failed: {err:#}");
                                        self.flash(format!("Autostart failed: {err}"));
                                    }
                                }
                            }
                            Err(err) => self.flash(format!("Cannot locate the executable: {err}")),
                        }
                    }
                    ui.end_row();

                    ui.label("Theme");
                    let mut dark = self.dark_mode;
                    if ui.checkbox(&mut dark, "Dark mode").changed() {
                        self.dark_mode = dark;
                    }
                    ui.end_row();
                });

            ui.add_space(4.0);
            ui.label(
                egui::RichText::new(format!(
                    "ID {} · port {} · addrs {}",
                    status.local.id,
                    status.local.port,
                    if status.local.addrs.is_empty() {
                        "—".to_string()
                    } else {
                        status.local.addrs.join(", ")
                    }
                ))
                .size(10.0)
                .color(theme.text_dim),
            );
        });

        ui.add_space(10.0);
        self.hotkey_card(ui, theme, status);
        ui.add_space(10.0);

        card(ui, theme, |ui| {
            card_title(ui, theme, "Files");
            ui.horizontal(|ui| {
                if ui.button("Open logs folder").clicked() {
                    let logs = flowlink_core::config::logs_dir(&self.config_dir);
                    if let Err(err) = std::fs::create_dir_all(&logs) {
                        warn!("could not create {}: {err}", logs.display());
                    }
                    open_in_explorer(&logs);
                }
                if ui.button("Reveal config").clicked() {
                    open_in_explorer(&self.config_dir);
                }
                if ui.button("Copy fingerprint").clicked() {
                    ui.ctx().copy_text(status.local.fingerprint.clone());
                    self.flash("Fingerprint copied");
                }
            });
            ui.label(
                egui::RichText::new(self.config_dir.display().to_string())
                    .size(10.0)
                    .color(theme.text_dim),
            );
        });

        ui.add_space(10.0);
        card(ui, theme, |ui| {
            card_title(ui, theme, "About");
            ui.label(format!("FlowLink {}", status.version));
            ui.label(
                egui::RichText::new(
                    "Close the window to keep FlowLink in the tray; use Quit to exit.",
                )
                .size(11.0)
                .color(theme.text_dim),
            );
            ui.add_space(4.0);
            ui.horizontal(|ui| {
                if ui.button("Restart onboarding").clicked() {
                    self.onboarding = true;
                    self.pending_mode = status.mode;
                }
                if ui.button("Quit FlowLink").clicked() {
                    let ctx = ui.ctx().clone();
                    self.quit(&ctx);
                }
            });
        });
    }

    fn hotkey_card(&mut self, ui: &mut egui::Ui, theme: &Theme, status: &StatusMirror) {
        card(ui, theme, |ui| {
            card_title(ui, theme, "Control hotkey");
            ui.label(
                egui::RichText::new(format!(
                    "Currently {} — used to toggle control on both machines.",
                    hotkey_label(status.hotkey)
                ))
                .size(11.0)
                .color(theme.text_dim),
            );
            ui.add_space(4.0);

            ui.horizontal(|ui| {
                ui.checkbox(&mut self.hotkey_draft.ctrl, "Ctrl");
                ui.checkbox(&mut self.hotkey_draft.alt, "Alt");
                ui.checkbox(&mut self.hotkey_draft.shift, "Shift");
                ui.checkbox(&mut self.hotkey_draft.meta, "Win");
            });
            ui.horizontal(|ui| {
                if self.hotkey_draft.listening {
                    ui.label(
                        egui::RichText::new("Press a key…")
                            .color(theme.accent)
                            .strong(),
                    );
                    if ui.button("Cancel").clicked() {
                        self.hotkey_draft.listening = false;
                    }
                } else {
                    let key = self
                        .hotkey_draft
                        .usage
                        .and_then(flowlink_core::hid::usage_name)
                        .unwrap_or("unset");
                    ui.label(format!("Key: {key}"));
                    if ui.button("Record key").clicked() {
                        self.hotkey_draft.listening = true;
                    }
                }
                if ui.button("Apply").clicked() {
                    if self.hotkey_draft.is_complete() {
                        if let Some(usage) = self.hotkey_draft.usage {
                            self.engine.execute(Cmd::SetHotkey(Hotkey {
                                modifiers: self.hotkey_draft.modifiers(),
                                usage,
                            }));
                            self.flash("Hotkey saved");
                        }
                    } else {
                        self.flash("A hotkey needs at least one modifier and a key");
                    }
                }
            });
        });
    }

    // ---------------------------------------------------------------------
    // Frame
    // ---------------------------------------------------------------------

    fn update(&mut self, ctx: &egui::Context) {
        self.pump_tray(ctx);
        self.handle_close_request(ctx);

        if !self.quitting {
            if self.force_show {
                self.force_show = false;
                self.show_window(ctx);
            }

            // Theme switch (egui keeps the style between frames).
            if self.dark_mode != ctx.style().visuals.dark_mode {
                theme::apply(ctx, self.dark_mode);
            }
        }

        let status = self.shared.snapshot_status();
        if !self.quitting {
            self.sync_edit_buffers(&status);
            self.refresh_qr(&status);
            self.handle_hotkey_recorder(ctx);

            let tray_state = (status.is_server(), status.is_running());
            if self.last_tray_state != Some(tray_state) {
                if let Some(tray) = &self.tray {
                    tray.set_mode(tray_state.0);
                    tray.set_running(tray_state.1);
                }
                self.last_tray_state = Some(tray_state);
            }
        }

        let theme = theme::theme_for(ctx);

        egui::TopBottomPanel::top("flowlink_header")
            .frame(
                egui::Frame::default()
                    .fill(theme.bg)
                    .inner_margin(egui::Margin::symmetric(12, 8)),
            )
            .show(ctx, |ui| self.header(ui, &theme, &status));

        egui::TopBottomPanel::bottom("flowlink_footer")
            .frame(
                egui::Frame::default()
                    .fill(theme.bg)
                    .inner_margin(egui::Margin::symmetric(12, 6)),
            )
            .show(ctx, |ui| self.footer(ui, &theme));

        egui::CentralPanel::default()
            .frame(
                egui::Frame::default()
                    .fill(theme.bg)
                    .inner_margin(egui::Margin::symmetric(12, 8)),
            )
            .show(ctx, |ui| {
                egui::ScrollArea::vertical()
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        if self.onboarding {
                            self.onboarding_page(ui, &theme);
                        } else {
                            match self.page {
                                Page::Server => self.server_page(ui, &theme, &status),
                                Page::Client => self.client_page(ui, &theme, &status),
                                Page::Settings => self.settings_page(ui, &theme, &status),
                            }
                        }
                    });
            });

        // The pairing countdown and the "Connecting…" states are time-based.
        if !self.quitting && !self.onboarding && self.page == Page::Server {
            ctx.request_repaint_after(Duration::from_millis(500));
        }

        self.screenshot_tick(ctx);
    }

    /// Preview/screenshot mode: let the engine settle, capture one frame,
    /// write it as PNG and quit. A no-op unless `--screenshot` was passed.
    fn screenshot_tick(&mut self, ctx: &egui::Context) {
        // Wall-clock settle time so the first engine status has arrived —
        // frame counts are useless because egui repaints as fast as it can.
        const SETTLE: Duration = Duration::from_millis(1200);
        // Give up if the backend never delivers the image.
        const GIVE_UP: Duration = Duration::from_secs(4);

        let Some(path) = self.screenshot.clone() else {
            return;
        };

        let started = *self.screenshot_started_at.get_or_insert_with(Instant::now);
        let elapsed = started.elapsed();

        if !self.screenshot_requested {
            ctx.request_repaint_after(Duration::from_millis(16));
            if elapsed >= SETTLE {
                ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(egui::UserData::default()));
                self.screenshot_requested = true;
                ctx.request_repaint();
            }
            return;
        }

        let mut captured = false;
        ctx.input(|input| {
            for event in &input.events {
                if let egui::Event::Screenshot { image, .. } = event {
                    save_screenshot(image, &path);
                    captured = true;
                }
            }
        });
        if captured {
            self.quitting = true;
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
            return;
        }
        ctx.request_repaint_after(Duration::from_millis(16));
        if elapsed >= SETTLE + GIVE_UP {
            tracing::error!("preview screenshot was never delivered by the backend");
            self.quitting = true;
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }
    }
}

impl eframe::App for WinApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        WinApp::update(self, ctx);
    }

    fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {
        // Backstop: covers exits that do not go through the Quit menu.
        self.cleanup();
    }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/// Build the engine, start the platform threads and run the UI.
///
/// Besides the normal application mode this also supports the cross-platform
/// preview facility used to keep the Windows design aligned with macOS:
///
/// ```text
/// --config-dir <dir>      use an isolated config directory
/// --preview-page <page>   server | client | settings (dashboard previews)
/// --screenshot <file>     capture one frame to a PNG and exit
/// ```
pub fn run() -> Result<()> {
    let mut preview_page: Option<Page> = None;
    let mut screenshot: Option<PathBuf> = None;
    let mut config_dir_override: Option<PathBuf> = None;
    let mut preview_theme: Option<bool> = None;
    {
        let mut args = std::env::args().skip(1);
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--preview-page" => {
                    preview_page = match args.next().as_deref() {
                        Some("server") => Some(Page::Server),
                        Some("client") => Some(Page::Client),
                        Some("settings") => Some(Page::Settings),
                        other => {
                            warn!(page = ?other, "unknown preview page");
                            None
                        }
                    };
                }
                "--screenshot" => screenshot = args.next().map(PathBuf::from),
                "--config-dir" => config_dir_override = args.next().map(PathBuf::from),
                "--preview-theme" => {
                    preview_theme = match args.next().as_deref() {
                        Some("dark") => Some(true),
                        Some("light") => Some(false),
                        other => {
                            warn!(theme = ?other, "unknown preview theme");
                            None
                        }
                    };
                }
                _ => {}
            }
        }
    }
    let start_hidden = std::env::args().any(|arg| arg == "--autostart") && preview_page.is_none();

    // Two instances would fight over the listen port (the second one ends up
    // with no pairing code) — refuse politely and point at the tray.
    if !crate::platform::single_instance::acquire() {
        return Ok(());
    }

    // Physical-pixel geometry must be settled before the hooks or the screen
    // metrics are used.
    input::init_process_dpi_awareness();

    let shared = AppShared::new();
    let config_dir = config_dir_override.unwrap_or_else(flowlink_core::config::default_config_dir);
    let onboarded_marker = config_dir.join(ONBOARDED_MARKER).exists();

    let engine = Engine::new(config_dir.clone(), Arc::clone(&shared) as Arc<dyn Host>)
        .context("starting the FlowLink engine")?;
    shared.set_engine(Arc::clone(&engine));

    // Platform threads. Hooks are essential: without them nothing works.
    let capture = input::start(Arc::clone(&engine), shared.capture_state())
        .context("starting input capture")?;
    clipboard::spawn_poller(
        Arc::clone(&engine),
        shared.fingerprint(),
        shared.capture_state(),
    );

    engine.execute(Cmd::SetLocalScreen(input::local_screen()));
    if onboarded_marker {
        engine.execute(Cmd::Start);
    } else {
        info!("waiting for onboarding to complete");
    }
    // Dashboard previews need realistic engine state (listening + code for
    // the server page, an idle client for the client page). Preview runs use
    // their own port so they never fight with a real FlowLink instance.
    if let Some(page) = preview_page {
        let mode = match page {
            Page::Client => Mode::Client,
            _ => Mode::Server,
        };
        engine.execute(Cmd::SetListenPort(PREVIEW_PORT));
        engine.execute(Cmd::SetMode(mode));
        if mode == Mode::Server {
            engine.execute(Cmd::Start);
        }
    }

    let (tray_tx, tray_rx) = crossbeam_channel::unbounded();

    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_title("FlowLink")
            .with_inner_size([420.0, 640.0])
            .with_min_inner_size([380.0, 520.0])
            .with_visible(!start_hidden),
        ..Default::default()
    };

    eframe::run_native(
        "FlowLink",
        options,
        Box::new(move |cc| {
            let app = WinApp::new(
                cc,
                Arc::clone(&shared),
                Arc::clone(&engine),
                capture,
                tray_rx,
                tray_tx,
                config_dir,
                onboarded_marker || preview_page.is_some(),
                start_hidden,
                preview_page,
                screenshot,
                preview_theme,
            )?;
            Ok(Box::new(app))
        }),
    )
    .map_err(|err| anyhow::anyhow!("running the user interface: {err}"))?;
    Ok(())
}

/// Write an egui screenshot to `path` as a PNG.
fn save_screenshot(image: &egui::ColorImage, path: &Path) {
    let width = image.size[0] as u32;
    let height = image.size[1] as u32;
    let mut rgba = Vec::with_capacity(image.pixels.len() * 4);
    for pixel in &image.pixels {
        rgba.extend_from_slice(&[pixel.r(), pixel.g(), pixel.b(), pixel.a()]);
    }
    match image::RgbaImage::from_raw(width, height, rgba) {
        Some(buffer) => match buffer.save(path) {
            Ok(()) => info!(path = %path.display(), "preview screenshot written"),
            Err(err) => tracing::error!("writing screenshot failed: {err}"),
        },
        None => tracing::error!("screenshot buffer has unexpected dimensions"),
    }
}

// ---------------------------------------------------------------------------
// Free helpers
// ---------------------------------------------------------------------------

/// A rounded card — the main layout primitive of the dashboards.
///
/// Cards always span the available width (like the macOS design), never
/// shrink-wrapping around short content.
fn card<R>(ui: &mut egui::Ui, theme: &Theme, contents: impl FnOnce(&mut egui::Ui) -> R) -> R {
    const MARGIN: f32 = 16.0;
    let inner_width = (ui.available_width() - MARGIN * 2.0 - 2.0).max(120.0);
    egui::Frame::default()
        .fill(theme.card)
        .stroke(egui::Stroke::new(1.0_f32, theme.border))
        .corner_radius(egui::CornerRadius::same(theme::CARD_RADIUS))
        .inner_margin(egui::Margin::same(MARGIN as i8))
        .show(ui, |ui| {
            ui.set_min_width(inner_width);
            contents(ui)
        })
        .inner
}

/// Small uppercase section header — the same visual signature as the macOS
/// design system ("PAIRING", "SCREEN EDGE", …).
fn card_title(ui: &mut egui::Ui, theme: &Theme, text: &str) {
    ui.label(
        egui::RichText::new(text.to_uppercase())
            .size(10.5)
            .strong()
            .color(theme.text_dim),
    );
}

/// A segmented-control style tab (used for the header navigation).
fn tab_button(ui: &mut egui::Ui, theme: &Theme, selected: bool, label: &str) -> egui::Response {
    let text = egui::RichText::new(label).size(12.5);
    let text = if selected {
        text.strong().color(egui::Color32::WHITE)
    } else {
        text.color(theme.text_dim)
    };
    let button = egui::Button::new(text)
        .fill(if selected {
            theme.accent
        } else {
            egui::Color32::TRANSPARENT
        })
        .corner_radius(egui::CornerRadius::same(7))
        .min_size(egui::vec2(0.0, 24.0));
    ui.add(button)
}

/// The little rounded-square brand glyph next to the window title.
fn brand_glyph(ui: &mut egui::Ui, theme: &Theme) {
    let (rect, _) = ui.allocate_exact_size(egui::vec2(22.0, 22.0), egui::Sense::hover());
    let painter = ui.painter();
    painter.rect_filled(rect, 6.0_f32, theme.accent);
    painter.text(
        rect.center(),
        egui::Align2::CENTER_CENTER,
        "⇄",
        egui::FontId::proportional(13.0),
        egui::Color32::WHITE,
    );
}

/// A selectable onboarding role card: fixed height, left-aligned content,
/// equal weight for both options.
fn mode_card(
    ui: &mut egui::Ui,
    theme: &Theme,
    width: f32,
    height: f32,
    glyph: &str,
    title: &str,
    detail: &str,
    selected: bool,
) -> egui::Response {
    let (rect, response) = ui.allocate_exact_size(egui::vec2(width, height), egui::Sense::click());
    let hovered = response.hovered();
    let fill = if selected {
        theme.accent.gamma_multiply(0.13)
    } else if hovered {
        theme.card.gamma_multiply(1.15)
    } else {
        theme.card
    };
    let stroke = if selected {
        egui::Stroke::new(1.6_f32, theme.accent)
    } else if hovered {
        egui::Stroke::new(1.0_f32, theme.accent.gamma_multiply(0.5))
    } else {
        egui::Stroke::new(1.0_f32, theme.border)
    };
    let painter = ui.painter();
    painter.rect(
        rect,
        theme::CARD_RADIUS as f32,
        fill,
        stroke,
        egui::StrokeKind::Inside,
    );

    let mut content = ui.new_child(
        egui::UiBuilder::new()
            .max_rect(rect.shrink(13.0))
            .layout(egui::Layout::top_down(egui::Align::Min)),
    );
    content.label(egui::RichText::new(glyph).size(17.0));
    content.add_space(4.0);
    content.label(
        egui::RichText::new(title)
            .size(13.0)
            .strong()
            .color(theme.text),
    );
    content.add_space(2.0);
    content.label(egui::RichText::new(detail).size(10.5).color(theme.text_dim));

    response.on_hover_cursor(egui::CursorIcon::PointingHand)
}

/// Metrics strip shown at the bottom of both dashboards.
fn metrics_card(ui: &mut egui::Ui, theme: &Theme, status: &StatusMirror) {
    let metrics = &status.metrics;
    card(ui, theme, |ui| {
        card_title(ui, theme, "Traffic");
        ui.label(
            egui::RichText::new(format!(
                "RTT {:.1} ms · ↓ {} · ↑ {} · events {} in / {} out · up {}",
                metrics.rtt_ms,
                human_bytes(metrics.rx_bytes),
                human_bytes(metrics.tx_bytes),
                metrics.events_in,
                metrics.events_out,
                human_duration(metrics.uptime_ms),
            ))
            .size(11.0)
            .color(theme.text_dim),
        );
    });
}

/// Draw a QR matrix: dark modules on a white quiet zone.
///
/// The module colour is fixed (not theme dependent) so scans work in both
/// modes — a light-mode "dark" colour would be invisible on white.
fn draw_qr(ui: &mut egui::Ui, matrix: &flowlink_core::qr::QrMatrix) {
    const MODULE_DARK: egui::Color32 = egui::Color32::from_rgb(0x12, 0x14, 0x1B);
    // Compact enough that the firewall/network card stays above the fold.
    let side = ui.available_width().min(172.0);
    let (rect, _) = ui.allocate_exact_size(egui::vec2(side, side), egui::Sense::hover());
    let painter = ui.painter();
    painter.rect_filled(rect, 8.0_f32, egui::Color32::WHITE);

    let modules = matrix.size as f32;
    if modules <= 0.0 {
        return;
    }
    let module = side / modules;
    for y in 0..matrix.size {
        for x in 0..matrix.size {
            if !matrix.dark(x, y) {
                continue;
            }
            let min = egui::pos2(
                rect.left() + x as f32 * module,
                rect.top() + y as f32 * module,
            );
            painter.rect_filled(
                egui::Rect::from_min_size(min, egui::vec2(module, module)),
                0.0_f32,
                MODULE_DARK,
            );
        }
    }
}

/// `123456` → `123 456` (easier to read out loud).
fn format_code(code: &str) -> String {
    if code.len() == 6 {
        format!("{} {}", &code[..3], &code[3..])
    } else {
        code.to_string()
    }
}

/// Title-case name for an edge (the picker labels).
fn edge_title(edge: Edge) -> &'static str {
    match edge {
        Edge::Left => "Left",
        Edge::Right => "Right",
        Edge::Top => "Top",
        Edge::Bottom => "Bottom",
    }
}

/// Human-readable hotkey chord, using the core's usage names.
fn hotkey_label(hotkey: Hotkey) -> String {
    let mut parts = Vec::new();
    if hotkey.modifiers & Modifiers::CTRL != 0 {
        parts.push("Ctrl".to_string());
    }
    if hotkey.modifiers & Modifiers::ALT != 0 {
        parts.push("Alt".to_string());
    }
    if hotkey.modifiers & Modifiers::SHIFT != 0 {
        parts.push("Shift".to_string());
    }
    if hotkey.modifiers & Modifiers::META != 0 {
        parts.push("Win".to_string());
    }
    parts.push(
        flowlink_core::hid::usage_name(hotkey.usage)
            .map(|name| name.to_string())
            .unwrap_or_else(|| format!("0x{:02X}", hotkey.usage)),
    );
    parts.join(" + ")
}

fn state_color(theme: &Theme, state: ConnStateMirror) -> egui::Color32 {
    match state {
        ConnStateMirror::Off => theme.gray,
        ConnStateMirror::Listening | ConnStateMirror::Connected => theme.ok,
        ConnStateMirror::Error => theme.err,
        _ => theme.warn,
    }
}

fn state_label(state: ConnStateMirror) -> &'static str {
    match state {
        ConnStateMirror::Off => "Off",
        ConnStateMirror::Listening => "Listening",
        ConnStateMirror::Connecting => "Connecting…",
        ConnStateMirror::Handshake => "Securing the connection…",
        ConnStateMirror::AwaitingPair => "Waiting for the pairing code",
        ConnStateMirror::Pairing => "Pairing…",
        ConnStateMirror::Connected => "Connected",
        ConnStateMirror::Reconnecting => "Reconnecting…",
        ConnStateMirror::Error => "Error",
    }
}

fn notice_color(theme: &Theme, kind: &str) -> egui::Color32 {
    match kind {
        "error" => theme.err,
        "pairing" | "control" => theme.accent,
        "connect" => theme.warn,
        _ => theme.text_dim,
    }
}

/// `host[:port]` → `(host, port)`, using `fallback_port` when the address
/// carries none.
fn split_address(address: &str, fallback_port: u16) -> (String, u16) {
    match address.rsplit_once(':') {
        Some((host, port)) => match port.trim().parse::<u16>() {
            Ok(port) if !host.is_empty() && port > 0 => (host.to_string(), port),
            _ => (address.to_string(), fallback_port),
        },
        None => (address.trim().to_string(), fallback_port),
    }
}

/// `host[:port]` → `(host, port)`; the default port comes from the core.
fn parse_host_port(input: &str) -> Option<(String, u16)> {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        return None;
    }
    match trimmed.rsplit_once(':') {
        Some((host, port)) => {
            if host.is_empty() {
                return None;
            }
            port.trim()
                .parse::<u16>()
                .ok()
                .filter(|port| *port > 0)
                .map(|port| (host.to_string(), port))
        }
        None => Some((trimmed.to_string(), flowlink_core::config::DEFAULT_PORT)),
    }
}

fn human_bytes(bytes: u64) -> String {
    const UNITS: [&str; 4] = ["B", "KiB", "MiB", "GiB"];
    let mut value = bytes as f64;
    let mut unit = 0;
    while value >= 1024.0 && unit + 1 < UNITS.len() {
        value /= 1024.0;
        unit += 1;
    }
    if unit == 0 {
        format!("{bytes} B")
    } else {
        format!("{value:.1} {}", UNITS[unit])
    }
}

fn human_duration(ms: u64) -> String {
    let seconds = ms / 1000;
    let (hours, minutes, seconds) = (seconds / 3600, (seconds % 3600) / 60, seconds % 60);
    if hours > 0 {
        format!("{hours}h {minutes:02}m {seconds:02}s")
    } else if minutes > 0 {
        format!("{minutes}m {seconds:02}s")
    } else {
        let tenths = (ms % 1000) / 100;
        format!("{seconds}.{tenths}s")
    }
}

/// Translate an egui key into a USB HID usage id (hotkey recorder).
///
/// Only the keys that make sense as a hotkey are covered; egui cannot tell
/// left from right modifiers, so the left variants are used.
fn egui_key_to_hid(key: egui::Key) -> Option<u16> {
    use egui::Key;
    use flowlink_core::hid::usage;
    Some(match key {
        Key::A => usage::A,
        Key::B => usage::B,
        Key::C => usage::C,
        Key::D => usage::D,
        Key::E => usage::E,
        Key::F => usage::F,
        Key::G => usage::G,
        Key::H => usage::H,
        Key::I => usage::I,
        Key::J => usage::J,
        Key::K => usage::K,
        Key::L => usage::L,
        Key::M => usage::M,
        Key::N => usage::N,
        Key::O => usage::O,
        Key::P => usage::P,
        Key::Q => usage::Q,
        Key::R => usage::R,
        Key::S => usage::S,
        Key::T => usage::T,
        Key::U => usage::U,
        Key::V => usage::V,
        Key::W => usage::W,
        Key::X => usage::X,
        Key::Y => usage::Y,
        Key::Z => usage::Z,
        Key::Num0 => usage::NUM_0,
        Key::Num1 => usage::NUM_1,
        Key::Num2 => usage::NUM_2,
        Key::Num3 => usage::NUM_3,
        Key::Num4 => usage::NUM_4,
        Key::Num5 => usage::NUM_5,
        Key::Num6 => usage::NUM_6,
        Key::Num7 => usage::NUM_7,
        Key::Num8 => usage::NUM_8,
        Key::Num9 => usage::NUM_9,
        Key::F1 => usage::F1,
        Key::F2 => usage::F2,
        Key::F3 => usage::F3,
        Key::F4 => usage::F4,
        Key::F5 => usage::F5,
        Key::F6 => usage::F6,
        Key::F7 => usage::F7,
        Key::F8 => usage::F8,
        Key::F9 => usage::F9,
        Key::F10 => usage::F10,
        Key::F11 => usage::F11,
        Key::F12 => usage::F12,
        Key::Space => usage::SPACE,
        Key::Enter => usage::ENTER,
        Key::Escape => usage::ESCAPE,
        Key::Tab => usage::TAB,
        Key::Backspace => usage::BACKSPACE,
        Key::Insert => usage::INSERT,
        Key::Delete => usage::DELETE,
        Key::Home => usage::HOME,
        Key::End => usage::END,
        Key::PageUp => usage::PAGE_UP,
        Key::PageDown => usage::PAGE_DOWN,
        Key::ArrowLeft => usage::LEFT_ARROW,
        Key::ArrowRight => usage::RIGHT_ARROW,
        Key::ArrowUp => usage::UP_ARROW,
        Key::ArrowDown => usage::DOWN_ARROW,
        Key::Minus => usage::MINUS,
        Key::Equals | Key::Plus => usage::EQUALS,
        Key::Comma => usage::COMMA,
        Key::Period => usage::PERIOD,
        Key::Slash => usage::SLASH,
        Key::Backslash => usage::BACKSLASH,
        Key::Semicolon => usage::SEMICOLON,
        Key::Quote => usage::QUOTE,
        Key::Backtick => usage::BACKQUOTE,
        Key::OpenBracket => usage::LEFT_BRACKET,
        Key::CloseBracket => usage::RIGHT_BRACKET,
        _ => return None,
    })
}

/// Open a folder in Explorer (a non-zero exit status is normal for
/// `explorer.exe`).
fn open_in_explorer(path: &Path) {
    if let Err(err) = std::process::Command::new("explorer").arg(path).spawn() {
        warn!("could not open {}: {err}", path.display());
    }
}
