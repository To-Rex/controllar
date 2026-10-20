# Muammolarni bartaraf etish

## Kod qabul qilinmayapti

- Kod 6 raqamdan iborat bo‘lishi va host ekranidagi kod bilan aynan mos
  kelishi kerak.
- Kod 180 sekund amal qiladi. Muddati tugagan bo‘lsa **Yangi kod** yarating.
- 5 marta xato kiritilsa kod bekor qilinadi.
- Host va guest bir xil LAN’da bo‘lishi kerak; guest Wi‑Fi client isolation
  yoqilgan bo‘lsa, qurilmalar bir-birini ko‘rmaydi.
- Pin/fingerprint xatosi bo‘lsa, QR orqali qayta juftlang.

## Qurilma topilmayapti (mDNS)

- Ikkala qurilma ham “Qurilmalarni qabul qilish” yoqilgan bo‘lsin.
- macOS’da **Local Network** ruxsatini bering.
- Windows Firewall’da ToRexLink qoidalarini sozlang (Sozlamalar → Windows
  Firewall).
- Router’da “AP/client isolation”ni o‘chiring.
- mDNS ishlamasa, **IP orqali** ulaning: host IP’sini `ipconfig`
  (Windows) yoki `ipconfig getifaddr en0` (macOS) bilan toping.

## macOS ruxsatlari ishlamayapti

1. System Settings → Privacy & Security → **Input Monitoring** → ToRexLink.
2. System Settings → Privacy & Security → **Accessibility** → ToRexLink.
3. Ruxsatni o‘chirib qayta yoqing yoki `+` bilan app’ni qo‘shing.
4. ToRexLink’ni to‘liq yoping va qayta oching.
5. Diagnostika bo‘limida ikkala ruxsat “Ruxsat berilgan” bo‘lishi kerak.

Ad-hoc imzo bilan build qilingan bo‘lsa, TCC ruxsatlari yangilanishdan keyin
qayta so‘ralishi mumkin. Developer ID bilan imzolangan barqaror build
ishlatish tavsiya etiladi.

## Ruxsat bergandan keyin ham input ishlamaydi

- Ba’zi macOS versiyalarida ruxsat bergach ilovani qayta ishga tushirish
  shart.
- macOS Secure Input (masalan, parol maydoni, login oynasi) synthetic inputni
  bloklaydi. Bu kutilgan xatti-harakat.
- Boshqa xavfsizlik dasturi event tap’ni bloklashi mumkin; ularni vaqtincha
  o‘chirib ko‘ring.
- Diagnostika → ruxsatlar va loglarni tekshiring.

## Windows’da input ishlamaydi

- UAC oynasi, lock screen va Ctrl+Alt+Del Secure Desktop’da ishlamaydi —
  bu Windows cheklovi.
- Ba’zi antiviruslar low-level hookni bloklaydi. ToRexLink’ni ishonchli
  dastur sifatida belgilang.
- Dasturni oddiy foydalanuvchi sifatida ishga tushiring; admin talab
  qilinmaydi (firewall UAC bundan mustasno).
- DPI scaling 200% bo‘lsa cursor mappingni tekshiring.

## Cursor chetidan o‘tmayapti

- Sozlamalar → **Kursor o‘tadigan ekran cheti** to‘g‘ri tanlanganini
  tekshiring. Ikkinchi kompyuter qaysi tomonda bo‘lsa, o‘sha tomon.
- “Ekran chetidan o‘tish” yoqilgan bo‘lsin.
- Multi-monitor mavjud bo‘lsa, hozircha faqat asosiy monitor qo‘llanadi.
- Edge switchdan tashqari `Ctrl+Alt+Space` har doim ishlaydi.

## Sichqoncha/klaviatura “qotib” qoldi

- `Ctrl+Alt+Space` bilan fokusni qaytaring yoki boshqa kompyuterning
  sichqonchasini qo‘zg‘ating.
