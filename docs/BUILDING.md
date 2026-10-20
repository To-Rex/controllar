# ToRexLink’ni ishga tushirish va yig‘ish

## 1. Talablar

| Platforma | Talab |
|---|---|
| macOS | macOS 14+ (PySide6 6.12 build target), Python 3.12, Xcode Command Line Tools |
| Windows | Windows 10/11 x64, Python 3.12 x64, PowerShell 5+ |
| Tarmoq | Ikkala qurilma bir xil ishonchli LAN’da |

Python 3.12 tavsiya etiladi. PyInstaller kross-kompilyatsiya qilmaydi:
macOS build faqat macOS’da, Windows build faqat Windows’da bajariladi.

## 2. Manbadan ishga tushirish

### macOS

```bash
xcode-select --install        # agar o‘rnatilmagan bo‘lsa
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m torexlink
```

### Windows

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m torexlink
```

Ishga tushirish variantlari:

```bash
python -m torexlink --background   # faqat tray/menu bar
python -m torexlink --debug        # batafsil log
python -m torexlink --version
```

## 3. macOS app bundle yig‘ish

```bash
chmod +x scripts/build_macos.sh
./scripts/build_macos.sh

# Natija:
# dist/ToRexLink.app
```

Build skripti:

1. `.venv` yaratadi va `requirements-dev.txt` o‘rnatadi;
2. ikonkalarni (`icns`, `ico`, PNG) generatsiya qiladi;
3. PyInstaller spec orqali `.app` yig‘adi;
4. mavjud bo‘lsa Developer ID bilan imzolaydi / aks holda ad-hoc imzo qo‘yadi;
5. `MAKE_DMG=1` bo‘lsa DMG yaratadi.

### Developer ID bilan imzolash

Production uchun barqaror imzo juda muhim: macOS TCC ruxsatlari imzo va bundle
ID bilan bog‘lanadi. Har build’da yangi ad-hoc imzo TCC ruxsatlarini
yo‘qotishi mumkin.

```bash
export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export NOTARY_PROFILE="torexlink-notary"   # ixtiyoriy
MAKE_DMG=1 ./scripts/build_macos.sh
```

Notarizatsiya (Apple ID bilan oldindan sozlangan profil):

```bash
xcrun notarytool store-credentials torexlink-notary \
  --apple-id "you@example.com" \
  --team-id TEAMID \
  --password "app-specific-password"
```

Keyin `NOTARY_PROFILE` bilan build qiling. Skript imzolangan `.app`ni zip
qiladi, `notarytool submit --wait` bajaradi va `stapler staple` qo‘llaydi.

### Info.plist

`packaging/Info.plist` quyidagilarni e’lon qiladi:

- `NSCameraUsageDescription` — QR skanerlash;
- `NSLocalNetworkUsageDescription` + `NSBonjourServices`
  (`_torexlink._tcp`) — LAN/mDNS;
- `NSAccessibilityUsageDescription` va `NSInputMonitoringUsageDescription`;
- `CFBundleIdentifier = uz.torexlink.app`;
- `LSMinimumSystemVersion = 14.0`.

Istisno: bu ilova App Sandbox’da ishlamaydi, chunki global HID event tap va
boshqa ilovalar ichiga event injection sandbox’da mumkin emas. Hardened
Runtime va notarizatsiya sandbox’siz ham qo‘llanadi.

### macOS ruxsatlarini tekshirish

Build ishga tushgach:

```
System Settings → Privacy & Security → Input Monitoring
System Settings → Privacy & Security → Accessibility
System Settings → Privacy & Security → Local Network
System Settings → Privacy & Security → Camera (QR uchun)
```

Ruxsat bergach ilovani **to‘liq yopib qayta oching**. `dist` ichidagi app’ni
har gal yangi joydan ishga tushirmang — TCC path/imzo o‘zgaradi.

Foydali tekshiruv:

```bash
codesign --verify --deep --strict --verbose=2 dist/ToRexLink.app
codesign -dv --verbose=4 dist/ToRexLink.app
spctl --assess --type execute --verbose=4 dist/ToRexLink.app
```

Ad-hoc imzo bilan `spctl` reject bo‘lishi normal. Developer ID + notarizatsiya
bilan Gatekeeper assess o‘tishi kerak.

TCC’ni qayta boshlash (faqat dev, ehtiyotkorlik bilan):

```bash
tccutil reset Accessibility uz.torexlink.app
tccutil reset ListenEvent uz.torexlink.app
```

`ListenEvent`/Input Monitoring reset bundle ID’ga bog‘liq bo‘lmasligi mumkin;
System Settings’dan qo‘lda o‘chirib qayta ruxsat bering.

### Yarim qolgan yoki buzilgan identity

Quyidagi fayllar bo‘lsa, ilova ularni `.broken-*` yoki `.orphan-*` nomi bilan
saqlaydi va yangi identity yaratadi:

```
~/Library/Application Support/ToRexLink/certs/device.pem
~/Library/Application Support/ToRexLink/certs/device-key.pem
```

Eski identity’ni tiklash uchun ilovani yopib, backup faylni asl nomiga
qaytaring. Peer’lar eski fingerprintni pinlagani uchun qayta juftlash kerak
bo‘lishi mumkin.

## 4. Windows build

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build_windows.ps1
```

