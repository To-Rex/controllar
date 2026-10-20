# ToRexLink xavfsizlik modeli

## 1. Maqsad

ToRexLink ishonchli lokal tarmoqda ikki qurilma o‘rtasida klaviatura,
sichqoncha va matnli clipboardni xavfsiz uzatish uchun mo‘ljallangan. Hech
qanday bulut servisi, akkaunt, telemetriya yoki tashqi CA ishlatilmaydi.

Xavfsizlik quyidagi tamoyillarga asoslanadi:

- **Zero trust in discovery** — mDNS faqat manzil topadi, autentifikatsiya
  qilmaydi.
- **Pinned identity** — juftlashdan keyin faqat ma’lum sertifikat qabul
  qilinadi.
- **No plaintext code** — 6 xonali kod CPace PAKE ichida ishlatiladi.
- **Defense in depth** — TLS 1.3 + CPace + token HMAC + private-key imzo.
- **Least privilege** — faqat kerakli OS ruxsatlari, firewall portlari.
- **Privacy by default** — clipboard va matn loglarga yozilmaydi.

## 2. Identitetlar

Har bir oʻrnatish birinchi ishga tushishda quyidagilarni yaratadi:

- RSA-3072 private key;
- 10 yil amal qiluvchi self-signed X.509 sertifikat;
- SHA-256 fingerprint (`AA:BB:...`) va device ID (64 hex);
- keyin random 256-bit pairing token (faqat juftlashda).

Sertifikatning o‘zi trust anchor: peer sertifikatni CA sifatida yuklaydi va
qo‘shimcha fingerprintni tekshiradi. Public CA yo‘q, shuning uchun DNS yoki
CA kompromissi bilan almashtirib bo‘lmaydi.

Sertifikat muddati tugasa, yangi identity yaratiladi va barcha eski juftliklar
qayta juftlashni talab qiladi. Build sertifikat validligini diagnostikada
ko‘rsatadi.

## 3. Juftlash (CPace + TLS)

1. Host 6 xonali kodni yaratadi (`secrets.randbelow`), 180 sekund TTL,
   5 urinish limiti.
2. Client TLS 1.3 bilan ulanadi. Sertifikat hali pinlanmagan, shuning uchun
   kanal autentifikatsiya qilinmagan hisoblanadi.
3. Host `session_id`, server nonce va CPace public point yuboradi.
4. Ikkala tomon `G = hash_to_curve(sid, code)` generatorini hisoblaydi,
   ephemeral scalar bilan public point almashadi va shared secret oladi.
5. Shared secretdan HKDF orqali:
   - `pair_token` (32 bytes),
   - client confirmation key,
   - server confirmation key
   olinadi.
6. Client CPace proof + o‘z sertifikati private key bilan transcript imzosini
   yuboradi.
7. Host tekshiradi va faqat shundan keyin peer’ni saqlaydi; server proof
   yuboradi.
8. Client server proofni tekshirgach token saqlanadi.

Muhim xususiyat: 6 xonali kod **hech qachon simdan o‘tmaydi**. Tarmoq
kuzatuvchisi faqat PAKE transcriptini ko‘radi va offline dictionary
hujumidan foydalana olmaydi. Onlayn urinishlar 5 ta bilan cheklanadi.

TLS sertifikat fingerprinti QR ichida bo‘lsa, client uni CPace’dan oldin ham
tekshiradi. Qo‘lda kiritilgan kodda TOFU modeli ishlaydi, lekin CPace tufayli
MITM keyingi auth bosqichida muvaffaqiyatli bo‘la olmaydi (pastda).

## 4. Qayta ulanish autentifikatsiyasi

Har bir reconnect:

1. Client pinned TLS context’da host sertifikatini tekshiradi
   (`verify_mode=CERT_REQUIRED`, faqat peer cert trust anchor sifatida).
2. Client hello yuboradi: device ID, nom, platforma, nonce, sertifikat.
3. Host challenge + server nonce yuboradi.
4. Transcript ikkala nonce, ikkala cert, device ID, versiyani bog‘laydi.
5. Client:
   - `HMAC-SHA256(token, "client" || transcript)`;
   - RSA-PSS(`SHA-256`, transcript) imzo
   yuboradi.
6. Host tokenni constant-time tekshiradi, imzo bilan client private key
   egaligini tasdiqlaydi.
7. Server xuddi shu tokendan server HMAC + RSA-PSS imzosini yuboradi.
8. Client buni tekshiradi.

