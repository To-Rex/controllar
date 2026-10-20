# Testlash va QA

## 1. Avtomatik testlar

```bash
python -m pytest -q
```

Talab: `pytest` va `pytest-asyncio` (`requirements-dev.txt`).

Test to‘plamlari:

| Fayl | Nima tekshiriladi |
|---|---|
| `test_pairing_crypto.py` | P-256 arithmetic, CPace agreement, wrong code, domain separation |
| `test_identity.py` | RSA cert generation/persistence, fingerprint, signatures, 0600 |
| `test_auth_and_focus.py` | auth transcript, HMAC + RSA-PSS proofs, focus state machine |
| `test_framing.py` | length frames, oversized/malformed rejection, input/clipboard validation |
| `test_pairing_payload.py` | QR URI/JSON roundtrip, invalid payloads, endpoint filtering |
| `test_settings_and_peers.py` | settings validation, corrupt backup, peer store/token permissions |
| `test_clipboard.py` | local send-once, remote loop prevention, digest and size limits |
| `test_service_integration.py` | real TLS 1.3 pair, token reconnect, auto-reconnect, clipboard, focus, edge takeover |
| `test_i18n.py` | UZ/RU/EN key parity, placeholders, static key coverage, fallback |
| `test_ui_smoke.py` | offscreen UI pages, language switch, dialogs, QR decode |

Integration testlari haqiqiy TLS server/client, sertifikat pinning, CPace va
focus oqimini ishlatadi. Faqat native input va clipboard mock qilinadi —
chunki test paytida foydalanuvchi klaviaturasi/sichqonchasi va real clipboardni
egallash xavfli.

Joriy natija (macOS 27, arm64):

```
46 passed
```

## 2. Qo‘lda tekshirilgan (bu loyihada)

- macOS native Quartz event tap yaratish va to‘xtatish, Input Monitoring +
  Accessibility preflight.
- O‘z synthetic eventlarini event tap’dan filtrlash (source PID + user data).
- Native NSPasteboard read/write roundtrip va tiklash.
- mDNS advertise + boshqa `DiscoveryService` tomonidan topish.
- PyInstaller `.app` yig‘ish va ishga tushirish (offscreen), mDNS va
  identity generatsiyasi.
- Onboarding, light/dark UI, UZ/RU, QR decode (kamera yo‘qligi fallbacki
  bilan), dialog layoutlari.
- Barcha avtomatik testlar.
- **Windows native backend real Windows qurilmasida ishga tushirilmagan.**
  Quyidagi QA Windows 10/11 qurilmasida bajarilishi shart.

## 3. Ikki qurilma QA checklist

### Tayyorgarlik

- Bir xil LAN, client isolation o‘chirilgan.
- Ikkala qurilmada ToRexLink 1.0.1.
- Windows firewall ruxsati berilgan.
- macOS ruxsatlari berilgan va ilova qayta ochilgan.

### Pairing

- [ ] Host kod yaratadi; kod 6 raqam, 3 daqiqa countdown.
- [ ] Guest kodni qo‘lda kiritib ulanadi.
- [ ] Guest QR’ni kameradan skanerlaydi.
- [ ] Kamera yo‘q holatda QR rasm faylidan skanerlanadi.
- [ ] Noto‘g‘ri kod rad etiladi; 5 xatodan keyin kod yopiladi.
- [ ] Pairing paytida muddat tugashi UI’da ko‘rinadi.
- [ ] Juftlashdan keyin darhol ulanish o‘rnatiladi.
- [ ] Ikkala tomonda device nomi va fingerprint to‘g‘ri ko‘rinadi.

### Autentifikatsiya va reconnect

- [ ] Guest’ni qayta ishga tushirish → token bilan auto reconnect.
- [ ] Host’ni qayta ishga tushirish → guest backoff bilan qayta ulanadi.
- [ ] Network kabelini uzish/ulash → auto reconnect.
- [ ] Peer’ni o‘chirish → token o‘chadi; eski token bilan ulanish rad etiladi.
- [ ] Boshqa kompyuterdan token/peer ma’lumotini ko‘chirib bo‘lmaydi.
- [ ] Sertifikat fingerprinti diagnostikada ko‘rinadi.

### Input

