# QA Report — RizqHub AI CRM V4 Consolidated RC1

Tanggal pemeriksaan: 31 Juli 2026

## Hasil pemeriksaan statis

| Pemeriksaan | Hasil |
|---|---|
| Parse/compile seluruh Python | PASS |
| Static release invariants | PASS |
| Docker Compose YAML | PASS |
| Service wajib: web, worker, beat, postgres, redis | PASS |
| Sintaks shell entrypoint/backup/smoke test | PASS |
| Sintaks JavaScript Inbox Live | PASS |
| Perbandingan schema V3 → V4 | PASS — 15 model baru, 0 model lama dihapus/diubah |
| Scan `.env`, dump, SQL, SQLite, dan pola secret nyata | PASS |
| Role guard operasi inbox/StarSender/broadcast | PASS |
| Credential terenkripsi disembunyikan dari Django admin | PASS |
| Backup archive validation `pg_restore --list` | PASS |
| Safe provider retry/uncertain quarantine | PASS |

## Pemeriksaan keamanan yang dipastikan oleh source

- Fitur broadcast, automation, campaign, dan SaaS nonaktif secara default.
- Aktivasi fitur berisiko membutuhkan teks `AKTIFKAN`.
- Menjalankan broadcast membutuhkan konfirmasi kedua `KIRIM`.
- Pengiriman memakai status `sending` sebagai claim atomik.
- Timeout/read error/HTTP 5xx diperlakukan sebagai `uncertain`, bukan dikirim ulang otomatis.
- Task `sending` yang macet lebih dari 15 menit dikarantina sebagai `uncertain`.
- Webhook memakai token acak, batas ukuran payload, rate limiting, event hash, dan deduplikasi message ID.
- Account API Key, Device Key, dan credential legacy disimpan terenkripsi serta tidak ditampilkan pada Django admin.
- Viewer tidak dapat menjalankan operasi mutasi inbox, StarSender, atau broadcast.

## Batas pengujian

Environment penyusunan paket tidak memiliki runtime Django/PostgreSQL/Redis/Celery atau Docker daemon. Karena itu pemeriksaan berikut **belum dapat dijalankan secara lokal**:

- `python manage.py check`
- `python manage.py v4_preflight`
- migration/syncdb terhadap salinan database produksi
- smoke test HTTP aplikasi
- panggilan nyata API StarSender/Gemini
- uji beban broadcast

Pemeriksaan runtime tersebut wajib dilakukan setelah deployment pada VPS dengan urutan di `UPGRADE-V4.md` dan `scripts/production_smoke.sh`.

## Status rilis

**Release Candidate untuk pilot produksi terbatas.** Mulai dengan satu device, satu nomor personal, dan satu grup uji. Jangan mengaktifkan broadcast massal sebelum seluruh checklist produksi selesai.
