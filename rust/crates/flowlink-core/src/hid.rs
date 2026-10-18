//! USB HID keyboard usage ids — the platform-neutral key representation used
//! on the wire. Each native shell keeps its own bidirectional mapping table.
//!
//! Reference: USB HID Usage Tables 1.12, section 10 (Keyboard/Keypad page 0x07).

/// Keyboard page usage ids.
pub mod usage {
    pub const A: u16 = 0x04;
    pub const B: u16 = 0x05;
    pub const C: u16 = 0x06;
    pub const D: u16 = 0x07;
    pub const E: u16 = 0x08;
    pub const F: u16 = 0x09;
    pub const G: u16 = 0x0A;
    pub const H: u16 = 0x0B;
    pub const I: u16 = 0x0C;
    pub const J: u16 = 0x0D;
    pub const K: u16 = 0x0E;
    pub const L: u16 = 0x0F;
    pub const M: u16 = 0x10;
    pub const N: u16 = 0x11;
    pub const O: u16 = 0x12;
    pub const P: u16 = 0x13;
    pub const Q: u16 = 0x14;
    pub const R: u16 = 0x15;
    pub const S: u16 = 0x16;
    pub const T: u16 = 0x17;
    pub const U: u16 = 0x18;
    pub const V: u16 = 0x19;
    pub const W: u16 = 0x1A;
    pub const X: u16 = 0x1B;
    pub const Y: u16 = 0x1C;
    pub const Z: u16 = 0x1D;

    pub const NUM_1: u16 = 0x1E;
    pub const NUM_2: u16 = 0x1F;
    pub const NUM_3: u16 = 0x20;
    pub const NUM_4: u16 = 0x21;
    pub const NUM_5: u16 = 0x22;
    pub const NUM_6: u16 = 0x23;
    pub const NUM_7: u16 = 0x24;
    pub const NUM_8: u16 = 0x25;
    pub const NUM_9: u16 = 0x26;
    pub const NUM_0: u16 = 0x27;

    pub const ENTER: u16 = 0x28;
    pub const ESCAPE: u16 = 0x29;
    pub const BACKSPACE: u16 = 0x2A;
    pub const TAB: u16 = 0x2B;
    pub const SPACE: u16 = 0x2C;
    pub const MINUS: u16 = 0x2D;
    pub const EQUALS: u16 = 0x2E;
    pub const LEFT_BRACKET: u16 = 0x2F;
    pub const RIGHT_BRACKET: u16 = 0x30;
    pub const BACKSLASH: u16 = 0x31;
    pub const SEMICOLON: u16 = 0x33;
    pub const QUOTE: u16 = 0x34;
    pub const BACKQUOTE: u16 = 0x35;
    pub const COMMA: u16 = 0x36;
    pub const PERIOD: u16 = 0x37;
    pub const SLASH: u16 = 0x38;
    pub const CAPS_LOCK: u16 = 0x39;

    pub const F1: u16 = 0x3A;
    pub const F2: u16 = 0x3B;
    pub const F3: u16 = 0x3C;
    pub const F4: u16 = 0x3D;
    pub const F5: u16 = 0x3E;
    pub const F6: u16 = 0x3F;
    pub const F7: u16 = 0x40;
    pub const F8: u16 = 0x41;
    pub const F9: u16 = 0x42;
    pub const F10: u16 = 0x43;
    pub const F11: u16 = 0x44;
    pub const F12: u16 = 0x45;

    pub const PRINT_SCREEN: u16 = 0x46;
    pub const SCROLL_LOCK: u16 = 0x47;
    pub const PAUSE: u16 = 0x48;
    pub const INSERT: u16 = 0x49;
    pub const HOME: u16 = 0x4A;
    pub const PAGE_UP: u16 = 0x4B;
    pub const DELETE: u16 = 0x4C;
    pub const END: u16 = 0x4D;
    pub const PAGE_DOWN: u16 = 0x4E;
    pub const RIGHT_ARROW: u16 = 0x4F;
    pub const LEFT_ARROW: u16 = 0x50;
    pub const DOWN_ARROW: u16 = 0x51;
    pub const UP_ARROW: u16 = 0x52;
    pub const NUM_LOCK: u16 = 0x53;

    pub const KP_DIVIDE: u16 = 0x54;
    pub const KP_MULTIPLY: u16 = 0x55;
    pub const KP_SUBTRACT: u16 = 0x56;
    pub const KP_ADD: u16 = 0x57;
    pub const KP_ENTER: u16 = 0x58;
    pub const KP_1: u16 = 0x59;
    pub const KP_2: u16 = 0x5A;
    pub const KP_3: u16 = 0x5B;
    pub const KP_4: u16 = 0x5C;
    pub const KP_5: u16 = 0x5D;
    pub const KP_6: u16 = 0x5E;
    pub const KP_7: u16 = 0x5F;
    pub const KP_8: u16 = 0x60;
    pub const KP_9: u16 = 0x61;
    pub const KP_0: u16 = 0x62;
    pub const KP_PERIOD: u16 = 0x63;
    pub const APPLICATION: u16 = 0x65;

