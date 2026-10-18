# Integrasi Khanza (baca-saja)

Status: **aktif (baca-saja) ke database `yatofaasli`** sejak 2026-10-18, dengan user `face_ro`.
Tanpa `KHANZA_ENABLED=true`, aplikasi kembali ke data sintetis (`apps/patient_lookup/demo_data.py`).
Tidak ada nama tabel/kolom Khanza di kode Python; semuanya ada di `config/khanza_queries.json`.

Pemeriksaan kapan saja (tidak mencetak identitas pasien):

```bash
.venv/bin/python manage.py face_khanza_check
.venv/bin/python manage.py face_khanza_check --rm <NO_RM> --date YYYY-MM-DD
```

Perilaku saat aktif:
* Registrasi wajah: No. RM wajib ada di `pasien`; nama diambil dari Khanza (bukan ketikan petugas);
  dasar pemrosesan wajib persetujuan pasien/peserta uji; referensi ditandai bukan data uji.
* Cari manual & verifikasi MANUAL: dari `pasien` (No. RM, nama, JK, tanggal lahir — maks. 20 baris).
* Status kontrol (setelah identitas diverifikasi): dicari untuk tanggal hari ini di tiga sumber.
* Khanza tidak dapat diakses → registrasi `503`, status kontrol `NEEDS_MANUAL_CHECK`; pelayanan tetap jalan.

## Registrasi kunjungan ke `reg_periksa` & login akun Khanza (sejak 2026-10-18)

Atas permintaan operator, aplikasi kini **boleh menulis** ke Khanza, terbatas pada pola
`simrskhanza/DlgReg.java`. Ini mengubah prinsip awal "tidak menulis ke Khanza".

* **Login**: `KHANZA_AUTH_ENABLED=true` → halaman login menerima ID user/password tabel `user`
  Khanza (`AES_ENCRYPT(?,'nur')` / `AES_ENCRYPT(?,'windi')`, pola `fungsi/akses.java`) dan tabel
  `admin`. Hanya `user.registrasi='true'` yang boleh masuk; mendapat grup `face_operator` +
  `face_registrar`, disinkronkan setiap login. Password Khanza tidak disimpan (user lokal `kz.<id>`
  tanpa password). Akun lokal (`admin_face`, dll.) tetap berfungsi.
* **Registrasi**: `KHANZA_REGISTRATION_ENABLED=true` → tombol **Daftarkan ke Poli** pada kejadian yang
  identitasnya sudah diverifikasi (CONFIRMED/MANUAL). Petugas mengklik poliklinik lalu dokter.
  **Pilihan poli & dokter diambil dari tabel `jadwal`** (hanya poli yang punya jadwal; hanya dokter
  yang terjadwal di poli tsb.), lengkap dengan jam praktik hari ini, jadwal mingguan, dan kuota.
  `KHANZA_JADWAL_MODE=hari_ini` (disarankan untuk operasional): hanya dokter yang praktik hari ini
  dan kuotanya belum penuh (jumlah reg_periksa dokter hari itu, seperti DlgReg) yang bisa dipilih;
  `semua`: semua dokter di tabel jadwal bisa dipilih (dipakai demo karena tabel jadwal tidak memuat
  hari Minggu). Server selalu menolak dokter yang tidak terjadwal di poli yang dipilih. Jika ada booking/surat kontrol
  BPJS (dipetakan via `maping_poli_bpjs`/`maping_dokter_dpjpvclaim`)/SKDP hari ini, muncul tombol
  **Pakai**. Pratinjau tidak menulis apa pun.
* **Aturan DlgReg yang ditiru** (`apps/integrations/khanza_registration.py`): no_rawat
  `yyyy/MM/dd/NNNNNN`; no_reg 3 digit menurut `KHANZA_URUTNOREG` (`dokter + poli`) dan
  `KHANZA_BASENOREG` (`booking`) — nilai dari `setting/database.xml` Khanza; stts_daftar, status_poli,
  biaya_reg (registrasi/registrasilama), p_jawab/almt_pj/hubunganpj/kd_pj dari pasien, umur Th/Bl/Hr,
  `stts='Belum'`, `Ralan`, `Belum Bayar`; retry 5x bila no_rawat bentrok; `UpdateUmur`; tolak bila
  rawat inap aktif atau (jika `wajib_closing_kasir='Yes'`) ada tagihan belum closing.
* **Tambahan aplikasi**: tolak registrasi ganda poli yang sama di hari yang sama; satu kejadian hanya
  bisa didaftarkan sekali (baris dikunci); `GET_LOCK` untuk penomoran; audit `KHANZA_VISIT_REGISTERED`
  (sukses & gagal); No. Rawat ditautkan ke kejadian.
