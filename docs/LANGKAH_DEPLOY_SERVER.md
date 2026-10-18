# Langkah Deploy di Server (mulai dari clone)

Skenario: server aplikasi (Docker + **Nginx Proxy Manager** di port 80/443) dan database `customsik` &
`yatofaasli` di **server database lain** dalam satu LAN, memakai akun MySQL **`sik`** yang sudah ada.

Ganti contoh berikut dengan nilai Anda:

| Contoh | Arti |
|---|---|
| `IP_APP` | IP server aplikasi (tempat Docker berjalan) |
| `IP_DB` | IP server database |
| `facerec.rs.local` | nama domain yang dibuka PC loket |

---

## 0. Persiapan di server database (sekali)

Login ke MySQL/MariaDB server database, pastikan `sik` boleh login dari server aplikasi:

```sql
SELECT user, host FROM mysql.user WHERE user = 'sik';
```

Host `%` atau `IP_APP` = sudah cukup. Pastikan juga port 3306 terbuka untuk server aplikasi:

```bash
sudo ufw allow from IP_APP to any port 3306 proto tcp      # di server database
```

## 1. Cek prasyarat di server aplikasi

```bash
docker --version && docker compose version   # Compose v2
docker ps                                     # harus jalan tanpa sudo (user di grup docker)
python3 --version && openssl version && git --version
docker ps --format '{{.Names}} {{.Image}}' | grep nginx-proxy-manager   # NPM terdeteksi
```

Uji koneksi ke database dari server aplikasi (opsional, butuh klien mysql):

```bash
mysql -h IP_DB -u sik -p yatofaasli -e "SELECT COUNT(*) FROM pasien"
```

## 2. Clone kode

```bash
sudo mkdir -p /srv/simrs-face-recognition
sudo chown "$USER:$USER" /srv/simrs-face-recognition
git clone https://github.com/krisnapranata/facerecognition-khanza.git /srv/simrs-face-recognition
cd /srv/simrs-face-recognition
```

## 3. Model AI (tidak ada di GitHub)

Pilih salah satu:

```bash
# A. Server punya internet
scripts/download_models.sh

# B. Server tanpa internet — jalankan DI LAPTOP, lalu kembali ke server
scp ~/iDRG/face-rcognation/simrs-face-recognition/models/*.onnx USER@IP_APP:/srv/simrs-face-recognition/models/
```

Hasil yang benar: `OK face_detection_yunet_2023mar.onnx` dan `OK face_recognition_sface_2021dec.onnx`.

## 4. Jalankan pemasang

```bash
cd /srv/simrs-face-recognition
deploy/install_server.sh
```

Jawab pertanyaannya:

| Pertanyaan | Isi |
|---|---|
| Nama domain aplikasi | `facerec.rs.local` (atau nama pilihan Anda) |
| IP server ini | `IP_APP` (biasanya sudah terisi) |
| IP/host database server B | `IP_DB` |
| Port database | Enter (`3306`) |
| User database customsik | `sik` |
| Password database customsik | password `sik` (tidak tampil saat diketik) |
| User untuk membaca data Khanza | **Enter** (pakai `sik`, password dipakai ulang) |
| User registrasi kunjungan Khanza | **Enter** (`sik`) |
| FACE_EMBEDDING_KEYS lama | Enter = buat baru; atau tempel dari `.env` laptop bila data wajah ingin dipindah |

Yang dilakukan skrip: cek model → deteksi NPM → buat `.env.production` (secret otomatis, chmod 600) →
build image → **uji koneksi database dari dalam container** → buat sertifikat → jalankan
`facerec_web`, `facerec_vision`, `facerec_nginx` **tanpa membuka port baru** → cetak ringkasan.

Jika berhenti di "Menguji koneksi ke database": perbaiki sesuai pesan (langkah 0), lalu jalankan
`deploy/install_server.sh` lagi — aman diulang, `.env.production` dan sertifikat tidak ditimpa.

> **Penting:** cadangkan nilai `FACE_EMBEDDING_KEYS` dari `.env.production` di tempat aman (terpisah
> dari backup server). Tanpa kunci ini, referensi wajah tidak bisa dibuka lagi.

## 5. Database & akun

Buat alias supaya perintah pendek (tambahkan ke `~/.bashrc` bila perlu):

```bash
cd /srv/simrs-face-recognition
alias fr='docker compose -f docker-compose.yml -f docker-compose.npm.yml --env-file .env.production'
```

```bash
fr run --rm web manage face_check_db        # baca hasilnya: tabel face_* sudah ada / akan dibuat
```

* Jika tertulis **"tidak ada; semua migrasi face_* sudah diterapkan"** → lewati migrasi.
* Jika ada daftar **"Migrasi yang akan diterapkan"** dan pemeriksaan **lolos** → jalankan:

