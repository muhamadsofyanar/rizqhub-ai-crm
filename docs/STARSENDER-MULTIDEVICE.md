# StarSender Multi-Device, Personal, dan Grup

## Pembagian credential

### Account API Key

Pada rilis ini dipakai untuk membaca daftar seluruh device akun. Credential disimpan satu kali pada `StarSenderAccount`; endpoint detail device/pesan dapat ditambahkan kemudian tanpa mengubah struktur credential.

### Device Key

Dipakai untuk pengiriman personal, pengiriman grup, dan sinkronisasi daftar grup. Disimpan terpisah pada setiap `StarSenderDevice`.

Account API Key tidak menggantikan Device Key untuk pengiriman inbox. Balasan harus memakai Device Key dari device asal pesan.

## Routing inbound

```text
Webhook Premium akun
→ baca device_id
→ cari StarSenderDevice
→ pilih Brand + Agent + ChannelConnection
→ personal atau grup berdasarkan chat_type/is_group
→ deduplikasi message_id/payload hash
→ simpan ke Unified Inbox
→ proses AI/handoff
→ balas melalui device asal
```

Payload device yang belum ditemukan disimpan dengan status `needs_mapping` dan menghasilkan notifikasi; sistem tidak menebak brand/agent.

## Grup

Daftar grup disinkronkan per device. CRM menyimpan:

- ID/JID grup
- nama
- device pemilik
- status aktif
- kategori
- locked/internal
- mode AI
- waktu sinkron terakhir

Mode AI:

- `off`: tidak membalas.
- `mention`: hanya memproses webhook yang menandai `is_mentioned`.
- `draft`: membuat draft internal untuk persetujuan.
- `autonomous`: dapat mengirim otomatis bila agent dan conversation aktif.

Rekomendasi awal: `off` atau `mention`.

## Preset

### Statis

Menyimpan daftar grup tertentu. Grup baru tidak otomatis masuk.

### Dinamis

Memilih semua grup aktif, tidak terkunci, pada device yang sama dan kategori yang dipilih. Bila preset memiliki Brand, device juga harus dipetakan ke Brand tersebut.

## Broadcast safety

Broadcast personal:

- hanya kontak `marketing_consent=true` atau nomor manual dengan konfirmasi consent;
- opt-out/unsubscribe selalu dilewati;
- nomor dinormalisasi dan divalidasi;
- maksimal penerima mengikuti `CAMPAIGN_MAX_RECIPIENTS`.

Broadcast grup:

- seluruh grup harus berasal dari device pengiriman;
- grup terkunci atau tidak aktif dilewati;
- konfirmasi izin grup wajib;
- preset selalu di-resolve ulang sebelum draft penerima dibuat.

Semua broadcast:

- dibuat sebagai draft;
- feature flag harus aktif;
- admin mengetik `KIRIM`;
- delay minimal 30 detik;
- recipient diklaim dengan row lock;
- idempotency key mencegah duplikasi tujuan;
- retry hanya untuk kegagalan yang diketahui aman (misalnya koneksi belum terbentuk atau HTTP 429);
- timeout/read error dan HTTP 5xx ditandai `status tidak pasti` dan tidak dikirim ulang otomatis;
- antrean dapat dibatalkan;
- status disimpan per penerima.

## Checklist pengujian per device

1. Account sync menemukan device.
2. Device Key lulus uji grup.
3. Brand dan Agent benar.
4. `send_enabled` baru diaktifkan sesudah pengujian.
5. Webhook Premium mengirim `device_id` yang sama dengan data sinkron.
6. Pesan personal masuk ke brand yang benar.
7. Balasan keluar dari nomor yang sama.
8. Sinkronisasi grup menampilkan daftar yang benar.
9. Satu grup uji diatur mention-only.
10. Satu pesan mention dibalas; pesan biasa tidak dibalas.
11. Grup internal dikunci.
12. Satu draft broadcast tes dikirim hanya ke grup uji.
