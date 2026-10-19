import CFlowLinkCore
import CoreGraphics
import XCTest
@testable import FlowLinkKit

final class KeyCodesTests: XCTestCase {
    func testTableRoundTripsBothDirections() {
        for pair in KeyCodes.table {
            XCTAssertEqual(KeyCodes.hid(fromCGKeyCode: pair.cg), pair.hid)
        }
        // Inverse mapping picks the first cg code for a usage.
        XCTAssertEqual(KeyCodes.cgKeyCode(fromHID: 0x04), 0x00) // A
        XCTAssertEqual(KeyCodes.cgKeyCode(fromHID: 0xE1), 0x38) // left shift
        XCTAssertEqual(KeyCodes.cgKeyCode(fromHID: 0x28), 0x24) // return
    }

    func testKnownMappings() {
        // The letters are a well-established sequence.
        XCTAssertEqual(KeyCodes.hid(fromCGKeyCode: 0x00), 0x04) // A
        XCTAssertEqual(KeyCodes.hid(fromCGKeyCode: 0x06), 0x1D) // Z
        XCTAssertEqual(KeyCodes.hid(fromCGKeyCode: 0x31), 0x2C) // space
        XCTAssertEqual(KeyCodes.hid(fromCGKeyCode: 0x7B), 0x50) // left arrow
        XCTAssertEqual(KeyCodes.hid(fromCGKeyCode: 0x3B), 0xE0) // left control
        XCTAssertNil(KeyCodes.hid(fromCGKeyCode: 0xFFFF))
    }

    func testModifierConversion() {
        let flags: CGEventFlags = [.maskShift, .maskCommand]
        let mods = KeyCodes.modifiers(from: flags)
        XCTAssertEqual(mods & FLModC.shift, FLModC.shift)
        XCTAssertEqual(mods & FLModC.meta, FLModC.meta)
        XCTAssertEqual(mods & FLModC.ctrl, 0)

        let back = KeyCodes.eventFlags(from: FLModC.ctrl | FLModC.alt)
        XCTAssertTrue(back.contains(.maskControl))
        XCTAssertTrue(back.contains(.maskAlternate))
        XCTAssertFalse(back.contains(.maskShift))
    }

    func testHotkeyDisplay() {
        let display = KeyCodes.hotkeyDisplay(
            modifiers: FLModC.ctrl | FLModC.alt,
            usage: 0x2C
        )
        XCTAssertEqual(display, "⌃⌥ Space")
    }
}

final class StatusDecodingTests: XCTestCase {
    func testDecodesCoreStatusJSON() throws {
        // Shape produced by the Rust core (status.rs).
        let json = """
        {
          "app": "FlowLink",
          "version": "0.1.0",
          "mode": "server",
          "state": "connected",
          "error": null,
          "reconnect_in_ms": null,
          "local": {"id": "dev-1", "name": "Mac", "fingerprint": "aa", "addrs": ["10.0.0.2"], "port": 24800},
          "peer": {"id": "dev-2", "name": "PC", "fingerprint": null, "addr": "10.0.0.9:24800", "paired": true, "since_ms": 42},
          "pairing": {"code": "123456", "attempts_left": 3, "expires_in_ms": 90000, "awaiting_input": false},
          "remote_active": true,
          "remote_edge": null,
          "edge": "right",
          "clipboard_enabled": true,
          "hotkey": {"modifiers": 6, "usage": 44},
          "discovered": [{"id": "dev-3", "name": "Laptop", "fingerprint": "bb", "addr": "10.0.0.3", "port": 24800, "role": "server", "version": "0.1.0", "last_seen_ms": 7}],
          "paired_devices": [{"id": "dev-2", "name": "PC", "fingerprint": "cc", "last_addr": "10.0.0.9:24800", "last_seen_ms": 5}],
          "metrics": {"rtt_ms": 1.5, "rx_bytes": 100, "tx_bytes": 200, "events_out": 3, "events_in": 4, "connected_since_ms": 42, "uptime_ms": 1000},
          "listening": true
        }
        """
        let status = try JSONDecoder().decode(FLStatus.self, from: Data(json.utf8))
        XCTAssertEqual(status.mode, .server)
        XCTAssertTrue(status.isConnected)
        XCTAssertEqual(status.pairing?.formattedCode, "123 456")
        XCTAssertEqual(status.hotkey.modifiers, FLModC.ctrl | FLModC.alt)
        XCTAssertEqual(status.discovered.count, 1)
        XCTAssertTrue(status.discovered[0].isServer)
        XCTAssertEqual(status.metrics.rttMs, 1.5, accuracy: 0.001)
        XCTAssertEqual(status.pairedDevices.first?.host, "10.0.0.9")
        XCTAssertEqual(status.pairedDevices.first?.port, 24800)
        XCTAssertEqual(status.edge, .right)
        XCTAssertTrue(status.remoteActive)
    }

    func testEdgeSemantics() {
        XCTAssertEqual(FLEdge.right.opposite, .left)
        XCTAssertEqual(FLEdge.top.opposite, .bottom)
    }

    func testByteFormatting() {
        XCTAssertEqual(ByteFormat.human(512), "512 B")
        XCTAssertEqual(ByteFormat.human(2048), "2.0 KB")
        XCTAssertEqual(ByteFormat.duration(65_000), "1m 5s")
    }

    func testFingerprintFormatting() {
        let fp = String(repeating: "ab12", count: 16)
        let compact = FingerprintText.format(fp, compact: true)
        XCTAssertTrue(compact.contains("…"))
        let full = FingerprintText.format(fp, compact: false)
        XCTAssertTrue(full.contains("AB12"))
    }
}

final class CoreLinkTests: XCTestCase {
    func testThemeIndependent() {
        // The core exposes its version through the C ABI.
        let version = flowlink_version()
        XCTAssertNotNil(version)
        XCTAssertTrue(String(cString: version!).contains("."))
    }
}