- Kutilmagan holatda ilovani tray’dan **Chiqish** qilib qayta oching.
- Lokal modifier tugab qolgan bo‘lsa, Shift/Ctrl/Alt tugmalarini bir marta
  bosing.
- macOS event tap OS tomonidan vaqtincha o‘chirilsa, ilova uni avtomatik
  qayta yoqadi (logda `event tap was disabled...` yozuvi bo‘lishi mumkin).

## Clipboard sinxronlanmayapti

- Sozlamalarda **Clipboard sinxronizatsiyasi** yoqilganini tekshiring.
- Faollash faqat ulanish vaqtida ishlaydi.
- 1 MB’dan katta matn o‘tkazilmaydi.
- Faqat matn; rasm/fayl/HTML uzatilmaydi.
- Ba’zi xavfsizlik dasturlari clipboardni boshqaradi; ularni vaqtincha
  o‘chirib ko‘ring.
- Ikkala tomonda ham matn nusxalashni sinab ko‘ring.

## Windows firewall ruxsati xatosi

- **Sozlamalar → Xavfsizlik → Windows Firewall** yoki **Diagnostika** orqali
  qayta urinib ko‘ring.
- UAC oynasida **Yes** tanlanishi kerak.
- Windows’da `netsh` man etilgan bo‘lsa, qo‘lda qo‘shing (faqat private,
  domain):

```bat
netsh advfirewall firewall add rule name="ToRexLink Inbound TCP" dir=in action=allow protocol=TCP localport=48620-48629 profile=private,domain
netsh advfirewall firewall add rule name="ToRexLink mDNS UDP" dir=in action=allow protocol=UDP localport=5353 profile=private,domain
```

- Public Wi‑Fi profilida portlar ataylab ochilmaydi.

## Auto reconnect ishlamayapti

- **Avtomatik qayta ulanish** yoqilganini tekshiring.
- Guest ilovasi ishlab turishi kerak.
- Host ishlab turishi va listener porti ochiq bo‘lishi kerak.
- Token yaroqsiz bo‘lsa (peer o‘chirilgan, cert almashtirilgan), auto
  reconnect to‘xtaydi. **Qurilmalar** bo‘limidan qayta juftlang.
- Tarmoq o‘zgargan bo‘lsa mDNS 8 sekundgacha yangilanadi; IP orqali qayta
  ulanish zaxira yo‘l.

## Ilova ochilmayapti

- macOS: `xattr -dr com.apple.quarantine /Applications/ToRexLink.app` faqat
  ishonchli, o‘zingiz yig‘gan build uchun. Developer ID + notarizatsiya
  ishlatish afzal.
- Windows SmartScreen: **More info → Run anyway** faqat ishonchli build uchun.
- Loglarni tekshiring:
  - macOS: `~/Library/Application Support/ToRexLink/logs/torexlink.log`
  - Windows: `%APPDATA%\ToRexLink\logs\torexlink.log`

## Port band

ToRexLink 48620–48629 oralig‘idan bo‘sh portni avtomatik tanlaydi. Port
o‘zgargan bo‘lsa, auto reconnect avval eski portni, keyin mDNS’dagi yangi
portni sinaydi. Guest eski portda qolsa, mDNS/Local Network ruxsatini
tekshiring yoki qayta ulang.

## Log va maxfiy ma’lumotlar

Loglarni yuborishdan oldin ularni ko‘zdan kechiring. ToRexLink token, kod va
clipboard mazmunini logga yozmaydi, lekin kompyuter nomi, LAN IP va xato
tafsilotlari mavjud bo‘lishi mumkin. Ularni umumiy joyga yuklashdan oldin
tozalang.

## Yordam uchun ma’lumot yig‘ish

**Diagnostika → Hisobotni nusxalash** yoki **Faylga saqlash**. Hisobotda
maxfiy tokenlar, juftlash kodlari va clipboard mazmuni yo‘q. Muammo tavsifi,
OS versiyasi, ikki qurilma turi va hisobotni yuboring.
