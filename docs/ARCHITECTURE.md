# ToRexLink arxitekturasi

## 1. Umumiy ko‘rinish

ToRexLink ikki qatlamga bo‘lingan:

1. **Qt bo‘lmagan async yadro** — tarmoq, kriptografiya, pairing, auth,
   focus arbitraji, clipboard, mDNS, native input.
2. **PySide6 UI** — yadroni alohida `asyncio` thread’da boshqaradi va faqat
   immutable state snapshotlar oladi.

Bu ajratish UI muzlashini oldini oladi, yadroni Qt’siz test qilish imkonini
beradi va native event tap/hook callbacklari hech qachon UI thread’ga
bog‘lanmaydi.

```
┌──────────────────────────── macOS / Windows ────────────────────────────┐
│                                                                          │
│  Native input backend (Quartz event tap / WH_*_LL hooks)                 │
│        │ physical event                         ▲ synthetic event        │
│        ▼                                        │                        │
│  InputBridge ◄──── focus state ──── PeerSession (TLS 1.3, JSON frames)   │
│        │                                 ▲            │                  │
│        ├──────── clipboard ──────────────┘            ▼                  │
│        │                                         Network manager         │
│  Qt UI (main thread) ◄── ServiceEvent snapshots ── ToRexLinkService      │
│        │                                              (asyncio thread)   │
│        └── AsyncRuntime.submit(...) ──────────────────────┘              │
└──────────────────────────────────────────────────────────────────────────┘
```

## 2. Modullar

### `security/`

- `identity.py` — qurilma RSA-3072 sertifikati va private key. Sertifikat
  o‘zi-o‘zini imzolaydi va TLS trust anchor sifatida pinlanadi. Fayllar 0600.
  Buzilgan yoki yarim qolgan identity fayli avtomatik o‘chirilmaydi; `.broken-*`
  yoki `.orphan-*` nomi bilan saqlanadi.
- `pairing_crypto.py` — P-256 ustida CPace PAKE. 6 xonali kod simdan
  o‘tmaydi. Ephemeral scalar, hash-to-curve, HKDF va confirmation HMAC.
- `auth.py` — token HMAC + RSA-PSS imzo bilan o‘zaro autentifikatsiya.
  Tokendan faqat HMAC kaliti emas, sertifikat private key egaligi ham
  talab qilinadi.

### `protocol/`

- `framing.py` — 4-byte big-endian uzunlik + UTF-8 JSON. Maksimal frame
  8 MiB; clipboard matni alohida 1 MiB bilan cheklanadi.
- `handshake.py` — hello/challenge/confirmation xabarlari, sertifikat va
  fingerprint tekshiruvi, canonical transcript.
- `models.py` — post-handshake xabarlar uchun qat’iy sxema va qiymat
  chegaralari.

### `core/`

- `transport.py` — TLS 1.3 contextlar, pin qilingan trust, ALPN, length-framed
  `SecureStream`.
- `server.py` — `asyncio.start_server`, port 48620–48629 oralig‘idan bo‘sh
  port, parallel ulanish limiti, to‘g‘ri yopilish.
- `session.py` — heartbeat, latency, input batching/coalescing, rate limits,
  clipboard dispatch, focus callbacklari. Input platform thread’dan
  `call_soon_threadsafe` orqali keladi; event loop bloklanmaydi.
- `pairing.py` — bir martalik kod lifecycle, 5 urinish limiti, CPace
  handshake, server tomonida peer’ni tasdiqlashdan oldin saqlash.
- `auth.py` — reconnect handshake, TLS pin tekshiruvi, token/imzo proof.
- `focus.py` — yagona focus authority. Faqat tarmoq server (host) focus
  state’ni yaratadi. Bu race va bir vaqtda ikki tomon boshqarishini oldini
  oladi.
- `clipboard.py` — native clipboard adapterlari va polling sync; loop
  himoyasi hash orqali.
- `discovery.py` — zeroconf mDNS browse/advertise. Discovery hech qachon
  trust manbai emas; faqat manzil topadi.
- `service.py` — yuqoridagilarni bog‘lovchi orchestrator. Snapshot va
  eventlar ishlab chiqaradi; UI faqat shu interfeys bilan ishlaydi.
- `state.py` — UI’ga uzatiladigan immutable dataclasslar.

### `input/`

- `events.py` — platform-neutral key/move/button/wheel eventlari.
- `bridge.py` — focus siyosati, suppression, cursor ko‘rinishi, edge va
  hotkey, inject qilingan eventlarni qayta ishlashni taqiqlash.
- `macos.py` — Quartz event tap, CGEvent injection, cursor hide/warp,
  Accessibility/Input Monitoring preflight.
- `windows.py` — `WH_KEYBOARD_LL` / `WH_MOUSE_LL`, `SendInput`, DPI-aware
  `GetSystemMetrics`, injected event filtri.
- `keys.py` — canonical key nomlari va platform key-code jadvallari.

### `platform/`

- `permissions.py` — ruxsat holati va tizim oynalarini ochish.
- `autostart.py` — macOS LaunchAgent yoki HKCU `Run`.
- `firewall.py` — Windows Firewall qoidalarini UAC bilan qo‘shish.
- `diagnostics.py` — maxfiy ma’lumotsiz diagnostika hisoboti.

### `ui/`

- `controller.py` — `QThread` ichidagi asyncio loop, callablelarni thread
  chegarasidan xavfsiz o‘tkazish, pending queue.
- `main_window.py` — sidebar, sahifalar, focus banner, tray menu, toast,
  single notification.
