# Release Manifest — V4 Consolidated RC1

Dokumen ini membedakan fitur yang **sudah diimplementasikan**, fitur yang **dipagari/nonaktif**, dan fitur yang **sengaja belum disertakan** agar pengoperasian konsisten dan tidak menimbulkan asumsi berlebihan.

## Sudah diimplementasikan

### Inbox dan AI

- Inbox Live untuk daftar percakapan dan isi pesan.
- Status pengiriman: queued, sending, sent, delivered, read, failed, dan uncertain.
- Claim atomik task pengiriman dan karantina hasil ambigu.
- Human takeover, aktivasi AI, assignment, catatan internal, dan persetujuan draft.
- Smart Handoff V2, customer memory dasar, evaluasi AI, dan kontrol panjang jawaban.
- Parser webhook personal/grup dan deduplikasi provider message ID.

### StarSender

- Account API Key untuk sinkronisasi daftar device.
- Device Key per device untuk kirim personal, kirim grup, dan sinkronisasi grup.
- Mapping device → Brand → Agent → ChannelConnection.
- Webhook Premium multi-device berdasarkan `device_id`.
- Balasan inbox melalui device asal percakapan.
- Sinkronisasi grup per device.
- Kategori grup, preset statis, dan preset dinamis.
- AI grup: off, mention-only, draft, atau autonomous.

### Broadcast

- Personal ke kontak consent atau nomor manual yang dikonfirmasi.
- Satu/banyak grup pada device yang sama.
- Draft, preview penerima, konfirmasi `KIRIM`, delay minimum, schedule, cancel, audit log, dan status per recipient.
- Fitur broadcast personal dan grup nonaktif secara default.

### Operasional

- Feature flag database tanpa redeploy.
- Backup PostgreSQL terjadwal dan script backup pra-upgrade dengan validasi archive.
- System Health, notifikasi aplikasi, audit log, rate limit webhook, dan batas payload.
- Role guard untuk operasi inbox, StarSender, dan broadcast.

## Tersedia tetapi nonaktif secara default

- Automation dan webhook n8n.
- Broadcast personal.
- Broadcast grup.
- Campaign legacy.
- Workspace/SaaS foundation.

Aktivasi dilakukan pada **Feature Settings** dan fitur berisiko meminta teks `AKTIFKAN`.

## Sengaja belum disertakan dalam RC1

- Device Rotator `/api/send/rotator`.
- Fallback otomatis ke nomor/device lain untuk balasan inbox.
- Pengambilan Device Key otomatis dari Account API Key.
- Rekonsiliasi otomatis status `uncertain` melalui API detail pesan.
- Billing/payment gateway produksi.
- WebSocket; Inbox Live masih memakai polling adaptif.
- Jaminan kompatibilitas dengan semua variasi payload akun StarSender tanpa uji akun nyata.

Pengecualian ini disengaja. Rotator/fallback dapat membuat pelanggan menerima balasan dari nomor berbeda, sedangkan resend hasil ambigu berisiko menggandakan pesan.

## Kriteria pilot produksi

1. Backup pra-upgrade tervalidasi.
2. Deployment dan `v4_preflight` berhasil.
3. Data V3 tetap tersedia.
4. Satu device personal lulus uji masuk/keluar.
5. Satu device tambahan lulus routing Brand/Agent.
6. Satu grup uji lulus sinkronisasi dan mention-only.
7. Broadcast tetap nonaktif sampai tes satu tujuan selesai.
8. Status failed/uncertain dapat dibedakan dan tidak dikirim ulang sembarangan.
