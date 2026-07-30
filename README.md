# RizqHub AI CRM

Starter SaaS CRM multi-brand dan multi-agent yang siap dijalankan dengan Docker Compose di Coolify. Paket ini dibangun untuk tiga bisnis awal:

- Jasa Legalitas
- STIFIn
- Produk Digital

Setiap bisnis memiliki brand, AI agent, knowledge base, dan sales pipeline sendiri. Sistem menerima pesan WhatsApp melalui webhook StarSender, menyimpan kontak dan percakapan, menyiapkan atau mengirim jawaban AI, serta menyediakan human handoff ke CS.

## Status implementasi

### Sudah berfungsi

- Login, super admin, workspace/tenant, dan membership.
- Multi-brand dan multi-agent.
- Kontak CRM dan Customer 360 sederhana.
- Unified Inbox WhatsApp.
- Webhook StarSender, deduplication, queue, dan outbound API.
- AI Agent Builder dengan mode Draft, Approval, Limited, Autonomous, dan Human.
- Knowledge base berbasis entri teks dan retrieval sederhana.
- Playground pengujian agent.
- Human takeover, aktif/nonaktif AI, handoff keyword.
- Sales pipeline per brand.
- Integrasi Mailketing: credential, webhook event, dan pengiriman email.
- Campaign WhatsApp/email berbasis consent, tag filter, personalization, throttling, dan status per penerima.
- PostgreSQL, Redis, Celery, Gunicorn, WhiteNoise.
- Enkripsi credential provider.
- Health check, persistent volume, bootstrap admin, serta data awal tiga bisnis.

### Fondasi tersedia, tetapi perlu pengembangan lanjutan

- Automation visual berbasis trigger-condition-action.
- Upload PDF/DOCX/XLSX serta vector search/pgvector.
- Order, payment gateway, booking, invoice, dan billing SaaS.
- Drag-and-drop pipeline.
- Role permission granular di UI.
- WhatsApp delivery/read status apabila payload provider tersedia.
- Subscription, quota, metering biaya, white-label, dan public API.

Aplikasi ini adalah fondasi produksi untuk pilot/internal use, bukan klaim bahwa seluruh blueprint enterprise sudah selesai dalam satu paket.

## Arsitektur

```text
Browser
  │
  ▼
Django + Gunicorn (web)
  ├── CRM, dashboard, inbox, agent builder
  ├── StarSender webhook
  └── Mailketing webhook
  │
  ├──────── PostgreSQL
  │
  └──────── Redis ───── Celery worker
                         ├── Proses pesan masuk
                         ├── Panggil OpenAI Responses API
                         └── Kirim balasan via StarSender
```

## Menjalankan lokal

1. Salin environment.

```bash
cp .env.example .env
```

2. Buat secret.

```bash
python scripts/generate_secrets.py
```

Salin hasilnya ke `.env`, kemudian isi `ADMIN_EMAIL`, `ADMIN_PASSWORD`, dan kredensial lain.

3. Jalankan.

```bash
docker compose up -d --build
```

4. Buka aplikasi pada port yang diproksikan oleh Docker/Coolify. Untuk lokal, tambahkan sementara pada service `web`:

```yaml
ports:
  - "8000:8000"
```

Lalu buka `http://localhost:8000`.

## Deployment Coolify

Panduan rinci tersedia di [`docs/COOLIFY.md`](docs/COOLIFY.md).

Ringkasnya:

1. Buat repository GitHub baru.
2. Upload seluruh isi folder ini ke root repository.
3. Di Coolify: **Project → Add Resource → Private Repository/GitHub App**.
4. Pilih build pack **Docker Compose**.
5. Base directory `/` dan compose location `/docker-compose.yml`.
6. Masukkan seluruh variable wajib dari `.env.example`.
7. Hubungkan domain ke service `web`, port `8000`.
8. Deploy.
9. Login memakai `ADMIN_EMAIL` dan `ADMIN_PASSWORD`.

## Setup StarSender

1. Login aplikasi.
2. Buka **Integrasi → Tambah integrasi**.
3. Provider: `StarSender`.
4. Pilih brand dan agent.
5. Isi Device API Key dari StarSender.
6. Simpan, lalu salin webhook URL yang ditampilkan.
7. Masukkan URL tersebut ke menu webhook StarSender.
8. Kirim pesan uji dari nomor lain.
9. Periksa **Unified Inbox**.
10. Pertahankan agent pada mode `Draft only` sampai knowledge dan jawaban selesai diuji.

Format webhook StarSender yang didukung:

```json
{
  "device_id": "device-id",
  "device": "Nama Device - 628xxx",
  "message_id": "message-id",
  "from": "628123456789",
  "push_name": "Nama Pelanggan",
  "message": "Saya ingin membuat PT",
  "file": "",
  "is_group": false,
  "is_me": false,
  "timestamp": 1760000000
}
```

Parser juga menerima format webhook dasar `message`, `from`, dan `timestamp`.

## Setup AI

Aplikasi mendukung **Gemini** dan **OpenAI**. Untuk memakai Gemini, masukkan pada environment Coolify:

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=isi-api-key-google-ai-studio
GEMINI_MODEL=gemini-2.5-flash
AUTO_REPLY_DEFAULT=false
```

Jangan menambahkan tanda kutip dan jangan mengunggah API key ke GitHub. Sebagai alternatif, gunakan `AI_PROVIDER=auto`; aplikasi akan memilih Gemini bila `GEMINI_API_KEY` tersedia, lalu menggunakan OpenAI bila hanya `OPENAI_API_KEY` yang tersedia.

Konfigurasi OpenAI tetap didukung:

```env
AI_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5-mini
AUTO_REPLY_DEFAULT=false
```

`AUTO_REPLY_DEFAULT=false` adalah pilihan aman. Percakapan baru tidak langsung dibalas otomatis. CS dapat membuka percakapan dan menekan **Aktifkan AI** setelah alur teruji.

### Mode agent

- `Draft only`: AI membuat draft internal.
- `Perlu persetujuan`: draft menunggu CS.
- `Auto reply terbatas`: dapat mengirim otomatis saat AI diaktifkan.
- `Autonomous`: dapat mengirim otomatis saat AI diaktifkan.
- `Human only`: AI tidak mengirim jawaban.

Starter ini belum menjalankan tool bisnis berisiko seperti payment/refund atau perubahan data otomatis. Itu harus ditambahkan dengan validasi, permission, idempotency, dan audit log.

## Setup Mailketing

1. Tambah integrasi dengan provider `Mailketing`.
2. Isi API token, nama pengirim, dan email pengirim yang sudah diverifikasi.
3. Salin webhook URL dari aplikasi ke menu Integration → Webhook di Mailketing.

Service pengiriman tersedia pada `crm/services/providers.py` melalui fungsi `send_mailketing()` dan dapat dipakai pada campaign/task berikutnya.

## Data awal

Perintah bootstrap otomatis membuat:

- Workspace `RizqHub`.
- Admin dari environment.
- Brand Jasa Legalitas, STIFIn, dan Produk Digital.
- Tiga AI agent.
- Pipeline dan stage per brand.
- Knowledge starter berupa batasan dan data awal pelanggan.

Bootstrap idempotent dan aman dijalankan ulang.

## Struktur repository

```text
config/                  Django settings, URL, Celery
crm/
  management/commands/   bootstrap dan wait_for_db
  services/              AI, encryption, provider, inbound parser
  static/                UI stylesheet
  templates/             dashboard dan halaman CRM
  models.py               model multi-tenant
  tasks.py                Celery jobs
  views.py                web UI dan webhook
scripts/                  generator secret
docs/                     panduan deployment dan arsitektur
Dockerfile
docker-compose.yml
```

## Keamanan penting

- Jangan commit `.env`.
- Jangan membuka PostgreSQL atau Redis ke internet.
- Gunakan domain HTTPS.
- Aktifkan MFA pada Coolify dan GitHub.
- Ganti password awal setelah login.
- Credential StarSender/Mailketing disimpan terenkripsi.
- URL webhook mengandung token rahasia; rotasi dengan membuat koneksi baru bila bocor.
- Untuk legalitas dan STIFIn, gunakan mode Draft/Approval dan human handoff.
- Lakukan backup volume PostgreSQL secara rutin.

Baca [`SECURITY.md`](SECURITY.md) sebelum membuka aplikasi kepada klien eksternal.

## Pengembangan schema

Starter menggunakan `MIGRATION_MODULES = {"crm": None}` dan `migrate --run-syncdb` agar paket pertama dapat langsung membuat seluruh tabel CRM. Sebelum melakukan perubahan model setelah aplikasi berisi data produksi, ubah ke migration Django formal:

1. Hapus pengaturan `MIGRATION_MODULES`.
2. Buat migration awal pada database development baru.
3. Terapkan strategi baseline/fake migration secara hati-hati pada database produksi.

Jangan mengubah model produksi tanpa backup dan migration plan.

## Lisensi

Kode starter ini disediakan untuk proyek Anda. Periksa secara terpisah hak penggunaan komersial, resale, agency, atau white-label dari StarSender, Mailketing, dan provider AI yang digunakan.
