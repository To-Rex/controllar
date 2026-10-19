# FlowLink — O‘rnatish va foydalanish qo‘llanmasi (o‘zbekcha)

FlowLink — bitta klaviatura, sichqoncha va clipboard orqali macOS va Windows
kompyuterlarni birgalikda boshqarish dasturi (Barrier/Synergy muqobili).
Aloqa TLS 1.3 bilan shifrlanadi; qurilmalar juftlik kodi orqali bir marta
tanishtiriladi va keyin o‘zaro eslab qolinadi.

---

## 1. Tushunchalar

| Atama | Ma’nosi |
|---|---|
| **Server** | Boshqaruvchi kompyuter. Uning klaviaturasi/sichqonchasi chetga o‘tganda narigi kompyuterni boshqaradi. |
| **Client** | Boshqariladigan kompyuter. Undan serverga qaytish ham mumkin (kursorni orqaga surish yoki hotkey orqali). |
| **Juftlik kodi** | Server ekranida ko‘rinadigan 6 xonali kod. 10 daqiqa amal qiladi, 5 marta xato kiritilsa yangilanadi. |
| **Hotkey** | Boshqaruvni almashtiruvchi tugmalar birikmasi. Standart: `Ctrl + Alt + Space`. |
| **Chet (edge)** | Server kursori qaysi tomondan chiqib narigi kompyuterga o‘tadi (chap/o‘ng/yuqori/past). |

## 2. Talablar

- macOS 13 yoki yangiroq (Apple Silicon yoki Intel), yoki Windows 10/11.
- Ikkala kompyuter **bitta lokal tarmoqda** (Wi-Fi yoki kabel).
- macOS’da ikki ruxsat: **Accessibility** va **Input Monitoring** (dastur o‘zi so‘raydi).
- Build qilish uchun: Rust (rustup) va macOS’da Xcode yoki Command Line Tools.

## 3. O‘rnatish

### macOS

```bash
# Rust o‘rnatilmagan bo‘lsa:
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Loyihani build qilish (dist/FlowLink.app hosil bo‘ladi):
./scripts/build-macos.sh

# Ishga tushirish:
open dist/FlowLink.app
```

> Eslatma: SwiftUI makroslari uchun Xcode kerak. Faqat Command Line Tools
> bo‘lsa, `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer` muhit
> o‘zgaruvchisini o‘rnatib build qiling.

### Windows

```powershell
# Rust o‘rnatilgan bo‘lishi kerak (https://rustup.rs), MSVC Build Tools bilan
cargo build --release --manifest-path rust/Cargo.toml -p flowlink-win
# Natija:
#   rust\target\release\flowlink-win.exe
```

FlowLink.exe’ni ishga tushiring — u tray (system tray)da turadi.

## 4. Birinchi ishga tushirish

1. **Rejim tanlang.** Dastur birinchi ochilganda “Server” yoki “Client”
   kartochkasini tanlaysiz va kompyuter nomini yozasiz. Rejimni keyin
   Sozlamalar yoki menu bar orqali istalgan payt almashtirish mumkin.
2. **Ruxsatlar (macOS).** “Grant” tugmalari orqali Accessibility va Input
   Monitoring ruxsatlarini bering
   (System Settings → Privacy & Security → Accessibility / Input Monitoring).
   Ruxsat berilgach dastur avtomatik aniqlaydi.
3. **Finish setup** tugmasi rejimni saqlaydi va ulanishni boshlaydi.

### Juftlash (pair qilish)

**Server kompyuterda:**
- Dashboard’da katta 6 xonali kod va “Show QR code” tugmasi bor.
- **Windows’da birinchi marta:** “NETWORK” kartasidagi **Allow through
  firewall…** tugmasini bosing (bir marta admin ruxsati so‘raladi). Bu
  bo‘lmasa narigi kompyuter Windows’ga ulan olmaydi.

**Client kompyuterda** (Client rejimida):
- *Found on this network* ro‘yxatidan serverni tanlab **Connect** bosing, yoki
- **Scan QR code** bilan serverdagi QR’ni skanerlang (eng tez usul — IP va
  kod avtomatik to‘ladi), yoki
- **Connect manually** bo‘limida IP + port (standart 24800) va kodni kiriting
  (yoki `flowlink://…` havolasini qo‘yib **Open** bosing).
- Server ekranidagi kod clientdagi maydonga kiritiladi → **Pair**.

Juftlashдan keyin ikkala tomon ham bir-birini eslab qoladi: keyingi safar
client avtomatik ulanadi (token orqali, kodsiz).

## 5. Kundalik foydalanish

- **Kursor server chegarasidan chiqadi** → boshqaruv clientga o‘tadi
  (server kursori shu payt yashiriladi).
- **Clientda kursorni kirgan chetiga qarab sursangiz** → boshqaruv serverga
  qaytadi.
- **`Ctrl + Alt + Space`** (standart hotkey) — boshqaruvni darhol almashtiradi.
  Hotkey’ni Sozlamalar → Toggle hotkey → **Change** orqali o‘zgartirish mumkin.
- **Clipboard**: matn ikki tomonga avtomatik sinxronlanadi (1 MB gacha).
  Parol menejerlarining “concealed” nusxalari **hech qachon** uzatilmaydi.
