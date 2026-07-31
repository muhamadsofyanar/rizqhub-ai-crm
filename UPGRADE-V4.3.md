# Upgrade RizqHub AI CRM V4.3 — Organic Growth Console

## Tujuan

Rilis ini memperbaiki pengalaman operasional yang terlihat pada V4.2: detail broadcast yang tidak jelas, Inbox sulit di-scroll, Pipeline melebar dan kurang operasional, template membingungkan, kategori/preset sulit dikelola, dan Campaign bercampur dengan broadcast WhatsApp.

## Dampak database

- Tidak ada tabel atau kolom baru.
- Tidak ada data lama yang dihapus.
- Tidak perlu migration khusus V4.3.
- Data broadcast, grup, preset, template, chat, kontak, dan pipeline lama tetap digunakan.

## Sebelum upload

1. Pastikan aplikasi V4.2 masih dapat dibuka.
2. Buat backup PostgreSQL melalui mekanisme yang sudah dipakai pada V4.
3. Jangan mengubah `POSTGRES_PASSWORD`, `DJANGO_SECRET_KEY`, `APP_ENCRYPTION_KEY`, Gemini API key, Account API Key, atau Device Key saat upgrade UI ini.
4. Pastikan repository GitHub tidak berisi `.env` atau credential nyata.

## Instalasi satu kali

1. Ekstrak ZIP V4.3.
2. Upload seluruh isi folder `rizqhub-ai-crm-v4-3-organic-growth-console` ke root repository GitHub dan timpa file lama.
3. Commit:

```text
Upgrade to V4.3 Organic Growth Console
```

4. Di Coolify klik **Redeploy** satu kali.
5. Tunggu sampai:

```text
postgres  Healthy
redis     Healthy
web       Healthy
worker    Started
beat      Started
```

6. Buka CRM dan tekan `Ctrl + F5`.

## Perubahan yang harus terlihat

### Promosi WhatsApp

- Halaman utama berisi tombol Kirim Personal, Kirim Grup, Template Pesan, Audiens Grup, Daftar Grup, dan Periksa Device.
- Detail broadcast menampilkan progress bar, preview pesan, penjelasan status, filter penerima, serta konfirmasi `KIRIM`.
- Pada broadcast Draft, penerima berstatus **Siap dikirim**, bukan seolah-olah sudah berjalan.

### Inbox

- Daftar percakapan di kiri dapat di-scroll.
- Isi chat di tengah dapat di-scroll.
- Detail pelanggan di kanan dapat di-scroll.
- Ketiga area tidak saling mengunci.

### Pipeline

- Pilih satu bisnis melalui tab di bagian atas.
- Kartu deal dapat dipindahkan tahapnya.
- Bagian “Ubah nilai, pemilik, atau hasil” dapat mengubah nilai potensi, status berhasil/tidak lanjut, penanggung jawab, target selesai, dan alasan tidak lanjut.

### Template

- Template tampil sebagai kartu dengan preview.
- Dapat dicari, difilter, diedit, dan diduplikasi.
- Editor menampilkan preview WhatsApp dan tombol variabel personalisasi.

### Audiens Grup

- Kategori digunakan untuk memberi label pada banyak grup.
- Preset statis menyimpan grup tertentu.
- Preset dinamis mengambil seluruh grup aktif dalam kategori.
- Preset dapat diedit atau diduplikasi; salinan dibuat nonaktif agar aman.
- Daftar Grup mendukung bulk category, lock/unlock, dan aktif/nonaktif.

### Campaign

- Campaign ditampilkan sebagai Campaign Email (Lanjutan).
- Form Campaign hanya menampilkan koneksi Mailketing.
- WhatsApp personal dan grup hanya dikerjakan melalui Promosi WhatsApp.

## Urutan uji setelah deployment

1. Buka **WhatsApp & Channel**.
2. Pada satu device, lakukan berurutan: **Isi Key & Mapping → Uji Key → Ambil Grup**.
3. Pastikan Daftar Grup berisi grup device tersebut.
4. Buat satu template teks.
5. Buat satu template media.
6. Buat kategori uji dan terapkan ke dua grup melalui bulk action.
7. Buat preset statis berisi dua grup.
8. Buat broadcast ke satu nomor milik sendiri sebagai Draft, tinjau, lalu kirim.
9. Buat broadcast ke satu grup uji.
10. Buat broadcast ke preset dua grup.

## Catatan error 401 StarSender

Aplikasi tidak dapat memperbaiki credential yang ditolak provider. Error `HTTP 401 Invalid API Key` pada **Uji Device Key** atau **Sinkronkan Grup** berarti Device Key yang tersimpan untuk device tersebut ditolak. Salin Device Key dari detail device yang sama, jangan Account API Key, simpan tanpa spasi, lalu uji kembali. Credential tidak perlu dan tidak boleh dikirim melalui chat.
