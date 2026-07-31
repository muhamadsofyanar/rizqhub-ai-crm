# Deployment Coolify — V4 Consolidated

## Resource

Gunakan resource Docker Compose yang sudah ada:

```text
Repository: muhamadsofyanar/rizqhub-ai-crm
Branch: main
Base Directory: /
Docker Compose Location: /docker-compose.yml
Domain service: web:8000
```

Jangan membuat domain publik untuk PostgreSQL, Redis, worker, atau beat.

## Environment wajib

```env
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<secret lama yang kuat>
POSTGRES_PASSWORD=<password database lama>
APP_ENCRYPTION_KEY=<fernet key lama>
ADMIN_EMAIL=admin@domainanda.com
ADMIN_PASSWORD=<password admin>
APP_BASE_URL=https://crm.domainanda.com
ALLOWED_HOSTS=crm.domainanda.com
CSRF_TRUSTED_ORIGINS=https://crm.domainanda.com
```

AI Gemini:

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=<key Google AI Studio>
GEMINI_MODEL=gemini-3.6-flash
AUTO_REPLY_DEFAULT=false
```

Tuning VPS 4 vCPU/8 GB:

```env
WEB_CONCURRENCY=3
CELERY_CONCURRENCY=2
LIVE_INBOX_POLL_SECONDS=2.5
CAMPAIGN_MAX_RECIPIENTS=500
WEBHOOK_RATE_LIMIT_PER_MINUTE=180
BACKUP_RETENTION_DAYS=14
BACKUP_HOUR=2
FEATURE_AUTOMATION=false
FEATURE_CAMPAIGN=false
FEATURE_SAAS=false
FEATURE_BACKUP=true
```

Account API Key dan Device Key StarSender tidak dimasukkan ke environment; keduanya dikelola melalui dashboard terenkripsi.

## Proses startup

Service web menjalankan:

1. `wait_for_db`
2. migration Django bawaan
3. `migrate --run-syncdb` untuk tabel additive CRM
4. `collectstatic`
5. `bootstrap`
6. `v4_preflight`
7. Gunicorn

Worker dan Beat menunggu service web sehat agar tidak menjalankan job sebelum schema siap.

## Verifikasi

```text
/health/
/login/
/system-health/
/settings/features/
/starsender/
```

Log sukses harus menunjukkan PostgreSQL/Redis healthy dan web/worker/beat started.

## Setelah deployment

1. Uji inbox personal lama.
2. Tambah Account API Key di StarSender Center.
3. Sinkronkan device.
4. Isi Device Key dan mapping Brand/Agent per device.
5. Pasang Webhook Premium akun.
6. Uji satu device, lalu device berikutnya.
7. Sinkronkan grup dan kunci grup internal.
8. Aktivasi broadcast hanya melalui Feature Settings setelah tes.

## Troubleshooting Git source

Bila Coolify menampilkan `Failed to read Git source`:

```bash
getent hosts github.com
docker run --rm ghcr.io/coollabsio/coolify-helper:1.0.14 \
  sh -lc 'git ls-remote https://github.com/muhamadsofyanar/rizqhub-ai-crm.git main'
```

Bila kedua tes berhasil tetapi Coolify masih memakai kondisi lama:

```bash
docker restart coolify
```

Restart hanya container `coolify`, bukan seluruh container aplikasi.

## Rollback

Redeploy commit sebelumnya tanpa menghapus volume. Tabel V4 bersifat additive dan dapat dibiarkan ketika kode V3 dijalankan kembali.
