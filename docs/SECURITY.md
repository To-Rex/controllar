# Security

What FlowLink protects against, how, and — just as important — what it
explicitly does not protect against.

## Threat model

FlowLink is designed for a trusted local network (home, office LAN) where the
adversary may be a passive eavesdropper, an active machine-in-the-middle on
the path, or a nearby device that pretends to be one of your computers. It is
not designed for hostile networks where the local endpoint itself is
compromised.

| Threat | Countermeasure |
| --- | --- |
| Eavesdropping on the LAN | TLS 1.3 on every connection; no plaintext mode |
| Machine-in-the-middle | Certificate fingerprint pinning on reconnect; TOFU with an on-screen pairing code on first contact |
| Rogue device claiming a known id | Pairing token check (constant-time), bound to the `Hello` device id |
| Replay of a captured pairing code | Code is single-use, expires after 120 s, dies after 3 wrong attempts |
| Token theft from disk | Token stored `0600` in a `0700` directory; boundary is the OS user account |
| Clipboard leakage of passwords | Concealed/transient pasteboard types are never relayed; text-only, size-capped |
| Secret leakage via logs | Clipboard contents, keys, codes and tokens are never logged |

## Identities and certificates

- Each installation generates an ECDSA P-256 self-signed certificate at
  first run (`rcgen`), stored as DER in `identity/identity-cert.der` and
  `identity/identity-key.der` (`0600`, directory `0700`).
- The **fingerprint** is the lowercase hex SHA-256 of the certificate DER
  (64 characters); the UI displays it as uppercase hex in 4-digit groups.
- The server presents its certificate during the TLS handshake. There are
  **no client certificates**: client authentication happens at the
  application layer with the pairing token.
- The client's rustls verifier (`crypto.rs`, `TofuVerifier`) accepts
  self-signed server certificates at the TLS layer but still enforces TLS
  handshake signature verification. Trust is established immediately after
  the handshake, before any secret is sent:
  1. if the target carries a pinned fingerprint (from a previous connection,
     discovery TXT record, or a `flowlink://` link), it must match — any
     mismatch aborts the connection with both fingerprints named in the
     error;
  2. otherwise this is a first contact: nothing is trusted yet, and a
     successful pairing with the code shown on the server screen is required.

This is standard TOFU (trust on first use). The security of the first contact
rests on the user comparing/typing information from the server's screen.

## Pairing ceremony

1. As long as the server is listening it holds one live pairing code:
   a random 6-digit number whose first digit is non-zero, TTL 120 s
   (`DEFAULT_TTL_MS`), 3 attempts (`MAX_ATTEMPTS`).
2. The code is displayed on the server (and embedded in the QR / invite link
   shown there). The client submits it via `PairRequest`; verification is
   constant-time.
3. On success (one-time use), the server generates a **32-byte random
   token**, hex-encoded (64 chars), returns it in `PairResult`, and stores it
   in `paired_devices` together with the device id and address. The client
   stores it as `last_server.token` and in its own `paired_devices`.
4. Wrong attempts decrement the counter (`attempts_left` in the reply);
   running out, or an expired code, invalidates it and the server
   automatically rotates in a fresh code. The UI can also rotate on demand
   ("Regenerate").
5. Subsequent connections send `Authenticate { device_id, token }`; the
   server checks the device id against the `Hello` and compares tokens in
   constant time (`subtle::ConstantTimeEq`). A mismatch yields
   `AuthResult { ok: false }` and the client falls back to pairing.

Tokens are bearer credentials: possession alone suffices, so treat
`config.json` as sensitive.

## What is NOT protected

- **First contact is TOFU.** The client cannot know a server's identity
  before pairing. Connecting through mDNS discovery or a scanned/pasted link
  pins the fingerprint straight from the server's own announcement or screen,
  so relay attacks fail. With manual `host:port` entry there is no
  fingerprint yet: an on-path attacker can relay the first connection, and a
  plain impostor can succeed only if they know the code — which is shown on
  the server screen and is single-use. For hostile networks, pair via QR/link
  or compare fingerprints out of band.
- **The QR/link contains the ephemeral pairing code.** Scanning or pasting
  the link is equivalent to typing the code; treat the link as sensitive for
  its 2-minute lifetime (and as useless afterwards — the code is single-use).
- **Local account compromise.** `config.json` (including tokens and paired
  device ids) is readable by the user account that runs FlowLink. File
  permissions are the boundary; Full Disk Access, backups and malware running
  as the user are outside the model.
- **No forward secrecy beyond TLS 1.3.** There is no key ratcheting or
  per-session rekeying at the application layer; the long-term token is sent
  inside TLS. TLS 1.3 itself provides forward secrecy for recorded traffic,
  but a token disclosed later allows impersonation.
- **No out-of-band fingerprint verification by default.** Comparing
  fingerprints is optional (Settings shows this device's fingerprint and
  paired devices'); enabling the comparison for each new pairing is a
  plausible "paranoid mode" workflow but is not enforced.
- **Denial of service.** Anyone who can reach the port can consume the
  server's single client slot (refused politely with `Bye`) or force pairing
  attempts; there is no rate limiting beyond the code's attempt counter.

TLS configuration note: rustls is compiled **without** the `tls12` feature —
FlowLink speaks TLS 1.3 only, there is no downgrade path.

## Clipboard

- Only **text** is relayed (`Message::Clipboard { text }`), capped in both
  directions by `clipboard_max_bytes` (default 1 MiB); the macOS pasteboard
  poller also refuses text above 1 MiB before it reaches the core. Oversized
  content is dropped with a log line that records the byte count, never the
  content.
- On macOS, the pasteboard watcher skips changes whose types include
  `org.nspasteboard.ConcealedType`, `org.nspasteboard.TransientType` or
  `com.agilebits.onepassword` — password managers and transient content are
  not transmitted.
- Echo suppression (hash of the last text seen in either direction) prevents
  loops; it is a loop guard, not an authenticity check, so on a fully
  compromised endpoint clipboard content is visible to that endpoint — that
  is inherent to clipboard sync.

## Permissions

| Platform | Permission | Why |
| --- | --- | --- |
| macOS | Accessibility | Create the CGEventTap (capture) and post synthetic events (inject) |
| macOS | Input Monitoring | Observe raw keyboard events from the tap |
| macOS | Camera (optional) | Only when scanning the pairing QR code |
| Windows | none | Normal user rights suffice to hook and inject input |

On Windows, injected input is subject to UIPI: events cannot reach windows of
higher integrity level (elevated processes) unless FlowLink itself runs
elevated. `UAC`/secure-desktop prompts are never reachable. See
PLATFORM-LIMITS.md.

## Logging

The file logs contain lifecycle facts (listening, connections, pairing
accepted/rejected with names and attempt counts, errors, discovery events,
byte counts). Reviewing the sources: no log statement includes key data,
clipboard text, pairing codes, tokens, or invite links. The in-app log viewer
reads only from the logs directory.
