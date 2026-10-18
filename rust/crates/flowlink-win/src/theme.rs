//! Design tokens for the Windows shell.
//!
//! The values mirror the macOS SwiftUI app so both platforms look like one
//! product: the same violet accent ramp, the same surface colours for light
//! and dark mode, 12–14 px rounded corners and the same status colours.
//!
//! Everything UI code colours itself with comes from [`Theme`]; the palette is
//! resolved once per frame with [`theme_for`], which follows egui's current
//! dark/light mode.

use egui::{Color32, CornerRadius, Stroke};

/// Corner radius used for cards and grouped panels.
pub const CARD_RADIUS: u8 = 12;
/// Corner radius used for windows and popups.
pub const WINDOW_RADIUS: u8 = 14;

/// Start of the accent gradient (violet). Also used for the tray icon, which
/// is painted without an egui context.
pub const ACCENT: Color32 = Color32::from_rgb(0x6E, 0x5B, 0xFF);
/// End of the accent gradient.
pub const ACCENT_2: Color32 = Color32::from_rgb(0x8B, 0x5C, 0xF6);

/// The resolved palette for the current mode.
#[derive(Clone, Copy, Debug)]
pub struct Theme {
    /// Whether this palette is the dark one.
    pub dark: bool,
    /// Primary accent (the start of the macOS accent gradient).
    pub accent: Color32,
    /// Secondary accent (the end of the macOS accent gradient); used for
    /// hover/emphasis where a flat colour is needed.
    pub accent_2: Color32,
    /// Window/background surface.
    pub bg: Color32,
    /// Card surface (slightly lifted from `bg`).
    pub card: Color32,
    /// Primary text.
    pub text: Color32,
    /// Secondary/muted text.
    pub text_dim: Color32,
    /// Subtle 1 px border colour for cards.
    pub border: Color32,
    /// Success/connected green.
    pub ok: Color32,
    /// Warning amber.
    pub warn: Color32,
    /// Error red.
    pub err: Color32,
    /// Neutral grey for idle/offline states.
    pub gray: Color32,
}

/// Palette for a specific mode.
pub fn theme_for_dark(dark: bool) -> Theme {
    if dark {
        Theme {
            dark,
            accent: Color32::from_rgb(0x8B, 0x5C, 0xF6),
            accent_2: Color32::from_rgb(0x6E, 0x5B, 0xFF),
            bg: Color32::from_rgb(0x0E, 0x10, 0x15),
            card: Color32::from_rgb(0x17, 0x1A, 0x21),
            text: Color32::from_rgb(0xEC, 0xEF, 0xF4),
            text_dim: Color32::from_rgb(0x9A, 0xA3, 0xB2),
            border: Color32::from_rgb(0x23, 0x27, 0x33),
            ok: Color32::from_rgb(0x2F, 0xBF, 0x71),
            warn: Color32::from_rgb(0xE8, 0xA3, 0x3D),
            err: Color32::from_rgb(0xE5, 0x48, 0x4D),
            gray: Color32::from_rgb(0x8A, 0x91, 0xA0),
        }
    } else {
        Theme {
            dark,
            accent: Color32::from_rgb(0x6E, 0x5B, 0xFF),
            accent_2: Color32::from_rgb(0x8B, 0x5C, 0xF6),
            bg: Color32::from_rgb(0xF5, 0xF6, 0xFA),
            card: Color32::from_rgb(0xFF, 0xFF, 0xFF),
            text: Color32::from_rgb(0x16, 0x18, 0x1F),
            text_dim: Color32::from_rgb(0x5C, 0x64, 0x72),
            border: Color32::from_rgb(0xE2, 0xE5, 0xEE),
            ok: Color32::from_rgb(0x2F, 0xBF, 0x71),
            warn: Color32::from_rgb(0xE8, 0xA3, 0x3D),
            err: Color32::from_rgb(0xE5, 0x48, 0x4D),
            gray: Color32::from_rgb(0x8A, 0x91, 0xA0),
        }
    }
}

/// Resolve the palette for the current egui mode.
pub fn theme_for(ctx: &egui::Context) -> Theme {
    theme_for_dark(ctx.style().visuals.dark_mode)
}

