# Deploy Produksi: Docker (server A) + Database (server B) + PC loket Windows dengan kamera USB

## 1. Kenapa begini?

Kamera USB yang tercolok di **PC Windows** tidak bisa dibaca oleh container Docker di **server A**
(USB hanya terlihat oleh komputer tempat ia tercolok). Solusinya:

```
PC loket Windows (Chrome/Edge + kamera USB)          Server A (Docker)                       Server B
┌──────────────────────────────┐   HTTPS :8443   ┌───────────────────────────────┐  3306  ┌──────────────┐
│ browser: getUserMedia()      │ ─────────────►  │ nginx (TLS, static)           │        │ MariaDB      │
│  • baca kamera USB           │  frame JPEG     │  ├─ web    : halaman, API,    │ ─────► │  customsik   │
│  • kirim ±4 fps, 640px, ~35KB│  ~1 Mbps/loket  │  │          registrasi, verif. │        │  (face_*)    │
│  • gambar kotak wajah        │ ◄─────────────  │  └─ vision : deteksi, tracking,│ ─────► │  yatofaasli  │
│ (tanpa install software)     │  JSON (bbox)    │             embedding, cocok   │        │  (Khanza)    │
└──────────────────────────────┘                 │  worker (opsional): RTSP IPcam │        └──────────────┘
                                                 └───────────────────────────────┘
```

* **Tidak ada software yang dipasang di PC loket** — cukup Edge/Chrome. Frame tidak disimpan di server.
* **HTTPS wajib**: browser hanya mengizinkan akses kamera di HTTPS (atau `localhost`). Dipakai **CA
  internal** rumah sakit yang dibuat offline; CA dipasang sekali di tiap PC loket.
* Container **vision** = satu proses (banyak thread) yang memegang model AI & tracker per loket;
  nginx meneruskan `/api/face/v1/stations/` ke sana. Container **web** melayani sisanya.
* IP camera RTSP di pintu masuk tetap didukung lewat container **worker** (profile `rtsp`).

Terukur (uji lokal, i7-8665U): ±3,8 fps per loket, latensi rata-rata ±53 ms, inferensi ±23 ms/frame.
Perkiraan kapasitas: ±1 loket aktif per 0,15–0,25 core CPU (tergantung jumlah wajah); atur `VISION_CPUS`.

## 2. Prasyarat

| Komponen | Kebutuhan |
|---|---|
| Server A | Docker Engine + Compose v2, CPU ≥ 4 core, RAM ≥ 4 GB, IP tetap (mis. `192.168.1.20`) |
| Server B | MariaDB/MySQL dengan `customsik` & `yatofaasli`; port 3306 terbuka **hanya** untuk IP server A |
| PC loket | Windows 10/11, Edge atau Chrome terbaru, kamera USB (UVC) |
| Jaringan | Satu LAN; PC loket → server A port 8443 |

## 3. Server B (database) — dilakukan DBA

1. MariaDB mendengarkan di LAN (`bind-address = 0.0.0.0` atau IP LAN) dan firewall hanya mengizinkan
   server A:
   ```bash
   sudo ufw allow from 192.168.1.20 to any port 3306 proto tcp
   ```
2. User baca-saja Khanza untuk **host server A** (user `face_ro@localhost` yang dibuat saat demo tidak
   berlaku dari server lain):
   ```sql
   CREATE USER 'face_ro'@'192.168.1.20' IDENTIFIED BY '<password-kuat>';
   GRANT SELECT ON yatofaasli.pasien TO 'face_ro'@'192.168.1.20';
   GRANT SELECT ON yatofaasli.booking_registrasi TO 'face_ro'@'192.168.1.20';
   GRANT SELECT ON yatofaasli.bridging_surat_kontrol_bpjs TO 'face_ro'@'192.168.1.20';
   GRANT SELECT ON yatofaasli.bridging_sep TO 'face_ro'@'192.168.1.20';
   GRANT SELECT ON yatofaasli.skdp_bpjs TO 'face_ro'@'192.168.1.20';
   GRANT SELECT ON yatofaasli.poliklinik TO 'face_ro'@'192.168.1.20';
   GRANT SELECT ON yatofaasli.dokter TO 'face_ro'@'192.168.1.20';
   ```