- **Chetni o‘zgartirish**: Sozlamalar → Cursor exit edge (Chap/O‘ng/Yuqori/Past).
- **Fon rejimi**: oynani yopsangiz ham dastur menu bar/tray’da ishlashda davom
  etadi. Menu bar belgisidan: boshqaruvni olish/qaytarish, rejimni almashtirish,
  to‘xtatish/ishga tushirish, chiqish.
- **Tizim bilan avtomatik ishga tushish**: Sozlamalar → Launch at login.

## 6. Muammolarni bartaraf qilish

| Muammo | Yechim |
|---|---|
| **Windows: QR/kod ko‘rinmayapti, „No pairing code yet“** | Odatda ikki sabab: (1) ilova ikki nusxada ochilgan — tray’dagi barcha FlowLink nusxalarini **Quit** qilib, faqat bittasini ishga tushiring (yangi versiya buni avtomatik bloklaydi va ogohlantiradi); (2) server rejimda emas — Sozlamalarda **Server** rejimini tanlab **Start** bosing. |
| **Port band (masalan 24800 ishlatilib bo‘lingan)** | Endi tashvishlanmang: FlowLink avtomatik keyingi bo‘sh portga o‘tadi (24801, 24802…) va buni o‘zi bildiradi. QR/invite link va tarmoqda ko‘rinish **haqiqiy portni** ishlatadi — hech narsa qo‘lda o‘zgartirish shart emas. |
| **Windows serverga ulanib bo‘lmayapti (eng ko‘p uchraydigan sabab!)** | Windows server sahifasida **NETWORK** kartasini toping → **Allow through firewall…** tugmasini bosing (bir marta administrator ruxsati so‘raladi) → qoidalar qo‘shiladi. Shu tugma TCP 24800 va UDP 5353 qoidalarini yaratadi. Qo‘lda ham mumkin (admin CMD): `netsh advfirewall firewall add rule name="FlowLink" dir=in action=allow protocol=TCP localport=24800` |
| **„Found on this network“ ro‘yxatida server ko‘rinmayapti** | Windows Firewall discovery (UDP 5353) ni bloklayapti yoki routerda „client isolation“ yoqilgan. Yechim: serverdagi **NETWORK** kartasidagi manzilni (masalan `192.168.1.50:24800`) clientda **Connect manually**ga kiriting — bu har doim ishlaydi. |
| **Umuman ulanmayapti (Wi-Fi ↔ Ethernet)** | Ikkala kompyuter **bitta router/tarmoqda** bo‘lishi kerak. Birinchi tekshiruv: Windows’da `ipconfig` → `192.168.x.x`, Mac’da `ifconfig` → bir xil `192.168.x.*` bo‘lishi kerak. Windows’da firewall ruxsatini bajaring (yuqoriga qarang). |
| Client serverni ko‘rmayapti | Ikkala kompyuter bir tarmoqda ekaniga ishonch hosil qiling; server rejimida “Listening” holatida turishi kerak. Manuel IP bilan urinib ko‘ring. |
| Ruxsat so‘ralmagan / ishlmayapti | System Settings → Privacy & Security’da FlowLink’ga Accessibility va Input Monitoring bering, dasturni qayta ishga tushiring. |
| Klaviatura ishlamayapti (macOS) | Parol maydonlari va Terminal’dagi *Secure Keyboard Entry* rejimida tizim sintetik klaviaturani bloklaydi — bu macOS cheklovi. |
| Windows’da ba’zi dasturlar javob bermayapti | Administrator sifatida ochilgan oynalar (UAC doirasi) FlowLink’ni ham administrator qilib ishga tushirishni talab qiladi. |
| Kod eskirgan | Serverda “New code” tugmasini bosing (yoki 3 xato urinishdan keyin avtomatik yangilanadi). |
| Log kerak bo‘lsa | Sozlamalar → Open logs folder yoki dastur ichidagi **Logs** sahifasi. |

> **Muhim:** eski versiyada bu muammolar (QR chiqmasligi, firewall) tuzatilmagan.
> Ikkala kompyuterda ham **yangilangan build** ishlayotganiga ishonch hosil
> qiling (Mac: `./scripts/build-macos.sh` → `dist/FlowLink.app`;
> Windows: `cargo build --release -p flowlink-win`).

## 7. Xavfsizlik qisqacha

- Har bir qurilma o‘z sertifikatiga ega; uning SHA-256 barmoq izi juftlashda
  pinlanadi. Qayta ulanishda barmoq izi o‘zgargan bo‘lsa, aloqa **to‘xtatiladi**.
- Juftlash tokeni faqat juftlash paytida TLS ichida uzatiladi va faylda
  0600 ruxsat bilan saqlanadi.
- QR/kod 10 daqiqa amal qiladi — uni begonalarga yubormang.
- Batafsil: `docs/SECURITY.md`.

## 8. Sozlamalar va fayllar joyi

- macOS: `~/Library/Application Support/FlowLink/`
  (`config.json`, `identity/`, `logs/`)
- Windows: `%APPDATA%\FlowLink\`
- Faqat matnli clipboard, 1 MB limiti, bitta aktiv client, macOS 13+ va
  Windows 10+ kabi cheklovlar: `docs/PLATFORM-LIMITS.md`.

## 9. Sinovlar

```bash
./scripts/test.sh        # Rust (unit + 2 ta haqiqiy e2e stsenariy) + Swift testlar
```
