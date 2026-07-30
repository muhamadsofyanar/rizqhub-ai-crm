# Upgrade RizqHub AI CRM ke V3

Dokumen ini untuk instalasi yang sudah berjalan di Coolify dengan Docker Compose.

## Sebelum upload

1. Pastikan CRM lama masih dapat menerima dan mengirim WhatsApp.
2. Buat backup PostgreSQL dari Coolify atau volume database.
3. Jangan mengganti `POSTGRES_PASSWORD`, `DJANGO_SECRET_KEY`, `APP_ENCRYPTION_KEY`, dan nama volume database.
4. Jangan mengunggah `.env` atau API key ke GitHub.

## Upload source

Unggah seluruh isi paket V3 ke root repository dan timpa file lama. Commit yang disarankan:

```text
Upgrade to RizqHub AI CRM V3 integrated pilot
```

## Environment awal yang aman

Tambahkan atau sesuaikan variable berikut di Coolify:

```env
FEATURE_LIVE_INBOX=true
FEATURE_MESSAGE_RETRY=true
FEATURE_AI_EVALUATION=true
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

Pertahankan konfigurasi AI yang sudah berhasil, misalnya:

```env
AI_PROVIDER=gemini
GEMINI_MODEL=gemini-3.6-flash
AUTO_REPLY_DEFAULT=false
```

## Deployment

1. Klik **Save** lalu **Redeploy**.
2. Pastikan service berikut aktif:
   - `postgres`: Healthy
   - `redis`: Healthy
   - `web`: Started
   - `worker`: Started
   - `beat`: Started
3. Buka `/health/` dan pastikan HTTP 200.

## Uji tahap pertama

1. Buka Inbox dan kirim pesan dari WhatsApp lain.
2. Pastikan pesan tampil tanpa refresh manual.
3. Aktifkan AI hanya pada percakapan uji.
4. Uji Ambil Alih, Aktifkan AI, note internal, assignment, dan retry pesan.
5. Periksa Knowledge Base serta AI Evaluation.

## Aktivasi lanjutan

Aktifkan satu per satu setelah pengujian:

```env
FEATURE_AUTOMATION=true
FEATURE_CAMPAIGN=true
FEATURE_SAAS=true
```

Campaign harus tetap memakai consent, opt-out, batas penerima, dan throttling. Mode autonomous untuk jasa legalitas tidak disarankan sebelum Knowledge Base dan skenario handoff tervalidasi.

## Rollback

Bila V3 bermasalah:

1. Jangan hapus volume PostgreSQL.
2. Deploy kembali commit stabil sebelumnya.
3. Tabel tambahan V3 boleh tetap berada di database karena tidak mengubah tabel lama.
4. Pulihkan backup hanya bila ada kerusakan atau perubahan data yang tidak dapat dibatalkan.
