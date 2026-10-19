//! USB HID ⇄ Windows virtual-key mapping.
//!
//! The wire protocol represents keys as USB HID usage ids
//! ([`flowlink_core::hid::usage`]); Windows speaks virtual-key (VK) codes.
//! A single `(vk, hid)` table drives both directions so the two mappings can
//! never drift apart.
//!
//! The module is kept free of Win32 calls — except [`modifiers_from_vk_states`],
//! which is Windows-only — so the table and its unit tests also build and run
//! on the macOS dev host.
//!
//! Notes on the choices made here:
//!
//! * Generic VKs are used for the keyboard variants Windows abstracts away:
//!   `VK_SHIFT`/`VK_CONTROL`/`VK_MENU` map to *left* modifier usages, while the
//!   explicit `VK_LSHIFT`/`VK_RSHIFT`/… codes map to the matching side. The
//!   reverse lookup therefore resolves a generic VK to the left variant;
//!   callers that care about the side use the explicit VKs (the low-level hook
//!   reports `VK_LSHIFT`/`VK_RSHIFT`, never plain `VK_SHIFT`).
//! * The numeric keypad keeps its HID usages (`KP_1`…`KP_0`), so numpad digits
//!   stay distinguishable from the main row.

use flowlink_core::hid::usage;

/// Bidirectional key table: `(virtual-key code, USB HID usage id)`.
///
/// One row per physical key. Both directions ([`vk_to_hid`], [`hid_to_vk`])
/// scan this table, which makes the mapping self-consistent by construction.
#[cfg_attr(not(windows), allow(dead_code))]
const KEY_TABLE: [(u16, u16); 103] = [
    // --- Letters: VK_A..VK_Z (0x41..=0x5A) → A..Z (0x04..=0x1D) -------------
    (0x41, usage::A),
    (0x42, usage::B),
    (0x43, usage::C),
    (0x44, usage::D),
    (0x45, usage::E),
    (0x46, usage::F),
    (0x47, usage::G),
    (0x48, usage::H),
    (0x49, usage::I),
    (0x4A, usage::J),
    (0x4B, usage::K),
    (0x4C, usage::L),
    (0x4D, usage::M),
    (0x4E, usage::N),
    (0x4F, usage::O),
    (0x50, usage::P),
    (0x51, usage::Q),
    (0x52, usage::R),
    (0x53, usage::S),
    (0x54, usage::T),
    (0x55, usage::U),
    (0x56, usage::V),
    (0x57, usage::W),
    (0x58, usage::X),
    (0x59, usage::Y),
    (0x5A, usage::Z),
    // --- Digits: VK_1..VK_9 (0x31..=0x39), VK_0 (0x30) → NUM_1..NUM_9, NUM_0 --
    (0x31, usage::NUM_1),
    (0x32, usage::NUM_2),
    (0x33, usage::NUM_3),
    (0x34, usage::NUM_4),
    (0x35, usage::NUM_5),
    (0x36, usage::NUM_6),
    (0x37, usage::NUM_7),
    (0x38, usage::NUM_8),
    (0x39, usage::NUM_9),
    (0x30, usage::NUM_0),
    // --- OEM punctuation (US layout) -----------------------------------------
    (0xBA, usage::SEMICOLON),     // VK_OEM_1       ;
    (0xBB, usage::EQUALS),        // VK_OEM_PLUS    =
    (0xBC, usage::COMMA),         // VK_OEM_COMMA   ,
    (0xBD, usage::MINUS),         // VK_OEM_MINUS   -
    (0xBE, usage::PERIOD),        // VK_OEM_PERIOD  .
    (0xBF, usage::SLASH),         // VK_OEM_2       /
    (0xC0, usage::BACKQUOTE),     // VK_OEM_3       `
    (0xDB, usage::LEFT_BRACKET),  // VK_OEM_4       [
    (0xDC, usage::BACKSLASH),     // VK_OEM_5       \
    (0xDD, usage::RIGHT_BRACKET), // VK_OEM_6       ]
    (0xDE, usage::QUOTE),         // VK_OEM_7       '
    // --- Editing / control keys ---------------------------------------------
    (0x20, usage::SPACE),     // VK_SPACE
    (0x0D, usage::ENTER),     // VK_RETURN
    (0x1B, usage::ESCAPE),    // VK_ESCAPE
    (0x08, usage::BACKSPACE), // VK_BACK
    (0x09, usage::TAB),       // VK_TAB
    (0x14, usage::CAPS_LOCK), // VK_CAPITAL
    // --- Arrows --------------------------------------------------------------
    (0x25, usage::LEFT_ARROW),  // VK_LEFT
    (0x26, usage::UP_ARROW),    // VK_UP
    (0x27, usage::RIGHT_ARROW), // VK_RIGHT
    (0x28, usage::DOWN_ARROW),  // VK_DOWN
    // --- Navigation / editing cluster ---------------------------------------
    (0x2D, usage::INSERT),    // VK_INSERT
    (0x2E, usage::DELETE),    // VK_DELETE
    (0x24, usage::HOME),      // VK_HOME
    (0x23, usage::END),       // VK_END
    (0x21, usage::PAGE_UP),   // VK_PRIOR
    (0x22, usage::PAGE_DOWN), // VK_NEXT
    // --- Function keys: VK_F1..VK_F12 (0x70..=0x7B) → F1..F12 (0x3A..=0x45) --
    (0x70, usage::F1),
    (0x71, usage::F2),
    (0x72, usage::F3),
    (0x73, usage::F4),
    (0x74, usage::F5),
    (0x75, usage::F6),
    (0x76, usage::F7),
    (0x77, usage::F8),
    (0x78, usage::F9),
    (0x79, usage::F10),
    (0x7A, usage::F11),
    (0x7B, usage::F12),
    // --- Modifiers -----------------------------------------------------------
    (0xA0, usage::LEFT_SHIFT),  // VK_LSHIFT
    (0xA1, usage::RIGHT_SHIFT), // VK_RSHIFT
    (0xA2, usage::LEFT_CTRL),   // VK_LCONTROL
    (0xA3, usage::RIGHT_CTRL),  // VK_RCONTROL
    (0xA4, usage::LEFT_ALT),    // VK_LMENU
    (0xA5, usage::RIGHT_ALT),   // VK_RMENU
    (0x5B, usage::LEFT_META),   // VK_LWIN
    (0x5C, usage::RIGHT_META),  // VK_RWIN
    // --- Numeric keypad ------------------------------------------------------
    (0x90, usage::NUM_LOCK),    // VK_NUMLOCK
    (0x6F, usage::KP_DIVIDE),   // VK_DIVIDE
    (0x6A, usage::KP_MULTIPLY), // VK_MULTIPLY
    (0x6D, usage::KP_SUBTRACT), // VK_SUBTRACT
    (0x6B, usage::KP_ADD),      // VK_ADD
    (0x6E, usage::KP_PERIOD),   // VK_DECIMAL
    (0x60, usage::KP_0),        // VK_NUMPAD0
    (0x61, usage::KP_1),        // VK_NUMPAD1
    (0x62, usage::KP_2),        // VK_NUMPAD2
    (0x63, usage::KP_3),        // VK_NUMPAD3
    (0x64, usage::KP_4),        // VK_NUMPAD4
    (0x65, usage::KP_5),        // VK_NUMPAD5
    (0x66, usage::KP_6),        // VK_NUMPAD6
    (0x67, usage::KP_7),        // VK_NUMPAD7
    (0x68, usage::KP_8),        // VK_NUMPAD8
    (0x69, usage::KP_9),        // VK_NUMPAD9
    // --- System / misc -------------------------------------------------------
    (0x2C, usage::PRINT_SCREEN), // VK_SNAPSHOT
    (0x91, usage::SCROLL_LOCK),  // VK_SCROLL
    (0x13, usage::PAUSE),        // VK_PAUSE
    (0x5D, usage::APPLICATION),  // VK_APPS (menu key)
];

