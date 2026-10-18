import CFlowLinkCore
import CoreGraphics
import Foundation

/// Bidirectional mapping between macOS virtual key codes (CGKeyCode) and USB
/// HID keyboard usages — the platform-neutral representation on the wire.
public enum KeyCodes {

    /// (CGKeyCode, HID usage) — kVK_* constants with their HID equivalents.
    public static let table: [(cg: UInt16, hid: UInt16)] = [
        // Letters
        (0x00, 0x04), (0x0B, 0x05), (0x08, 0x06), (0x02, 0x07), (0x0E, 0x08),
        (0x03, 0x09), (0x05, 0x0A), (0x04, 0x0B), (0x22, 0x0C), (0x26, 0x0D),
        (0x28, 0x0E), (0x25, 0x0F), (0x2E, 0x10), (0x2D, 0x11), (0x1F, 0x12),
        (0x23, 0x13), (0x0C, 0x14), (0x0F, 0x15), (0x01, 0x16), (0x11, 0x17),
        (0x20, 0x18), (0x09, 0x19), (0x0D, 0x1A), (0x07, 0x1B), (0x10, 0x1C),
        (0x06, 0x1D),
        // Digits & punctuation (US layout physical positions)
        (0x12, 0x1E), (0x13, 0x1F), (0x14, 0x20), (0x15, 0x21), (0x17, 0x22),
        (0x16, 0x23), (0x1A, 0x24), (0x1C, 0x25), (0x19, 0x26), (0x1D, 0x27),
        (0x18, 0x2E), (0x1B, 0x2D), (0x21, 0x2F), (0x1E, 0x30), (0x2A, 0x31),
        (0x29, 0x33), (0x27, 0x34), (0x32, 0x35), (0x2B, 0x36), (0x2F, 0x37),
        (0x2C, 0x38),
        // Editing & whitespace
        (0x24, 0x28), (0x35, 0x29), (0x33, 0x2A), (0x30, 0x2B), (0x31, 0x2C),
        (0x39, 0x39), (0x72, 0x49), (0x73, 0x4A), (0x74, 0x4B), (0x75, 0x4C),
        (0x77, 0x4D), (0x79, 0x4E),
        // Arrows
        (0x7B, 0x50), (0x7C, 0x4F), (0x7D, 0x51), (0x7E, 0x52),
        // Function row
        (0x7A, 0x3A), (0x78, 0x3B), (0x63, 0x3C), (0x76, 0x3D), (0x60, 0x3E),
        (0x61, 0x3F), (0x62, 0x40), (0x64, 0x41), (0x65, 0x42), (0x6D, 0x43),
        (0x67, 0x44), (0x6F, 0x45),
        // Modifiers (left/right variants)
        (0x3B, 0xE0), (0x38, 0xE1), (0x3A, 0xE2), (0x37, 0xE3),
        (0x3E, 0xE4), (0x3C, 0xE5), (0x3D, 0xE6), (0x36, 0xE7),
        // Keypad
        (0x41, 0x63), (0x43, 0x55), (0x45, 0x57), (0x47, 0x53), (0x4B, 0x54),
        (0x4C, 0x58), (0x4E, 0x56), (0x52, 0x62), (0x53, 0x59), (0x54, 0x5A),
        (0x55, 0x5B), (0x56, 0x5C), (0x57, 0x5D), (0x58, 0x5E), (0x59, 0x5F),
        (0x5B, 0x60), (0x5C, 0x61),
    ]

    private static let cgToHidMap: [UInt16: UInt16] = {
        var map = [UInt16: UInt16](minimumCapacity: table.count)
        for pair in table { map[pair.cg] = pair.hid }
        return map
    }()

    private static let hidToCgMap: [UInt16: UInt16] = {
        var map = [UInt16: UInt16](minimumCapacity: table.count)
        for pair in table where map[pair.hid] == nil { map[pair.hid] = pair.cg }
        return map
    }()

    public static func hid(fromCGKeyCode code: UInt16) -> UInt16? {
        cgToHidMap[code]
    }

    public static func cgKeyCode(fromHID usage: UInt16) -> UInt16? {
        hidToCgMap[usage]
    }

    // MARK: Modifiers

    /// Map CGEventFlags to the core's modifier bit set.
    public static func modifiers(from flags: CGEventFlags) -> UInt8 {
        var result: UInt8 = 0
        if flags.contains(.maskShift) { result |= FLModC.shift }
        if flags.contains(.maskControl) { result |= FLModC.ctrl }
        if flags.contains(.maskAlternate) { result |= FLModC.alt }
        if flags.contains(.maskCommand) { result |= FLModC.meta }
        return result
    }

    /// CGEventFlags equivalent of the core's modifier bits.
    public static func eventFlags(from modifiers: UInt8) -> CGEventFlags {
        var flags: CGEventFlags = []
        if modifiers & FLModC.shift != 0 { flags.insert(.maskShift) }
        if modifiers & FLModC.ctrl != 0 { flags.insert(.maskControl) }
        if modifiers & FLModC.alt != 0 { flags.insert(.maskAlternate) }
        if modifiers & FLModC.meta != 0 { flags.insert(.maskCommand) }
        return flags
    }

    /// HID uses for the four left-side modifier keys (used to materialize a
    /// modifier state before injecting a key).
    public enum ModifierKey {
        public static let shift: UInt16 = 0xE1
        public static let ctrl: UInt16 = 0xE0
        public static let alt: UInt16 = 0xE2
        public static let meta: UInt16 = 0xE3
    }

    public static func modifierUsage(for flag: UInt8) -> UInt16? {
        switch flag {
        case FLModC.shift: return ModifierKey.shift
        case FLModC.ctrl: return ModifierKey.ctrl
        case FLModC.alt: return ModifierKey.alt
        case FLModC.meta: return ModifierKey.meta
        default: return nil
        }
    }

    // MARK: Display

    /// Short human name for a HID usage (hotkey UI).
    public static func displayName(for usage: UInt16) -> String {
        switch usage {
        case 0x2C: return "Space"
        case 0x28: return "Return"
        case 0x29: return "Esc"
        case 0x2A: return "Delete"
        case 0x2B: return "Tab"
        case 0x39: return "Caps Lock"
        case 0x4A: return "Home"
        case 0x4D: return "End"
        case 0x4B: return "Page Up"
        case 0x4E: return "Page Down"
        case 0x49: return "Insert"
        case 0x4C: return "Forward Delete"
        case 0x50: return "←"
        case 0x4F: return "→"
        case 0x51: return "↓"
        case 0x52: return "↑"
        case 0x04...0x1D:
            let letters = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            return String(letters[Int(usage - 0x04)])
        case 0x1E...0x26:
            return String(Int(usage - 0x1E) + 1)
        case 0x27: return "0"
        case 0x3A...0x45:
            return "F\(Int(usage - 0x3A) + 1)"
        default:
            return String(format: "Key 0x%02X", usage)
        }
    }

    /// Modifier symbols in the conventional macOS order (⌃⌥⇧⌘).
    public static func modifierSymbols(_ modifiers: UInt8) -> String {
        var result = ""
        if modifiers & FLModC.ctrl != 0 { result += "⌃" }
        if modifiers & FLModC.alt != 0 { result += "⌥" }
        if modifiers & FLModC.shift != 0 { result += "⇧" }
        if modifiers & FLModC.meta != 0 { result += "⌘" }
        return result
    }

    public static func hotkeyDisplay(modifiers: UInt8, usage: UInt16) -> String {
        "\(modifierSymbols(modifiers)) \(displayName(for: usage))"
    }
}
