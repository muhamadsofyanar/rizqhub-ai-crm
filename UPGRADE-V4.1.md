# Upgrade RizqHub AI CRM V4.1 — Simple Operations UX

Rilis ini menyederhanakan navigasi dan alur kerja tanpa menghapus data atau mengganti model database V4.

## Perubahan utama

- Navigasi operasional dipangkas menjadi Beranda, Inbox, Kontak & Leads, Pipeline, Broadcast, AI Agent, Pengetahuan, dan Evaluasi AI.
- Menu teknis dipindahkan ke Mode Lanjutan.
- Inbox menjadi tiga kolom: percakapan, chat, dan detail pelanggan.
- Status percakapan disederhanakan: AI Menangani, Perlu Admin, Admin Menangani, Menunggu Pelanggan, dan Selesai.
- Broadcast menggunakan wizard enam langkah dan tetap membuat draft terlebih dahulu.
- Pengaturan StarSender menjadi wizard lima langkah dengan Account API Key dan Device Key yang dipisahkan jelas.
- Dashboard menampilkan checklist setup dan prioritas kerja.
- Bantuan kontekstual tersedia di setiap halaman utama.
- Tidak ada migration atau perubahan skema database pada rilis ini.

## Sebelum deploy

1. Pastikan V4 RC1 yang sedang berjalan sehat.
2. Buat backup PostgreSQL dari Coolify/VPS.
3. Catat commit produksi saat ini untuk rollback.
4. Jangan mengubah Gemini API Key, Device Key, Account API Key, password database, atau secret lain.

## Deployment satu kali

1. Ekstrak ZIP.
2. Upload seluruh isi folder ke root repository `muhamadsofyanar/rizqhub-ai-crm` dan timpa file lama.
3. Commit:

   `Upgrade to V4.1 Simple Operations UX`

4. Di Coolify klik **Redeploy** satu kali.
5. Tunggu sampai `postgres`, `redis`, dan `web` Healthy; `worker` dan `beat` Started.
6. Buka CRM lalu tekan `Ctrl + F5`.

## Smoke test setelah deploy

1. Login dan buka Beranda.
2. Ubah Mode Sederhana ke Mode Lanjutan lalu kembali lagi.
3. Buka Inbox dan pastikan pesan live muncul tanpa refresh.
4. Ubah status chat ke AI Menangani, Admin Menangani, Menunggu Pelanggan, lalu kembalikan sesuai kebutuhan.
5. Kirim satu balasan manual dan pastikan AI berhenti pada chat tersebut.
6. Buka WhatsApp & Channel dan pastikan akun/device lama tetap terlihat.
7. Buat broadcast uji sebagai draft; jangan mengetik KIRIM.
8. Periksa menu Fitur; jangan aktifkan broadcast massal sebelum pengujian selesai.

## Rollback

Jika web gagal sehat atau halaman utama error:

1. Deploy kembali commit V4 RC1 sebelumnya dari Coolify.
2. Jangan menghapus volume PostgreSQL/Redis.
3. Karena rilis ini tidak mengubah skema database, rollback source tidak memerlukan restore database.

## Catatan keamanan

- Credential tetap terenkripsi dan tidak ditampilkan kembali.
- Broadcast tetap membutuhkan feature flag dan konfirmasi `KIRIM`.
- Balasan manual tetap mematikan AI untuk mencegah balasan ganda.
- Mode Lanjutan hanya mengubah tampilan menu; tidak mengubah hak akses backend.
