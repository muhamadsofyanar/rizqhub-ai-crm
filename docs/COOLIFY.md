# Instalasi di Coolify

## 1. Buat repository GitHub

Buat repository privat, misalnya `rizqhub-ai-crm`, lalu upload seluruh isi paket ke root repository.

```bash
git init
git add .
git commit -m "Initial RizqHub AI CRM"
git branch -M main
git remote add origin <repository-anda>
git push -u origin main
```

## 2. Tambahkan resource

Di Coolify:

1. Buka project tujuan.
2. Klik **Add Resource**.
3. Pilih repository privat melalui GitHub App atau Deploy Key.
4. Pilih repository dan branch `main`.
5. Ubah build pack menjadi **Docker Compose**.
6. Base Directory: `/`.
7. Docker Compose Location: `/docker-compose.yml`.

## 3. Environment variables wajib

```env
DJANGO_SECRET_KEY=<random panjang>
POSTGRES_PASSWORD=<password database kuat>
APP_ENCRYPTION_KEY=<fernet key>
ADMIN_EMAIL=admin@domainanda.com
ADMIN_PASSWORD=<password admin kuat>
APP_BASE_URL=https://crm.domainanda.com
ALLOWED_HOSTS=crm.domainanda.com
CSRF_TRUSTED_ORIGINS=https://crm.domainanda.com
DJANGO_DEBUG=false
```

Opsional AI:

```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5-mini
AUTO_REPLY_DEFAULT=false
```

Tuning VPS 4 core/8 GB:

```env
WEB_CONCURRENCY=3
CELERY_CONCURRENCY=2
```

## 4. Domain

Hubungkan domain ke service `web` dengan container port `8000`.

Contoh:

```text
https://crm.domainanda.com
```

Jangan membuat domain publik untuk service `postgres`, `redis`, atau `worker`.

## 5. Deploy

Klik **Deploy**. Service `web` akan:

1. Menunggu PostgreSQL.
2. Menjalankan migration bawaan Django dan membuat tabel CRM.
3. Mengumpulkan static files.
4. Membuat admin, workspace, brand, agent, pipeline, dan knowledge starter.
5. Menjalankan Gunicorn.

Service `worker` menjalankan Celery untuk pemrosesan webhook dan pengiriman pesan.

## 6. Verifikasi

- Health: `/health/`
- Login: `/login/`
- Admin: `/admin/`
- Dashboard: `/`

## 7. Auto deploy GitHub

Aktifkan auto-deploy pada integrasi GitHub Coolify apabila setiap push ke `main` ingin langsung memicu deployment.

## 8. Backup

Minimal backup volume `postgres_data`. Untuk produksi berbayar, gunakan backup terjadwal ke object storage di luar VPS dan uji proses restore.

## Troubleshooting

### CSRF verification failed

Pastikan:

```env
APP_BASE_URL=https://crm.domainanda.com
ALLOWED_HOSTS=crm.domainanda.com
CSRF_TRUSTED_ORIGINS=https://crm.domainanda.com
```

Lalu redeploy.

### Login admin gagal

Bootstrap hanya mengatur password saat admin pertama kali dibuat. Untuk memaksa reset satu kali:

```env
RESET_ADMIN_PASSWORD=true
```

Redeploy, login, lalu hapus variable tersebut.

### Pesan masuk tidak muncul

- Pastikan koneksi StarSender aktif.
- Pastikan webhook URL disalin lengkap termasuk token dan `/` terakhir.
- Periksa log service `web` dan `worker`.
- Periksa tabel Webhook Events melalui Django Admin.
- Pastikan Redis dan worker sehat.

### AI tidak menjawab

- Pastikan `OPENAI_API_KEY` benar.
- Buka agent playground untuk melihat error.
- Pastikan knowledge entry aktif.
- Pastikan percakapan sudah menekan **Aktifkan AI**.
- Pastikan agent bukan mode Human Only.

### Pesan outbound gagal

- Pastikan API key berasal dari device yang benar.
- Pastikan device StarSender connected.
- Periksa status dan raw payload Message melalui Django Admin.
