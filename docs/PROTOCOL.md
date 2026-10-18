# Protocol

FlowLink wire protocol, version 1. One TCP connection carries one TLS
session carrying one FlowLink session, always initiated by the client.

## Framing

Every message is one frame:

```text
+----------------+--------------------------------------+
| u32 BE length  | bincode 1.3 payload (fixint)         |
+----------------+--------------------------------------+
```

- Length prefix is 4 bytes big-endian, payload length only.
- Payloads are `bincode 1.3` (`DefaultOptions` with `fixint` encoding and a
  size limit), so integers are fixed-width little-endian and enums are
  `u32` discriminants.
- `MAX_FRAME_BYTES = 4 MiB` (`4 * 1024 * 1024`): larger declared lengths are
  rejected before allocation, and the encoder refuses to emit them.
- A clean EOF before any header byte yields an orderly close; EOF inside a
  header or payload is an error.

## Version

`PROTOCOL_VERSION = 1`. The server rejects a mismatched `Hello` and a client
rejects a mismatched `HelloAck`, both with a `Bye` where possible. Version
negotiation is not attempted.

## Handshake

```text
  client                                          server
    |                                               |
    |------ TCP connect --------------------------->|
    |------ TLS handshake (server cert only) ------>|   (client verifies
    |                                               |    pinned fingerprint
    |                                               |    before sending secrets)
    |------ Hello { protocol, device_id, name, ----->|
    |        version, mode, screen }                |
    |<----- HelloAck { protocol, device_id, --------|
    |        name, version, known_device }          |
    |                                               |
    |  client has a stored token:                   |
    |------ Authenticate { device_id, token } ----->|
    |<----- AuthResult { ok } / { ok:false, reason }|
    |                                               |
    |  client has none (pairing required):          |
    |------ PairRequest { device_id, name, code } -->|
    |<----- PairResult { ok, token, attempts_left }-|   (code shown on server)
    |                                               |
    |==== READY: Input / Clipboard / Heartbeat =====|
```

Timeouts: TLS + `Hello`/`HelloAck` 10 s; the server allows 180 s for
authentication/pairing after `Hello`; the client waits up to 300 s for the
user to obtain a pairing code.

The server refuses a second client with `Bye { reason: "server is busy with
another client" }`. The client chooses the path itself: with a stored token
it sends `Authenticate`, otherwise it goes straight to `PairRequest` — the
`known_device` flag in `HelloAck` is informational only. A stored token
rejected by the server (`AuthResult { ok: false }`) falls back to the pairing
flow on the same connection.

## Messages

| Message | Direction | Fields | Notes |
| --- | --- | --- | --- |
| `Hello` | client → server | `protocol: u16`, `device_id`, `name`, `version`, `mode`, `screen: ScreenInfo` | First message; must arrive within the handshake timeout |
| `HelloAck` | server → client | `protocol`, `device_id`, `name`, `version`, `known_device: bool` | `known_device` is true when the server already has a token for this device id |
| `Authenticate` | client → server | `device_id`, `token` | Proves possession of the pairing token |
| `AuthResult` | server → client | `ok: bool`, `reason: Optional<String>` | Server also requires `device_id` to equal the `Hello` id |
| `PairRequest` | client → server | `device_id`, `name`, `code` | Code is the 6-digit code displayed on the server |
| `PairResult` | server → client | `ok`, `reason`, `token: Optional<String>`, `attempts_left: Optional<u8>` | Token present on success; `attempts_left` is informational |
| `Input` | server → client | `InputEvent` | Only sent while remote control is active |
| `Clipboard` | both ways | `text: String` | UTF-8 text, ≤ `clipboard_max_bytes` |
| `Heartbeat` | both ways | `seq: u64` | Sent every 1 s |
| `Pong` | both ways | `seq: u64` | Echoes the heartbeat sequence; used for RTT |
| `Reclaim` | client → server | – | User pushed the return edge or hit the hotkey on the client |
| `Bye` | both ways | `reason: String` | Orderly end, e.g. `busy`, `shutting down`, `pairing timed out` |

Messages that are invalid for a role or state are ignored (trace-logged)
rather than fatal, except during the handshake where a protocol violation
closes the connection.

## Input events

`Input` wraps one of these variants:

| Variant | Fields | Semantics |
| --- | --- | --- |
| `Enter` | `edge: Edge`, `along_norm: f32` | Remote cursor enters from `edge`; position along that edge, 0..1 |
| `Key` | `usage: u16`, `down: bool`, `modifiers: Modifiers` | Physical key event; `usage` is a USB HID keyboard usage id |
| `MouseMove` | `dx: f32`, `dy: f32` | Relative movement in pixels |
| `MouseButton` | `button: MouseButton`, `down`, `modifiers` | Button edges |
| `Scroll` | `dx: f32`, `dy: f32`, `precise: bool` | Wheel/trackpad deltas |
| `ReleaseAll` | – | Release every key/button the receiving side holds |