Natijada token o‘g‘irlangan taqdirda ham, peer sertifikatining private key’i
bo‘lmasa, autentifikatsiya o‘tmaydi. Faqat tokenni emas, pinned TLS
sertifikatini ham bilish kerak.

### Nega MITM pairing’dan keyin ishlamaydi?

Agar hujumchi pairing vaqtida o‘rtada tursa, CPace prooflarini relay qilishi
mumkin, lekin:

- client noto‘g‘ri (hujumchi) sertifikatini pinlaydi;
- haqiqiy server client’ni boshqa peer sifatida saqlaydi;
- keyingi auth’da hujumchi tokenni bilmaydi va server imzosini yasay olmaydi;
- server challenge’idagi fingerprint MITM sertifikatiga mos kelmaydi.

Shu sababli pairing paytida to‘liq MITM ham uzoq muddatli kirishga ega
bo‘lmaydi. Baribir foydalanuvchi QR’dagi fingerprintni ishonchli kanal orqali
olishi tavsiya etiladi.

## 5. TLS sozlamalari

- Minimum va maksimum: **TLS 1.3**.
- ALPN: `torexlink/1`.
- Server self-signed cert + RSA key.
- Client pinned context: faqat peer cert trust anchor sifatida.
- Client pairing context: `CERT_NONE` (chunki hali pin yo‘q), lekin CPace va
  cert fingerprint bilan app-level autentifikatsiya.
- TLS compression/renegotiation o‘chirilgan.
- Sessiya ticketlari Python `ssl` default’ida; long-lived token auth hali ham
  har ulanishda tekshiriladi.
- Sertifikat hostname’i ishlatilmaydi; identity pin orqali.
- PIN o‘zgarsa, client hech qachon avtomatik “davom etish” qilmaydi.

## 6. Token va maxfiy saqlash

- Tokenlar 0600 faylda (`peers.json`), parent papka 0700.
- Token UI’da, logda, diagnostika hisobotida chiqmaydi.
- Pairing kodlari ham logga yozilmaydi.
- Log redaction filtri naqshlar bo‘yicha qo‘shimcha himoya qiladi.
- Clipboard matni hech qachon logga yozilmaydi.
- Atomik yozish: vaqtinchalik fayl + fsync + `os.replace`.
- Sertifikat/key fayllari 0600.

Clipboard OS darajasida boshqa ilovalar tomonidan o‘qilishi mumkin — bu
operatsion tizimning umumiy xususiyati. Maxfiy matnlar uchun clipboard
sinxronizatsiyasini o‘chiring.

## 7. Tarmoq himoyasi

- Faqat TCP; portlar 48620–48629 diapazonidan.
- Bitta vaqtda bitta faol session.
- Server handshake concurrency 8.
- Auth failure limiti: bir IP’dan 60 sekundda 6 marta xato → 30 sekund blok.
- Pairing: 5 urinish, 1 sekund failure delay, 180 sekund TTL.
- Frame limiti 8 MiB; clipboard 1 MiB.
- Input rate limiti ~4500 event/s burst 7000; clipboard ~1.2/s burst 8.
- Noto‘g‘ri JSON, noma’lum tur, ortiqcha maydon, NaN va noto‘g‘ri qiymatlar
  rad etiladi.
- xato javoblari autentifikatsiyadan oldin generic (`auth_failed`,
  `pair_denied`) — enumeration kamaytiriladi.
- Token compare `hmac.compare_digest`.
- RSA-PSS signature verification.
- Sertifikat fingerprinti time-constant compare orqali emas, lekin public
  qiymat; pinning xavfsizligi TLS trust modeliga tayanadi.

## 8. Native input xavfsizligi

- macOS event tap va CGEvent injection faqat foydalanuvchi bergan Input
  Monitoring + Accessibility ruxsatlari bilan ishlaydi.
- macOS app sandbox ishlatilmaydi; sandbox global HID tap uchun mos emas.
- Windows low-level hooks qo‘shimcha ruxsat talab qilmaydi.
- O‘z synthetic eventlarimizni qayta ishlash taqiqlanadi:
  - macOS: source PID + maxsus `kCGEventSourceUserData` tegi;
  - Windows: `LLKHF_INJECTED`/`LLMHF_INJECTED` + `dwExtraInfo` tegi.
- Bu feedback loop, cheksiz inject va “o‘z-o‘zini boshqarish”ning oldini
  oladi.
- Clipboard faqat matn, HTML/file kabi boshqa formatlar uzatilmaydi.
- macOS Secure Input, Windows Secure Desktop/UAC/lock screen boshqarilmaydi —
  bu ataylab.

