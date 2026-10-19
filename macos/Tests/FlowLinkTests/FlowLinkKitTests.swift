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
          "focused": false,
          "forwarding": true,
          "remote_edge": null,
          "edge": "right",
          "peer_edge": "left",
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
        XCTAssertEqual(status.peerEdge, .left)
        XCTAssertTrue(status.forwarding ?? false)
        XCTAssertTrue(status.isForwarding)
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

final class EdgePushTrackerTests: XCTestCase {
    private let width = 1920.0
    private let height = 1080.0

    /// Entered from a peer that exited `remoteEdge`, then moved well inside.
    private func armed(remoteEdge: FLEdge) -> EdgePushTracker {
        var tracker = EdgePushTracker()
        tracker.enter(remoteEdge: remoteEdge)
        let localEdge = remoteEdge.opposite
        let inside: (Double, Double, Double, Double)
        switch localEdge {
        case .left: inside = (300, 500, 10, 0)
        case .right: inside = (width - 300, 500, -10, 0)
        case .top: inside = (500, 300, 0, 10)
        case .bottom: inside = (500, height - 300, 0, -10)
        }
        XCTAssertNil(tracker.feed(
            x: inside.0, y: inside.1, dx: inside.2, dy: inside.3,
            width: width, height: height, nowNanos: 1
        ))
        return tracker
    }

    func testEnterMirrorsTheRemoteEdge() {
        var tracker = EdgePushTracker()
        tracker.enter(remoteEdge: .right)
        XCTAssertEqual(tracker.peerEdge, .left)
        tracker.enter(remoteEdge: .top)
        XCTAssertEqual(tracker.peerEdge, .bottom)
        tracker.clear()
        XCTAssertNil(tracker.peerEdge)
    }

    func testPushAtTheFacingEdgeHandsOff() {
        var tracker = armed(remoteEdge: .right) // local facing edge = left
        XCTAssertNil(tracker.feed(
            x: 0, y: 540, dx: -10, dy: 0,
            width: width, height: height, nowNanos: 2
        ))
        let result = tracker.feed(
            x: 0, y: 540, dx: -20, dy: 0,
            width: width, height: height, nowNanos: 3
        )
        XCTAssertEqual(result?.0, .left)
        XCTAssertEqual(result?.1 ?? 0, 0.5, accuracy: 0.01)
    }

    func testTailOfTheEntryMoveDoesNotBounceBack() {
        var tracker = EdgePushTracker()
        tracker.enter(remoteEdge: .right) // enters at x = 24, facing left
        XCTAssertNil(tracker.feed(
            x: 24, y: 540, dx: -24, dy: 0,
            width: width, height: height, nowNanos: 1
        ))
        XCTAssertNil(tracker.feed(
            x: 0, y: 540, dx: -30, dy: 0,
            width: width, height: height, nowNanos: 2
        ))
    }

    func testInwardTravelRearms() {
        var tracker = EdgePushTracker()
        tracker.enter(remoteEdge: .right)
        XCTAssertNil(tracker.feed(
            x: 0, y: 540, dx: -30, dy: 0,
            width: width, height: height, nowNanos: 1
        ))
        XCTAssertNil(tracker.feed(
            x: 100, y: 540, dx: 100, dy: 0,
            width: width, height: height, nowNanos: 2
        ))
        XCTAssertNotNil(tracker.feed(
            x: 0, y: 540, dx: -30, dy: 0,
            width: width, height: height, nowNanos: 3
        ))
    }

    func testCooldownRecoversFromARejectedHandoff() {
        var tracker = armed(remoteEdge: .right)
        XCTAssertNotNil(tracker.feed(
            x: 0, y: 540, dx: -30, dy: 0,
            width: width, height: height, nowNanos: 10
        ))
        XCTAssertNil(tracker.feed(
            x: 0, y: 540, dx: -30, dy: 0,
            width: width, height: height, nowNanos: 11
        ))
        let later = 10 + EdgePushTracker.cooldownNanos
        XCTAssertNil(tracker.feed(
            x: 500, y: 540, dx: 40, dy: 0,
            width: width, height: height, nowNanos: later
        ))
        XCTAssertNotNil(tracker.feed(
            x: 0, y: 540, dx: -30, dy: 0,
            width: width, height: height, nowNanos: later + 1
        ))
    }

    func testPushesAwayFromTheFacingEdgeAreIgnored() {
        var tracker = armed(remoteEdge: .right) // facing left
        XCTAssertNil(tracker.feed(
            x: width - 1, y: 540, dx: 30, dy: 0,
            width: width, height: height, nowNanos: 2
        ))
        XCTAssertNil(tracker.feed(
            x: 900, y: 0, dx: 0, dy: -30,
            width: width, height: height, nowNanos: 3
        ))
    }