    pub const LEFT_CTRL: u16 = 0xE0;
    pub const LEFT_SHIFT: u16 = 0xE1;
    pub const LEFT_ALT: u16 = 0xE2;
    pub const LEFT_META: u16 = 0xE3;
    pub const RIGHT_CTRL: u16 = 0xE4;
    pub const RIGHT_SHIFT: u16 = 0xE5;
    pub const RIGHT_ALT: u16 = 0xE6;
    pub const RIGHT_META: u16 = 0xE7;
}

/// All modifier usages, in order.
pub const MODIFIER_USAGES: [u16; 8] = [
    usage::LEFT_CTRL,
    usage::LEFT_SHIFT,
    usage::LEFT_ALT,
    usage::LEFT_META,
    usage::RIGHT_CTRL,
    usage::RIGHT_SHIFT,
    usage::RIGHT_ALT,
    usage::RIGHT_META,
];

/// True when the usage id is one of the eight keyboard modifiers.
pub fn is_modifier(usage: u16) -> bool {
    (0xE0..=0xE7).contains(&usage)
}

/// The modifier flag a modifier usage sets (both left and right variants map
/// to the same logical flag, mirroring `CGEventFlags`/VK semantics).
pub fn modifier_flag(usage: u16) -> Option<u8> {
    use crate::types::Modifiers;
    match usage {
        usage::LEFT_CTRL | usage::RIGHT_CTRL => Some(Modifiers::CTRL),
        usage::LEFT_SHIFT | usage::RIGHT_SHIFT => Some(Modifiers::SHIFT),
        usage::LEFT_ALT | usage::RIGHT_ALT => Some(Modifiers::ALT),
        usage::LEFT_META | usage::RIGHT_META => Some(Modifiers::META),
        _ => None,
    }
}

/// Human-readable name for common usages (hotkey recorder UI + logs).
pub fn usage_name(usage: u16) -> Option<&'static str> {
    use usage::*;
    Some(match usage {
        A => "A",
        B => "B",
        C => "C",
        D => "D",
        E => "E",
        F => "F",
        G => "G",
        H => "H",
        I => "I",
        J => "J",
        K => "K",
        L => "L",
        M => "M",
        N => "N",
        O => "O",
        P => "P",
        Q => "Q",
        R => "R",
        S => "S",
        T => "T",
        U => "U",
        V => "V",
        W => "W",
        X => "X",
        Y => "Y",
        Z => "Z",
        NUM_0 => "0",
        NUM_1 => "1",
        NUM_2 => "2",
        NUM_3 => "3",
        NUM_4 => "4",
        NUM_5 => "5",
        NUM_6 => "6",
        NUM_7 => "7",
        NUM_8 => "8",
        NUM_9 => "9",
        ENTER => "Return",
        ESCAPE => "Escape",
        BACKSPACE => "Backspace",
        TAB => "Tab",
        SPACE => "Space",
        MINUS => "-",
        EQUALS => "=",
        LEFT_BRACKET => "[",
        RIGHT_BRACKET => "]",
        BACKSLASH => "\\",
        SEMICOLON => ";",
        QUOTE => "'",
        BACKQUOTE => "`",
        COMMA => ",",
        PERIOD => ".",
        SLASH => "/",
        CAPS_LOCK => "Caps Lock",
        F1 => "F1",
        F2 => "F2",
        F3 => "F3",
        F4 => "F4",
        F5 => "F5",
        F6 => "F6",
        F7 => "F7",
        F8 => "F8",
        F9 => "F9",
        F10 => "F10",
        F11 => "F11",
        F12 => "F12",
        PRINT_SCREEN => "Print Screen",
        SCROLL_LOCK => "Scroll Lock",
        PAUSE => "Pause",
        INSERT => "Insert",
        HOME => "Home",
        PAGE_UP => "Page Up",
        DELETE => "Delete",
        END => "End",
        PAGE_DOWN => "Page Down",
        RIGHT_ARROW => "Right",
        LEFT_ARROW => "Left",
        DOWN_ARROW => "Down",
        UP_ARROW => "Up",
        LEFT_CTRL | RIGHT_CTRL => "Ctrl",
        LEFT_SHIFT | RIGHT_SHIFT => "Shift",
        LEFT_ALT | RIGHT_ALT => "Alt",
        LEFT_META | RIGHT_META => "Meta",
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::Modifiers;

    #[test]
    fn modifier_flags_are_symmetric() {
        assert_eq!(modifier_flag(usage::LEFT_CTRL), Some(Modifiers::CTRL));
        assert_eq!(modifier_flag(usage::RIGHT_CTRL), Some(Modifiers::CTRL));
        assert_eq!(modifier_flag(usage::LEFT_META), Some(Modifiers::META));
        assert!(modifier_flag(usage::A).is_none());
        assert!(is_modifier(usage::RIGHT_ALT));
        assert!(!is_modifier(usage::ENTER));
    }
}
