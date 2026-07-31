# QA V4.2 Organic Broadcast

Pemeriksaan yang dijalankan:

- seluruh file Python berhasil diparse dan dikompilasi;
- static QA proyek berhasil;
- URL yang dipakai template terdaftar;
- JavaScript wizard broadcast lolos `node --check` setelah substitusi tag Django;
- status StarSender `disconnected` tidak lagi salah dibaca sebagai `connected`;
- respons list grup mendukung array objek, array ID, map nama→JID, dan map JID→nama;
- Account API Key test sekarang sekaligus menyinkronkan device;
- tabel baru bersifat additive (`BroadcastTemplate`);
- media template menggunakan token acak dan tidak memerlukan login agar StarSender dapat mengambil file;
- broadcast tetap dibuat sebagai draft;
- delay minimum 30 detik tetap dipertahankan;
- consent personal, izin grup, locked group, idempotency, uncertain-send quarantine, audit, dan konfirmasi `KIRIM` tetap dipertahankan.

Batas QA:

- koneksi Account API Key, Device Key, daftar grup, dan pengiriman nyata harus diuji pada akun StarSender pengguna;
- format respons API StarSender dapat berubah; parser dibuat defensif, tetapi respons nyata tetap perlu diperiksa bila sinkronisasi menghasilkan nol grup;
- rilis ini tidak menjamin deliverability dan tidak boleh digunakan untuk spam.
