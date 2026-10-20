# ToRexLink wire protocol v1

## Transport

- TCP port 48620–48629 (first free).
- TLS 1.3 exactly, ALPN `torexlink/1`.
- Frames: 4-byte unsigned big-endian length + UTF-8 JSON object.
- Maximum frame: 8 MiB. Clipboard fields are limited to 1 MiB UTF-8.
- All post-handshake messages include `"v": 1` and `"type"`.
- Unknown fields are ignored; invalid fields close the session.

JSON is used for clarity and debuggability. Input is batched and coalesced at
the session layer, not in JSON. If bandwidth becomes critical, a binary input
channel can be added under a protocol version bump.

## Identity

```
fingerprint = SHA-256(DER certificate), colon display form
device_id   = lowercase hex SHA-256(DER certificate)
```

The certificate is RSA-3072, self-signed, CA:TRUE, valid for 10 years. Peers
pin the certificate PEM and device ID after pairing.

## Pairing handshake

All messages here are inside TLS 1.3. The TLS channel is not yet identity-pinned.

### 1. Client hello

```json
{
  "v": 1,
  "type": "hello",
  "mode": "pair",
  "client": {
    "id": "<sha256 cert hex>",
    "name": "Studio Mac",
    "platform": "macos",
    "app_version": "1.0.0",
    "nonce": "<base64url 32 bytes>",
    "cert_pem": "<PEM>"
  }
}
```

### 2. Host challenge

```json
{
  "v": 1,
  "type": "pair_challenge",
  "server": {
    "id": "<sha256 cert hex>",
    "name": "Workstation",
    "platform": "windows",
    "app_version": "1.0.0",
    "nonce": "<base64url 32 bytes>",
    "fingerprint": "<AA:BB:...>"
  },
  "sid": "<base64url 32 bytes>",
  "cpace_pub": "<base64url compressed P-256 point>",
  "expires_at": 1760000000,
  "issued_at": 1760000000
}
```

`sid = SHA-256("ToRexLink pairing sid v1" || server_nonce ||
client_nonce || client_device_id_bytes)`.

The QR fingerprint, if present, must equal the actual TLS certificate
fingerprint before proceeding.

### 3. Client confirmation

```json
{
  "v": 1,
  "type": "pair_response",
  "cpace_pub": "<base64url compressed P-256 point>",
  "proof": "<base64url HMAC-SHA256>",
  "signature": "<base64url RSA-PSS SHA-256>"
}
```

CPace:

```
G          = TryAndIncrement_P256(SHA-512("ToRexLink-CPace-P256-v1" || sid || code))
X, Y       = x*G, y*G
shared     = SHA-256("CPace-P256-shared\0" || encode(x*Y))
material   = HKDF-SHA256(shared, salt=sid, info="ToRexLink-CPace-Key-v1", len=96)
token      = HKDF-SHA256(material[0:32], salt=sid, info="ToRexLink-Pair-Token-v1", len=32)
client_key = material[32:64]
server_key = material[64:96]
```

Transcript:

```json
{
  "domain": "ToRexLink pairing v1",
  "v": 1,
  "sid": "...",
  "client": { "id": "...", "name": "...", "platform": "...",
              "app_version": "...", "nonce": "...", "cert_sha256": "..." },
  "server": { "id": "...", "name": "...", "platform": "...",
              "app_version": "...", "nonce": "...", "fingerprint": "..." },
  "client_cpace_pub": "...",
  "server_cpace_pub": "..."
}
```

`proof = HMAC-SHA256(client_key, "client\0" || SHA256(canonical_json(transcript)))`

`signature = RSA-PSS(SHA-256, "ToRexLink-Pair-Client-Signature-v1\0" || digest)`

The host also checks that the client certificate hash matches `hello.client.id`.

### 4. Host confirmation

```json
{
  "v": 1,
  "type": "pair_confirmed",
  "proof": "<base64url HMAC-SHA256>"
}
```

`proof = HMAC-SHA256(server_key, "server\0" || transcript_digest)`.

Both parties persist the peer and token. A TCP connection failure immediately
after this point may produce one removable pairing on the host; the next auth
will fail until the client retries with the stored token.

## Authentication handshake

### 1. Client hello

