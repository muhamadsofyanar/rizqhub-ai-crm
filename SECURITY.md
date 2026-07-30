# Security checklist

Sebelum platform digunakan oleh klien eksternal:

- [ ] Perjanjian penggunaan komersial provider telah diverifikasi.
- [ ] Coolify dan GitHub memakai MFA.
- [ ] PostgreSQL dan Redis tidak memiliki port publik.
- [ ] Seluruh domain memakai HTTPS.
- [ ] Backup database terenkripsi dan restore sudah diuji.
- [ ] Password admin awal sudah diganti.
- [ ] `DJANGO_DEBUG=false`.
- [ ] `ALLOWED_HOSTS` dan `CSRF_TRUSTED_ORIGINS` spesifik.
- [ ] API key provider dirotasi bila pernah tampil di screenshot/log.
- [ ] Mode AI Legalitas dan STIFIn masih Draft/Approval.
- [ ] SOP human handoff tersedia.
- [ ] Consent, opt-out, retention, dan penghapusan data ditetapkan.
- [ ] Audit keamanan cross-tenant dilakukan.
- [ ] Rate limiting/WAF ditambahkan sebelum trafik publik besar.
- [ ] Monitoring error dan uptime aktif.

## Pelaporan kerentanan

Jangan membuat issue publik yang memuat API key, data pelanggan, token webhook, atau detail server. Laporkan melalui kanal privat pemilik repository.
