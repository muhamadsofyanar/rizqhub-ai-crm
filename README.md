# RizqHub AI CRM V4 Consolidated

Rilis gabungan untuk CRM multi-brand dan multi-agent yang berjalan dengan Django, PostgreSQL, Redis, Celery, Gunicorn, Docker Compose, dan Coolify. V4 dirancang agar **kode dipasang sekali**, lalu modul diaktifkan dari dashboard melalui feature flag database tanpa redeploy berulang.

## Prinsip rilis

- Upgrade bersifat **additive**: tabel V4 ditambahkan tanpa menghapus tabel/data V3.
- Fitur berisiko seperti automation dan broadcast tetap **nonaktif secara default**.
- Account API Key, Device Key, dan API key AI disimpan terenkripsi atau melalui environment; tidak boleh dimasukkan ke GitHub.
- Startup menjalankan `v4_preflight` dan tidak membuka web jika tabel atau credential encryption utama bermasalah.
- Worker dan Beat baru dimulai setelah service web sehat.

## Modul utama

### Operasional inbox

- Inbox Live untuk daftar percakapan dan isi pesan tanpa refresh manual.
- Pesan personal dan grup dalam Unified Inbox.
- Status pesan `queued`, `sending`, `sent`, `delivered`, `read`, `failed`, dan `status tidak pasti`.
- Claim atomik mencegah task ganda memanggil provider bersamaan. Retry otomatis hanya dilakukan pada kegagalan yang aman; timeout/hasil ambigu dikarantina sebagai `status tidak pasti` agar tidak mengirim pesan ganda.
- Human takeover, aktivasi AI, assignment, catatan internal, dan approval draft.

### AI dan keamanan jawaban

- Gemini/OpenAI provider.
- Smart Handoff V2: sapaan dan pesan tes tidak memicu handoff.
- Confidence rendah meminta klarifikasi; handoff otomatis hanya untuk pemicu kuat atau bila diaktifkan secara khusus.
- Customer Memory dasar agar agent tidak menanyakan data yang sudah diberikan.
- Knowledge retrieval tanpa fallback ke sumber yang tidak relevan.
- Pencegahan thought leakage dan jawaban terpotong.
- Batas panjang jawaban dan jumlah pertanyaan per balasan.
- Evaluasi jawaban AI dan metadata sumber/confidence.

### StarSender multi-device

- Satu atau beberapa akun StarSender menggunakan **Account API Key**.
- Sinkronisasi seluruh device pada akun.
- **Device Key** terpisah untuk tiap device.
- Mapping setiap device ke Brand dan AI Agent.
- Satu webhook Premium tingkat akun; sumber pesan dibedakan memakai `device_id`.
- Balasan inbox selalu dikirim melalui device asal percakapan.
- Sinkronisasi grup per device.
- Mode AI grup: nonaktif, mention-only, draft, atau autonomous.

### Personal, grup, kategori, dan preset

- Broadcast personal ke kontak ber-consent atau nomor manual yang divalidasi.
- Broadcast ke satu atau banyak grup pada device yang sama.
- Kategori grup.
- Preset statis dan dinamis.
- Grup internal dapat dikunci agar tidak pernah menjadi tujuan broadcast.
- Draft, preview daftar penerima, konfirmasi `KIRIM`, delay minimal, scheduling, cancel, claim atomik, dan status per tujuan. Hasil provider yang ambigu tidak dikirim ulang otomatis.
- Fitur personal/group broadcast baru dapat dipakai setelah diaktifkan dengan konfirmasi `AKTIFKAN` pada Feature Settings.

### Platform dan observabilitas

- Pipeline, task, automation foundation, n8n webhook action, campaign legacy, workspace/user foundation.
- System Health untuk database, Redis, webhook, device mapping, pesan gagal, broadcast, backup, notifikasi, dan audit log.
- Backup PostgreSQL otomatis dengan checksum dan retensi; script pra-upgrade juga memvalidasi archive dengan `pg_restore --list`.
- Rate limit dan batas ukuran payload webhook.

## Aktivasi aman setelah deployment

Fitur berikut aktif secara default:

```text
Inbox Live
Retry Pesan
Evaluasi AI
Smart Handoff V2
Customer Memory
StarSender Multi-Device
Backup Otomatis
```

Fitur berikut nonaktif secara default:

```text
Automation & n8n
Broadcast Personal
Broadcast Grup
Campaign Legacy
Workspace & SaaS
```

Aktivasi dilakukan melalui:

```text
CRM → Feature Settings
```

