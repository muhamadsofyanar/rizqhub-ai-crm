# Security Policy

## Credential

- Jangan commit `.env`, dump database, Account API Key, Device Key, token Mailketing, atau API key AI.
- `APP_ENCRYPTION_KEY` harus tetap sama selama masih ada credential terenkripsi. Menggantinya tanpa re-enkripsi membuat credential lama tidak dapat dibaca.
- Credential StarSender hanya dapat diubah oleh role owner/admin.
- Webhook token hanya ditampilkan kepada owner/admin.
- Rotasi credential yang pernah terlihat di chat, screenshot, atau log dilakukan secara terencana dan satu per satu.

## Network

- PostgreSQL dan Redis tidak boleh memiliki port publik.
- Gunakan HTTPS untuk aplikasi dan webhook.
- Batasi `ALLOWED_HOSTS` dan `CSRF_TRUSTED_ORIGINS` ke domain produksi.
- `DJANGO_DEBUG=false` pada produksi.
- Rate limit webhook dan batas payload harus tetap aktif.

## AI

- `AUTO_REPLY_DEFAULT=false` sampai agent dan Knowledge Base diuji.
- Jasa legalitas tidak boleh memberi keputusan hukum final atau jaminan izin.
- Grup default `off` atau `mention`.
- Human handoff wajib tersedia.
- Review System Health dan evaluasi AI untuk error, confidence, dan sumber yang dipakai.

## Broadcast

- Personal broadcast hanya untuk kontak dengan consent yang sah.
- Grup hanya boleh dikirim bila ada izin yang sesuai.
- Grup internal harus dikunci.
- Feature broadcast default nonaktif dan aktivasi memerlukan `AKTIFKAN`.
- Pengiriman memerlukan konfirmasi `KIRIM`, delay minimal, dan audit log.
- Jangan menaikkan batas recipient sebelum pengujian load dan kebijakan provider selesai.

## Data dan backup

- Backup harus berada di luar volume database utama.
- Simpan checksum dan uji restore pada lingkungan terpisah.
- Tetapkan retensi, akses, ekspor, dan penghapusan data pelanggan.
- Jangan melakukan restore hanya untuk memperbaiki error aplikasi; gunakan rollback kode terlebih dahulu bila data sehat.

## Pelaporan kerentanan

Jangan membuat issue publik yang memuat credential, token webhook, data pelanggan, atau detail server. Gunakan kanal privat pemilik repository.
