# RizqHub V4.3.2 — Text + Media & CTA Hotfix

Perubahan:
- Mempertahankan hotfix PostgreSQL broadcast lock V4.3.1.
- UI membedakan **Teks saja** dan **Teks + media**.
- Isi teks wajib dan menjadi caption saat media dipilih.
- Media upload disajikan dengan MIME type sebenarnya (image/png, image/jpeg, application/pdf, dan lainnya) agar StarSender tidak selalu membacanya sebagai dokumen generik.
- Menambahkan builder hingga 3 tautan CTA yang kompatibel dengan chat personal dan grup.
- Tidak menambahkan tombol WhatsApp native karena API publik StarSender yang terdokumentasi hanya menyediakan messageType `text` dan `media`.

Tidak ada migration database dan tidak ada perubahan environment variable.
