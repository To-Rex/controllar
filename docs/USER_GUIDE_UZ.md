# ToRexLink foydalanuvchi qo‘llanmasi

## 1. Bu nima?

ToRexLink ikki kompyuterni bitta klaviatura va sichqoncha bilan boshqarish
uchun dastur. Kompyuterlar bir-biriga to‘g‘ridan-to‘g‘ri lokal tarmoq orqali
(xavfsiz TLS kanalda) ulanadi. Internet, akkaunt yoki server kerak emas.

## 2. Birinchi ishga tushirish

1. ToRexLink’ni oching.
2. Tilni tanlang: **O‘zbekcha / Русский / English**.
3. Qurilmaga nom bering (masalan, “Studio Mac”).
4. macOS’da tizim ruxsatlarini bering:
   - Input Monitoring;
   - Accessibility.
5. Ilovani qayta ishga tushiring.
6. **Boshlash** tugmasini bosing.

ToRexLink fonda ishlaydi: oynani yopsangiz, macOS menu bar yoki Windows
system tray’da qoladi. Tray menyusidan istalgan vaqtda qayta ochish mumkin.

## 3. Ikkinchi kompyuterni juftlash

### A usul — kod bilan

1. Birinchi kompyuterda **Ulanish → Bu kompyuterni ulash**.
2. 6 xonali kod va QR paydo bo‘ladi. Kod **3 daqiqa** amal qiladi.
3. Ikkinchi kompyuterda ToRexLink’ni oching.
4. **Ulanish → Kod bilan ulanish**.
5. Host IP manzilini kiriting (masalan, `192.168.1.20`) va kodni kiriting.
6. **Ulash**ni bosing.

Host manzilini bilmasangiz, yaqin atrofdagi qurilmalar ro‘yxatidan foydalaning:
**Kod bilan ulanish** tugmasi o‘sha qurilma uchun kod oynasini ochadi.

### B usul — QR bilan

1. Host’da kod yaratilgach **QR kodni ko‘rsatish**ni bosing.
2. Ikkinchi kompyuterda **Ulanish → QR orqali**.
3. Kamerani host ekranidagi QR’ga qarating.
4. Kamera bo‘lmasa, **Rasmdan QR tanlash** orqali skrinshot/rasm faylini
   tanlang.

QR ichida host manzili, port, sertifikat fingerprinti va vaqtinchalik kod
bor. QR’ni boshqa odamga yubormang.

### C usul — oldin juftlangan qurilma

**Qurilmalar** bo‘limida saqlangan qurilma ustida **Ulanish**ni bosing. Kod
kerak emas: ulanish token va pinned sertifikat bilan avtomatik
autentifikatsiya qilinadi.

## 4. Kundalik foydalanish

### Fokusni almashtirish

- **Sichqoncha bilan:** kursorni sozlamalarda tanlangan ekran chetiga olib
  boring. Masalan, ikkinchi kompyuter o‘ng tomonda bo‘lsa, “O‘ng”ni tanlang.
- **Klaviatura bilan:** `Ctrl+Alt+Space`.
- **Qaytish:** kursor kirgan chetga qaytib o‘ting yoki `Ctrl+Alt+Space`
  bosing. Passiv kompyuterda klaviatura/sichqoncha bosilsa, input kursor
  turgan kompyuterga yuboriladi (keyboard follows cursor).
- Boshqa kompyuterda ishlayotganda bu kompyuterning kursori yashiriladi va
  har bir harakatdan keyin markazga qaytariladi; ekranda faqat faol kompyuter
  kursori harakatlanadi.
- Qaytganingizdan so‘ng tasodifiy “sakrash” bo‘lmasligi uchun kursor chetdan
  kamida 56 px ichkariga surilishi kerak — shundan keyin keyingi o‘tish
  faollashadi.
- Boshqa kompyuterdan qaytish uchun kursor chetga yetib kelgach, yana kamida
  36 px tashqariga suring. Oddiy tegib o‘tish yoki mayda tebranish qaytarmaydi.

Ekranning yuqori qismidagi banner va tray tooltip qaysi kompyuter boshqaruvda
ekanligini ko‘rsatadi.

### Klaviatura

- Fokus qaysi kompyuterda bo‘lsa, klaviatura o‘sha yerda ishlaydi.
- Ikkala kompyuterning fizik klaviaturasi ham boshqaruvni o‘z zimmasiga olishi
  mumkin.
- `Cmd`/`Ctrl` va boshqa modifierlar o‘z ekvivalentlarida uzatiladi.
- Klaviatura joylashuvi ikki qurilmada bir xil bo‘lsa, matn kiritish eng
  yaxshi ishlaydi. Murakkab IME/kompozitsiya uzatilmaydi.

### Sichqoncha

- Harakat, chap/o‘ng/o‘rta va qo‘shimcha tugmalar, scroll uzatiladi.
- Controller kompyuterning kursori yashiriladi; remote’da kursor ko‘rinadi.
- Windows’da cursor chetga yaqinlashsa, delta uzluksiz bo‘lishi uchun markazga
  qaytariladi (foydalanuvchiga sezilmaydi).