- [ ] `Ctrl+Alt+Space` bilan fokus oldinga/orqaga o‘tadi.
- [ ] O‘ng/left/top/bottom edge sozlamasi cursor chiqishini boshqaradi.
- [ ] Aktiv host’da yozish remote’ga boradi.
- [ ] Guest’ning fizik sichqonchasi tegsa boshqaruv guest’ga qaytadi.
- [ ] Guest’ning fizik klaviaturasi tegsa boshqaruv guest’ga qaytadi.
- [ ] Inject qilingan harakatlar takeover trigger qilmaydi.
- [ ] Cursor remote’da to‘g‘ri joyga warp bo‘ladi.
- [ ] Remote tugmalar (Ctrl+C, Cmd+C, Shift+arrow, F1–F12) ishlaydi.
- [ ] Caps Lock holati chalkashmaydi.
- [ ] Hotkeydan keyin lokal modifier tugab qolmaydi.
- [ ] macOS: dock/menyu va boshqa app input suppression to‘g‘ri.
- [ ] Windows: secure desktop (UAC/Ctrl+Alt+Del) boshqarilmaydi va bu kutilgan.

### Clipboard

- [ ] Oddiy matn A→B va B→A sinxronlanadi.
- [ ] 1 MB limitdan katta matn o‘tkazilmaydi, noto‘g‘ri xabar yo‘q.
- [ ] Clipboard-loop yo‘q (bir marta set → qayta yuborilmaydi).
- [ ] Clipboard o‘chirilgan sozlamada hech narsa yuborilmaydi.
- [ ] Emoji va Unicode matn buzilmaydi.

### UI / lifecycle

- [ ] Barcha sahifalar UZ, RU, EN’da to‘g‘ri ko‘rinadi (matn kesilmaydi).
- [ ] Light/dark/system mavzular jonli almashadi.
- [ ] Oyna yopilganda background mode’da tray/menu barga yashiriladi.
- [ ] Tray’dan Show, Pair, Devices, Disconnect, Quit ishlaydi.
- [ ] Launch at login yoqilgach qayta login’da ilova fonda ishga tushadi.
- [ ] macOS menu bar icon mavjud; Windows tray icon mavjud.
- [ ] Loglar eksporti, diagnostika hisoboti nusxalash/eksport ishlaydi.
- [ ] Windows firewall qoidasi foydalanuvchi tasdig‘isiz qo‘shilmaydi.
- [ ] Quit paytida barcha oqimlar yopiladi; process osilib qolmaydi.

### Security regression

- [ ] TLS 1.2 yoki ALPN mismatch ulanishi rad etiladi.
- [ ] Boshqa sertifikat bilan ulanish pinned client tomonidan rad etiladi.
- [ ] Noto‘g‘ri token/server proof rad etiladi.
- [ ] Oversized frame, noto‘g‘ri JSON, noma’lum eventlar sessionni xavfsiz
      yopadi.
- [ ] Log faylida token, kod yoki clipboard matni yo‘q.
- [ ] Input backend faqat ruxsatlar berilganda ishga tushadi.

## 4. Native Windows uchun qo‘shimcha QA

- [ ] `SendInput` bilan Unicode bo‘lmagan key mapping (Ctrl+shortcuts).
- [ ] Mouse delta cursor chetida centerga warp qilinadi.
- [ ] Injected hook filtri ishlaydi (loop yo‘q).
- [ ] Admin bo‘lmagan foydalanuvchi UAC orqali firewall qoidasini qo‘sha oladi.
- [ ] Antivirus/SmartScreen bilan imzolangan build tekshiriladi.
- [ ] HKCU `Run` autostart yoqish/o‘chirish.
- [ ] 100%, 125%, 150%, 200% DPI scaling.
- [ ] Windows 10 va 11, Intel/AMD/ARM (Windows on ARM test bo‘lsa).

## 5. Testni qanday ishga tushirish

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

UI smoke test headless rejimda ishlaydi (`QT_QPA_PLATFORM=offscreen`). macOS
native testlari ruxsatga bog‘liq; avtomatik testlar ularni ishlatmaydi.

## 6. Screenshotlarni qayta yaratish

```bash
QT_QPA_PLATFORM=offscreen python scripts/capture_screenshots.py
```

Skript real service va sahifalarni ishlatadi, faqat input backend no-op
almashtiriladi. `docs/images/` yangilanadi.