3. Akun untuk `customsik` (`DB_USER`) dan akun tulis Khanza (`KHANZA_RW_DB_USER`, saat ini `sik`
   sesuai keputusan operator) harus boleh login dari IP server A. Disarankan user khusus berhak
   terbatas (lihat docs/KHANZA_INTEGRATION.md).

Uji dari server A: `mysql -h <IP-DB-server-B> -u face_ro -p yatofaasli -e "SELECT COUNT(*) FROM pasien"`.

## 4. Server A (aplikasi)

```bash
# 1) Salin folder proyek (tanpa .venv, var/, media_private/) ke server A, mis. /opt/simrs-face-recognition
cd /opt/simrs-face-recognition

# 2) Model AI (sekali; ikut dibangun ke image). Server tanpa internet: salin folder models/ dari mesin lain.
scripts/download_models.sh          # atau: pastikan models/*.onnx sudah ada (checksum di docs/MODELS.md)

# 3) Konfigurasi
cp .env.production.example .env.production && chmod 600 .env.production
nano .env.production                # isi IP, password DB, secret; FACE_DEFAULT_CAMERA=usb

# 4) Sertifikat HTTPS (CA internal; offline)
deploy/make_certs.sh --ip 192.168.1.20 --dns facerec.rs.local

# 5) Folder data milik uid 1000 (user di container)
mkdir -p data/var data/media_private data/static && sudo chown -R 1000:1000 data

# 6) Build & jalankan
docker compose --env-file .env.production up -d --build
docker compose --env-file .env.production ps
```

**Kunci biometrik**: bila memindahkan data dari server demo, salin `FACE_EMBEDDING_KEYS` yang sama dan
folder `media_private/` → `data/media_private/`. Tanpa kunci yang sama, embedding & foto lama tidak dapat
didekripsi. Simpan cadangan kunci terpisah dari cadangan data.

### Cara cepat (server dengan NPM): `deploy/install_server.sh`

```bash
# di laptop: salin proyek ke server (models/ ikut; .venv, data, .env tidak)
rsync -av --exclude-from=.dockerignore ./ user@<IP-server>:/srv/simrs-face-recognition/
# di server
cd /srv/simrs-face-recognition && deploy/install_server.sh
```

Skrip ini menjalankan langkah di bawah secara otomatis:
1. Memeriksa prasyarat dan model.
2. Mendeteksi container dan jaringan NPM.
3. Mengecek bentrok nama container.
4. Membuat `.env.production` (secret dibuat otomatis, password tidak ditampilkan).
5. Menguji koneksi ke server B **dari dalam container**. Kalau gagal, skrip berhenti dan mencetak SQL GRANT dan perintah firewall untuk DBA.
6. Membuat sertifikat dan menjalankan container tanpa port publik.
7. Mencetak isian form NPM.

Skrip aman dijalankan ulang: `.env.production`, sertifikat, dan data tidak ditimpa. Skrip tidak menyentuh
container lain, tidak mengubah NPM, dan tidak menjalankan migrasi `customsik`.

Periksa port yang sudah terpakai dengan `docker ps` / `ss -ltn`. Dalam mode NPM aplikasi ini **tidak
memublikasikan port apa pun**, sehingga tidak bentrok dengan aplikasi lain di server.

### 4-NPM. Server yang sudah memakai Nginx Proxy Manager (server bersama dengan banyak aplikasi)

Bila port 80/443 sudah dipegang **Nginx Proxy Manager** (`jc21/nginx-proxy-manager`, container
`proxy-npm-1`) dan banyak aplikasi lain berjalan di server yang sama, jangan
publikasikan port sendiri — taruh aplikasi wajah **di belakang NPM**:

```
PC loket ──HTTPS 443──► NPM (proxy-npm-1, sertifikat) ──HTTP──► facerec_nginx:80 ──► web / vision
                                     jaringan Docker NPM (NPM_NETWORK)
```

