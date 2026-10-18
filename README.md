# SIMRS Face Recognition (Demo)

Aplikasi bantu identifikasi pasien berbasis pengenalan wajah real-time: Django + AI worker Python
(OpenCV, YuNet, SFace, ONNX Runtime CPU) + MySQL/MariaDB `customsik`.

> **Mode demo.** Tanpa Khanza: data pasien sintetis (`UJI-xxxx`). Dengan `KHANZA_ENABLED=true`: data pasien
> dibaca (baca-saja) dari Khanza — lihat `docs/KHANZA_INTEGRATION.md`. Tetap **tanpa
> liveness/anti-spoofing** (belum diterapkan; foto/layar dapat menipu sistem). Sistem hanya
> memberi *kandidat*; identitas selalu diputuskan petugas. Jangan menolak pelayanan karena
> sistem gagal mengenali wajah.

## Arsitektur

```
Kamera (webcam / RTSP)
   │ OpenCV (thread, antrian 2 frame, reconnect)
   ▼
AI worker  ── deteksi YuNet → tracking IoU → kualitas → bingkai terbaik → embedding SFace
   │  POST /api/face/v1/candidates/  (HMAC + nonce)       ▲ MJPEG / snapshot (127.0.0.1:8765, token)
   ▼                                                      │
Django ── pencocokan vs referensi terenkripsi ── dashboard (proxy stream, polling status)
   │
   ├─ customsik (MariaDB): face_patient_reference, face_recognition_event, face_audit_log
   └─ SQLite lokal (var/): auth, session milik aplikasi ini
```

* **Kenapa auth di SQLite?** `customsik` sudah berisi tabel `auth_user`, `django_session`, dll.
  milik aplikasi SIMRS lain. Agar tidak mengubah/mencampur data aplikasi tersebut, tabel framework
  Django aplikasi ini disimpan di `var/django_local.sqlite3`. Di `customsik` hanya dibuat 3 tabel
  baru `face_*` (router: `config/routers.py`). Label app diberi prefix `face_` karena label
  `dashboard` sudah dipakai aplikasi lain di `customsik`.
* **Pencocokan di server Django**, bukan di worker: kunci enkripsi embedding hanya dipakai Django,
  dan ID kandidat tidak pernah dipercaya dari client. Nama/nomor RM hanya dikembalikan ke worker
  untuk label di overlay kamera, dan worker hanya menggambarnya bila token stream tersebut terikat
  izin `view_candidate_identity`; Recording/registrasi dan stream tanpa izin tetap tanpa identitas.
* **Akses kamera OpenCV vs browser.** OpenCV (worker) membuka perangkat langsung di server,
  berjalan terus tanpa browser, dan bisa memakai RTSP; browser hanya menerima video hasil proxy
  Django (URL/password RTSP tidak pernah sampai ke browser). `getUserMedia` di browser membuka
  kamera *di komputer pengguna* dan hanya aktif selama halaman terbuka — dipakai sebagai cadangan
  di halaman registrasi. Satu webcam umumnya tidak bisa dibuka dua aplikasi bersamaan, jadi saat
  worker berjalan gunakan mode "Kamera worker".
* **Stream**: MJPEG lewat proxy Django yang mewajibkan login (paling stabil, tanpa dependensi
  WebSocket); metadata (status, jumlah wajah, kandidat) via polling JSON tiap 2 detik.

## 1. Dependensi sistem (Ubuntu 24.04)

```bash
sudo apt install python3 python3-venv python3-dev build-essential pkg-config default-libmysqlclient-dev
```

Hanya paket sistem di atas yang membutuhkan `sudo`. **Paket Python tidak pernah dipasang ke Python
sistem** (tanpa `sudo pip`, tanpa `--break-system-packages`).

## 2. Virtual environment & instalasi

Cara cepat (membuat `.venv`, memasang paket, membuat `.env` dengan secret acak, mengunduh model):

```bash
cd simrs-face-recognition
scripts/setup_ubuntu.sh
```

Manual:

