# Architecture

How FlowLink is put together, from the native shells down to the wire.

## Layering

```text
+----------------------- platform shells ------------------------+
|  Swift/SwiftUI (macOS)                  Rust + Win32 (Windows)  |
|  FlowLink app + FlowLinkKit             flowlink-win (app,      |
|  via C ABI (flowlink.h) + CFlowLinkCore egui/tray, platform/)   |
+----------------------------+------------------------------------+
                             |   commands in / callbacks out
+----------------------------v------------------------------------+
|                            engine (engine.rs)                   |
|  single Cmd mpsc command loop (500 ms tick), status snapshots,  |
|  edge state machine, clipboard gate, move coalescer, pairing    |
+----+----------------+-----------------+------------------------+
     |                |                 |
 session.rs       discovery.rs      config.rs / crypto.rs
 TLS + protocol   mDNS browse +     config.json, identity
 pump, auth       announce          (cert/key DER), logs
```

`flowlink-core` has no UI dependencies and builds on macOS, Windows and
Linux; only the shells are platform-specific. The Windows shell links the
core directly (same process, `Host` trait); the macOS app talks through the
`flowlink-ffi` C ABI, whose structs and callbacks mirror the `Host` trait.

## Engine internals

One `Engine` instance owns the runtime:

- **Command loop** — `Engine::execute(Cmd)` sends on an unbounded tokio mpsc
  channel. A `select!` loop drains commands and a 500 ms interval tick. Each
  tick refreshes metrics (2 Hz status push), pairing countdown, LAN addresses
  (every 2 s) and prunes discovered devices not seen for 15 s.
- **Role task** — `start_run()` spawns either `session::server_loop` (bind
  with retry, then accept; each TCP connection gets a session task, but only
  one can be live at a time) or `session::client_loop` (dial, backoff,
  retry). Both are cancelled through a `CancellationToken`; `stop_run()`
  cancels gracefully, waits up to 700 ms, then aborts.
- **Session slot** — when a session is ready it registers an
  `UnboundedSender<Message>` in `Engine::session`. `push_to_peer()` sends
  through it and counts outbound input events; with no session it is a no-op.
  A `SessionGuard` deregisters the slot on any exit path.
- **Status** — the engine mutates `Status` under a lock and serializes it to
  JSON for the host on every meaningful change (`on_status`), plus every
  500 ms tick. Publication is serialized end-to-end by `notify_lock`, so an
  older snapshot can never overtake a newer one (which would leave a shell
  swallowing the wrong keyboard). The UI renders exclusively from this
  snapshot.
- **Lock order** — no code path holds the config lock while taking the status
  lock: `notify` reads config before locking status, and `ForgetDevice`
  clones its data before touching status. This ordering was the source of a
  real ABBA deadlock (UI Forget + a session publishing status). The same rule
  applies to every non-reentrant mutex: the tick releases the `devices` lock
  before `refresh_devices_status` takes it again (holding it across the call
  froze the engine the first time a discovered peer aged out, 15–30 s after
  start — it looked like the cursor stopping or returning to its place).
  `poll_discovered_devices` is covered by a watchdog regression test.
- **Pairing** — the server keeps one live `PairingCode`; the client parks a
  typed code in `pending_pair_code`, which the session's pairing flow polls
  at least every 250 ms (no startup races).

### Input path — the symmetric focus model

Either computer can drive the link: the *shared cursor* lives on exactly one
screen at a time (`focused`), and every physical keyboard/mouse is routed to
whichever screen shows it. Both roles behave identically; server/client only
decides who listens.

```
focused side:
  native capture ──► Cmd::HostInput ──► hotkey? ── yes ─► toggle focus
                                  │ no
                                  ├─► EdgeTracker.feed(edge, ...) ─► Enter sent,
                                  │                                 forwarding starts
                                  └─ (other events: used locally by the OS)

unfocused side (cursor is on the peer):
  native capture ──► Cmd::HostInput ──► MoveCoalescer ──► Message::Input(MouseMove)
                                        Key/Button/Scroll ──► Message::Input(…)
  incoming Message::Input ──► ignored (the cursor is elsewhere)

focus transfer:
  focused side crosses its edge ──► InputEvent::Enter { edge, along } ──► peer
  peer, while focused, presses the hotkey ──► Message::Reclaim ──► focused side
                                             releases (Enter goes the other way
                                             on its next edge push)
```

