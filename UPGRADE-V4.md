# Upgrade V3 → V4 Consolidated

Panduan ini dirancang untuk satu kali redeploy utama dengan risiko minimal. Jangan menghapus resource Coolify, volume PostgreSQL, Redis, media, atau backup.

## 1. Bekukan perubahan sementara

Sebelum upgrade:

- Jangan membuat campaign/broadcast baru.
- Jangan mengubah password database.
- Jangan mengganti `APP_ENCRYPTION_KEY`.
- Catat commit V3 terakhir yang berjalan agar rollback aplikasi mudah dilakukan.

## 2. Backup PostgreSQL

Di terminal VPS, lihat nama container PostgreSQL:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep postgres
```

Ekstrak paket V4, lalu jalankan:

```bash
chmod +x scripts/backup_from_host.sh
sudo ./scripts/backup_from_host.sh NAMA_CONTAINER_POSTGRES /root/rizqhub-backups
```

Script memvalidasi archive dengan `pg_restore --list`. Hasil wajib berupa dua file:

```text
rizqhub-pre-upgrade-YYYYMMDD-HHMMSS.dump
rizqhub-pre-upgrade-YYYYMMDD-HHMMSS.dump.sha256
```

Verifikasi:

```bash
ls -lh /root/rizqhub-backups/
sha256sum -c /root/rizqhub-backups/rizqhub-pre-upgrade-*.dump.sha256
```

Jangan lanjut bila file kosong, validasi archive gagal, atau checksum gagal.

## 3. Upload source ke GitHub

Upload seluruh isi folder V4 ke **root repository** dan timpa file lama. Jangan upload:

```text
.env
*.dump
API key
password
file backup
```

Commit yang disarankan:

```text
Upgrade to RizqHub AI CRM V4 consolidated RC1
```

## 4. Periksa Environment Variables Coolify

Pertahankan secret lama yang sudah berfungsi:

```text
DJANGO_SECRET_KEY
POSTGRES_PASSWORD
APP_ENCRYPTION_KEY
ADMIN_PASSWORD
GEMINI_API_KEY
```

Pastikan:

```env
APP_BASE_URL=https://taysriulqurani.id
AUTO_REPLY_DEFAULT=false
FEATURE_AUTOMATION=false
FEATURE_CAMPAIGN=false
FEATURE_SAAS=false
FEATURE_BACKUP=true
LIVE_INBOX_POLL_SECONDS=2.5
CAMPAIGN_MAX_RECIPIENTS=500
WEBHOOK_RATE_LIMIT_PER_MINUTE=180
BACKUP_RETENTION_DAYS=14
BACKUP_HOUR=2
```

Personal/group broadcast tidak perlu environment flag baru. Setelah deployment, keduanya dikontrol dari Feature Settings di database.

## 5. Redeploy satu kali

Klik **Redeploy** pada resource Docker Compose yang sudah ada.

Deployment yang sehat menampilkan:

```text
postgres Healthy
redis Healthy
web Started
worker Started
beat Started
New container started
```

Startup web akan menjalankan:

```text
wait_for_db
migrate
migrate --run-syncdb
collectstatic
bootstrap
v4_preflight
Gunicorn
```

`v4_preflight` menghentikan startup bila tabel V4 tidak terbentuk atau credential terenkripsi lama tidak dapat dibaca.

## 6. Smoke test setelah deployment

1. Buka `/health/`; database dan Redis harus `true`.
2. Login dan buka **System Health**.
3. Buka **Unified Inbox** dan kirim satu pesan personal dari HP lain.
4. Pastikan pesan dan jawaban AI muncul tanpa refresh.
5. Pastikan tombol Ambil Alih/Aktifkan AI bekerja.
6. Pastikan pesan keluar dikirim melalui device lama yang sebelumnya berfungsi.
7. Periksa bahwa tidak ada pesan berstatus `sending` menetap; hasil ambigu harus tampil sebagai `status tidak pasti`, bukan terkirim ulang otomatis.

Jangan mengaktifkan broadcast pada tahap ini.

## 7. Setup StarSender multi-device

1. **StarSender Center → Tambah akun**.
2. Masukkan Account API Key dan uji koneksi.
3. Sinkronkan device.
4. Pada setiap device, isi Device Key, Brand, dan Agent.
5. Aktifkan pengiriman hanya setelah Uji Key berhasil.
6. Salin webhook akun dan pasang sebagai Webhook Premium pada device terkait.
7. Kirim pesan uji ke setiap device dan periksa routing brand/agent.
8. Sinkronkan grup per device.
9. Kunci grup internal dan biarkan AI grup `Nonaktif` hingga pengujian mention selesai.

## 8. Aktivasi tanpa redeploy

Buka:

```text
Feature Settings
```

Mulai dengan konfigurasi default. Aktivasi yang disarankan:

```text
Hari 1: Inbox Live + Smart Handoff + Customer Memory
Hari 2: StarSender Multi-Device + grup sinkron
Hari 3: satu preset grup dan satu pengiriman tes
Setelah lolos: Broadcast Personal atau Broadcast Grup
```

Fitur berisiko mewajibkan teks:

```text
AKTIFKAN
```

Broadcast juga mewajibkan konfirmasi kedua:

```text
KIRIM
```

## 9. Rollback aplikasi

Bila aplikasi baru bermasalah tetapi database tetap sehat:

1. Jangan hapus volume.
2. Redeploy commit V3 terakhir.
3. Tabel tambahan V4 dapat dibiarkan; V3 tidak menggunakannya.

Restore database hanya dilakukan bila terbukti ada perubahan/kerusakan data. Jangan menjalankan restore hanya karena error tampilan atau error kode.

## 10. Kriteria rilis selesai

- Health endpoint normal.
- Web, worker, beat stabil.
- Inbox personal live.
- AI tidak bocor thought dan tidak terpotong.
- Handoff tidak terjadi pada sapaan/tes.
- Setiap device terpetakan ke brand/agent yang benar.
- Pesan dibalas dari device asal.
- Grup internal terkunci.
- Broadcast berisiko masih nonaktif sampai tes selesai.
- Backup terbaru berstatus completed dan checksum tersedia.