```bash
python3 -m venv .venv
source .venv/bin/activate
which python            # harus .../simrs-face-recognition/.venv/bin/python
python --version
python -m pip --version
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Semua perintah di README memakai `.venv/bin/python` secara eksplisit agar tidak pernah salah
interpreter.

## 3. Konfigurasi (`.env`)

Salin `.env.example` → `.env` (dilakukan otomatis oleh setup). Nilai penting:

| Variabel | Keterangan |
|---|---|
| `DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT` | koneksi `customsik` (password hanya di `.env`) |
| `FACE_EMBEDDING_KEYS` | kunci Fernet (dipisah koma). **Cadangkan secara aman** — tanpa kunci, embedding & foto tidak dapat didekripsi |
| `FACE_WORKER_SECRET` | secret HMAC worker ↔ Django |
| `CAMERA_SOURCE` | `0` (webcam) atau `rtsp://user:pass@ip:554/...` |
| `FACE_DJANGO_URL` | alamat Django untuk worker; port juga dipakai `run_demo.sh` (default `8020`) |
| `FACE_MATCH_THRESHOLD`, `FACE_REVIEW_THRESHOLD`, `FACE_MATCH_MARGIN` | ambang awal (lihat §Pencocokan) |
| `TIME_ZONE` | default dari `/etc/timezone` |

`.env` ber-permission 600 dan ada di `.gitignore`.

## 4. Pemeriksaan database & migrasi

```bash
scripts/db_check.sh            # koneksi, alias target, migrasi & tabel yang akan dibuat (baca-saja)
scripts/migrate_customsik.sh   # cek ulang → konfirmasi → migrasi
```

`face_check_db` menolak lanjut jika rencana migrasi menyentuh selain pembuatan tabel/indeks baru
`face_*`, jika tabel target sudah ada, atau jika ada migrasi app lain yang akan dicatat di
`customsik`. Migrasi `customsik` selalu ditargetkan ke app (`migrate --database=customsik face_events`),
sehingga hanya `face_registry.0001` dan `face_events.0001` yang diterapkan. Satu-satunya perubahan
pada tabel yang sudah ada adalah 2 baris pencatatan di `django_migrations` (mekanisme standar Django).
Tidak ada script yang menjalankan `DROP`, `TRUNCATE`, `FLUSH`, atau reset database.

## 5. Akun & peran

```bash
.venv/bin/python manage.py face_setup_roles
.venv/bin/python manage.py createsuperuser
```

| Grup | Hak |
|---|---|
| `face_operator` | dashboard, riwayat, lihat identitas kandidat, verifikasi, cari pasien |
| `face_admin` | operator + registrasi/nonaktifkan/hapus referensi + audit log |

Tambahkan user ke grup lewat `/admin/`. User tanpa grup tidak dapat melihat data pasien.

## 6. Model AI

```bash
scripts/download_models.sh     # verifikasi SHA-256
```

| Model | Fungsi | Lisensi | File |
|---|---|---|---|
| YuNet 2023mar (OpenCV Zoo) | deteksi wajah + 5 landmark | MIT | `models/face_detection_yunet_2023mar.onnx` |
| SFace 2021dec (OpenCV Zoo) | embedding 128-d | Apache-2.0 | `models/face_recognition_sface_2021dec.onnx` |

Detail & alasan pemilihan: `docs/MODELS.md`. Jika model tidak ada, worker tetap menampilkan video
dengan pesan "MODEL TIDAK TERSEDIA" dan **tidak** melakukan pencocokan; registrasi ditolak dengan
`MODEL_UNAVAILABLE`.

## 7. Uji kamera

```bash
scripts/check_camera.sh                 # webcam default
scripts/check_camera.sh --camera 1      # perangkat lain
```

Tidak ada gambar yang disimpan. Hentikan worker dulu (kamera hanya bisa dibuka satu proses).

## 8. Menjalankan

```bash
scripts/run_demo.sh            # Django + AI worker; Ctrl+C menghentikan keduanya
```

Atau terpisah (dua terminal):

```bash
.venv/bin/python manage.py runserver 127.0.0.1:8020
.venv/bin/python -m ai_worker.worker            # opsi: --camera 0, --max-seconds 30
```

Buka **http://127.0.0.1:8020/** lalu login.

## 9. Registrasi wajah uji

1. Login sebagai `face_admin` → menu **Registrasi**.
2. Isi nama & No. RM uji (`UJI-0001` … mode demo mewajibkan prefix `UJI-`), pilih dasar pemrosesan,
   centang konfirmasi persetujuan.
3. **Ambil Foto** (kamera worker atau kamera browser). Sistem memeriksa: satu wajah, ukuran ≥80 px,
   ketajaman, pencahayaan, kemiringan, arah hadap, skor deteksi. **Ambil Ulang** jika kurang.
4. **Simpan Referensi** → embedding dihitung, dienkripsi, foto (crop wajah) disimpan terenkripsi
   dengan nama acak di `media_private/`, file sementara dihapus.
