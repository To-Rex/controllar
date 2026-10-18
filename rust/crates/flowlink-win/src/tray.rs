//! System-tray icon and menu.
//!
//! The tray is the app's real home: closing the window hides it, and the menu
//! offers the same actions as the main window. `tray-icon` handles are
//! reference-counted and not `Send` — they are created on the UI thread (in
//! the eframe app creator) and only touched from there.
//!
//! Menu clicks arrive on the tray's own event thread; they are forwarded into
//! a [`crossbeam_channel`] that the egui update loop drains, with a repaint
//! request so the UI reacts even while hidden.

use std::sync::Arc;

use anyhow::{Context, Result};
use crossbeam_channel::Sender;
use tray_icon::menu::{CheckMenuItem, Menu, MenuEvent, MenuId, MenuItem, PredefinedMenuItem};
use tray_icon::{Icon, MouseButton, TrayIcon, TrayIconBuilder, TrayIconEvent};

use crate::app::AppShared;
use crate::theme;

/// Actions requested from the tray menu.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TrayCommand {
    /// Bring the main window back.
    Show,
    /// Switch to server mode (and start).
    ModeServer,
    /// Switch to client mode (and start).
    ModeClient,
    /// Start the engine in the current mode.
    Start,
    /// Stop the engine (keep the app running in the tray).
    Stop,
    /// Exit the application.
    Quit,
}

/// Identifiers of the menu items, so the (global) menu event handler can map
/// events back to commands.
#[derive(Clone)]
struct MenuIds {
    show: MenuId,
    server: MenuId,
    client: MenuId,
    start: MenuId,
    stop: MenuId,
    quit: MenuId,
}

/// Live tray icon plus the item handles whose state the UI keeps in sync.
pub struct TrayHandle {
    /// Kept alive for as long as the app runs (dropping it removes the icon).
    _tray: TrayIcon,
    server_item: CheckMenuItem,
    client_item: CheckMenuItem,
    start_item: MenuItem,
    stop_item: MenuItem,
}

impl TrayHandle {
    /// Reflect the active mode in the two radio-style entries.
    pub fn set_mode(&self, server: bool) {
        self.server_item.set_checked(server);
        self.client_item.set_checked(!server);
    }

    /// Reflect whether the engine is currently running.
    pub fn set_running(&self, running: bool) {
        self.start_item.set_enabled(!running);
        self.stop_item.set_enabled(running);
    }
}

/// Create the tray icon and wire its menu to `sender`.
///
/// Must be called on the thread that pumps Win32 messages (the UI thread).
pub fn install(
    shared: &Arc<AppShared>,
    sender: Sender<TrayCommand>,
    server_mode: bool,
    running: bool,
) -> Result<TrayHandle> {
    let menu = Menu::new();

    let show_item = MenuItem::new("Show FlowLink", true, None);
    let separator_top = PredefinedMenuItem::separator();
    let server_item = CheckMenuItem::new("Server mode", true, server_mode, None);
    let client_item = CheckMenuItem::new("Client mode", true, !server_mode, None);
    let start_item = MenuItem::new("Start", !running, None);
    let stop_item = MenuItem::new("Stop", running, None);
    let separator_bottom = PredefinedMenuItem::separator();
    let quit_item = MenuItem::new("Quit", true, None);

    menu.append_items(&[
        &show_item,
        &separator_top,
        &server_item,
        &client_item,
        &start_item,
        &stop_item,
        &separator_bottom,
        &quit_item,
    ])
    .context("building the tray menu")?;

    let ids = MenuIds {
        show: show_item.id().clone(),
        server: server_item.id().clone(),
        client: client_item.id().clone(),
        start: start_item.id().clone(),
        stop: stop_item.id().clone(),
        quit: quit_item.id().clone(),
    };

    // Menu clicks: map the id back to a command. The handler is global and can
    // only be installed once per process.
    let handler_ids = ids.clone();
    let handler_sender = sender.clone();
    let handler_shared = Arc::clone(shared);
    MenuEvent::set_event_handler(Some(move |event: MenuEvent| {
        let command = if event.id == handler_ids.show {
            Some(TrayCommand::Show)
        } else if event.id == handler_ids.server {
            Some(TrayCommand::ModeServer)
        } else if event.id == handler_ids.client {
            Some(TrayCommand::ModeClient)
        } else if event.id == handler_ids.start {
            Some(TrayCommand::Start)
        } else if event.id == handler_ids.stop {
            Some(TrayCommand::Stop)
        } else if event.id == handler_ids.quit {
            Some(TrayCommand::Quit)
        } else {
            None
        };
        if let Some(command) = command {
            if command == TrayCommand::Show {
                // Restore the window right away: while it is hidden the UI
                // thread may not be drawing frames yet.
                handler_shared.show_window();
            }
            let _ = handler_sender.send(command);
            handler_shared.request_repaint();
        }
    }));

    // Icon clicks: double-clicking the tray restores the window (the left
    // click opens the menu, which is the Windows convention).
    let click_sender = sender;
    let click_shared = Arc::clone(shared);
    TrayIconEvent::set_event_handler(Some(move |event: TrayIconEvent| {
        if let TrayIconEvent::DoubleClick {
            button: MouseButton::Left,
            ..
        } = event
        {
            click_shared.show_window();
            let _ = click_sender.send(TrayCommand::Show);
            click_shared.request_repaint();
        }
    }));

    let tray = TrayIconBuilder::new()
        .with_tooltip("FlowLink — keyboard, mouse and clipboard sharing")
        .with_icon(build_icon()?)
        .with_menu(Box::new(menu))
        .build()
        .context("creating the tray icon")?;

    Ok(TrayHandle {
        _tray: tray,
        server_item,
        client_item,
        start_item,
        stop_item,
    })
}

