# Langkah Deploy di Server (mulai dari clone)

Panduan ini untuk server aplikasi **Docker** yang sudah memakai **Nginx Proxy Manager (NPM)** di port
80/443 dan menjalankan aplikasi lain, dengan database `customsik` + database **SIMRS Khanza** di
**server database terpisah** dalam satu LAN, memakai akun MySQL **`sik`** yang sudah ada.

## 0. Gambaran

```
                     internet / LAN RS
                            │
        https://DOMAIN (443)│                 https://IP_APP:8020 (LAN, opsional)
                            ▼                                  │
                 ┌──────────────────────┐                      │
                 │ Nginx Proxy Manager  │ Let's Encrypt        │ CA internal
                 └──────────┬───────────┘                      │
                            │ http://facerec_nginx:80          │
                            ▼                                  ▼
                 ┌──────────────────────────────────────────────────┐
  Server aplikasi│ facerec_nginx ─┬─► facerec_web    (halaman, API) │
   (IP_APP)      │                └─► facerec_vision (kamera USB AI)│
                 └───────────────────────────┬──────────────────────┘
                                             │ 3306
                                             ▼
                      Server database (IP_DB): customsik (tabel face_*), sik (Khanza)

PC loket Windows: Edge/Chrome + kamera USB → frame JPEG ±4 fps ke server (tanpa install software)
```

* Kamera USB dibaca **browser** di PC loket; browser hanya mengizinkan kamera di **HTTPS**.
* Dua pintu akses (boleh salah satu atau keduanya):
  1. **Domain publik** lewat NPM + sertifikat **Let's Encrypt** → PC tidak perlu CA tambahan.
  2. **IP LAN** `https://IP_APP:8020` dengan sertifikat **CA internal** → CA dipasang sekali per PC.

Ganti contoh berikut dengan nilai Anda:

| Contoh | Arti |
|---|---|
| `IP_APP` | IP LAN server aplikasi (mis. `192.168.1.176`) |
| `IP_DB` | IP server database |
| `DOMAIN` | domain publik aplikasi (mis. `face.rs-anda.co.id`) |
| `USER` | user SSH di server aplikasi |

---

## 1. Server database (sekali)

Di MySQL/MariaDB server database:

```sql
SELECT user, host FROM mysql.user WHERE user = 'sik';   -- host '%' atau IP_APP = OK
SHOW DATABASES;                                          -- catat nama database Khanza (umumnya: sik)
```

* Database **`customsik`** harus sudah ada (aplikasi hanya menambah tabel `face_*` ke dalamnya).
  Bila belum ada: `CREATE DATABASE customsik CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;`
* Izinkan server aplikasi ke port 3306 (di server database):
  ```bash
  sudo ufw allow from IP_APP to any port 3306 proto tcp
  ```
* MariaDB harus mendengarkan di LAN (`bind-address` bukan `127.0.0.1`).

## 2. Prasyarat server aplikasi

```bash
docker --version && docker compose version     # Docker + Compose v2
docker ps                                       # tanpa sudo (user di grup docker)
python3 --version && openssl version && git --version
docker ps --format '{{.Names}} {{.Image}}' | grep nginx-proxy-manager   # NPM berjalan
```

Opsional, uji koneksi database dari server aplikasi: `mysql -h IP_DB -u sik -p sik -e "SELECT COUNT(*) FROM pasien"`.

## 3. Clone kode

```bash
sudo mkdir -p /srv/simrs-face-recognition
sudo chown "$USER:$USER" /srv/simrs-face-recognition
git clone https://github.com/krisnapranata/facerecognition-khanza.git /srv/simrs-face-recognition
cd /srv/simrs-face-recognition
```

## 4. Model AI (tidak ada di GitHub)

```bash
# A. Server punya internet
scripts/download_models.sh

# B. Tanpa internet — jalankan DI LAPTOP yang punya model:
scp ~/iDRG/face-rcognation/simrs-face-recognition/models/*.onnx USER@IP_APP:/srv/simrs-face-recognition/models/
```