### Clipboard

Ikkala kompyuterda matn nusxalash avtomatik sinxronlanadi:

- faqat **matn**;
- maksimal **1 MB**;
- ikki yo‘nalishda;
- 0.45 sekundlik polling;
- qayta-yuborish loop’i yo‘q.

Parol yoki maxfiy matnlar uchun clipboard sinxronizatsiyasini Sozlamalardan
o‘chirib turing.

## 5. Sozlamalar

### Umumiy

- **Qurilma nomi** — boshqa kompyuterda ko‘rinadigan nom.
- **Interfeys tili** — O‘zbekcha/Ruscha/Inglizcha, jonli almashadi.
- **Mavzu** — Tizim / Yorug‘ / Qorong‘i.

### Ulanish va boshqaruv

- **Qurilmalarni qabul qilish** — TLS tinglovchi. O‘chirilsa, yangi ulanishlar
  qabul qilinmaydi.
- **Avtomatik qayta ulanish** — uzilganda token bilan qayta urinish.
- **Clipboard sinxronizatsiyasi**.
- **Kursor o‘tadigan ekran cheti** — Chap/O‘ng/Yuqori/Past.
- **Ekran chetidan o‘tish** — yoq/o‘chir.
- **Tezkor almashtirish** — `Ctrl+Alt+Space` (global hotkey).

### Ishga tushish

- **Fon rejimida ishlash** — oyna yopilganda tray’da qolish.
- **Tizim ishga tushganda ochish** — login’da fonda avtomatik start.
- **Bildirishnomalar**.

### Xavfsizlik

- **Qurilma sertifikati fingerprinti** — uni boshqa qurilmadagi qiymat bilan
  solishtirish mumkin.
- **Barcha juftliklarni o‘chirish** — token va sertifikat pinlarini o‘chiradi.
- **Tizim ruxsatlari** — macOS Input Monitoring/Accessibility.
- **Windows Firewall** — faqat Windows’da; rozilik va UAC orqali.

### Qo‘shimcha

Texnik ma’lumotlar (port, data papkasi, protokol versiyasi) yig‘ilgan holatda
turadi. Oddiy foydalanuvchi ularni o‘zgartirishi shart emas.

## 6. Holat belgilari

| Holat | Ma’nosi |
|---|---|
| O‘chirilgan | Hosting o‘chirilgan, faol ulanish yo‘q |
| Qabul qilishga tayyor | Listener faol, kod kutilmoqda |
| Juftlash faol | Kod/QR ko‘rsatilmoqda |
| Ulanmoqda | TLS/auth jarayoni |
| Ulangan | Xavfsiz session faol |
| Qayta ulanmoqda | Avtomatik tiklash |
| Xatolik | Diagnostikani oching |

## 7. Tray / Menu bar

- **Ochish/Yashirish** — oynani boshqarish.
- **Yangi qurilma juftlash**.
- **Qurilmalar**.
- **Ulanishni uzish** (faol bo‘lsa).
- **Chiqish** — barcha ulanishlar yopiladi.

## 8. Loglar va diagnostika

- **Loglar** — ulanish, xavfsizlik va xatolar tarixi. Tokens/kodlar/clipboard
  yozilmaydi.
- **Boshqa qurilma loglari** — faol ulanishda ikkinchi kompyuterdan loglarni
  xavfsiz TLS sessiyasi orqali olib keladi. Ikkala qurilmada 1.0.5 bo‘lishi
  kerak. Log lokal `logs/remote-<peer>.log` fayliga ham saqlanadi.
- **Diagnostika** — ruxsatlar, sertifikat, TLS, mDNS, firewall, port va
  oxirgi xato. Hisobotni nusxalash yoki faylga saqlash mumkin.

## 9. Xavfsizlik maslahatlari

- Kodni faqat ishonchli odamga ayting; QR’ni skrinshot qilib boshqalarga
  yubormang.
- Har bir juftlikni faqat o‘zingiz nazorat qiladigan qurilmalar bilan yarating.
  Juftlangan qurilma sizning loglaringizning oxirgi qismini so‘rashi mumkin
  (256 KiB, redaksiya qilingan).
- Ishonchsiz umumiy Wi‑Fi’da juftlik yaratmang.
- Ishlatilmayotgan juftliklarni **Qurilmalar → O‘chirish** orqali olib tashlang.
- Yangi noma’lum ulanish bildirishnomasini ko‘rsangiz, darhol barcha
  juftliklarni o‘chirib, qayta juftlang.
- Kompyuterni boshqasiga topshirishdan oldin `peers.json` va certs papkasini
  o‘chiring yoki **Barcha juftliklarni o‘chirish**dan foydalaning.

## 10. Cheklovlar

- Har bir qurilmada bitta asosiy monitor.
- Secure input/UAC/lock screen boshqarilmaydi.
- File transfer/audio/video yo‘q.
- Windows build real hardware QA talab qiladi (docs/TESTING.md).
