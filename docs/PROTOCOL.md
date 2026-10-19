# Protocol

FlowLink wire protocol, version 2. One TCP connection carries one TLS
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

`PROTOCOL_VERSION = 2`. The server rejects a mismatched `Hello` and a client
rejects a mismatched `HelloAck`, both with a `Bye` where possible. Version
negotiation is not attempted.

`Topology` is an **additive** message introduced for 0.3 peers without a
protocol bump: bincode numbers enum variants in declaration order, so it was
appended after `Bye` and every 0.2.x message keeps its index. A 0.3 build
only sends it to a peer whose `Hello.version`/`HelloAck.version` is 0.3+;
older peers simply never see the variant. `protocol::supports_topology`
performs that check, and a unit test pins the variant indices.

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

The server refuses a second, *different* client with `Bye { reason: "server is
busy with another client" }`. A reconnect from the **same device id** is not
refused: its previous socket may still be inside the dead-man window after a
Wi-Fi dropout, so the new connection replaces it and cancels the old session.
The client chooses the auth path itself: with a stored token it sends
`Authenticate`, otherwise it goes straight to `PairRequest` — the
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
| `Input` | both ways | `InputEvent` | Forwarded by the side that does **not** own the cursor; injected by the focused side |
| `Clipboard` | both ways | `text: String` | UTF-8 text, ≤ `clipboard_max_bytes` |
| `Heartbeat` | both ways | `seq: u64` | Sent every 1 s |
| `Pong` | both ways | `seq: u64` | Echoes the heartbeat sequence; used for RTT |
| `Reclaim` | both ways | – | The unfocused side asks for the shared cursor (hotkey / UI) |
| `Topology` | both ways | `edge: Edge` | Local edge facing the peer changed; receiver mirrors it. 0.3+ peers only |
| `Bye` | both ways | `reason: String` | Orderly end, e.g. `busy`, `shutting down`, `pairing timed out` |

Messages that are invalid for a role or state are ignored (trace-logged)
rather than fatal, except during the handshake where a protocol violation
closes the connection.

## Focus model (one shared cursor)

Exactly one side owns the shared cursor (`focused`). Server/client only
decide who listens; input routing is symmetric:

- The **focused** side uses its local input locally. When its pointer crosses
  its peer-facing edge, it hides its cursor, sends
  `Input(Enter { edge, along_norm })` and starts forwarding every subsequent
  event as `Input`. The peer becomes focused and injects those events.
- The **unfocused** side hides its cursor, captures all of its input and
  forwards it. Its keyboard therefore types into the focused screen.
- **The peer-facing edge is shared state.** After the handshake the server
  sends `Topology { edge }`; the client stores the mirror
  (`edge.opposite()`) as its own facing edge and persists it. Changing the
  edge on either side sends `Topology` again, so both computers move the seam
  together instead of relying on two independent settings being mirrored by
  hand. Any incoming `Enter` is also geometric truth and updates the local
  facing edge to `Enter.edge.opposite()` — this self-heals a mismatched
  configuration and 0.2.x peers, which have no `Topology`.
- Pressing the hotkey on the unfocused side sends `Reclaim`; the focused side
  stops showing the cursor (the requester warps its own cursor to the edge
  facing the peer). Pressing the hotkey on the focused side sends `Enter`
  instead, handing the cursor over.
- **Injected-edge return:** when the physical mouse is on the other computer,
  the focused side receives synthetic moves. Its injector watches them: after
  `Enter` the pointer must travel 32 px inside, then 20 px of outward
  overshoot at the peer-facing edge causes the shell to issue a local
  `HandOff` command; the core (only if still focused and on that edge) sends
  `Enter` back. One physical mouse can therefore cross the shared seam in
  both directions with no hotkey.
- Every focus change emits a local `ReleaseAll` so no key/button stays held.
- At session start the **server** side is focused; the client begins by
  forwarding. A client that owned the cursor when the link dropped reclaims
  it immediately after reconnecting.

| Variant | Fields | Semantics |
| --- | --- | --- |
| `Enter` | `edge: Edge`, `along_norm: f32` | The *sender's* edge the cursor exited through; the receiver places the pointer on the mirrored side (see `edge::enter_position`) |
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
no frame of any kind arrives for 30 s (`DEAD_AFTER`), the session errors out;
the client then re-enters its backoff loop (250 ms → 4 s). On shutdown a
session sends `Bye { reason: "shutting down" }` before closing.

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