### HID convention

Keys travel as USB HID Usage Tables 1.12, Keyboard/Keypad page `0x07`:
`A = 0x04` … `Z = 0x1D`, `1 = 0x1E` … `0 = 0x27`, `Enter = 0x28`,
`Space = 0x2C`, `F1 = 0x3A`, modifiers `0xE0–0xE7` (left/right
Ctrl, Shift, Alt, Meta). Keys are physical positions; the receiving OS
applies its own layout (see PLATFORM-LIMITS.md).

Modifier bits in `Modifiers` (also the FFI `FL_MOD_*` constants):

| Bit | Value | Meaning |
| --- | --- | --- |
| shift | 1 | Shift |
| ctrl | 2 | Control |
| alt | 4 | Alt / Option |
| meta | 8 | Cmd / Windows key |

Mouse buttons use HID button-page ids: `1` left, `2` right, `4` middle.

### Scroll convention

- Positive `dy` scrolls the wheel **away from the user** (up). Positive `dx`
  means **towards the left** (this mirrors the macOS axis signs, which are
  negated relative to Windows' horizontal `HWHEEL`).
- macOS captures `scrollWheelEventDeltaAxis1/2` for discrete wheels (one
  notch per physical tick) and `scrollWheelEventPointDeltaAxis1/2` for
  trackpads; Windows captures `HIWORD(mouseData) / WHEEL_DELTA` (notches).
- `precise = true` marks pixel deltas (trackpads). `precise = false` is a
  notch-based wheel event; both shells deliver the sign conventions above.
- Injection: the macOS shell posts a pixel-unit `CGEvent`, multiplying one
  notch by `12` pixels; the Windows shell multiplies one notch by
  `WHEEL_DELTA` (120) and divides precise pixel deltas by `12` first, so a
  trackpad swipe crosses the platforms at the same speed.

## Heartbeats and liveness

Each side sends `Heartbeat { seq }` every 1 s and answers with `Pong { seq }`.
The round trip updates the RTT metric (the last 8 pending pongs are kept). If
no frame of any kind arrives for 6 s (`DEAD_AFTER`), the session errors out;
the client then re-enters its backoff loop. On shutdown a session sends
`Bye { reason: "shutting down" }` before closing.

## Discovery (mDNS/DNS-SD)

Servers announce the service `_flowlink._tcp.local.` while listening; every
engine browses it (no Bonjour/Avahi installation required — pure Rust
`mdns-sd`).

- Instance name: `<device name> [<first 8 chars of device id>]`
- Host name: `flowlink-<id-prefix>.local.`
- Port: the configured `listen_port`
- TXT record fields:

| Key | Example | Meaning |
| --- | --- | --- |
| `id` | `5f1e9a2c-…` | FlowLink device id (UUID) |
| `name` | `Studio Mac` | User-visible computer name |
| `role` | `server` | Advertised role |
| `ver` | `0.1.0` | Core version string |
| `fp` | `a1b2…` (64 hex chars) | SHA-256 fingerprint of the TLS certificate |

Example resolved record:

```text
_flowlink._tcp.local.  Studio Mac [5f1e9a2c]  host flowlink-5f1e9a2c.local.
  192.168.1.24:24800  id=5f1e9a2c-… name="Studio Mac" role=server ver=0.1.0 fp=a1b2…
```

The engine prefers private IPv4 addresses, then other addresses, and prunes
devices not re-seen for 15 s.

## Connection links (QR and paste)

The server renders an invite link as a QR code (error correction level M,
4-module quiet zone) and shows it beside the pairing code. Format:

```text
flowlink://connect?h=<host>&p=<port>&fp=<sha256-hex>&c=<code>
```

| Param | Required | Meaning |
| --- | --- | --- |
| `h` (or `host`) | yes | Server address (IPv4, IPv6 or DNS name; percent-decoded) |
| `p` (or `port`) | no | TCP port, defaults to `24800` |
| `fp` (or `fingerprint`) | no | Expected certificate fingerprint, pinned immediately |
| `c` (or `code`) | no | Live pairing code; omitted when no code is active |

Example:

```text
flowlink://connect?h=192.168.1.24&p=24800&fp=7d08c2f19e0b…&c=123456
```

Parsing is tolerant: the link may be embedded in surrounding text, and the
port falls back to the default; only a missing host is fatal.
