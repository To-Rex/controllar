import SwiftUI

// MARK: - Status pill

public struct StatusPill: View {
    public let status: FLStatus?

    public init(status: FLStatus?) {
        self.status = status
    }

    public var body: some View {
        let tint = status?.tint ?? DS.idle
        let label = status?.stateLabel ?? "Starting…"
        HStack(spacing: 7) {
            Circle()
                .fill(tint)
                .frame(width: 8, height: 8)
            Text(label)
                .font(.system(.caption, weight: .semibold))
                .foregroundStyle(DS.text)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(tint.opacity(0.14), in: Capsule())
        .overlay(Capsule().stroke(tint.opacity(0.35), lineWidth: 1))
    }
}

// MARK: - Small building blocks

public struct SectionHeader: View {
    public let title: String
    public init(_ title: String) { self.title = title }

    public var body: some View {
        Text(title.uppercased())
            .font(.system(.caption2, weight: .semibold))
            .tracking(0.8)
            .foregroundStyle(DS.textFaint)
    }
}

public struct StatRow: View {
    public let label: String
    public let value: String
    public var mono: Bool = false

    public init(label: String, value: String, mono: Bool = false) {
        self.label = label
        self.value = value
        self.mono = mono
    }

    public var body: some View {
        HStack {
            Text(label)
                .font(.system(.callout))
                .foregroundStyle(DS.textDim)
            Spacer()
            Text(value)
                .font(mono ? .system(.callout, design: .monospaced) : .system(.callout, weight: .medium))
                .foregroundStyle(DS.text)
                .textSelection(.enabled)
        }
    }
}

public struct KeyCap: View {
    public let text: String
    public init(_ text: String) { self.text = text }

    public var body: some View {
        Text(text)
            .font(.system(.callout, design: .rounded).weight(.semibold))
            .foregroundStyle(DS.text)
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).stroke(DS.border, lineWidth: 1))
            .shadow(color: .black.opacity(0.06), radius: 1, y: 1)
    }
}

public struct FingerprintText: View {
    public let fingerprint: String
    public var compact: Bool = true

    public init(fingerprint: String, compact: Bool = true) {
        self.fingerprint = fingerprint
        self.compact = compact
    }

    public var body: some View {
        let display = Self.format(fingerprint, compact: compact)
        Text(display)
            .font(.system(.caption, design: .monospaced))
            .foregroundStyle(DS.textDim)
            .textSelection(.enabled)
    }

    public static func format(_ fingerprint: String, compact: Bool) -> String {
        guard fingerprint.count == 64 else { return fingerprint }
        var groups: [String] = []
        var index = fingerprint.startIndex
        while index < fingerprint.endIndex {
            let next = fingerprint.index(index, offsetBy: 4, limitedBy: fingerprint.endIndex) ?? fingerprint.endIndex
            groups.append(String(fingerprint[index..<next]).uppercased())
            index = next
        }
        if compact {
            return "\(groups.first ?? "")…\(groups.last ?? "")"
        }
        return groups.joined(separator: " ")
    }
}

// MARK: - Empty state

public struct EmptyHint: View {
    public let symbol: String
    public let title: String
    public let message: String

    public init(symbol: String, title: String, message: String) {
        self.symbol = symbol
        self.title = title
        self.message = message
    }

    public var body: some View {
        VStack(spacing: 8) {
            Image(systemName: symbol)
                .font(.system(size: 26, weight: .regular))
                .foregroundStyle(DS.textFaint)
            Text(title)
                .font(.system(.callout, weight: .semibold))
                .foregroundStyle(DS.text)
            Text(message)
                .font(.system(.caption))
                .foregroundStyle(DS.textDim)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 26)
    }
}

// MARK: - Notice list

public struct NoticeTicker: View {
    public let notices: [FLNotice]

    public init(notices: [FLNotice]) {
        self.notices = notices
    }

    public var body: some View {
        if let notice = notices.last {
            HStack(spacing: 8) {
                Image(systemName: symbol(for: notice.kind))
                    .foregroundStyle(color(for: notice.kind))
                Text(notice.message)
                    .font(.system(.caption))
                    .foregroundStyle(DS.textDim)
                    .lineLimit(2)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(DS.cardAlt, in: RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: DS.radiusSmall, style: .continuous)
                    .stroke(DS.border, lineWidth: 1)
            )
        }
    }

    private func symbol(for kind: String) -> String {
        switch kind {
        case "error": return "exclamationmark.triangle.fill"
        case "pairing": return "lock.shield"
        case "connected": return "checkmark.circle.fill"
        case "control": return "cursorarrow.rays"
        default: return "info.circle"
        }
    }

    private func color(for kind: String) -> Color {
        switch kind {
        case "error": return DS.err
        case "pairing": return DS.warn
        case "connected": return DS.ok
        default: return DS.textDim
        }
    }
}

// MARK: - Byte formatting

public enum ByteFormat {
    public static func human(_ bytes: UInt64) -> String {
        let units = ["B", "KB", "MB", "GB"]
        var value = Double(bytes)
        var unit = 0
        while value >= 1024, unit < units.count - 1 {
            value /= 1024
            unit += 1
        }
        return unit == 0 ? "\(Int(value)) \(units[unit])" : String(format: "%.1f %@", value, units[unit])
    }

    public static func duration(_ ms: UInt64) -> String {
        let seconds = Int(ms / 1000)
        let h = seconds / 3600
        let m = (seconds % 3600) / 60
        let s = seconds % 60
        if h > 0 { return "\(h)h \(m)m" }
        if m > 0 { return "\(m)m \(s)s" }
        return "\(s)s"
    }
}
