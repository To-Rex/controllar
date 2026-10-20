# Changelog

Barcha muhim o‘zgarishlar shu faylda qayd etiladi. Format
[Keep a Changelog](https://keepachangelog.com/)ga asoslangan; versiyalar
Semantic Versioning’ga ergashadi.

## [1.0.4] — 2026-10-20

### Fixed

- Mac Windows’ni boshqarayotganda lokal kursor endi ko‘rinadigan tarzda
  siljimaydi. Har bir forwarding mouse event’idan keyin lokal pointer
  `CGWarpMouseCursorPosition` bilan markazga qaytariladi; bu warp qo‘shimcha
  event yaratmaydi.
- `CGAssociateMouseAndMouseCursorPosition(False)` ishlatilmaydi: u crash
  bo‘lganda butun macOS sichqonchasini qotirib qo‘yishi mumkin edi. Warp usuli
  xavfsiz va Windows’ga deltalar uzluksiz boradi.
- Passive/idle kompyuter kursori ham yashirilgan va qotirilgan holatda qoladi.
- Regressiya testi har bir forwarding move’da `freeze_pointer_motion`
  chaqirilishini tekshiradi.

## [1.0.3] — 2026-10-20

### Fixed

- Boshqaruvchi Mac’da kursor Windows’ga o‘tganda lokal kursor endi
  harakatlanmaydi: macOS event tap move eventlarini to‘xtatadi, CGEvent
  deltalari esa Windows’ga uzatiladi. Kursor markazda qotiriladi.
- Forwarding va passive holatda kursor yashiriladi; sessiya boshlanishida uni
  qayta ko‘rsatib yuborgan start-tartibi tuzatildi.
- macOS’da kursor yana ko‘rinib qolsa, `CGCursorIsDrawnInFramebuffer` orqali
  davriy qayta yashirish va hide-count balansi qo‘shildi.
- Fokusni faqat sichqoncha harakati bilan emas, **klaviatura tugmasi, sichqoncha
  bosishi yoki `Ctrl+Alt+Space`** bilan olish mumkin. Bu remote injected
  harakatlar yoki tasodifiy siljishlar sabab fokus “sakrashini” yo‘qotadi.
- Faqat faol kompyuter kursori ko‘rinadi; ikkinchi kompyuter kursori yashirin
  va harakatsiz bo‘ladi.

## [1.0.2] — 2026-10-20

### Fixed

- Target-edge return endi ataylab 36 px tashqariga bosishni talab qiladi.
  Chekka bir marta tegib o‘tish yoki kursorning kichik tebranishi Mac’ga
  qaytarib yubormaydi.
- Return settle oynasi 150 ms → 350 ms; kursor chetga yetib kelgan eventning
  o‘zi return qilmaydi, faqat chekka yetib kelgandan keyingi davomiy harakat
  hisobga olinadi.
- Peer ilova versiyasi lokal versiyadan farq qilsa, logda ogohlantirish
  yoziladi — ikki qurilma turli versiyada qolib ketishi handoff’ni
  buzmasligi uchun.
- Windows uchun GitHub Actions orqali portable build workflow’i qo‘shildi.

## [1.0.1] — 2026-10-20

### Fixed

- KRITIK: `JoinDialog`dagi spinner noto‘g‘ri `QPen` argumenti tufayli macOS’da
  ilova segfault bo‘lishi tuzatildi. Barcha custom `paintEvent`lar endi
  istisnolarga chidamli va `QPainter` har doim yopiladi.
- Cursor edge handoff: qaytishdan so‘ng darhol yana edge’ga chiqib ketish
  (ping-pong) oldini olish uchun 56 px ichkariga harakat qilish talab qilinadi;
  target tomonda ham return gesture 40 px re-arm bilan himoyalandi.
- Tasodifiy takeover’ni kamaytirish uchun sichqoncha harakati chegarasi
  8 px → 24 px.
- Ikki tomonlama cursor o‘tishini tekshiruvchi regressiya testi qo‘shildi.

## [1.0.0] — 2026-10-20

### Added

- Windows va macOS uchun ToRexLink secure KVM.
- Host/guest TLS 1.3 connections with certificate pinning.
- CPace P-256 short-code pairing, 5-attempt limit, 180 s TTL.
- QR pairing via camera or image; 6-digit manual code.
- 256-bit pairing token + RSA-PSS signatures for automatic reconnect.
- mDNS discovery/advertisement and network-change refresh.
- Focus arbitration with screen-edge and `Ctrl+Alt+Space` switching.
- Two-way physical takeover support from either computer.
- macOS Quartz event tap + CGEvent injection.
- Windows low-level hooks + `SendInput`.
- Bidirectional plain-text clipboard sync up to 1 MiB.
- macOS menu bar and Windows system tray, background mode, launch at login.
- Windows Firewall consent flow via UAC (ports only, private/domain).
- Full Uzbek UI plus Russian and English, live switching.
- Light, dark and system themes.
- Status, connection, devices, settings, logs and diagnostics pages.
- Rotating logs with secret redaction.
- Unit and integration tests; PyInstaller macOS/Windows build scripts.
- UZ documentation: architecture, protocol, security, build, QA and
  troubleshooting.
