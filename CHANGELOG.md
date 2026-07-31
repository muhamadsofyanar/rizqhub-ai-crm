# V4.2 Organic Broadcast

- Memperbaiki koneksi Account API Key dan sinkronisasi device.
- Memperbaiki normalisasi status disconnected.
- Menambah sinkronisasi semua grup dan daftar grup lintas device.
- Menambah pemilih grup visual, multi-group, preset cepat, dan pencarian grup.
- Menambah template teks/media tanpa batas aplikasi dengan upload media persistent.
- Menambah UI broadcast yang sederhana dan fokus pada pemasaran organik.

# Changelog

## 4.1.0 — Simple Operations UX

- Menyederhanakan navigasi utama dan menambahkan Mode Sederhana/Mode Lanjutan.
- Mendesain ulang Inbox menjadi tiga kolom dengan satu status percakapan yang jelas.
- Menambahkan status Menunggu Pelanggan tanpa mematikan kelanjutan AI.
- Menambahkan filter dan pencarian Inbox.
- Mengubah Broadcast menjadi wizard enam langkah yang selalu membuat draft terlebih dahulu.
- Mengubah StarSender Center menjadi setup wizard multi-device.
- Menambahkan onboarding checklist, bantuan kontekstual, dan dashboard berbasis tindakan.
- Tidak mengubah skema database.


## V4 Consolidated RC1 — final safety pass

- Menambahkan role guard untuk operasi inbox, StarSender Center, preset, dan broadcast.
- Menyembunyikan URL webhook legacy dari role selain Owner/Admin.
- Memperjelas safe retry: hanya kegagalan yang diketahui aman yang diulang; hasil ambigu menjadi `uncertain`.
- Menambahkan release manifest dan QA report.
- Memvalidasi bahwa upgrade schema bersifat additive terhadap V3.


## 4.0.0-rc1 — Consolidated safe release

### Added

- Feature flag database; modul dapat diaktifkan tanpa redeploy.
- Inbox Live V2 untuk daftar percakapan dan isi pesan.
- Smart Handoff V2 dan runtime policy per agent.
- Customer Memory dan ringkasan data pelanggan.
- StarSender Account API Key, sinkronisasi multi-device, serta Device Key per device.
- Mapping device ke Brand/Agent dan routing balasan melalui device asal.
- Webhook Premium akun dengan deduplikasi, rate limit, payload limit, dan status needs-mapping.
- Sinkronisasi grup, mode AI grup, kategori, preset statis/dinamis, dan grup terkunci.
- Broadcast personal dan multi-group dengan consent/permission, draft, delay, scheduling, cancel, idempotency, claim atomik, dan status per penerima.
- System Health untuk webhook multi-device, broadcast, notifikasi, dan backup.
- Startup `v4_preflight`, worker/beat health gate, static QA, backup host tervalidasi, serta recovery task untuk send yang macet.

### Changed

- Knowledge retrieval tidak lagi memakai fallback sumber yang tidak relevan.
- Greeting/no-knowledge memakai respons deterministik yang lebih aman.
- Handoff confidence rendah default menjadi klarifikasi, bukan mematikan AI permanen.
- Parser boolean webhook menangani string `true/false` dengan benar.
- Credential kosong tidak dienkripsi sebagai nilai palsu.
- Delay broadcast minimum dinaikkan menjadi 30 detik.
- Retry provider hanya dilakukan untuk kegagalan yang aman; timeout/HTTP 5xx menjadi `status tidak pasti` untuk mencegah duplikasi.
- Duplikasi webhook lintas URL legacy/account dihentikan berdasarkan provider message ID.
- Credential terenkripsi disembunyikan dari form Django admin.

### Safety defaults

- Automation, personal broadcast, group broadcast, campaign legacy, dan SaaS nonaktif.
- `AUTO_REPLY_DEFAULT=false` tetap direkomendasikan.
- Broadcast memerlukan aktivasi `AKTIFKAN` dan konfirmasi pengiriman `KIRIM`.

## 3.0.0 — Integrated pilot release

- Inbox Live polling.
- Message retry, status delivery, deduplikasi webhook.
- Human handoff, assignment, note internal, approval draft AI.
- Knowledge revision, evaluasi AI, pipeline, automation, campaign, workspace foundation.
- Celery Beat dan backup PostgreSQL.

## 2026-07-30 — Gemini provider update

- Dukungan Gemini dan OpenAI.
- Pencegahan thought leakage dan respons terpotong.