/// Draw the tray icon at runtime: an accent rounded square with a white
/// "share" glyph — three linked nodes, one here and two peers (no image
/// assets to ship, and it stays legible at the 16×16 the Windows tray
/// actually uses).
fn build_icon() -> Result<Icon> {
    const SIZE: u32 = 32;
    /// Samples per axis for anti-aliasing (2×2 = 4 samples per pixel).
    const SUBSAMPLES: u32 = 2;

    let mut rgba = vec![0u8; (SIZE * SIZE * 4) as usize];
    let accent = theme::ACCENT;
    let accent_2 = theme::ACCENT_2;

    for y in 0..SIZE {
        for x in 0..SIZE {
            // Vertical accent gradient across the tile.
            let t = y as f32 / (SIZE - 1) as f32;
            let background = [
                lerp(accent.r() as f32, accent_2.r() as f32, t),
                lerp(accent.g() as f32, accent_2.g() as f32, t),
                lerp(accent.b() as f32, accent_2.b() as f32, t),
            ];

            let mut tile_hits = 0.0f32;
            let mut glyph_hits = 0.0f32;
            let samples = (SUBSAMPLES * SUBSAMPLES) as f32;
            for sy in 0..SUBSAMPLES {
                for sx in 0..SUBSAMPLES {
                    let px = x as f32 + (sx as f32 + 0.5) / SUBSAMPLES as f32;
                    let py = y as f32 + (sy as f32 + 0.5) / SUBSAMPLES as f32;
                    if inside_rounded_rect(px, py, 16.0, 16.0, 15.5, 15.5, 8.0) {
                        tile_hits += 1.0 / samples;
                        if is_share_glyph(px, py) {
                            glyph_hits += 1.0 / samples;
                        }
                    }
                }
            }

            let coverage = tile_hits.clamp(0.0, 1.0);
            let glyph = glyph_hits.clamp(0.0, 1.0);
            let alpha = (coverage * 255.0).round() as u8;
            let index = ((y * SIZE + x) * 4) as usize;
            // White glyphed over the accent tile; both anti-aliased.
            let mix = |channel: f32| -> u8 { channel.round().clamp(0.0, 255.0) as u8 };
            rgba[index] = mix(lerp(background[0], 255.0, glyph));
            rgba[index + 1] = mix(lerp(background[1], 255.0, glyph));
            rgba[index + 2] = mix(lerp(background[2], 255.0, glyph));
            rgba[index + 3] = alpha;
        }
    }

    Icon::from_rgba(rgba, SIZE, SIZE).context("building the tray icon image")
}

/// True inside the "share" glyph: three nodes joined by two links.
fn is_share_glyph(px: f32, py: f32) -> bool {
    // Left-top, left-bottom and right node of the 32×32 tile.
    const NODES: [(f32, f32); 3] = [(10.5, 11.0), (10.5, 21.0), (21.5, 16.0)];
    const NODE_RADIUS: f32 = 3.2;
    const LINK_HALF_WIDTH: f32 = 1.15;

    for (cx, cy) in NODES {
        let (dx, dy) = (px - cx, py - cy);
        if dx * dx + dy * dy <= NODE_RADIUS * NODE_RADIUS {
            return true;
        }
    }
    // Both left nodes connect to the right one.
    [(NODES[0], NODES[2]), (NODES[1], NODES[2])]
        .iter()
        .any(|(from, to)| distance_to_segment(px, py, *from, *to) <= LINK_HALF_WIDTH)
}

/// Distance from `(px, py)` to the segment `from`–`to`.
fn distance_to_segment(px: f32, py: f32, from: (f32, f32), to: (f32, f32)) -> f32 {
    let (vx, vy) = (to.0 - from.0, to.1 - from.1);
    let (wx, wy) = (px - from.0, py - from.1);
    let length_sq = vx * vx + vy * vy;
    let t = if length_sq == 0.0 {
        0.0
    } else {
        ((wx * vx + wy * vy) / length_sq).clamp(0.0, 1.0)
    };
    let (dx, dy) = (px - (from.0 + t * vx), py - (from.1 + t * vy));
    (dx * dx + dy * dy).sqrt()
}

/// Point-in-rounded-rectangle test (centre + half extents + corner radius).
fn inside_rounded_rect(
    px: f32,
    py: f32,
    cx: f32,
    cy: f32,
    half_w: f32,
    half_h: f32,
    radius: f32,
) -> bool {
    let inner_w = (half_w - radius).max(0.0);
    let inner_h = (half_h - radius).max(0.0);
    let dx = (px - cx).abs() - inner_w;
    let dy = (py - cy).abs() - inner_h;
    if dx <= 0.0 && dy <= 0.0 {
        return true;
    }
    let ox = dx.max(0.0);
    let oy = dy.max(0.0);
    ox * ox + oy * oy <= radius * radius
}

fn lerp(from: f32, to: f32, t: f32) -> f32 {
    from + (to - from) * t
}
