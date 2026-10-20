# Performance va uzoq muddatli barqarorlik

ToRexLink’ning kritik yo‘li — native input event → bridge → asyncio session →
TLS → peer → native injection. UI bu yo‘lda ishtirok etmaydi.

## Optimizatsiyalar

- Native input eventlar thread-safe burst buferida yig‘iladi; har bir event
  uchun `call_soon_threadsafe` qilinmaydi. Input queue 2048, native burst
  buferi 16 384 event bilan cheklangan.
- Outgoing frame hajmi framing qatlamidan qaytariladi; byte statistikasi uchun
  JSON ikkinchi marta serialize qilinmaydi.
- macOS display o‘lchami 2 sekundga cache qilinadi; pointer freeze markazi bir
  marta hisoblanadi, har event’da `CGDisplayBounds` chaqirilmaydi.
- LAN manzillari service snapshot uchun cache qilinadi.
- Permission preflight 2 sekundga cache qilinadi; UI timer’i force-refresh
  qiladi.
- UI faqat faol sahifani yangilaydi va event turiga qarab filter qiladi.
  Home/Devices/Connect ro‘yxatlari o‘zgarmagan bo‘lsa qayta qurilmaydi.
- Logs sahifasi log faylining `mtime/size`i o‘zgarmagan bo‘lsa o‘qimaydi.
- Tray menyusi har event’da emas, faqat ochilishdan oldin quriladi.
- Focus almashinuvida notification/banner chiqmaydi.

## O‘lchovlar

Sintetik localhost test (macOS, Python 3.12, fake input backend):

| Ko‘rsatkich | Natija |
|---|---|
| Input software round-trip | avg **0.16 ms**, p95 **0.23 ms** |
| 5 000 event burst CPU | ~11 ms |
| 25 000 event soak | 0.65 s, queue’lar 0, peak xotira ~1.2 MB |
| Focus UI update | **0.33 ms/event** |
| Barcha sahifalarni yangilash (eski usul) | 2.4 ms/event |
| UI yaxshilanish | ~**7.4x** |

Bu raqamlar real Wi‑Fi/LAN kechikishini o‘z ichiga olmaydi; ular ilova
ichidagi CPU/queue/UI yukini ko‘rsatadi. Real tarmoq kechikishi TLS RTT va
Wi‑Fi sharoitiga bog‘liq.

## Uzoq muddatli barqarorlik

- Barcha input queue’lar cheklangan; sekin peer uchun eng eski movement
  tashlanadi, key/button yo‘qolmaydi.
- Session heartbeat 10 s, dead-man 32 s; uzilishda input darhol bo‘shatiladi.
- Loglar 5 MiB × 3 rotatsiya.
- Auth failure va remote-log waiter’lari cheklangan/tozalanadi.
- Native backend stop va focus o‘zgarishlarida kursor association/visibility
  tiklanadi.

## Reproduktsiya

```bash
python scripts/benchmark_input.py
```
Benchmark test harness’dagi fake backend va localhost TLS’dan foydalanadi;
natijalar apparat/tarmoqqa qarab farq qiladi. UI benchmark’i alohida
`QApplication` harness’ida o‘lchangan.