1. Cari nama jaringan NPM dan isi `NPM_NETWORK` di `.env.production`:
   ```bash
   docker inspect proxy-npm-1 --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
   ```
2. Tentukan nama domain internal, mis. `facerec.rs.local`, lalu di `.env.production`:
   ```dotenv
   DJANGO_ALLOWED_HOSTS=facerec.rs.local,web,localhost
   CSRF_TRUSTED_ORIGINS=https://facerec.rs.local
   BEHIND_TLS_PROXY=true
   ```
3. Sertifikat dari CA internal untuk domain tsb. (CA yang sama dipasang di PC loket):
   ```bash
   deploy/make_certs.sh --dns facerec.rs.local --ip <IP-server>
   ```
4. Jalankan dengan override NPM (tanpa port yang dipublikasikan):
   ```bash
   cd /srv/simrs-face-recognition
   docker compose -f docker-compose.yml -f docker-compose.npm.yml --env-file .env.production up -d --build
   ```
5. Di UI NPM (`http://<server>:81`):
   * **SSL Certificates → Add SSL Certificate → Custom**: unggah `deploy/certs/server.key` (Certificate Key)
     dan `deploy/certs/server.crt` (Certificate). (Let's Encrypt tidak bisa untuk domain `.local`.)
   * **Hosts → Proxy Hosts → Add Proxy Host**
     * Domain Names: `facerec.rs.local`
     * Scheme `http`, Forward Hostname `facerec_nginx`, Forward Port `80`
     * Block Common Exploits: aktif; Websockets: tidak perlu
     * Tab **SSL**: pilih sertifikat custom tadi, aktifkan **Force SSL** dan **HTTP/2**
     * Tab **Advanced** → Custom Nginx Configuration:
       ```nginx
       client_max_body_size 8m;
       proxy_buffering off;
       proxy_read_timeout 120s;
       ```
6. DNS: tambahkan record `facerec.rs.local → <IP-server>` di DNS internal/router, **atau** biarkan
   `setup-loket.ps1 -HostsIp <IP-server>` menulis file hosts di tiap PC.
7. PC loket:
   ```powershell
   .\setup-loket.ps1 -ServerUrl https://facerec.rs.local -HostsIp <IP-server> -Station loket-1
   ```

Catatan server bersama: `VISION_CPUS` membatasi CPU container vision agar tidak berebut dengan layanan
berat lain (mis. layanan AI/LLM); `GUNICORN_WORKERS=2` sudah cukup untuk web.

Bila `yatofaasli`/`customsik` berada di container MySQL di server yang sama (mis. `casemixidrg-db-1`)
dan tidak dipublikasikan ke LAN, sambungkan juga ke jaringan compose casemix dan pakai nama service
sebagai host (`DB_HOST=db`, `KHANZA_DB_HOST=db`) — lihat `docker network ls`. Bila database di server B,
cukup isi IP server B.

### 4a. Migrasi database (manual, aman)

Container `web` hanya memigrasi SQLite lokalnya sendiri. `customsik` dimigrasi manual setelah diperiksa:

```bash
docker compose --env-file .env.production run --rm web manage face_check_db
docker compose --env-file .env.production run --rm web manage migrate --database=customsik face_events
docker compose --env-file .env.production run --rm web manage face_khanza_check
```

### 4b. Akun

Login memakai **akun Khanza** (tabel `user`, hak Registrasi) bila `KHANZA_AUTH_ENABLED=true`.
Akun administrator lokal (untuk mengelola referensi wajah & audit):

```bash
docker compose --env-file .env.production run --rm web manage createsuperuser
```

Tambahkan user ke grup `face_admin` lewat `https://<server>:8443/admin/`.

## 5. PC loket Windows (sekali per PC)

1. Salin folder `deploy/windows/out/` (berisi `simrs-ca.crt` dan `setup-loket.ps1`) ke PC, mis. `C:\simrs\`.
2. Buka **PowerShell sebagai Administrator**:
   ```powershell
   cd C:\simrs
   powershell -ExecutionPolicy Bypass -File .\setup-loket.ps1 -ServerUrl https://192.168.1.20:8443 -Station loket-1
   # mode kiosk layar penuh (opsional): tambahkan -Kiosk
   ```
   Skrip memasang CA internal, mengizinkan kamera untuk alamat server di Edge & Chrome (kebijakan
   `VideoCaptureAllowedUrls`, tanpa prompt izin), dan membuat shortcut **SIMRS Identifikasi Wajah**.
3. Colokkan kamera USB, buka shortcut, login, pilih **Kamera USB di PC ini**, isi **Nama stasiun**
   (`loket-1`, `loket-2`, … unik per PC), pilih kamera, klik **Mulai**. Pilihan diingat browser dan
   kamera otomatis aktif saat halaman dibuka lagi.

Untuk banyak PC sekaligus: distribusikan CA dan kebijakan lewat Group Policy (GPO) domain —
CA ke *Trusted Root Certification Authorities*, kebijakan Edge/Chrome *VideoCaptureAllowedUrls*.

### Tips kamera USB
* Pakai kamera UVC 720p/1080p; posisikan setinggi wajah, jarak 0,5–1,5 m, cahaya dari depan (bukan
  membelakangi jendela).
* Lebih dari satu kamera: pilih di dropdown **Perangkat kamera**.
* Kamera hanya bisa dipakai satu aplikasi: tutup Zoom/Teams/Camera atau tab lain yang memakai kamera.
* Registrasi wajah dari PC loket: halaman **Registrasi** → **Kamera USB di PC ini** (resolusi lebih tinggi).
  Hentikan dulu kamera di tab dashboard bila memakai kamera yang sama.

## 6. IP camera RTSP pintu masuk (opsional)

Isi `CAMERA_SOURCE=rtsp://user:pass@192.168.1.50:554/stream1` dan `CAMERA_ID` di `.env.production`, lalu:
```bash
docker compose --env-file .env.production --profile rtsp up -d
```
Di dashboard pilih **Kamera server / RTSP**. URL/password RTSP tidak pernah dikirim ke browser.

## 7. Operasional

| Tugas | Perintah |
|---|---|
| Log | `docker compose --env-file .env.production logs -f web vision nginx` |
| Update aplikasi | salin kode baru → `docker compose --env-file .env.production up -d --build` → jalankan `face_check_db` bila ada migrasi |
| Retensi biometrik (cron harian) | `docker compose --env-file .env.production run --rm web manage face_purge_expired` |
| Perbarui sertifikat (IP berubah / habis) | `deploy/make_certs.sh --ip …` → `docker compose restart nginx` (CA tetap, PC loket tidak perlu diubah) |
| Cadangan | `data/var/` (SQLite user/sesi), `data/media_private/` (terenkripsi), `.env.production` & `deploy/certs/ca.key` (simpan terpisah, aman) |

## 8. Troubleshooting

| Gejala | Penyebab / solusi |
|---|---|
| "Kamera USB butuh HTTPS" | Buka lewat `https://…:8443`, bukan `http://` |
| Peringatan sertifikat di browser | CA belum terpasang: jalankan `setup-loket.ps1` (Administrator), tutup semua jendela browser lalu buka lagi |
| `NotReadableError` | Kamera dipakai aplikasi/tab lain |
| `NotAllowedError` | Izin kamera ditolak — cek `edge://policy` → `VideoCaptureAllowedUrls` berisi alamat server persis (skema+host+port) |
| CSRF 403 saat login | Tambahkan origin persis (mis. `https://192.168.1.20:8443`) ke `CSRF_TRUSTED_ORIGINS`, restart `web` |
| 400 Bad Request | Tambahkan IP/nama server ke `DJANGO_ALLOWED_HOSTS` |
| Health: Khanza ERROR | Grant `face_ro@<IP server A>` di server B, firewall 3306 |
| Deteksi lambat / fps turun | Naikkan `VISION_CPUS`, turunkan `FACE_STATION_MAX_FPS` ke 3 |
| Kamera USB tidak terdeteksi Windows | Cek Device Manager → Cameras; coba port USB lain |
