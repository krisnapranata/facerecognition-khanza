# Privasi & Keamanan

Dokumen ini adalah titik awal teknis, **bukan** nasihat hukum. Penilaian akhir harus dilakukan
bersama Pejabat Pelindungan Data Pribadi (PPDP/DPO), bagian hukum, dan komite etik/manajemen
rumah sakit, merujuk UU No. 27/2022 tentang Pelindungan Data Pribadi (data biometrik dan data
kesehatan = data pribadi spesifik) serta regulasi rekam medis yang berlaku.

## 1. Dasar pemrosesan

| Mode | Dasar yang dicatat (`processing_basis`) |
|---|---|
| Demo | `DEMO_SYNTHETIC` (data sintetis) atau `TEST_PARTICIPANT_CONSENT` (peserta uji dengan persetujuan tertulis) |
| Produksi | `PATIENT_CONSENT` (persetujuan eksplisit pasien) atau peserta uji |

Registrasi ditolak tanpa centang konfirmasi persetujuan. Penggunaan pada pasien harus bersifat
**sukarela** dengan **alternatif identifikasi manual** yang setara (KTP/kartu pasien/pertanyaan
identitas standar). Pasien tidak boleh dirugikan bila menolak atau bila sistem gagal mengenali.

## 2. Pemberitahuan (contoh teks)

> Rumah sakit menyediakan identifikasi wajah **opsional** untuk mempercepat pendaftaran. Foto
> wajah Anda diubah menjadi kode biometrik terenkripsi dan hanya dipakai untuk membantu petugas
> mengenali Anda di pintu masuk. Petugas tetap memverifikasi identitas Anda. Data disimpan
> paling lama *N* hari / sampai Anda mencabut persetujuan, tidak dikirim ke pihak luar, dan dapat
> dihapus kapan saja atas permintaan Anda di loket pendaftaran. Anda tetap dapat dilayani tanpa
> identifikasi wajah.

Pasang pemberitahuan di area kamera pintu masuk (kamera CCTV memproses semua orang yang lewat,
termasuk yang tidak terdaftar — wajah mereka dideteksi tetapi tidak disimpan).

## 3. Penilaian dampak (DPIA ringkas)

| Risiko | Mitigasi yang sudah ada | Sisa risiko / tindakan lanjut |
|---|---|---|
| Salah identifikasi (false match) | status `NEEDS_REVIEW` untuk skor sedang/ambigu, verifikasi wajib oleh petugas, nama hanya untuk kandidat & pengguna berizin | validasi ambang lokal; pelatihan petugas |
| Gagal dikenali (false non-match) | alur manual & pencarian pasien; status kontrol netral | — |
| Kebocoran biometrik | embedding & foto terenkripsi (Fernet), file nama acak permission 600, tidak ada URL publik, kunci di `.env` | pindahkan kunci ke secret store/HSM; enkripsi disk; batasi akses OS |
| Penyalahgunaan oleh internal | RBAC (`face_operator`/`face_admin`), audit login, registrasi, lihat foto, verifikasi, pencarian, hapus | review audit berkala |
| Spoofing (foto/layar) | **belum ada liveness** | wajib sebelum produksi tanpa pengawasan petugas |
| Pelacakan berlebihan | tidak menyimpan video/frame CCTV; ID track sementara; event tanpa embedding | — |
| Retensi berlebihan | `expires_at` + `face_purge_expired` | jadwalkan cron harian |
| Bias demografis model | — | evaluasi akurasi per kelompok sebelum produksi |

## 4. Kontrol teknis

* Login Django, sesi 8 jam, cookie `HttpOnly`/`SameSite=Lax` (Secure pada produksi), nama cookie
  terpisah dari aplikasi SIMRS lain di host yang sama.
* CSRF untuk semua request browser yang mengubah data; API worker memakai HMAC-SHA256 +
  timestamp ±30 detik + nonce sekali pakai (perlindungan replay).
* Rate limit: login 10/menit/IP, verifikasi 60/menit, registrasi 20/menit, capture 30/menit,
  pencarian 60/menit, API worker 600/menit.
* Validasi input ketat (regex ID, dimensi & nilai embedding, tipe/ukuran/resolusi gambar,
  re-encode gambar untuk membuang metadata EXIF).
* Embedding tidak pernah dikembalikan ke browser. Nama/No. RM hanya dikirim ke worker untuk
  label overlay kamera, dan worker hanya menggambarnya bila token stream tersebut terikat izin
  `view_candidate_identity`; snapshot registrasi dan stream tanpa izin tetap tanpa identitas.
* Server HTTP worker hanya di `127.0.0.1` dengan token; URL RTSP disamarkan di log.
* `APP_MODE=production` memaksa `DEBUG=False` dan cookie Secure.
* Tidak ada pengiriman biometrik ke cloud.

## 5. Penghapusan & pencabutan

* **Nonaktifkan**: referensi tidak dipakai pencocokan (dapat ditinjau).
* **Hapus**: baris referensi + file foto dihapus permanen; riwayat kejadian tetap ada dengan
  `candidate_reference = NULL`.
* Retensi otomatis: `manage.py face_purge_expired` (cron harian disarankan):
  `0 2 * * * cd /path/simrs-face-recognition && .venv/bin/python manage.py face_purge_expired`

## 6. Cadangan & salinan data

* Cadangan database `face_*` dan folder `media_private/` tetap terenkripsi karena isinya sudah
  dienkripsi di tingkat aplikasi; **kunci disimpan terpisah** dari cadangan.
* Penghapusan permanen tidak otomatis menghapus salinan di cadangan lama: tetapkan masa simpan
  cadangan ≤ masa retensi, atau buang kunci lama setelah rotasi (`face_rotate_keys`) agar salinan
  lama tidak dapat didekripsi (crypto-shredding).
* Jangan menyalin `media_private/`, `.env`, atau database ke repository/flashdisk tanpa enkripsi.

## 7. Pengujian keamanan yang sudah otomatis (`tests/`)

autentikasi wajib, hak akses per peran, CSRF, HMAC salah/kedaluwarsa/replay/body diubah, validasi
payload, path traversal penyimpanan, file terenkripsi & permission 600, embedding tidak bocor di
respons, candidate ID dari client diabaikan, adapter Khanza menolak non-SELECT.

Disarankan sebelum produksi: `manage.py check --deploy`, uji penetrasi, TLS (reverse proxy
nginx), firewall agar MariaDB tidak terbuka ke jaringan tak tepercaya (saat audit, MariaDB
lokal terdeteksi listen di `0.0.0.0:3306` dan user `sik` memiliki `ALL PRIVILEGES ... WITH GRANT
OPTION` — buat user khusus dengan hak terbatas pada tabel `face_*` untuk produksi).
