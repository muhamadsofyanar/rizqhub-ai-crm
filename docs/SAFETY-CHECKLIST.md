# Safety Checklist Produksi

## Sebelum deployment

- [ ] Backup PostgreSQL dibuat di luar volume aplikasi.
- [ ] Checksum backup berhasil dan archive lolos `pg_restore --list`.
- [ ] Commit V3 terakhir dicatat.
- [ ] `.env`, dump, dan API key tidak berada di GitHub.
- [ ] `APP_ENCRYPTION_KEY` tidak diubah.
- [ ] `AUTO_REPLY_DEFAULT=false`.
- [ ] Automation, campaign, dan SaaS masih nonaktif.

## Sesudah deployment

- [ ] PostgreSQL dan Redis healthy.
- [ ] Web, worker, dan beat started.
- [ ] `/health/` normal.
- [ ] `v4_preflight` berhasil.
- [ ] Login dan data lama masih tersedia.
- [ ] Inbox personal masuk/keluar normal.
- [ ] Inbox Live tidak menggandakan pesan.
- [ ] Sapaan/tes tidak memicu handoff.
- [ ] Pesan manual mematikan AI untuk mencegah balasan ganda.

## StarSender

- [ ] Account API Key dimasukkan langsung di dashboard.
- [ ] Seluruh device tersinkron.
- [ ] Device Key tidak pernah dikirim melalui chat/screenshot.
- [ ] Brand/Agent terpetakan per device.
- [ ] Device yang belum diuji tetap `send_enabled=false`.
- [ ] Webhook Premium memakai HTTPS dan token akun.
- [ ] Pesan dari device tidak dikenal masuk `needs_mapping`, bukan diterka.
- [ ] Grup internal dikunci.
- [ ] AI grup default off/mention.

## Broadcast

- [ ] Feature broadcast masih nonaktif selama setup.
- [ ] Consent personal atau izin grup diverifikasi.
- [ ] Draft penerima ditinjau satu per satu untuk tes awal.
- [ ] Delay minimal 30 detik.
- [ ] Kirim tes ke satu nomor atau satu grup dahulu.
- [ ] Tidak ada grup internal dalam preset.
- [ ] Opt-out dan unsubscribe dilewati.
- [ ] Status `failed` diperiksa sebelum pengiriman berikutnya.
- [ ] Status `tidak pasti` diverifikasi langsung di WhatsApp/StarSender dan tidak dikirim ulang sembarangan.

## Keamanan berkelanjutan

- [ ] Secret yang pernah terlihat di chat dirotasi secara terencana.
- [ ] MFA aktif pada GitHub dan Coolify.
- [ ] PostgreSQL/Redis tidak diekspos ke internet.
- [ ] HTTPS aktif.
- [ ] HSTS baru dinaikkan setelah domain/HTTPS stabil.
- [ ] Backup harian berstatus completed.
- [ ] Restore diuji pada lingkungan terpisah.
- [ ] Audit log dan System Health diperiksa berkala.
- [ ] Pesan/tujuan dengan status `sending` lebih dari 15 menit otomatis dikarantina sebagai `tidak pasti`, bukan dikirim ulang.
