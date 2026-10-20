# Changelog

Barcha muhim o‘zgarishlar shu faylda qayd etiladi. Format
[Keep a Changelog](https://keepachangelog.com/)ga asoslangan; versiyalar
Semantic Versioning’ga ergashadi.

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
