import Foundation

// MARK: - Codable mirrors of the Rust core's status/config JSON

public enum FLMode: String, Codable, CaseIterable {
    case client
    case server

    public var title: String {
        switch self {
        case .client: return "Client"
        case .server: return "Server"
        }
    }

    public var explanation: String {
        switch self {
        case .server: return "This computer’s keyboard and mouse drive the others."
        case .client: return "This computer is controlled from the other machine."
        }
    }
}

public enum FLEdge: String, Codable, CaseIterable {
    case left, right, top, bottom

    public var title: String {
        switch self {
        case .left: return "Left"
        case .right: return "Right"
        case .top: return "Top"
        case .bottom: return "Bottom"
        }
    }

    public var symbol: String {
        switch self {
        case .left: return "arrow.left.square"
        case .right: return "arrow.right.square"
        case .top: return "arrow.up.square"
        case .bottom: return "arrow.down.square"
        }
    }

    public var opposite: FLEdge {
        switch self {
        case .left: return .right
        case .right: return .left
        case .top: return .bottom
        case .bottom: return .top
        }
    }
}

public struct FLLocalInfo: Codable {
    public var id: String
    public var name: String
    public var fingerprint: String
    public var addrs: [String]
    public var port: Int
}

public struct FLPeerInfo: Codable {
    public var id: String
    public var name: String
    public var version: String?
    public var fingerprint: String?
    public var addr: String
    public var paired: Bool
    public var sinceMs: UInt64

    enum CodingKeys: String, CodingKey {
        case id, name, version, fingerprint, addr, paired
        case sinceMs = "since_ms"
    }
}

public struct FLPairingInfo: Codable {
    public var code: String?
    public var attemptsLeft: Int
    public var expiresInMs: UInt64
    public var awaitingInput: Bool

    enum CodingKeys: String, CodingKey {
        case code
        case attemptsLeft = "attempts_left"
        case expiresInMs = "expires_in_ms"
        case awaitingInput = "awaiting_input"
    }

    public var formattedCode: String? {
        guard let code, code.count == 6 else { return code }
        let mid = code.index(code.startIndex, offsetBy: 3)
        return "\(code[code.startIndex..<mid]) \(code[mid...])"
    }
}

public struct FLHotkey: Codable {
    public var modifiers: UInt8
    public var usage: UInt16
}

public struct FLDevice: Codable, Identifiable, Hashable {
    public var id: String
    public var name: String
    public var fingerprint: String
    public var addr: String?
    public var port: Int
    public var role: String?
    public var version: String?
    public var lastSeenMs: UInt64

    enum CodingKeys: String, CodingKey {
        case id, name, fingerprint, addr, port, role, version
        case lastSeenMs = "last_seen_ms"
    }

    public var isServer: Bool { role == nil || role == "server" }
}

public struct FLPairedDevice: Codable, Identifiable, Hashable {
    public var id: String
    public var name: String
    public var fingerprint: String
    public var lastAddr: String?
    public var lastSeenMs: UInt64

    enum CodingKeys: String, CodingKey {
        case id, name, fingerprint
        case lastAddr = "last_addr"
        case lastSeenMs = "last_seen_ms"
    }

    public var port: Int {
        guard let lastAddr, let colon = lastAddr.lastIndex(of: ":") else {
            return 24800
        }
        return Int(lastAddr[lastAddr.index(after: colon)...]) ?? 24800
    }

    public var host: String {
        guard let lastAddr, let colon = lastAddr.lastIndex(of: ":") else {
            return lastAddr ?? ""
        }
        return String(lastAddr[..<colon])
    }
}

public struct FLMetrics: Codable {
    public var rttMs: Double
    public var rxBytes: UInt64
    public var txBytes: UInt64
    public var eventsOut: UInt64
    public var eventsIn: UInt64
    public var connectedSinceMs: UInt64?
    public var uptimeMs: UInt64

    enum CodingKeys: String, CodingKey {
        case rttMs = "rtt_ms"
        case rxBytes = "rx_bytes"
        case txBytes = "tx_bytes"
        case eventsOut = "events_out"
        case eventsIn = "events_in"
        case connectedSinceMs = "connected_since_ms"
        case uptimeMs = "uptime_ms"
    }
}

/// Full status snapshot (`flowlink_status_json`).
public struct FLStatus: Codable {
    public var app: String
    public var version: String
    public var mode: FLMode
    public var state: String
    public var error: String?
    public var reconnectInMs: UInt64?
    public var local: FLLocalInfo
    public var peer: FLPeerInfo?
    public var pairing: FLPairingInfo?
    public var remoteActive: Bool
    /// This side owns the shared cursor.
    public var focused: Bool?
    /// This side's input is being forwarded to the peer.
    public var forwarding: Bool?
    public var remoteEdge: FLEdge?
    public var edge: FLEdge
    /// Effective local edge facing the peer for this session (synced with the
    /// other computer; may differ from the configured `edge` after a peer
    /// topology update).
    public var peerEdge: FLEdge?
    public var clipboardEnabled: Bool
    /// Core-enforced clipboard text limit (bytes); shells honor the same cap.
    public var clipboardMaxBytes: Int?
    public var hotkey: FLHotkey
    public var discovered: [FLDevice]
    public var pairedDevices: [FLPairedDevice]
    public var metrics: FLMetrics
    public var listening: Bool

    enum CodingKeys: String, CodingKey {
        case app, version, mode, state, error, local, peer, pairing, edge, hotkey, discovered, metrics, listening
        case focused, forwarding
        case reconnectInMs = "reconnect_in_ms"
        case remoteActive = "remote_active"
        case remoteEdge = "remote_edge"
        case peerEdge = "peer_edge"
        case clipboardEnabled = "clipboard_enabled"
        case clipboardMaxBytes = "clipboard_max_bytes"
        case pairedDevices = "paired_devices"
    }

    // MARK: Derived UI state

    public var isConnected: Bool { state == "connected" }
    public var isRunning: Bool { state != "off" }
    /// True while this Mac owns the shared cursor.
    public var ownsCursor: Bool { focused ?? !remoteActive }
    /// True while this Mac's input is being captured for the other computer.
    public var isForwarding: Bool { forwarding ?? remoteActive }

    public var stateLabel: String {
        switch state {
        case "off": return "Off"
        case "listening": return "Listening"
        case "connecting": return "Connecting…"
        case "handshake": return "Handshaking…"
        case "awaiting_pair": return "Pairing needed"
        case "pairing": return "Pairing…"
        case "connected": return "Connected"
        case "reconnecting": return "Reconnecting…"
        case "error": return "Error"
        default: return state
        }
    }

    public var stateSymbol: String {
        switch state {
        case "connected": return "checkmark.circle.fill"
        case "error": return "exclamationmark.triangle.fill"
        case "off": return "circle.slash"
        default: return "arrow.triangle.2.circlepath"
        }
    }
}

public struct FLNotice: Identifiable {
    public let id = UUID()
    public let kind: String
    public let message: String
    public let date: Date
}