/// Translate a Windows virtual-key code into a USB HID usage id.
#[cfg_attr(not(windows), allow(dead_code))]
pub fn vk_to_hid(vk: u16) -> Option<u16> {
    KEY_TABLE
        .iter()
        .find(|(candidate, _)| *candidate == vk)
        .map(|(_, hid)| *hid)
}

/// Translate a USB HID usage id into a Windows virtual-key code.
#[cfg_attr(not(windows), allow(dead_code))]
pub fn hid_to_vk(hid: u16) -> Option<u16> {
    KEY_TABLE
        .iter()
        .find(|(_, candidate)| *candidate == hid)
        .map(|(vk, _)| *vk)
}

/// Read the current modifier state straight from the keyboard, as a
/// [`flowlink_core::types::Modifiers`] bit set.
///
/// Low-level hook callbacks run before the event reaches the target window, so
/// asking Win32 for the *physical* key state is the reliable way to know which
/// modifiers were held while an event was generated.
#[cfg(windows)]
pub fn modifiers_from_vk_states() -> u8 {
    use flowlink_core::types::Modifiers;
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        GetAsyncKeyState, VK_CONTROL, VK_LWIN, VK_MENU, VK_RWIN, VK_SHIFT,
    };

    /// True while `vk` is physically held (`GetAsyncKeyState` high bit).
    fn down(vk: u16) -> bool {
        // SAFETY: GetAsyncKeyState has no preconditions; a zero result simply
        // means "not pressed / no keyboard state available".
        (unsafe { GetAsyncKeyState(vk as i32) } as u16 & 0x8000) != 0
    }

    let mut bits = 0u8;
    if down(VK_SHIFT.0) {
        bits |= Modifiers::SHIFT;
    }
    if down(VK_CONTROL.0) {
        bits |= Modifiers::CTRL;
    }
    // `VK_MENU` is the generic Alt; `VK_LWIN`/`VK_RWIN` are the Windows
    // equivalent of the macOS Meta/Command modifier.
    if down(VK_MENU.0) {
        bits |= Modifiers::ALT;
    }
    if down(VK_LWIN.0) || down(VK_RWIN.0) {
        bits |= Modifiers::META;
    }
    bits
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every key that matters for round-tripping: one sample per group.
    const SAMPLE_VKS: [u16; 24] = [
        0x41, // A
        0x5A, // Z
        0x30, // 0
        0x39, // 9
        0xBA, // ;
        0xDE, // '
        0x20, // space
        0x0D, // return
        0x1B, // escape
        0x25, // left
        0x28, // down
        0x2D, // insert
        0x70, // F1
        0x7B, // F12
        0xA0, // L-shift
        0xA5, // R-alt
        0x5B, // L-win
        0x5C, // R-win
        0x60, // numpad 0
        0x69, // numpad 9
        0x6F, // numpad /
        0x2C, // print screen
        0x5D, // menu key
        0x14, // caps lock
    ];

    #[test]
    fn sample_keys_round_trip() {
        for vk in SAMPLE_VKS {
            let hid = vk_to_hid(vk).unwrap_or_else(|| panic!("missing VK mapping for {vk:#04X}"));
            assert_eq!(
                hid_to_vk(hid),
                Some(vk),
                "round trip failed for VK {vk:#04X} (HID {hid:#04X})"
            );
        }
    }

    #[test]
    fn individual_mappings_match_the_spec() {
        assert_eq!(vk_to_hid(0x41), Some(usage::A));
        assert_eq!(vk_to_hid(0x5A), Some(usage::Z));
        assert_eq!(vk_to_hid(0x30), Some(usage::NUM_0));
        assert_eq!(vk_to_hid(0x31), Some(usage::NUM_1));
        assert_eq!(vk_to_hid(0xBA), Some(usage::SEMICOLON));
        assert_eq!(vk_to_hid(0xDE), Some(usage::QUOTE));
        assert_eq!(vk_to_hid(0x20), Some(usage::SPACE));
        assert_eq!(vk_to_hid(0x0D), Some(usage::ENTER));
        assert_eq!(vk_to_hid(0x25), Some(usage::LEFT_ARROW));
        assert_eq!(vk_to_hid(0x27), Some(usage::RIGHT_ARROW));
        assert_eq!(vk_to_hid(0x70), Some(usage::F1));
        assert_eq!(vk_to_hid(0x7B), Some(usage::F12));
        assert_eq!(vk_to_hid(0xA0), Some(usage::LEFT_SHIFT));
        assert_eq!(vk_to_hid(0xA1), Some(usage::RIGHT_SHIFT));
        assert_eq!(vk_to_hid(0xA2), Some(usage::LEFT_CTRL));
        assert_eq!(vk_to_hid(0xA4), Some(usage::LEFT_ALT));
        assert_eq!(vk_to_hid(0x5B), Some(usage::LEFT_META));
        assert_eq!(vk_to_hid(0x5C), Some(usage::RIGHT_META));
        assert_eq!(vk_to_hid(0x60), Some(usage::KP_0));
        assert_eq!(vk_to_hid(0x61), Some(usage::KP_1));
        assert_eq!(vk_to_hid(0x6E), Some(usage::KP_PERIOD));
        assert_eq!(vk_to_hid(0x6F), Some(usage::KP_DIVIDE));
        assert_eq!(vk_to_hid(0x90), Some(usage::NUM_LOCK));
    }

    #[test]
    fn reverse_mappings_match_the_spec() {
        assert_eq!(hid_to_vk(usage::A), Some(0x41));
        assert_eq!(hid_to_vk(usage::NUM_0), Some(0x30));
        assert_eq!(hid_to_vk(usage::SPACE), Some(0x20));
        assert_eq!(hid_to_vk(usage::LEFT_META), Some(0x5B));
        assert_eq!(hid_to_vk(usage::RIGHT_META), Some(0x5C));
        assert_eq!(hid_to_vk(usage::KP_PERIOD), Some(0x6E));
        assert_eq!(hid_to_vk(usage::PRINT_SCREEN), Some(0x2C));
        assert_eq!(hid_to_vk(usage::APPLICATION), Some(0x5D));
    }

    #[test]
    fn table_has_no_duplicates() {
        for (index, (vk, hid)) in KEY_TABLE.iter().enumerate() {
            for (other_vk, other_hid) in &KEY_TABLE[index + 1..] {
                assert_ne!(vk, other_vk, "duplicate VK {vk:#04X} in table");
                assert_ne!(hid, other_hid, "duplicate HID usage {hid:#04X} in table");
            }
        }
    }

    #[test]
    fn unmapped_codes_are_none() {
        assert_eq!(vk_to_hid(0x00), None);
        assert_eq!(vk_to_hid(0xFF), None);
        assert_eq!(hid_to_vk(0x00), None);
        assert_eq!(hid_to_vk(0xFF), None);
        // Media keys are not part of the protocol yet.
        assert_eq!(vk_to_hid(0xAD), None);
    }
}
