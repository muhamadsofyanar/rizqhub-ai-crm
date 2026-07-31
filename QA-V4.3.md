# QA Report — RizqHub AI CRM V4.3

## Pemeriksaan yang berhasil

- Seluruh 37 file Python lolos pemeriksaan AST/static QA.
- `python -m compileall` berhasil.
- Struktur pasangan tag template Django (`if/endif`, `for/endfor`, `block/endblock`, dan lainnya) seimbang.
- `docker-entrypoint.sh` lolos `bash -n`.
- `docker-compose.yml` dapat diparse dan memuat service `web`, `worker`, `beat`, `postgres`, serta `redis`.
- Tidak ditemukan `.env`, database dump, atau API key nyata dalam paket.
- Perubahan V4.3 tidak menambah model atau migration database.

## Area yang diperiksa secara statis

- Route dan view detail/status broadcast.
- Filter dan bulk action grup.
- Template create/edit/duplicate.
- Preset create/edit/duplicate.
- Pipeline move dan quick update.
- Campaign email-only.
- CSS scroll Inbox dan responsive layout.
- Pesan error Device Key 401/403.

## Pengujian yang tetap wajib di VPS

Paket tidak memiliki akses ke database produksi atau credential StarSender Anda. Karena itu, setelah deployment perlu verifikasi langsung:

1. Login dan hak akses tiap role.
2. Scroll tiga kolom Inbox di Chrome desktop dan mobile.
3. Uji Device Key pada setiap device.
4. Sinkronisasi grup pada setiap device.
5. Pengiriman personal teks/media.
6. Pengiriman satu grup teks/media.
7. Pengiriman preset multi-grup dengan delay.
8. Update progres dan status penerima pada detail broadcast.
9. Perubahan tahap/nilai/status deal pada Pipeline.
10. Upload dan akses media template melalui domain produksi.

## Batas kejujuran rilis

V4.3 telah lolos pemeriksaan source statis, tetapi belum dapat dinyatakan lulus integrasi StarSender nyata sebelum Device Key valid dan pengujian personal/grup dijalankan di akun produksi pengguna.