Hasil benar: `OK face_detection_yunet_2023mar.onnx` dan `OK face_recognition_sface_2021dec.onnx`.

## 5. Jalankan pemasang

```bash
cd /srv/simrs-face-recognition
deploy/install_server.sh
```

| Pertanyaan | Isi |
|---|---|
| Nama domain aplikasi (dibuka PC loket) | `DOMAIN` |
| IP server ini (server A) | `IP_APP` (biasanya sudah terisi) |
| IP/host server database | `IP_DB` |
| Port database | Enter (`3306`) |
| Nama database SIMRS Khanza | Enter (`sik`) — sesuaikan bila berbeda |
| User database customsik | `sik` |
| Password database customsik | password `sik` (tidak tampil saat diketik) |
| User untuk membaca data Khanza | **Enter** (pakai `sik`, password dipakai ulang) |
| User registrasi kunjungan Khanza | **Enter** (`sik`) |
| FACE_EMBEDDING_KEYS lama | Enter = buat baru; atau tempel kunci lama bila memindahkan data wajah |

Yang dilakukan skrip (aman diulang — `.env.production`, sertifikat, dan data tidak ditimpa):

1. cek model → deteksi NPM & jaringannya (bukan `bridge`) → cek bentrok nama container;
2. buat `.env.production` (secret otomatis, chmod 600; `KHANZA_ALLOW_PRIVILEGED_USER=true` karena memakai `sik`);
3. siapkan `data/` (milik uid 1000; `data/static` 755) → build image;
4. **uji koneksi database dari dalam container** (`face_check_db`, `face_khanza_check`);
5. buat sertifikat CA internal → jalankan `facerec_web`, `facerec_vision`, `facerec_nginx` **tanpa port publik**;
6. uji dari jaringan NPM → cetak ringkasan (langkah NPM/Let's Encrypt untuk domain publik).

Bila berhenti dengan error, perbaiki sesuai pesan lalu jalankan skrip lagi. Bila aplikasi tidak
merespons, skrip mencetak diagnostik otomatis.

> **Cadangkan `FACE_EMBEDDING_KEYS`** dari `.env.production` di tempat aman, terpisah dari backup
> server. Tanpa kunci ini, data wajah tidak bisa dibuka lagi.

## 6. Alias perintah

Tambahkan ke `~/.bashrc` (pilih sesuai pintu akses yang aktif):

```bash
cd /srv/simrs-face-recognition
# hanya domain publik (NPM)
alias fr='docker compose -f docker-compose.yml -f docker-compose.npm.yml --env-file .env.production'
# domain publik + akses LAN IP:8020 (setelah langkah 9) — WAJIB pakai yang ini bila LAN aktif
alias fr='docker compose -f docker-compose.yml -f docker-compose.npm.yml -f docker-compose.lan.yml --env-file .env.production'
```

## 7. Database & akun

```bash
fr run --rm web manage face_check_db
```

* **"tidak ada; semua migrasi face_* sudah diterapkan"** → lewati migrasi.
* Ada daftar **"Migrasi yang akan diterapkan"** dan **"Pemeriksaan lolos"** → jalankan:
  ```bash
  fr run --rm web manage migrate --database=customsik face_events
  ```

```bash
fr run --rm web manage face_khanza_check     # harus berakhir "Integrasi Khanza siap (baca-saja)"
fr run --rm web manage createsuperuser       # akun admin lokal, mis. admin_face
```

## 8. Domain publik lewat NPM + Let's Encrypt

1. **DNS publik**: A record `DOMAIN` → **IP publik RS**. Cek: `dig +short DOMAIN @1.1.1.1` harus sama
   dengan `curl -s https://api.ipify.org` dari server.
2. **Router/firewall RS**: port **80 dan 443** diteruskan ke `IP_APP` (port 80 wajib untuk verifikasi
   Let's Encrypt; biasanya sudah ada bila NPM melayani aplikasi lain).
3. NPM (`http://IP_APP:81`) → **SSL Certificates → Add SSL Certificate → Let's Encrypt**: Domain
   `DOMAIN` (ketik persis sama dengan DNS), email, I Agree → Save.
4. **Hosts → Proxy Hosts → Add Proxy Host** (bila domain sudah punya Proxy Host, **edit** yang ada):
   * *Details*: Domain `DOMAIN`, Scheme `http`, Forward Hostname `facerec_nginx`, Port `80`,
     **Block Common Exploits** ON
   * *SSL*: sertifikat Let's Encrypt `DOMAIN`, **Force SSL**, **HTTP/2**, **HSTS** ON
   * *Advanced* (tempel tanpa tanda ```):
     ```
     client_max_body_size 8m;
     proxy_buffering off;
     proxy_read_timeout 120s;
     ```
5. **Access List (sangat disarankan)** — aplikasi memuat data biometrik & data pasien:
   **Access Lists → Add**: *Allow* `192.168.0.0/16` (LAN RS) + IP publik cabang yang perlu, *Deny* sisanya
   → pilih di Proxy Host.
6. **Split-DNS** (disarankan): di DNS internal/router arahkan `DOMAIN → IP_APP`, agar frame kamera dari
   PC loket tetap lewat LAN.
7. Pastikan `.env.production` memakai domain yang **sama persis**:
   ```bash
   grep -E '^(CSRF_TRUSTED_ORIGINS|DJANGO_ALLOWED_HOSTS)=' .env.production
   # CSRF_TRUSTED_ORIGINS=https://DOMAIN   dan   DJANGO_ALLOWED_HOSTS memuat DOMAIN dan facerec_web
   ```
   Bila diubah: `fr up -d --force-recreate web vision`.

Uji: buka `https://DOMAIN` → halaman **Masuk** bertema kaca gelap, tanpa peringatan sertifikat.

> Domain internal tanpa internet (mis. `facerec.rs.local`): pakai **Custom certificate** di NPM
> (`deploy/certs/server.crt` + `server.key`) dan PC loket memasang CA internal (langkah 11 tanpa `-NoCa`).

## 9. Akses LAN lewat IP (opsional): `https://IP_APP:8020`

```bash
cd /srv/simrs-face-recognition
deploy/enable_lan_https.sh IP_APP 8020
```

Skrip (aman diulang): tambah IP & `https://IP_APP:8020` ke `.env.production`, pastikan sertifikat CA
internal memuat IP (dibuat ulang bila perlu, CA lama tetap), buka port 8020 di `facerec_nginx` (NPM tidak
terganggu), uji → `200 OK`. `http://IP_APP:8020` otomatis dialihkan ke `https://`.

* Setelah ini gunakan alias `fr` versi **dengan** `docker-compose.lan.yml` (langkah 6).
* Firewall bila ufw aktif: `sudo ufw allow from 192.168.0.0/16 to any port 8020 proto tcp`

## 10. Hak akses pengguna

Login sebagai admin lokal → `https://DOMAIN/admin/` → Users.

| Siapa | Cara login | Hak |
|---|---|---|
| Petugas loket | **akun SIMRS Khanza** (ID user & password) dengan hak *Registrasi* | otomatis: dashboard, lihat nama kandidat, verifikasi, cari pasien, daftarkan ke poli |
| Pendaftar wajah pasien | akun Khanza (login sekali) → di `/admin/` user `kz.<id_user>` tambahkan grup **`face_admin`** | + registrasi/hapus referensi wajah, audit log |
| Admin lokal | akun `createsuperuser` → tambahkan grup **`face_admin`** | semua |

## 11. Setup setiap PC loket Windows (sekali per PC)

1. Salin dari server ke PC, mis. `C:\simrs\`:
   * domain publik: cukup `deploy/windows/setup-loket.ps1`
   * akses LAN IP: folder `deploy/windows/out/` (berisi `simrs-ca.crt` + `setup-loket.ps1`)
   ```bash
   scp -r USER@IP_APP:/srv/simrs-face-recognition/deploy/windows/out ./simrs
   ```
2. **PowerShell sebagai Administrator**:
   ```powershell
   cd C:\simrs
   # domain publik (Let's Encrypt):
   powershell -ExecutionPolicy Bypass -File .\setup-loket.ps1 -ServerUrl https://DOMAIN -NoCa -Station loket-1
   # akses LAN lewat IP (CA internal):
   powershell -ExecutionPolicy Bypass -File .\setup-loket.ps1 -ServerUrl https://IP_APP:8020 -Station loket-1
   ```
   | Parameter | Fungsi |
   |---|---|
   | `-Station loket-1` | nama stasiun, unik per PC (`loket-2`, `loket-3`, …) |
   | `-NoCa` | lewati pemasangan CA internal (khusus domain publik) |
   | `-HostsIp IP_APP` | tulis file hosts bila domain belum ada di DNS |
   | `-Kiosk` | shortcut mode layar penuh |

   Skrip memasang CA (bila perlu), mengizinkan kamera untuk alamat server di Edge & Chrome tanpa prompt,
   dan membuat shortcut **SIMRS Identifikasi Wajah**. Bisa dijalankan dua kali untuk dua alamat.
3. Tutup **semua** jendela browser → buka shortcut → login → **Kamera USB di PC ini** → pilih kamera → **Mulai**.
4. Dashboard: **"Kamera USB: aktif"** dan **"Server AI: memproses … ms/frame"**.

## 12. Uji alur lengkap

1. **Registrasi wajah** (akun `face_admin`) → menu **Registrasi** → **Kamera USB di PC ini** → isi
   No. RM → **Cek di Khanza** (pastikan nama benar) → dasar **Persetujuan eksplisit pasien** → centang
   persetujuan → Ambil Foto → Simpan.
2. **Pengenalan**: berdiri di depan kamera loket → kotak hijau berlabel **`NAMA (No. RM)`**
   (kuning + `?` = perlu review). Label nama hanya tampil untuk akun berizin melihat identitas.
3. **Verifikasi** di kartu kandidat → Konfirmasi.
4. **Daftarkan ke Poli** → pilih poli & dokter (dari tabel `jadwal`) → **Daftarkan** → No. Reg & No. Rawat
   tampil; cek di Khanza desktop.
   * `KHANZA_JADWAL_MODE=hari_ini` (default): hanya dokter yang praktik **hari ini**. Untuk uji di hari
     tanpa jadwal: `sed -i 's/^KHANZA_JADWAL_MODE=.*/KHANZA_JADWAL_MODE=semua/' .env.production &&
     fr up -d --force-recreate web vision` lalu kembalikan ke `hari_ini`.
5. Batalkan kunjungan uji dari Khanza desktop (hapus/ubah status Batal).

## 13. Operasional

| Tugas | Perintah |
|---|---|
| Status | `fr ps` |
| Log | `fr logs -f web vision nginx` (Ctrl+C keluar) |
| Diagnostik lengkap (baca-saja, tanpa secret) | `deploy/diagnose.sh DOMAIN` |
| Restart | `fr restart web vision nginx` |
| Update aplikasi | `git pull && fr up -d --build && fr up -d --force-recreate --no-deps nginx` lalu `fr run --rm web manage face_check_db` |
| Retensi data wajah (cron harian) | `crontab -e` → `0 2 * * * cd /srv/simrs-face-recognition && docker compose -f docker-compose.yml -f docker-compose.npm.yml -f docker-compose.lan.yml --env-file .env.production run --rm web manage face_purge_expired` |
| Backup | `data/` (SQLite user/sesi + `media_private` terenkripsi), `.env.production`, `deploy/certs/ca.key` — simpan aman, terpisah |
| Hentikan aplikasi | `fr down` (data tetap di `data/`) |

> Setelah `git pull`, nginx **wajib** dibuat ulang (`--force-recreate --no-deps nginx`): nginx membaca
> template konfigurasi hanya saat start.

## 14. Masalah umum

| Gejala | Penyebab / solusi |
|---|---|
| `model AI belum ada/checksum salah` | Langkah 4 |
| `container Nginx Proxy Manager tidak ditemukan` | NPM belum jalan, atau pakai mode port sendiri (`docs/DEPLOY_PRODUCTION.md` bagian 4) |
| NPM hanya di jaringan `bridge` | `docker network create npm_proxy && docker network connect npm_proxy proxy-npm-1`, ulangi skrip |
| Gagal "Menguji koneksi ke database" | Langkah 1: host `sik`, firewall 3306, `bind-address`, password |
| `Unknown database 'yatofaasli'` / nama Khanza salah | `sed -i 's/^KHANZA_DB_NAME=.*/KHANZA_DB_NAME=sik/' .env.production`, ulangi skrip |
| `face_khanza_check`: hak tulis/administratif | `KHANZA_ALLOW_PRIVILEGED_USER=true` di `.env.production` saat memakai `sik` |
| 502, log nginx `upstream ... 172.x.x.x:8000` refused | nginx masih konfigurasi lama: `docker restart facerec_nginx` |
| Halaman tampil polos tanpa CSS | `sudo chmod -R a+rX data/static && docker restart facerec_nginx` |
| NPM: **Internal Error** saat simpan | Let's Encrypt gagal: domain salah ketik / DNS belum ke IP publik / port 80 tertutup. Cek di https://letsdebug.net; simpan dulu dengan SSL *None* |
| NPM: 502 Bad Gateway | Forward Hostname harus `facerec_nginx`, port `80`; cek `fr ps` |
| 400 Bad Request | Domain/IP belum ada di `DJANGO_ALLOWED_HOSTS` → tambahkan, `fr up -d --force-recreate web vision` |
| Login 403 (CSRF) | Origin persis (`https://DOMAIN` atau `https://IP_APP:8020`) belum ada di `CSRF_TRUSTED_ORIGINS` |
| Peringatan sertifikat (akses IP) | CA internal belum terpasang: `setup-loket.ps1` tanpa `-NoCa` sebagai Administrator, tutup semua jendela browser |
| "Kamera USB butuh HTTPS" | Buka lewat `https://`, bukan `http://` |
| Kamera tidak bisa dibuka (`NotReadableError`) | Tutup Zoom/Teams/Camera atau tab lain yang memakai kamera |
| Label masih `T.. CANDIDATE` (bukan nama) | Akun tanpa izin lihat identitas, atau JS lama: Ctrl+Shift+R, Berhenti → Mulai, keluar-masuk jangkauan kamera |
| Tidak ada dokter bisa dipilih | Hari ini tidak ada jadwal di tabel `jadwal` (mode `hari_ini`) — lihat langkah 12.4 |

Bila masih bermasalah, kirim output `deploy/diagnose.sh DOMAIN`.

## 15. File penting

| File | Isi |
|---|---|
| `.env.production` | konfigurasi & secret produksi (chmod 600, jangan dibagikan) |
| `data/` | SQLite lokal, cache, foto referensi terenkripsi, static |
| `deploy/certs/` | CA internal & sertifikat server (rahasiakan `ca.key`) |
| `deploy/windows/out/` | paket PC loket (CA + `setup-loket.ps1`) |
| `deploy/install_server.sh` | pemasang server |
| `deploy/enable_lan_https.sh` | akses LAN lewat IP |
| `deploy/diagnose.sh` | diagnostik |

Latar belakang arsitektur & opsi lain: `docs/DEPLOY_PRODUCTION.md`.