5. Menu **Referensi**: lihat status, nonaktifkan, atau hapus permanen (file ikut terhapus).

## 10. Pencocokan

Berdiri di depan kamera. Worker hanya mengirim wajah berkualitas `GOOD`, maksimal 1 kali/2 detik
per track (maks. 5 kali). Status:

| Status | Aturan (nilai awal) |
|---|---|
| `CANDIDATE` | skor ≥ 0.50 **dan** selisih dengan pasien lain ≥ 0.08 |
| `NEEDS_REVIEW` | skor 0.363–0.50, atau dua pasien berskor berdekatan |
| `NO_MATCH` | skor < 0.363 / tidak ada referensi aktif |
| `ERROR` | versi model/dimensi embedding tidak sesuai |

Skor = cosine similarity, **bukan persentase kepastian**. 0.363 adalah ambang yang dilaporkan
OpenCV untuk SFace pada LFW; ambang ini **wajib divalidasi** dengan peserta uji lokal (pencahayaan,
jarak 3–5 m, kamera produksi) sebelum dipakai. Petugas memverifikasi lewat tombol **Verifikasi**
(konfirmasi, tolak, atau pilih pasien manual). Status kontrol hanya diperiksa setelah identitas
diverifikasi.

## 11. Pengujian

```bash
scripts/test_all.sh
```

Memakai SQLite in-memory (`config/settings_test.py`): tidak butuh kamera, MySQL, maupun Khanza,
dan tidak membuat/menghapus database di MariaDB. Tidak ada foto wajah sungguhan di repository —
uji memakai gambar noise dan vektor sintetis.

## 12. Pemeliharaan data

```bash
.venv/bin/python manage.py face_purge_expired [--dry-run]   # retensi (jadwalkan via cron harian)
.venv/bin/python manage.py face_rotate_keys                 # setelah menambah kunci baru di depan FACE_EMBEDDING_KEYS
```

## Troubleshooting Ubuntu

| Gejala | Solusi |
|---|---|
| `Port 8020 sudah dipakai` | ubah `FACE_DJANGO_URL` di `.env` ke port lain |
| Kamera `UNAVAILABLE` | `scripts/check_camera.sh`; pastikan tidak dipakai aplikasi lain (`fuser /dev/video0`); tambahkan user ke grup `video` lalu login ulang |
| Kamera browser gagal `NotReadableError` | worker sedang memakai kamera → pakai mode "Kamera worker" |
| Dashboard "Worker tidak berjalan" | jalankan `.venv/bin/python -m ai_worker.worker`, cek `FACE_WORKER_SECRET` sama di Django & worker |
| `SERVICE_AUTH_EXPIRED` | jam sistem worker & Django berbeda >30 detik (aktifkan NTP) |
| `mysqlclient` gagal build | `sudo apt install python3-dev default-libmysqlclient-dev build-essential pkg-config` |
| `ImproperlyConfigured ... wajib diisi` | lengkapi `.env` (lihat `.env.example`) |
| Peringatan ONNX Runtime "Initializer ... appears in graph inputs" | tidak berbahaya (format ekspor model lama) |

## Produksi (Docker) & kamera USB di PC loket Windows

Ringkas: aplikasi di Docker server A (`web` + `vision` + `nginx` TLS), database di server B, PC loket
Windows memakai **kamera USB lewat browser** (Edge/Chrome, tanpa install) — frame dikirim ke server via
HTTPS. Langkah lengkap: **`docs/DEPLOY_PRODUCTION.md`**.

```bash
cp .env.production.example .env.production   # isi
deploy/make_certs.sh --ip <IP-server-A>
docker compose --env-file .env.production up -d --build
```

Server yang sudah memakai **Nginx Proxy Manager** (port 80/443): cukup jalankan
`deploy/install_server.sh`. Skrip ini interaktif, aman dijalankan ulang, dan tidak memublikasikan port.

Mode demo lokal (`scripts/run_demo.sh`) juga mendukung kamera USB browser saat dibuka di
`http://127.0.0.1:8020` (localhost dianggap aman oleh browser).

## Dokumen lain

* `docs/MODELS.md` — pemilihan model, lisensi, performa terukur
* `docs/PRIVACY_SECURITY.md` — dasar pemrosesan, pemberitahuan/persetujuan, DPIA ringkas, retensi, cadangan, uji keamanan
* `docs/KHANZA_INTEGRATION.md` — integrasi Khanza baca-saja (`yatofaasli`), query yang dipakai, `face_khanza_check`