* **Koneksi tulis** `khanza_rw` memakai akun MySQL yang sama dengan Khanza desktop (`KHANZA_RW_DB_USER`,
  keputusan operator). Karena hak akun itu luas, `apps/integrations/khanza_rw.py` menolak setiap SQL
  tulis kecuali **persis** `INSERT INTO reg_periksa (...)` dan `UPDATE pasien SET umur=...` yang
  terdaftar. Untuk produksi disarankan user khusus: SELECT tabel terkait + `INSERT` reg_periksa +
  `UPDATE (umur)` pasien.
* **Membatalkan** kunjungan yang salah: lakukan dari Khanza desktop (hapus/ubah status Batal), seperti biasa.

## Prinsip

* Pencarian pasien & status kontrol tetap baca-saja (user `face_ro`). Penulisan hanya registrasi
  kunjungan di atas; tidak pernah mengubah data klinis.
* User database **baca-saja** khusus, contoh (jalankan oleh DBA, sesuaikan nama tabel setelah
  diverifikasi):
  ```sql
  CREATE USER 'face_ro'@'127.0.0.1' IDENTIFIED BY '<password kuat>';
  GRANT SELECT ON sik.<tabel_pasien> TO 'face_ro'@'127.0.0.1';
  GRANT SELECT ON sik.<tabel_jadwal_kontrol> TO 'face_ro'@'127.0.0.1';
  ```
* Koneksi alias `khanza` memakai `SET SESSION TRANSACTION READ ONLY`; router menolak migrasi
  ke alias ini.
* Query parameterized (`%(nama)s`), hanya satu `SELECT`, tanpa `;`/komentar/kata kunci tulis,
  maksimal 20 baris.

## Langkah mengaktifkan

1. Verifikasi skema Khanza yang sebenarnya di server (jangan mengandalkan asumsi/versi lain):
   ```sql
   SHOW TABLES;
   SHOW CREATE TABLE <tabel_kandidat>;
   ```
   Catat tabel/kolom untuk: identitas pasien (No. RM, nama) dan jadwal/surat kontrol (No. RM,
   tanggal, poli, dokter).
2. Salin `docs/khanza_queries.example.json` ke lokasi aman (mis. `/etc/simrs-face/khanza_queries.json`),
   isi SQL dengan nama tabel/kolom yang **sudah diverifikasi**. Parameter yang tersedia:
   * `patient_search`: `%(q)s` (pola LIKE), `%(limit)s`
   * `patient_get`: `%(no_rkm_medis)s`
   * `control_schedule`: `%(no_rkm_medis)s`, `%(tanggal)s`
   `columns` memetakan nama field aplikasi → nama kolom hasil query.
3. Isi `.env`:
   ```dotenv
   KHANZA_ENABLED=true
   KHANZA_SCHEMA_VERIFIED=true
   KHANZA_QUERY_FILE=/etc/simrs-face/khanza_queries.json
   KHANZA_DB_NAME=...
   KHANZA_DB_USER=face_ro
   KHANZA_DB_PASSWORD=...
   KHANZA_DB_HOST=...
   ```
4. Uji di lingkungan staging; dokumentasikan query final di dokumen ini.

Jika `KHANZA_SCHEMA_VERIFIED` belum `true` atau query tidak valid, layanan melempar
`KhanzaNotConfigured`, dan status kontrol menjadi `NEEDS_MANUAL_CHECK` (pelayanan tetap berjalan).

## Query yang digunakan (yatofaasli, diverifikasi 2026-10-18)

Skema diperiksa via `information_schema` (semua kolom kunci `latin1_swedish_ci`, MariaDB 10.11).

| Kebutuhan | Tabel/kolom | Catatan |
|---|---|---|
| Identitas | `pasien.no_rkm_medis` (PK), `nm_pasien`, `jk`, `tgl_lahir` | 66.143 baris |
| Jadwal: booking | `booking_registrasi` (PK `no_rkm_medis,tanggal_periksa`) + `poliklinik.nm_poli` + `dokter.nm_dokter` | status `Terdaftar`/`Belum` |
| Jadwal: surat kontrol BPJS | `bridging_surat_kontrol_bpjs.tgl_rencana`, `nm_poli_bpjs`, `nm_dokter_bpjs` → `bridging_sep.no_sep` → `bridging_sep.nomr` | indeks `idx_nomr` |
| Jadwal: SKDP | `skdp_bpjs.no_rkm_medis`, `tanggal_datang` + `dokter.nm_dokter` | kecuali `Batal Periksa` |

SQL lengkap: `config/khanza_queries.json`. Hak `face_ro`: `SELECT` pada `pasien`, `booking_registrasi`,
`bridging_surat_kontrol_bpjs`, `bridging_sep`, `skdp_bpjs`, `poliklinik`, `dokter` (host `127.0.0.1`/`localhost`).

Hasil uji (data historis, karena data terbaru di salinan ini: booking 2026-06-08, surat kontrol
2024-11-08): ketiga sumber mengembalikan jadwal yang benar; query ±0–20 ms.

Mencabut akses bila perlu:
```sql
DROP USER 'face_ro'@'127.0.0.1', 'face_ro'@'localhost';
```