# QA Report — RizqHub AI CRM V4.1

## Pemeriksaan yang berhasil

- Seluruh file Python berhasil diparse dan dikompilasi.
- Static QA bawaan V4 berhasil untuk 37 file Python.
- Jumlah tag template `if/endif`, `for/endfor`, dan `block/endblock` seimbang.
- Lima blok JavaScript utama lulus `node --check`.
- Tidak ada model atau field V4 yang dihapus.
- Tidak ada migration baru.
- Proteksi role pada action Inbox, StarSender, dan Broadcast tetap dipertahankan.
- Konfirmasi Broadcast `KIRIM`, consent personal, izin grup, delay minimal, dan batas penerima tetap berada di backend.
- Credential terenkripsi tidak dipindahkan ke template atau JavaScript.

## Perubahan backend terbatas

- Menambahkan status UI yang mudah dibaca dari data Conversation yang sudah ada.
- Menambahkan filter Inbox untuk belum dibaca, perlu admin, AI aktif, grup, dan pencarian.
- Menambahkan action `wait-customer` yang mempertahankan AI agar dapat melanjutkan saat pelanggan membalas.
- Menambahkan context checklist setup Dashboard dan StarSender.

## Batas pengujian lokal

Pengujian koneksi nyata ke PostgreSQL, Redis, Gemini, dan StarSender tetap harus dilakukan pada VPS produksi setelah deployment. Paket tidak memuat credential atau salinan database pengguna.
