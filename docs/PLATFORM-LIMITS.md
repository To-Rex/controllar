# Platform Limits

Everything here is a deliberate boundary, a platform constraint, or a known
rough edge — not a bug to be filed anonymously.

## macOS

- **Accessibility permission is tied to the binary.** FlowLink builds are
  ad-hoc signed; rebuilding changes the code signature and macOS treats the
  app as new, so it stops appearing under Accessibility/Input Monitoring
  until you remove the old entry and grant it again. A stable signing
  identity (Developer ID) makes grants persist — see BUILD.md.
- **Secure input fields block event taps.** Password prompts, Terminal's
  Secure Keyboard Entry and anything else using `EnableSecureEventInput`
  suspend local keyboard events, so FlowLink cannot capture or forward keys
  while they are active.
- **No synthetic input at the login window / secure desktop.** CGEvent
  injection does not reach the login screen, lock screen or FileVault prompt;
  a driven Mac cannot log itself in.
- **Disabled taps are re-enabled.** macOS may disable an event tap
  (`tapDisabledByTimeout`, `tapDisabledByUserInput`); the tap thread
  re-enables it and continues.
- **Not every key exists on the wire.** Fn, Touch ID, Eject and media
  brightness keys have no entry in the key table and are dropped, not
  translated.
- **Two masters, one cursor.** Either computer can drive the link: the
  shared cursor lives on one screen at a time and both physical keyboards /
  mice are routed to it. While the cursor is on the other computer this
  Mac's input is captured (and its cursor hidden); when the cursor comes
  back, everything is local again. Only the side that starts as the server
  needs an edge pointing at the peer: the return edge follows the edge the
  cursor actually entered through, so mismatched per-machine settings do
  not break the trip back.

## Windows

- **Windows Firewall blocks inbound connections by default.** This is the
  single most common reason a macOS client cannot reach a Windows server: the
  TCP listen port (24800 by default) and mDNS discovery replies (UDP 5353)
  are dropped until a rule exists. FlowLink handles this: the server page's
  **Network** card shows the firewall state and offers **Allow through
  firewall…**, which adds the rules through one UAC prompt (`netsh
  advfirewall firewall add rule …`); the exact commands are also shown and
  copyable for manual use. Without the rules, discovery fails *and* manual
  connections time out. On networks with router-side “client isolation”
  (some guest Wi-Fi setups), even a correct firewall cannot help — put both
  computers on the same normal LAN, or use cables.
- **Single instance.** A named mutex makes FlowLink refuse to start twice;
  the tray is where an existing copy lives. Two instances would fight over
  the listen port (the second one ends up with no pairing code), so a second
  launch now shows a message instead.
- **Injected input obeys UIPI.** Events posted with `SendInput` cannot reach
  windows of a higher integrity level. Typing into an elevated app (an
  Administrator terminal, an installer's elevation prompt) while FlowLink
  runs unelevated silently does nothing there; run FlowLink elevated if that
  is required. FlowLink itself does not require elevation for normal use.
- **UAC and the secure desktop are unreachable.** Nothing can be typed into
  a UAC consent dialog from either side.
- **Injected events are skipped by the hooks.** The low-level keyboard/mouse
  hooks ignore events flagged as injected so FlowLink's own output never
  loops back as input.
- **UI surface is the tray menu and the settings window** (`tray-icon` +
  `egui`, see `rust/Cargo.toml`). FlowLink installs no DWM hooks and draws no
  overlays on the desktop; the settings window is a plain eframe window.
  The egui UI shares the macOS design tokens (`theme.rs`) and can be
  inspected offscreen from any platform — see docs/BUILD.md, "UI preview
  mode".
- The Windows shell lives in `rust/crates/flowlink-win` and is the newest
  part of the tree; behavior described here reflects the intended design.

## Cross-platform

- **The listen port may shift automatically.** If the configured port (24800
  by default) is busy, the server binds the next free one and says so; the
  real port is what appears in the status, the invite link/QR and the mDNS
  announcement, so peers always dial the right place. Invite links therefore
  stay valid across a port fallback.
- **The pairing code is valid for 10 minutes** and survives failed connection
  attempts (a dropped connection no longer forces a retype). It dies after
  five wrong entries and is single-use; the server then rotates it.
- **Mouse deltas are 1:1, not DPI-scaled.** Relative pixels captured on one
  side are injected as relative pixels on the other. A very high-DPI server
  driving a low-DPI client can feel fast (and the reverse sluggish);
  mitigate with the OS pointer-speed slider, or keep similar pixel
  densities.
- **Keyboard sends positions, not characters.** Keys travel as USB HID
  usage ids, so the receiving OS applies its own layout. A QWERTY server's
  physical `Z` (usage `0x1D`) types `Y` on a QWERTZ client, and dead keys /
  IME composition are resolved independently on each side. Both machines
  should use layouts you are willing to type on blindly.
- **Clipboard is text-only, ≤ 1 MiB.** Images, files, HTML/RTF flavors and
  huge payloads are not synced in v1.
- **Audio and notification sync are out of scope.** FlowLink moves input and
  clipboard text, nothing else.
- **One client at a time.** A server can accept a single active client;
  further connections are politely refused (`Bye: busy`). For more machines,
  run additional server/client pairs.
- **Platform floor:** macOS 13+ (the package targets `.macOS(.v13)` and
  `LSMinimumSystemVersion` is 13.0) and Windows 10+.
- **No Linux/Wayland target.** The core crate compiles and its tests run on
  Linux, but there is no Linux shell: Wayland forbids global input capture
  by design, so a port would need per-compositor protocols. The wire
  protocol itself is portable.

## Compared with Barrier/Synergy

Short, non-exhaustive; "typical Barrier/Synergy use" refers to the Synergy 1
protocol lineage with hand-edited configuration.

| Aspect | Barrier/Synergy (typical) | FlowLink v1 |
| --- | --- | --- |
| Transport | Plain TCP (TLS optional in Barrier) | TLS required, self-signed identities |
| First connect | Edit a config file / trust the network | Pairing code or QR shown on the server |
| Identity | Optional TLS with fingerprints in config (Barrier) | TOFU pinning on every reconnect |
| Auth memory | Config file entries | 32-byte tokens in `config.json` |
| Discovery | Bonjour/Avahi | Built-in mDNS, zero external deps |
| Clipboard | Text | Text ≤ 1 MiB, concealment types excluded |
| Common setup | Hand-edit `.conf` files | UI only; no files to hand-edit |
| Clients per server | Several | One |
| Platform floor | Broad, incl. X11 | macOS 13+, Windows 10+ |