```bash
fr run --rm web manage migrate --database=customsik face_events
```

Akun administrator lokal (kelola referensi wajah & audit log):

```bash
fr run --rm web manage createsuperuser
```

Petugas loket cukup login dengan **akun SIMRS Khanza** masing-masing (harus punya hak *Registrasi*).

## 6. Nginx Proxy Manager

1. Ambil file sertifikat ke PC Anda (WinSCP, atau dari PC):
   ```bash
   scp USER@IP_APP:/srv/simrs-face-recognition/deploy/certs/server.crt .
   scp USER@IP_APP:/srv/simrs-face-recognition/deploy/certs/server.key .
   ```
2. Buka `http://IP_APP:81` → **SSL Certificates → Add SSL Certificate → Custom**
   * Name: `facerec`
   * Certificate Key: `server.key`
   * Certificate: `server.crt`
3. **Hosts → Proxy Hosts → Add Proxy Host**
   * Tab *Details*: Domain Names `facerec.rs.local`, Scheme `http`, Forward Hostname/IP `facerec_nginx`,
     Forward Port `80`, **Block Common Exploits** ON
   * Tab *SSL*: pilih sertifikat `facerec`, **Force SSL** ON, **HTTP/2 Support** ON
   * Tab *Advanced* → Custom Nginx Configuration:
     ```nginx
     client_max_body_size 8m;
     proxy_buffering off;
     proxy_read_timeout 120s;
     ```
   * Save

## 7. DNS

Pilih salah satu:

* Tambahkan record `facerec.rs.local → IP_APP` di DNS internal / router (semua PC otomatis), **atau**
* biarkan skrip PC loket menulis file `hosts` (parameter `-HostsIp`, langkah 8).

## 8. Setup setiap PC loket Windows (sekali per PC)

1. Salin folder `deploy/windows/out/` dari server ke PC, mis. `C:\simrs\`
   (isi: `simrs-ca.crt` dan `setup-loket.ps1`):
   ```bash
   scp -r USER@IP_APP:/srv/simrs-face-recognition/deploy/windows/out ./simrs
   ```
2. Buka **PowerShell sebagai Administrator**:
   ```powershell
   cd C:\simrs
   powershell -ExecutionPolicy Bypass -File .\setup-loket.ps1 -ServerUrl https://facerec.rs.local -HostsIp IP_APP -Station loket-1
   ```
   PC berikutnya: `-Station loket-2`, `loket-3`, … (unik per PC). Tambahkan `-Kiosk` untuk layar penuh.
3. Tutup **semua** jendela browser, buka shortcut **SIMRS Identifikasi Wajah** di desktop.
4. Login akun Khanza → pilih **Kamera USB di PC ini** → pilih kamera → **Mulai**.

## 9. Cek hasil

```bash
fr ps                                   # facerec_web, facerec_vision, facerec_nginx: Up
fr logs -f web vision nginx             # Ctrl+C untuk keluar
fr run --rm web manage face_khanza_check
```

Di PC loket: `https://facerec.rs.local` terbuka **tanpa peringatan sertifikat**, status di dashboard
**"Kamera USB: aktif"** dan **"Server AI: memproses … ms/frame"**, kotak wajah muncul saat ada orang.

## 10. Update aplikasi di kemudian hari

```bash
cd /srv/simrs-face-recognition
git pull
fr up -d --build
fr run --rm web manage face_check_db     # bila ada migrasi baru, jalankan migrate seperti langkah 5
```

## Masalah umum

| Gejala | Solusi |
|---|---|
| `model AI belum ada/checksum salah` | Langkah 3 |
| `container Nginx Proxy Manager tidak ditemukan` | NPM belum jalan, atau pakai mode port sendiri (`docs/DEPLOY_PRODUCTION.md` bagian 4) |
| Gagal "Menguji koneksi ke database" | Langkah 0: host `sik`, firewall 3306, `bind-address` MariaDB |
| `face_khanza_check`: hak tulis | `.env.production` harus berisi `KHANZA_ALLOW_PRIVILEGED_USER=true` saat memakai `sik` |
| NPM: 502 Bad Gateway | Forward Hostname harus `facerec_nginx`; cek `fr ps` |
| Peringatan sertifikat di browser | Jalankan ulang `setup-loket.ps1` sebagai Administrator; tutup semua jendela browser |
| Login 403 (CSRF) | Domain di NPM harus sama dengan `CSRF_TRUSTED_ORIGINS` di `.env.production`; lalu `fr up -d` |
| "Kamera USB butuh HTTPS" | Buka lewat `https://facerec.rs.local`, bukan IP/HTTP |
| Kamera tidak bisa dibuka | Tutup Zoom/Teams/Camera atau tab lain yang memakai kamera |

Panduan lengkap & latar belakang arsitektur: `docs/DEPLOY_PRODUCTION.md`.