- `pages/` — Home, Connect, Devices, Settings, Logs, Diagnostics.
- `dialogs.py` — Join, QR scanner, Confirm.
- `onboarding.py` — 3 qadamli birinchi ishga tushish.
- `theme.py`, `widgets.py`, `icons.py` — design system.

## 3. Threading model

| Thread | Vazifa |
|---|---|
| Qt main thread | Barcha widgetlar, tray, dialoglar |
| `AsyncRuntime` (`QThread`) | `asyncio` loop, TLS, mDNS, pairing, auth, clipboard poll |
| macOS HID thread | CFRunLoop + event tap |
| Windows hook thread | GetMessage loop + low-level hooks |

Qoidalar:

- Native callbacklar hech qachon `await` qilmaydi. Ular bridge ichida tez
  qaror qabul qiladi; tarmoqqa yuborish `SecureStream`ning thread-safe input
  queue’siga `call_soon_threadsafe` orqali topshiriladi.
- Qt signal/slot thread chegarasini avtomatik queued connection bilan kesib
  o‘tadi.
- UI hech qachon `asyncio` objectlarini to‘g‘ridan-to‘g‘ri ushlamaydi.
- `InputBridge` state’i `threading.RLock` bilan himoyalangan.

## 4. Connection lifecycle

### Host

1. Identity yuklanadi/yaratiladi.
2. `PeerServer` 0.0.0.0:48620–48629 da TLS 1.3 listener ochadi.
3. mDNS `_torexlink._tcp.local.` e’lon qilinadi.
4. Pairing uchun kod yaratilganda `pairing_challenge` faqat kod egasi
   transkriptni tasdiqlay oladigan CPace sessiyasini boshlaydi.
5. Auth ulanishida server peer store’dan tokenni topadi va proofni tekshiradi.
6. Faol session bittadan oshmaydi; bir xil peer qayta ulansa eski session
   yopiladi.
7. Host focus authority hisoblanadi.

### Guest

1. Client pinned TLS context bilan host’ga ulanadi yoki birinchi marta CPace
   pairing qiladi.
2. Auth muvaffaqiyatli bo‘lsa `PeerSession` boshlanadi.
3. Host yuborgan `focus_state` input bridge’ni ishga tushiradi.
4. Uzilishda auto-reconnect exponential backoff (1–30 s + jitter) bilan
   ishlaydi; mDNS yangi port/address topsa ishlatadi.

## 5. Focus modeli

- **controller** — fizik kirishi ishlatilayotgan kompyuter.
- **target** — kirish qabul qilayotgan kompyuter.
- Odatda ikkisi bir xil. `controller=A`, `target=B` bo‘lsa A’ning sichqoncha
  va klaviaturasi B’ga uzatiladi.
- Host authority har bir `focus_request`ni epoch bilan tasdiqlaydi va ikkala
  tomonga `focus_state` yuboradi.
- Har qanday qurilmaning fizik inputi `takeover` qilishi mumkin. Inject
  qilingan eventlar source PID / injected flag bilan ajratiladi va takeover
  trigger qilmaydi.
- Hotkey `Ctrl+Alt+Space`:
  - controller o‘zida bo‘lsa target’ni almashtiradi;
  - controller boshqa tomonda bo‘lsa lokal `takeover` yuboradi.
- Edge:
  - controller o‘z ekranining tanlangan chetidan chiqsa, target’ga `edge`
    yuboriladi va host qarama-qarshi chetni hisoblab beradi;
  - target’dagi kursor shu chetdan tashqariga chiqsa `return` yuboriladi.
- Cursorni recenter qilish va suppression:
  - macOS’da input delta maydoni suppressed holatda ham mavjud;
  - Windows’da cursor harakati bloklanmaydi (hidden), shunda delta olinadi;
    chetga yaqinlashganda center’ga warp qilinadi.

## 6. State and errors

`ToRexLinkService.snapshot()` quyidagi immutable ma’lumotni beradi:

- status, listener port, mDNS holati;
- local device + fingerprint;
- pairing offer (code faqat lokal snapshotda);
- connection metrikalari (latency, TLS, bytes);
- focus (controller/target, local bayroqlar);
- discovered va paired devices;
- oxirgi xato.

Appropriate expected errors `ServiceError(code, detail)` ko‘rinishida. UI
kodni lokalizatsiya qiladi. Kutilmagan xatolar logga stack trace bilan
yoziladi, UI esa umumiy xabar ko‘rsatadi.

## 7. Persistence

macOS:

```
~/Library/Application Support/ToRexLink/
├── settings.json
├── peers.json              # tokenlar, 0600
├── certs/device.pem
├── certs/device-key.pem
└── logs/torexlink.log
```

Windows:

```
%APPDATA%\ToRexLink\...
```

Token hech qachon log yoki UI’da ko‘rsatilmaydi. Sertifikat fingerprinti esa
public ma’lumot sifatida ko‘rsatiladi.

## 8. Resource lifecycle

- App `aboutToQuit` → controller shutdown → service stop → session/server/
  discovery/input/clipboard yopiladi → asyncio loop to‘xtaydi.
- Har bir session close input backend va clipboardni to‘xtatadi.
- Input queue chegaralangan (2048); to‘lib qolsa eng eski harakat tashlanadi,
  key/button yo‘qolsa session xavfsiz yopiladi.
- Server bir vaqtda 8 handshake bilan cheklanadi; faol peer 1 ta.
- Loglar rotating (5 MiB × 3).