`HostInput::MouseMove` carries the absolute cursor position (union-screen
pixels) plus the pixel delta; `EdgeTracker` activates once when the cursor is
on the peer-facing edge and pushing outward (`REARM_MARGIN_PX = 10.0`,
`PUSH_EPSILON_PX = 0.4`), sending `InputEvent::Enter { edge, along_norm }` and
starting to forward. The edge used while focused is the **peer-facing edge**
(`Engine::peer_edge`), not a per-event read of the local config:

- the server announces its edge after the handshake (`Message::Topology`);
  the client mirrors it and persists it;
- either side changing the edge in Settings sends the same message, so the
  seam moves on both machines immediately;
- every incoming `Enter` is geometric truth (the mirror of the sender's exit
  edge), so a mismatched or older configuration self-heals;
- the shells place the pointer 24 px inside the edge so a sideways twitch is
  not a deliberate push.

While forwarding, moves go through the coalescer (flush at most every 4 ms;
an 8 ms flusher covers the tail; pending motion is flushed before every
discrete key/button/scroll event). Any focus switch sends
`InputEvent::ReleaseAll` locally so no injected key or button stays held.
Both capture layers also remember which physical keys/buttons they delivered
locally, so if focus moves away while one is held its *release* still reaches
the local OS (otherwise an app could see a stuck key or an endless drag). The
shell hides the physical cursor while forwarding and passes events through
while focused; injected events carry a marker (`eventSourceUserData` on
macOS, `LL*HF_INJECTED` on Windows) so the tap/hook never forwards them
back.

**Injected-edge return.** A physical mouse on the machine that does *not* own
the cursor moves the remote cursor with synthetic events, which the local
capture path deliberately ignores. Without more, that mouse could never push
the cursor back. Both shells therefore run a small pure state machine
(`EdgePushTracker`, implemented and unit-tested in Swift and Rust) over every
injected move: after `Enter` the pointer must first travel 32 px inside, then
20 px of outward overshoot at the peer-facing edge triggers
`Cmd::HandOff { edge, along }`. The shell also seeds the tracker from
`Status::peer_edge`, so the server can detect the return even at session
start, when it owns the cursor but has not received an `Enter` yet. The core
re-validates that it is focused and that the edge matches, then performs the
normal `Enter` handoff. A one-second cooldown keeps a rejected handoff
(session gone) from disabling the gesture.

Because `Input` flows both ways, the keyboard on either desk types into the
focused screen and either mouse moves the shared cursor — the "one body"
behavior.

Client (is driven):

```
session pump ──► Engine::handle_incoming_input ──► Host::on_input ──► Injector
```

Events are dropped unless `forwarding`, except `Enter` and `ReleaseAll`.
The macOS injector owns a private serial queue, warps the cursor to the
mirrored edge on `Enter`, injects via `CGEvent` (`.cghidEventTap`), and calls
`Cmd::HandOff` when injected movement crosses the peer-facing edge. The
Windows injector does the same with `SendInput`/`SetCursorPos`.

Session pump guarantees (`session.rs`): reads go through a `FrameReader` that
keeps partial reads in the reader itself, so a `tokio::select!` branch
(heartbeat, outbound message) winning a race mid-frame can no longer drop
buffered bytes and desync the stream. Heartbeats and replies are written from
the same loop; session messages (input, clipboard, focus) are applied on an
ordered side task so a slow handler can never stall heartbeats. Every teardown
— including handshake-level failures — sends a best-effort `Bye { reason }`
and shuts TLS down cleanly, so the peer logs the reason instead of an
unexplained `closed without TLS close_notify`. An outbound message over
`MAX_FRAME_BYTES` (4 MiB) is dropped with a warning instead of killing the
session.

## Threading model

| Component | Runs on |
| --- | --- |
| Engine command loop, role task, discovery, flusher | tokio multi-thread runtime, 2 worker threads (`flowlink-rt`) |
| Host callbacks (`on_status`, `on_input`, `on_clipboard`, `on_notice`) | core runtime threads; shells marshal to their UI thread |
| CGEventTap capture (macOS) | dedicated thread with its own CFRunLoop (`app.flowlink.eventtap`) |
| Input injection (macOS) | serial DispatchQueue (`app.flowlink.injector`) |
| Clipboard polling (macOS) | `Timer` on the main run loop (0.4 s) |
| Clipboard polling (Windows) | timer thread in the Windows shell |

`flowlink_*` functions are callable from any thread and return promptly;
strings passed into callbacks are only valid during the call, and returned
strings/byte buffers must be released with `flowlink_free_string` /
`flowlink_free_bytes`.

## FFI boundary

`rust/crates/flowlink-ffi/include/flowlink.h` is the contract:

| Struct | Size | Notes |
| --- | --- | --- |
| `FlHostInput` | 48 bytes | shell → core: kind, usage, down, modifiers, x, y, dx, dy, flags |
| `FlInputEvent` | 32 bytes | core → shell: kind, usage, down, modifiers, x, y, flags |

Both are `#[repr(C)]` with compile-time size assertions on the Rust side, so
ABI drift breaks the build instead of corrupting memory. Constants (modes,
kinds, modifier bits, edges, mouse buttons) are mirrored in Swift's
`CConstants.swift`.

## Data storage

Config directory: `~/Library/Application Support/FlowLink` (macOS) or
`%APPDATA%\FlowLink` (Windows). The directory is created `0700`; files
FlowLink creates in it (config, identity) are `0600`.

| Path | Contents |
| --- | --- |
| `config.json` | all persistent settings and device memory (see table below) |
| `identity/identity-cert.der` | self-signed X.509 certificate (DER) |
| `identity/identity-key.der` | matching PKCS#8 private key (DER), `0600` |
| `logs/flowlink.log.<date>` | daily-rotated logs (in-app viewer reads the newest) |
| `config.corrupt-<ms>.json` | backup of an unparseable config; a fresh one is written |

`config.json` keys (serde defaults apply; a version-0 file is upgraded in
place):

| Key | Type | Default |
| --- | --- | --- |
| `version` | u32 | `1` |
| `device_id` | UUID string | generated once |
| `device_name` | string | hostname |
| `mode` | `"server"` / `"client"` | `"server"` |
| `listen_port` | u16 | `24800` |
| `edge` | `"left"` / `"right"` / `"top"` / `"bottom"` | `"right"` |
| `hotkey` | `{ "modifiers": u8, "usage": u16 }` | Ctrl+Alt+Space (`modifiers: 6`, `usage: 0x2C`) |
| `clipboard_enabled` | bool | `true` |
| `clipboard_max_bytes` | usize | `1048576` |
| `log_level` | string | `"info"` |
| `last_server` | `{ host, port, device_id, fingerprint, token }` or null | null |
| `paired_devices` | array of `{ id, name, token, fingerprint, last_addr, last_seen_ms }` | `[]` |

Saves are atomic (temp file + rename) with `0600` permissions.

## Test strategy

Unit tests live next to the code that they exercise: `backoff` (growth,
jitter bounds, reset), `clipboard` (echo suppression, size limits, disabled),
`coalesce` (interval flush, epsilon, clear), `config` (persistence, corrupt
recovery, device memory), `crypto` (identity reload, fingerprint
normalization), `edge` (activation, hysteresis, mirrored enter geometry),
`engine` (initial state, command persistence, pairing workflow), `framing`
(single/many frames, EOF handling, oversized rejection, partial-frame
survival when a future is dropped mid-read), `hid` (modifier
flags), `host` (notice JSON), `pairing` (code shape, attempts, expiry,
constant-time compare), `protocol` (message round-trips, garbage rejection,
append-only variant indices, topology capability), `session` (oversized
outbound skipped, stream stays framed), `qr` (link round-trip, noisy parse,
defaults), `transport` (TLS round-trip, fingerprint visibility), `util`
(clock/hostname sanity), the Windows `edge_push` state machine (re-arm,
threshold, cooldown), plus FFI conversion tests and Swift `KeyCodes` /
`EdgePushTracker` tests.

`rust/crates/flowlink-core/tests/e2e_pairing.rs` runs two full engines with
real TCP + TLS on loopback and free ephemeral ports:

1. Server listens; the 6-digit code is read from its status JSON.
2. Client connects with that code; both report `connected`; the server has
   one paired device, the client stores the token.
3. Cursor exit on the right edge (1920×1080) produces `Enter { edge: Right,
   along_norm ≈ 0.5 }` on the client; key events are relayed; mouse motion is
   coalesced.
4. Clipboard travels client → server, including non-ASCII text.
5. `RequestControl` releases remote control on both sides; disconnecting
   returns the server to `listening`.
6. Reconnecting without a code succeeds with the stored token, and clipboard
   now flows server → client.
7. Mismatched edge settings are corrected from the server topology and the
   cursor still round-trips.
8. An injected-edge push (`Cmd::HandOff`) returns focus; a push on the wrong
   edge is ignored.
9. Changing the edge while connected syncs both configs and the new axis is
   used for the next handoff.
10. `cursor_round_trips_repeatedly` performs three full round-trips.

A second test drives a wrong code (rejected, no connection), then submits the
correct one on the still-open connection and reaches `connected`.