    func testVerticalEdge() {
        var tracker = armed(remoteEdge: .bottom) // local facing edge = top
        let result = tracker.feed(
            x: 800, y: 0, dx: 0, dy: -30,
            width: width, height: height, nowNanos: 2
        )
        XCTAssertEqual(result?.0, .top)
        XCTAssertEqual(result?.1 ?? 0, 800.0 / width, accuracy: 0.01)
    }
}

final class EdgePushTrackerStatusTests: XCTestCase {
    private let width = 1920.0
    private let height = 1080.0

    func testEdgeCanBeLearnedWithoutAnEnter() {
        var tracker = EdgePushTracker()
        tracker.setPeerEdge(.left)
        XCTAssertEqual(tracker.peerEdge, .left)
        // Move inside first, then push over the facing edge.
        XCTAssertNil(tracker.feed(
            x: 300, y: 500, dx: 10, dy: 0,
            width: width, height: height, nowNanos: 1
        ))
        XCTAssertNotNil(tracker.feed(
            x: 0, y: 500, dx: -30, dy: 0,
            width: width, height: height, nowNanos: 2
        ))
    }

    func testChangingTheStatusEdgeResetsTheGesture() {
        var tracker = EdgePushTracker()
        tracker.setPeerEdge(.left)
        // This only arms the tracker; a real push follows.
        XCTAssertNil(tracker.feed(
            x: 300, y: 500, dx: 10, dy: 0,
            width: width, height: height, nowNanos: 1
        ))
        XCTAssertNotNil(tracker.feed(
            x: 0, y: 500, dx: -30, dy: 0,
            width: width, height: height, nowNanos: 2
        ))

        tracker.setPeerEdge(.right)
        XCTAssertNil(tracker.feed(
            x: 0, y: 500, dx: -30, dy: 0,
            width: width, height: height, nowNanos: 3
        ))
        XCTAssertNil(tracker.feed(
            x: width - 300, y: 500, dx: -10, dy: 0,
            width: width, height: height, nowNanos: 4
        ))
        XCTAssertNotNil(tracker.feed(
            x: width - 1, y: 500, dx: 30, dy: 0,
            width: width, height: height, nowNanos: 5
        ))
    }

    func testNilEdgeDisablesTheGesture() {
        var tracker = EdgePushTracker()
        tracker.setPeerEdge(.left)
        tracker.setPeerEdge(nil)
        XCTAssertNil(tracker.peerEdge)
        XCTAssertNil(tracker.feed(
            x: 0, y: 500, dx: -30, dy: 0,
            width: width, height: height, nowNanos: 1
        ))
    }
}

final class EdgePushContactTests: XCTestCase {
    private let width = 1920.0
    private let height = 1080.0

    func testFastFlickFromMidScreenDoesNotHandOff() {
        var tracker = EdgePushTracker()
        tracker.setPeerEdge(.left)
        // Arm from a position well inside.
        XCTAssertNil(tracker.feed(
            x: 300, y: 500, dx: 10, dy: 0,
            width: width, height: height, nowNanos: 1
        ))
        // A single event asking to jump far past the edge is ignored while
        // the cursor is still at x=300.
        XCTAssertNil(tracker.feed(
            x: 300, y: 500, dx: -2000, dy: 0,
            width: width, height: height, nowNanos: 2
        ))
        // The cursor is now at the edge; the next outward event counts.
        XCTAssertNotNil(tracker.feed(
            x: 0, y: 500, dx: -30, dy: 0,
            width: width, height: height, nowNanos: 3
        ))
    }

    func testEntryRunwayRequiresMovingInsideFirst() {
        var tracker = EdgePushTracker()
        tracker.enter(remoteEdge: .left) // local facing edge = right
        // Enter landed 24 px inside; an immediate outward push must not fire.
        XCTAssertNil(tracker.feed(
            x: width - 24, y: 500, dx: 30, dy: 0,
            width: width, height: height, nowNanos: 1
        ))
        XCTAssertNil(tracker.feed(
            x: width - 1, y: 500, dx: 30, dy: 0,
            width: width, height: height, nowNanos: 2
        ))
        // Move deep inside to arm, then cross.
        XCTAssertNil(tracker.feed(
            x: width - 300, y: 500, dx: -10, dy: 0,
            width: width, height: height, nowNanos: 3
        ))
        XCTAssertNotNil(tracker.feed(
            x: width - 1, y: 500, dx: 30, dy: 0,
            width: width, height: height, nowNanos: 4
        ))
    }
}