Fitur berisiko mewajibkan pemilik/admin mengetik `AKTIFKAN`. Perubahan berlaku pada request/job berikutnya dan tidak memerlukan redeploy.

## Urutan setup StarSender

1. Buka **StarSender Center**.
2. Tambahkan akun dan masukkan Account API Key.
3. Jalankan **Uji koneksi** lalu **Sinkronkan device**.
4. Buka setiap device, masukkan Device Key, lalu pilih Brand dan Agent.
5. Aktifkan `send_enabled` hanya pada device yang sudah diuji.
6. Salin webhook Premium akun dan pasang pada setiap device StarSender terkait.
7. Kirim pesan personal uji; pastikan percakapan masuk ke brand/agent dan dibalas melalui device yang sama.
8. Jalankan **Sinkronkan grup** untuk setiap device.
9. Kunci grup internal, atur kategori, mode AI, dan preset.
10. Buat broadcast sebagai draft; aktifkan feature broadcast hanya setelah daftar penerima dan consent/izin grup diperiksa.

Baca [`docs/STARSENDER-MULTIDEVICE.md`](docs/STARSENDER-MULTIDEVICE.md) untuk detail routing dan pengujian, serta [`docs/RELEASE-MANIFEST.md`](docs/RELEASE-MANIFEST.md) untuk batas fitur RC1.

## Upgrade dari V3

Gunakan panduan [`UPGRADE-V4.md`](UPGRADE-V4.md). Ringkasnya:

1. Backup PostgreSQL dan verifikasi file tidak kosong.
2. Upload seluruh isi paket V4 ke root repository, menimpa file lama.
3. Jangan mengubah environment secret/database yang sudah berfungsi.
4. Commit dan lakukan satu kali redeploy.
5. Pastikan `postgres`, `redis`, `web`, `worker`, dan `beat` sehat.
6. Jalankan smoke test dan uji inbox personal sebelum mengatur multi-device/grup.
7. Aktivasi modul berisiko dari Feature Settings, bukan environment.

## Menjalankan lokal

```bash
cp .env.example .env
python scripts/generate_secrets.py
docker compose up -d --build
```

Untuk QA statis tanpa Django:

```bash
python scripts/static_qa.py
```

Di dalam container web setelah konfigurasi database tersedia:

```bash
python manage.py check
python manage.py v4_preflight
```

## Environment utama

```env
DJANGO_SECRET_KEY=...
POSTGRES_PASSWORD=...
APP_ENCRYPTION_KEY=...
APP_BASE_URL=https://crm.domainanda.com
ALLOWED_HOSTS=crm.domainanda.com
CSRF_TRUSTED_ORIGINS=https://crm.domainanda.com

AI_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.6-flash
AUTO_REPLY_DEFAULT=false

WEB_CONCURRENCY=3
CELERY_CONCURRENCY=2
LIVE_INBOX_POLL_SECONDS=2.5
CAMPAIGN_MAX_RECIPIENTS=500
WEBHOOK_RATE_LIMIT_PER_MINUTE=180
BACKUP_RETENTION_DAYS=14
BACKUP_HOUR=2
```

Account API Key dan Device Key StarSender dimasukkan melalui dashboard, bukan environment.

## Batas rilis

V4 adalah **release candidate untuk pilot produksi**, bukan jaminan bahwa semua kombinasi payload StarSender, volume broadcast, atau kebijakan bisnis sudah teruji pada akun produksi Anda. Fitur berisiko sengaja dipagari dengan feature flag, consent, permission, delay, idempotency, audit log, dan aktivasi manual. Tetap lakukan pengujian satu device, satu nomor, dan satu grup terlebih dahulu.

## Struktur penting

```text
crm/models.py                         Model additive V4
crm/services/starsender.py            Account/device/group API dan pengiriman
crm/services/inbound.py               Parser webhook personal/grup
crm/services/handoff.py               Smart Handoff V2
crm/services/features.py              Feature flag database
crm/tasks.py                           Queue, retry, sync, broadcast, backup
crm/management/commands/v4_preflight.py Startup guard
scripts/static_qa.py                   QA statis rilis
scripts/backup_from_host.sh            Backup sebelum upgrade
UPGRADE-V4.md                          Panduan satu kali deployment
```

## Keamanan

Baca [`SECURITY.md`](SECURITY.md) dan [`docs/SAFETY-CHECKLIST.md`](docs/SAFETY-CHECKLIST.md). Jangan mengirim API key melalui chat, screenshot, atau commit GitHub.
