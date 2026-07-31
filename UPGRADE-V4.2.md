# Upgrade RizqHub AI CRM V4.2 — Organic Broadcast

## Tujuan rilis

Rilis ini memprioritaskan pemasaran organik melalui WhatsApp sebelum optimalisasi AI Agent:

- koneksi Account API Key StarSender sekaligus sinkronisasi device;
- sinkronisasi grup per device atau semua device;
- daftar semua grup dari seluruh device;
- broadcast personal, satu grup, dan multi-grup;
- pemilihan grup secara visual berdasarkan device;
- preset grup statis/dinamis dan preset cepat dari pilihan broadcast;
- template pesan teks/media tanpa batas aplikasi;
- upload media template ke persistent volume;
- draft, review penerima, delay, audit, cancel, dan konfirmasi KIRIM.

## Sebelum upload

1. Buat backup PostgreSQL.
2. Pastikan source V4.1 saat ini masih berjalan.
3. Jangan mengganti `APP_ENCRYPTION_KEY`, `DJANGO_SECRET_KEY`, password PostgreSQL, Gemini API Key, Account API Key, atau Device Key.

## Upload GitHub

Ekstrak ZIP lalu upload seluruh isi folder ke root repository dan timpa file lama.

Commit yang disarankan:

```
Upgrade to V4.2 Organic Broadcast
```

## Deploy

Klik Redeploy satu kali di Coolify. Startup menjalankan:

```
python manage.py migrate --noinput
python manage.py migrate --noinput --run-syncdb
```

`run-syncdb` membuat tabel additive baru untuk template pesan. Tidak ada model atau tabel lama yang dihapus.

Status sukses:

```
postgres Healthy
redis Healthy
web Healthy
worker Started
beat Started
```

## Konfigurasi setelah deploy

1. Buka **Pengaturan → WhatsApp & Channel**.
2. Pada akun StarSender klik **Uji & Sinkronkan**.
3. Edit setiap device:
   - isi Device Key;
   - pilih Brand;
   - pilih AI Agent;
   - aktifkan pengiriman;
   - aktifkan sinkronisasi grup.
4. Klik **Sinkronkan Semua Grup**.
5. Buka **Broadcast → Daftar Grup** dan pastikan grup tampil.
6. Buat template teks/media.
7. Buat draft broadcast ke satu nomor sendiri atau satu grup uji.
8. Aktifkan feature flag personal/grup hanya setelah pengujian berhasil.

## Urutan uji aman

- Personal: satu nomor milik sendiri.
- Grup: satu grup uji yang Anda kelola.
- Multi-grup: dua grup uji.
- Preset: dua grup uji.
- Media: satu gambar kecil melalui template upload.

Jangan memulai pengiriman massal sebelum semua pengujian di atas berhasil.
