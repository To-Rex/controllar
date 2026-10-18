import AppKit
import SwiftUI

// MARK: - Color helpers

public extension NSColor {
    convenience init(hex: UInt32, alpha: CGFloat = 1) {
        let r = CGFloat((hex >> 16) & 0xFF) / 255.0
        let g = CGFloat((hex >> 8) & 0xFF) / 255.0
        let b = CGFloat(hex & 0xFF) / 255.0
        self.init(srgbRed: r, green: g, blue: b, alpha: alpha)
    }
}

public extension Color {
    /// Appearance-aware color (light / dark).
    init(light: UInt32, dark: UInt32) {
        self.init(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            return NSColor(hex: isDark ? dark : light)
        })
    }
}

// MARK: - Design tokens

/// FlowLink's design system: a small, opinionated set of tokens shared by
/// every screen (accent, surfaces, status colors, metrics).
public enum DS {
    // Brand
    public static let accent = Color(light: 0x6553F5, dark: 0x8A7BFF)
    public static let accentSoft = Color(light: 0xECE9FF, dark: 0x272150)
    public static let accentText = Color(light: 0xFFFFFF, dark: 0x0D0B1A)

    // Surfaces
    public static let bg = Color(light: 0xF5F6FA, dark: 0x0E1015)
    public static let sidebar = Color(light: 0xEEEFF5, dark: 0x12151B)
    public static let card = Color(light: 0xFFFFFF, dark: 0x171A21)
    public static let cardAlt = Color(light: 0xFAFAFD, dark: 0x1D2129)
    public static let border = Color(light: 0xE3E5EE, dark: 0x272B34)
    public static let hover = Color(light: 0xEDEEF4, dark: 0x1F232B)

    // Text
    public static let text = Color(light: 0x15171E, dark: 0xF2F3F7)
    public static let textDim = Color(light: 0x6D7482, dark: 0x9AA2B3)
    public static let textFaint = Color(light: 0x9AA0AC, dark: 0x666E7E)

    // Status
    public static let ok = Color(light: 0x1B9D5E, dark: 0x3ED57F)
    public static let okSoft = Color(light: 0xE4F7EC, dark: 0x143024)
    public static let warn = Color(light: 0xB56E00, dark: 0xF0A93A)
    public static let warnSoft = Color(light: 0xFCF1DD, dark: 0x33270F)
    public static let err = Color(light: 0xCE3A4C, dark: 0xFF6B7A)
    public static let errSoft = Color(light: 0xFCE9EB, dark: 0x351A1E)
    public static let idle = Color(light: 0x767E8C, dark: 0x8B93A3)

    // Metrics
    public static let radius: CGFloat = 16
    public static let radiusSmall: CGFloat = 10
    public static let radiusPill: CGFloat = 999
    public static let gutter: CGFloat = 20
    public static let cardPadding: CGFloat = 18

    // Typography
    public static let mono = Font.system(.body, design: .monospaced).weight(.semibold)
    public static let monoLarge = Font.system(size: 34, weight: .bold, design: .monospaced)
    public static let monoSmall = Font.system(.caption, design: .monospaced)
}

// MARK: - Status styling

public extension FLStatus {
    var tint: Color {
        switch state {
        case "connected": return DS.ok
        case "error": return DS.err
        case "off": return DS.idle
        case "listening", "awaiting_pair", "pairing": return DS.warn
        default: return DS.accent
        }
    }
}

// MARK: - Common modifiers

public struct CardBackground: ViewModifier {
    public var padding: CGFloat = DS.cardPadding
    public var tint: Color = DS.card

    public func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(tint, in: RoundedRectangle(cornerRadius: DS.radius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: DS.radius, style: .continuous)
                    .stroke(DS.border, lineWidth: 1)
            )
            .shadow(color: Color.black.opacity(0.05), radius: 10, y: 2)
    }
}

public extension View {
    func card(padding: CGFloat = DS.cardPadding, tint: Color = DS.card) -> some View {
        modifier(CardBackground(padding: padding, tint: tint))
    }
}

// MARK: - Button styles

public struct PrimaryButtonStyle: ButtonStyle {
    public init() {}
    public func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(.body, weight: .semibold))
            .foregroundStyle(DS.accentText)
            .padding(.horizontal, 16)
            .padding(.vertical, 9)
            .background(DS.accent, in: RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous))
            .opacity(configuration.isPressed ? 0.85 : 1)
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

public struct SecondaryButtonStyle: ButtonStyle {
    public init() {}
    public func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(.body, weight: .medium))
            .foregroundStyle(DS.text)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(DS.hover, in: RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous)
                    .stroke(DS.border, lineWidth: 1)
            )
            .opacity(configuration.isPressed ? 0.8 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

public struct QuietButtonStyle: ButtonStyle {
    public init() {}
    public func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(.body, weight: .medium))
            .foregroundStyle(DS.textDim)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(configuration.isPressed ? DS.hover : .clear, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .contentShape(Rectangle())
    }
}