Natijalar:

```
dist\ToRexLink\ToRexLink.exe
dist\ToRexLink-Setup.exe        # Inno Setup o‘rnatilgan bo‘lsa
```

Skript Python 3.12, PyInstaller va ikonkalarni tayyorlaydi. Windows’da
`ToRexLink.ico`, version metadata va `console=False` ishlatiladi.

### Inno Setup installer

- `packaging/ToRexLink.iss`
- `PrivilegesRequired=lowest` — HKCU’ga o‘rnatadi;
- firewall o‘rnatish vaqtida **o‘zgartirilmaydi**; ilova ichida UAC bilan,
  foydalanuvchi tasdig‘idan keyin sozlanadi;
- autostart ham foydalanuvchi xohishiga ko‘ra ilova ichida yoqiladi.

### Windows’da imzolash

Production uchun Authenticode (EV yoki OV code signing) tavsiya etiladi.
Imzosiz `.exe` SmartScreen ogohlantirishi beradi. `signtool` bilan imzolang:

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
  /a "dist\ToRexLink\ToRexLink.exe"
```

Hooks va `SendInput` tufayli ba’zi antiviruslar dastlab ogohlantirishi
mumkin; imzo, barqaror versiya va vendor hujjatlari bilan kamaytiriladi.

## 5. Ikonkalar

```bash
python scripts/generate_icons.py
```

- `packaging/assets/ToRexLink.icns`
- `packaging/assets/ToRexLink.ico`
- `packaging/assets/ToRexLink-*.png`
- `torexlink/resources/torexlink.png`

Manba: `torexlink/resources/torexlink.svg`. macOS’da `.icns` yaratish uchun
`iconutil` kerak (Xcode CLT bilan keladi).

## 6. PyInstaller spec

`packaging/ToRexLink.spec`:

- `run.py` entry;
- barcha `torexlink/resources` data;
- PyObjC/Quartz/AppKit/ApplicationServices hidden importlari (macOS);
- zeroconf submodullari;
- PySide6 WebEngine/PDF kabi keraksiz katta modullar chiqarib tashlangan;
- `.app` Info.plist va ikonka;
- Windows version resource va ikonka;
- `console=False`.

Builddan keyin `dist` papkasini saqlang. `build/` va `.venv/` repoga
kiritilmaydi.

## 7. Versiyani oshirish

Bir vaqtda yangilang:

1. `torexlink/constants.py` — `APP_VERSION`
2. `pyproject.toml` — `project.version`
3. `packaging/Info.plist` — `CFBundleShortVersionString`, `CFBundleVersion`
4. `packaging/version_info.txt` — file/product version
5. `CHANGELOG.md`

## 8. Yig‘ilgan ilovani tekshirish

macOS:

```bash
open dist/ToRexLink.app
# yoki menu barda ishlatish:
open dist/ToRexLink.app --args --background
```

Windows:

```powershell
.\dist\ToRexLink\ToRexLink.exe
```

Yig‘ilgan ilovaning bog‘liqliklarini tez tekshirish:

```bash
dist/ToRexLink.app/Contents/MacOS/ToRexLink --self-test
# Windows:
dist\ToRexLink\ToRexLink.exe --self-test
```

JSON natijada `"ok": true` bo‘lishi kerak. Bu Qt oynasini ochmaydi va maxfiy
ma’lumot chiqarmaydi.

Tekshirish ro‘yxati:

- onboarding, til va mavzu;
- mac permission statuslari;
- mDNS ko‘rinishi va kod/QR;
- ikki qurilma pairing;
- cursor edge/hotkey;
- clipboard ikki yo‘nalishda;
- tray/menu bar, background close, avtostart;
- Windows firewall UAC;
- qayta ishga tushirgandan keyin auto reconnect;
- logs/diagnostics.

Batafsil QA: [`TESTING.md`](TESTING.md).

## 9. Muammolarni bartaraf etish

- **PyInstaller PySide6 topmayapti** — `.venv`ni tozalab qayta yarating va
  PySide6-Essentials + Addons mos versiyasini o‘rnating.
- **`iconutil` topilmadi** — Xcode Command Line Tools’ni o‘rnating.
- **TCC input ishlamaydi** — appni `dist`dan ishga tushiring, izchil imzo
  ishlating va System Settings’da ruxsat bering.
- **Windows firewall status noto‘g‘ri** — `netsh advfirewall firewall show
  rule name="ToRexLink Inbound TCP"` bilan tekshiring.
- **mDNS ishlamaydi** — local network ruxsati, Wi‑Fi client isolation va
  firewallni tekshiring; IP orqali ulanish zaxira yo‘l.