Same shape, `"mode": "auth"`, includes client certificate and fresh nonce.

### 2. Host challenge

```json
{
  "v": 1,
  "type": "auth_challenge",
  "server": { "...": "..." },
  "issued_at": 1760000000
}
```

The client validates the server descriptor fingerprint against the actual TLS
certificate and the pinned certificate.

### 3. Client proof

```json
{
  "v": 1,
  "type": "auth_response",
  "proof": {
    "hmac": "<base64url 32 bytes>",
    "signature": "<base64url RSA-PSS 384 bytes>"
  }
}
```

Auth transcript binds:

- protocol version;
- client device ID, name, platform, app version, nonce, cert SHA-256;
- server device ID, name, platform, app version, nonce, fingerprint.

`digest = SHA-256(canonical_json(transcript))`.

```
hmac      = HMAC-SHA256(token, "ToRexLink-Auth-Client-v1\0" || digest)
signature = RSA-PSS(SHA-256, "ToRexLink-Auth-Client-v1\0" || digest)
```

The server requires both the token and the private key belonging to the stored
client certificate.

### 4. Host proof

```json
{
  "v": 1,
  "type": "auth_ok",
  "proof": {
    "hmac": "<base64url>",
    "signature": "<base64url>"
  }
}
```

Same construction with the server role label and server private key. The client
also verified the pinned TLS certificate before any of this.

## Session messages

### Heartbeat

```json
{"v":1,"type":"ping","id":42}
{"v":1,"type":"pong","id":42}
```

A ping is sent every 10 s; no data/pong for 32 s closes the session.

### Input

```json
{"v":1,"type":"input","seq":17,"event":{"t":"key","key":"a","pressed":true,"repeat":false,"mods":["ctrl"]}}
{"v":1,"type":"input","seq":18,"event":{"t":"move","dx":0.0031,"dy":-0.0012}}
{"v":1,"type":"input","seq":19,"event":{"t":"button","button":"left","pressed":true}}
{"v":1,"type":"input","seq":20,"event":{"t":"wheel","dx":0.0,"dy":1.0}}
```

- `move` deltas are normalized to the controller’s screen size, bounded ±2.
- `button` is `left|right|middle|x1|x2`.
- `wheel` units are detents (Windows 120 units = 1.0).
- `mods` is advisory; modifier key events are themselves forwarded.
- Keys are canonical names, not Unicode characters. This preserves keyboard
  shortcuts; keyboard layout should match for character-level parity.

### Focus

```json
{"v":1,"type":"focus_request","action":"edge","edge":"right","position":0.42,"request_id":"..."}
{"v":1,"type":"focus_state","epoch":7,"controller":"<id>","target":"<id>","reason":"edge","entry_edge":"left","position":0.42}
```

- Only the network server issues `focus_state`.
- Actions: `toggle`, `edge`, `takeover`, `return`.
- `entry_edge` is relative to the target and used to warp its pointer when an
  edge transition occurs.
- Epoch increases monotonically; stale states are ignored.

### Clipboard

```json
{"v":1,"type":"clipboard","text":"...","origin":"<device id>","sha256":"<hex>"}
```

- UTF-8 ≤ 1 MiB.
- SHA-256 must match; mismatches are rejected.
- Remote-applied values are not echoed back.
- Only text is transmitted.

### Close / errors

```json
{"v":1,"type":"bye","reason":"normal"}
{"v":1,"type":"error","code":"auth_failed"}
```

Error codes include `bad_handshake`, `pair_unavailable`, `pair_denied`,
`auth_failed`, `busy`, `hosting_disabled`, `session_failed`.

## Limits and policy

| Item | Limit |
|---|---|
| Frame | 8 MiB |
| Clipboard text | 1 MiB UTF-8 |
| Pairing attempts | 5 per offer |
| Pairing TTL | 180 s |
| Handshake timeout | 8–12 s |
| Input rate | ~4500/s, burst 7000 |
| Clipboard rate | ~1.2/s, burst 8 |
| Concurrent handshakes | 8 |
| Active peer sessions | 1 |

## Versioning

`v` is checked in hello and every message. A future breaking change must bump
the protocol version and provide a migration or reject old peers with a clear
UI message. Never weaken certificate pinning or TLS minimums without a
documented security review.