## 9. Windows Firewall

Faqat foydalanuvchi tasdiqlagandan keyin:

- TCP 48620–48629 inbound (private, domain);
- UDP 5353 mDNS inbound (private, domain).

Qoidalar `netsh advfirewall` orqali UAC bilan o‘rnatiladi. Ilova o‘z-o‘zidan
admin huquqi olmaydi. Public profilga qoida qo‘shilmaydi.

macOS’da Application Firewall tizim tomonidan boshqariladi; ilova faqat
tizim so‘roviga javob beradi.

## 10. Ruxsatlar va TCC

macOS TCC ruxsatlari bundle ID + code signature’ga bog‘langan. Ad-hoc yoki
har safar boshqa imzo bilan build qilinsa, foydalanuvchi ruxsatni qayta berishi
mumkin. Bu kamchilik emas — OS himoyasi. Ishlab chiqarish uchun barqaror
Developer ID va notarizatsiya tavsiya etiladi.

Ruxsatlarni majburan aylanib o‘tish, event tap’ni yashirish yoki foydalanuvchi
roziligisiz firewall o‘zgartirish **taqiqlanadi**.

## 11. Threat model — nima himoyalangan

| Tahdid | Himoya |
|---|---|
| LAN kuzatuvi | TLS 1.3 |
| Kodni online brute force | CPace, 5 urinish, delay |
| Kodning offline brute force | PAKE (kod simdan o‘tmaydi) |
| Boshqa qurilma qalbakilashtirishi | Pinlangan cert + CPace |
| Token o‘g‘irlanishi | Private-key signature talabi |
| Replay | Nonce, transcript HMAC, yangi TLS |
| Malicious JSON/frame | Qat’iy sxema, limitlar, rate limit |
| Input feedback loop | Injected event teglari |
| Clipboard secret loglari | Log qilinmaydi, redaction |
| Discovery spoofing | mDNS trust qilinmaydi, kod/pin talab |
| DNS/CA hijack | CA ishlatilmaydi |
| Ruxsatsiz boshqarish | OS consent + code/QR |

## 12. Non-goals / cheklovlar

- Foydalanuvchi allaqachon juftlashgan qurilmalarga ishonadi. Ulardan biri
  to‘liq kompromiss bo‘lsa, u boshqaruvni boshqarishi mumkin — bu KVM’ning
  tabiati. Qurilmalarni faqat ishonchli kompyuterlar bilan juftlang.
- Lokal administrator huquqiga ega hujumchi process’ni o‘zgartirishi mumkin;
  bu OS threat modelidan tashqarida.
- Clipboard OS miqyosida boshqa ilovalar tomonidan kuzatilishi mumkin.
- TLS pin sertifikat muddati 10 yil; yangilanish qayta juftlash talab qiladi.
- Kod 6 xonali (1 000 000 kombinatsiya) — PAKE va attempt limit bilan.
- Pairing vaqtida QR ekranini ko‘rgan odam kodni bilib oladi; QR’ni boshqa
  odamga ko‘rsatmang.
- Secure Desktop / login sahifalarida input ishlamaydi.
- Traffic analysis (qachon ulangan, qancha event) oldini olinmaydi.
- CPace uchun P-256 scalar multiplication Python’da implement qilingan va
  formal ma’noda constant-time emas. Pairing bir martalik, kod 20-bit va
  urinishlar 5 ta bilan cheklangan; shunga qaramay yuqori darajadagi yon-kanal
  auditi talab qilinsa, vetted constant-time guruh implementatsiyasiga
  o‘tish tavsiya etiladi.
- IPv6 listener hozircha qo‘llanmaydi; LAN IPv4 ishlatiladi.
- Juftlangan qurilma autentifikatsiyalangan sessiya orqali loglarning oxirgi
  256 KiB qismini so‘rashi mumkin. Loglar maxfiy qiymatlarni redaksiya qiladi,
  ammo qurilma nomi, IP manzil, fayl yo‘li va xato tafsilotlari ko‘rinishi
  mumkin. Faqat ishonchli qurilmalar bilan juftlaning.

## 13. Zaiflik haqida xabar berish

Xavfsizlik muammosini ommaviy issue sifatida emas, loyiha egasiga xususiy
kanal orqali xabar bering. Hisobotda:

- ta’sir doirasi;
- takrorlash qadamlari;
- ToRexLink versiyasi va OS;
- loglar (token, kod va clipboard mazmunisiz).

Javobgarlik oshkor qilish siyosati alohida kelishiladi.