/// Apply the FlowLink visual style for the given mode.
///
/// Called on startup and whenever the user flips the theme switch; egui keeps
/// the style, so this is not a per-frame operation.
pub fn apply(ctx: &egui::Context, dark: bool) {
    let theme = theme_for_dark(dark);
    let egui_theme = if dark {
        egui::Theme::Dark
    } else {
        egui::Theme::Light
    };
    // Start from the built-in palette of the target mode and override it.
    let mut style = (*ctx.style_of(egui_theme)).clone();

    let mut visuals = if dark {
        egui::Visuals::dark()
    } else {
        egui::Visuals::light()
    };
    visuals.dark_mode = dark;
    visuals.override_text_color = Some(theme.text);
    visuals.panel_fill = theme.bg;
    visuals.window_fill = theme.card;
    visuals.window_stroke = Stroke::new(1.0_f32, theme.border);
    visuals.window_corner_radius = CornerRadius::same(WINDOW_RADIUS);
    visuals.menu_corner_radius = CornerRadius::same(CARD_RADIUS);
    visuals.extreme_bg_color = if theme.dark {
        Color32::from_rgb(0x0A, 0x0B, 0x0F)
    } else {
        Color32::from_rgb(0xEC, 0xEE, 0xF5)
    };
    visuals.faint_bg_color = if theme.dark {
        Color32::from_rgb(0x13, 0x16, 0x1C)
    } else {
        Color32::from_rgb(0xF0, 0xF2, 0xF8)
    };
    visuals.hyperlink_color = theme.accent_2;
    visuals.selection = egui::style::Selection {
        bg_fill: theme.accent.gamma_multiply(0.35),
        stroke: Stroke::new(1.0_f32, theme.accent),
    };

    // Widgets: cards are flat with a subtle border, interactive elements get
    // the accent on hover/active.
    let widgets = &mut visuals.widgets;
    widgets.noninteractive.bg_fill = theme.card;
    widgets.noninteractive.weak_bg_fill = theme.card;
    widgets.noninteractive.bg_stroke = Stroke::new(1.0_f32, theme.border);
    widgets.noninteractive.fg_stroke = Stroke::new(1.0_f32, theme.text_dim);
    widgets.noninteractive.corner_radius = CornerRadius::same(CARD_RADIUS);

    widgets.inactive.bg_fill = if dark {
        Color32::from_rgb(0x21, 0x25, 0x2F)
    } else {
        Color32::from_rgb(0xEE, 0xF0, 0xF6)
    };
    widgets.inactive.weak_bg_fill = widgets.inactive.bg_fill;
    widgets.inactive.bg_stroke = Stroke::new(1.0_f32, theme.border);
    widgets.inactive.fg_stroke = Stroke::new(1.0_f32, theme.text);
    widgets.inactive.corner_radius = CornerRadius::same(10);

    widgets.hovered.bg_fill = theme.accent.gamma_multiply(0.22);
    widgets.hovered.weak_bg_fill = widgets.hovered.bg_fill;
    widgets.hovered.bg_stroke = Stroke::new(1.0_f32, theme.accent.gamma_multiply(0.6));
    widgets.hovered.fg_stroke = Stroke::new(1.5_f32, theme.text);
    widgets.hovered.corner_radius = CornerRadius::same(10);

    widgets.active.bg_fill = theme.accent.gamma_multiply(0.35);
    widgets.active.weak_bg_fill = widgets.active.bg_fill;
    widgets.active.bg_stroke = Stroke::new(1.0_f32, theme.accent);
    widgets.active.fg_stroke = Stroke::new(1.5_f32, theme.text);
    widgets.active.corner_radius = CornerRadius::same(10);

    widgets.open.bg_fill = widgets.inactive.bg_fill;
    widgets.open.weak_bg_fill = widgets.inactive.bg_fill;
    widgets.open.bg_stroke = Stroke::new(1.0_f32, theme.border);
    widgets.open.fg_stroke = Stroke::new(1.0_f32, theme.text);
    widgets.open.corner_radius = CornerRadius::same(10);

    style.visuals = visuals;
    style.spacing.item_spacing = egui::vec2(8.0, 8.0);
    style.spacing.button_padding = egui::vec2(10.0, 6.0);
    style.spacing.interact_size.y = 24.0;

    // Pin the preference to the explicit choice and install the palette for
    // exactly that theme, so egui cannot swap it for its built-in one.
    ctx.set_theme(egui_theme);
    ctx.set_style_of(egui_theme, style);
}

/// Install the theme using egui's current mode (called on startup).
pub fn install(ctx: &egui::Context) {
    let dark = ctx.style().visuals.dark_mode;
    apply(ctx, dark);
}
