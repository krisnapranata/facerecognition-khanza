#!/usr/bin/env bash
# Pemasang SIMRS Face Recognition di server Docker yang sudah memakai Nginx Proxy Manager (NPM).
# Database customsik & yatofaasli berada di server lain (server B).
#
#   cd /srv/simrs-face-recognition && deploy/install_server.sh
#
# Aman dijalankan ulang: .env.production, sertifikat, dan data yang sudah ada TIDAK ditimpa.
# Tidak menyentuh container lain, tidak mengubah NPM (diatur lewat UI NPM), tidak menjalankan
# migrasi customsik maupun GRANT di server B.
#
# Jawaban bisa diberikan lewat variabel (mode non-interaktif / otomatis):
#   INSTALL_DOMAIN INSTALL_SERVER_IP INSTALL_DB_HOST INSTALL_DB_PORT INSTALL_DB_USER INSTALL_DB_PASSWORD
#   INSTALL_RO_USER INSTALL_RO_PASSWORD INSTALL_RW_USER INSTALL_RW_PASSWORD INSTALL_EMBEDDING_KEYS
#   NPM_CONTAINER (paksa nama container NPM)  INSTALL_YES=1 (tanpa konfirmasi)
#   INSTALL_SKIP_KHANZA_CHECK=1 (TIDAK disarankan; hanya untuk uji)
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"
ENV_FILE=".env.production"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.npm.yml --env-file "$ENV_FILE")
CONTAINERS=(facerec_web facerec_vision facerec_nginx facerec_worker)

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo; echo "==> $*"; }
warn() { echo "PERINGATAN: $*" >&2; }

ask() {  # ask VAR "pertanyaan" "default"
  local var="$1" prompt="$2" def="${3:-}" val="${!1:-}"
  if [ -z "$val" ]; then
    read -r -p "$prompt${def:+ [$def]}: " val
    val="${val:-$def}"
  fi
  printf -v "$var" '%s' "$val"
}

ask_secret() {  # ask_secret VAR "pertanyaan"
  local var="$1" prompt="$2" val="${!1:-}"
  if [ -z "$val" ]; then
    read -r -s -p "$prompt (tidak ditampilkan): " val; echo
  fi
  printf -v "$var" '%s' "$val"
}

confirm() {
  [ "${INSTALL_YES:-0}" = "1" ] && return 0
  local ans; read -r -p "$1 [y/N] " ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ]
}

valid_ip() { [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; }

# ---------------------------------------------------------------- 1. prasyarat
info "Memeriksa prasyarat"
command -v docker >/dev/null || die "docker tidak ditemukan"
docker compose version >/dev/null 2>&1 || die "docker compose v2 tidak tersedia"
docker info >/dev/null 2>&1 || die "user $(id -un) tidak bisa menjalankan docker (tambahkan ke grup docker atau pakai sudo)"
command -v python3 >/dev/null || die "python3 tidak ditemukan (dipakai untuk membuat secret)"
command -v openssl >/dev/null || die "openssl tidak ditemukan (dipakai untuk sertifikat)"
[ -f docker-compose.yml ] && [ -f docker-compose.npm.yml ] || die "jalankan dari folder proyek simrs-face-recognition"
echo "docker $(docker version --format '{{.Server.Version}}'), $(docker compose version --short 2>/dev/null || echo compose)"

if ! scripts/download_models.sh >/dev/null 2>&1; then
  die "model AI belum ada/checksum salah di models/. Salin folder models/ dari laptop atau jalankan scripts/download_models.sh (butuh internet)."
fi
echo "Model AI: OK (checksum cocok)"

# ---------------------------------------------------------------- 2. deteksi NPM
info "Mendeteksi Nginx Proxy Manager"
NPM_CONTAINER="${NPM_CONTAINER:-$(docker ps --format '{{.Names}} {{.Image}}' | awk '$2 ~ /nginx-proxy-manager/ {print $1; exit}')}"
[ -n "$NPM_CONTAINER" ] || die "container Nginx Proxy Manager tidak ditemukan. Untuk server tanpa NPM pakai mode mandiri (docs/DEPLOY_PRODUCTION.md bagian 4)."
NPM_NETWORK_DETECTED="$(docker inspect "$NPM_CONTAINER" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')"
[ -n "$NPM_NETWORK_DETECTED" ] || die "jaringan Docker milik $NPM_CONTAINER tidak terbaca"
echo "NPM: $NPM_CONTAINER, jaringan: $NPM_NETWORK_DETECTED"

# ---------------------------------------------------------------- 3. bentrok nama container
info "Memeriksa nama container"
for c in "${CONTAINERS[@]}"; do
  if docker ps -a --format '{{.Names}}' | grep -qx "$c"; then
    owner="$(docker inspect "$c" --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null || true)"
    [ "$owner" = "$PROJECT_DIR" ] || die "container $c sudah ada dan milik proyek lain ($owner)"
    echo "$c: milik instalasi ini (akan diperbarui)"
  fi
done
echo "Port: mode NPM tidak memublikasikan port apa pun (tidak bentrok dengan aplikasi lain)."

# ---------------------------------------------------------------- 4. .env.production
if [ -f "$ENV_FILE" ]; then
  info "$ENV_FILE sudah ada — tidak diubah"
else
  info "Membuat $ENV_FILE"
  DEFAULT_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  ask INSTALL_DOMAIN "Nama domain aplikasi (dibuka PC loket)" "facerec.rs.local"
  ask INSTALL_SERVER_IP "IP server ini (server A)" "$DEFAULT_IP"
  valid_ip "$INSTALL_SERVER_IP" || die "IP server tidak valid: $INSTALL_SERVER_IP"
  ask INSTALL_DB_HOST "IP/host database server B (customsik & yatofaasli)" ""
  [ -n "$INSTALL_DB_HOST" ] || die "host database wajib diisi"
  ask INSTALL_DB_PORT "Port database" "3306"
  ask INSTALL_DB_USER "User database customsik" "sik"
  ask_secret INSTALL_DB_PASSWORD "Password database customsik"
  ask INSTALL_RO_USER "User untuk membaca data Khanza (Enter = pakai user yang sama)" "$INSTALL_DB_USER"
  if [ -z "${INSTALL_RO_PASSWORD:-}" ] && [ "$INSTALL_RO_USER" = "$INSTALL_DB_USER" ]; then
    INSTALL_RO_PASSWORD="$INSTALL_DB_PASSWORD"
  fi
  ask_secret INSTALL_RO_PASSWORD "Password $INSTALL_RO_USER"
  # Akun yang sudah ada (mis. sik) biasanya berhak tulis: izinkan secara eksplisit. Koneksi baca
  # tetap dikunci READ ONLY per sesi oleh aplikasi.
  INSTALL_ALLOW_PRIVILEGED="false"
  if [ "$INSTALL_RO_USER" != "face_ro" ]; then
    INSTALL_ALLOW_PRIVILEGED="true"
    echo "Catatan: user '$INSTALL_RO_USER' dipakai untuk membaca Khanza -> KHANZA_ALLOW_PRIVILEGED_USER=true"
    echo "         (koneksi baca tetap READ ONLY per sesi; hanya SELECT dari config/khanza_queries.json)."
  fi
  ask INSTALL_RW_USER "User registrasi kunjungan Khanza (INSERT reg_periksa)" "$INSTALL_DB_USER"
  if [ -z "${INSTALL_RW_PASSWORD:-}" ] && [ "$INSTALL_RW_USER" = "$INSTALL_DB_USER" ]; then
    INSTALL_RW_PASSWORD="$INSTALL_DB_PASSWORD"
  fi
  ask_secret INSTALL_RW_PASSWORD "Password $INSTALL_RW_USER"
  if [ -z "${INSTALL_EMBEDDING_KEYS:-}" ]; then
    echo "Kunci enkripsi biometrik: kosongkan untuk membuat baru, atau tempel FACE_EMBEDDING_KEYS lama"
    echo "(wajib sama bila memindahkan referensi wajah yang sudah ada)."
    ask_secret INSTALL_EMBEDDING_KEYS "FACE_EMBEDDING_KEYS lama"
  fi
  CPUS="$(nproc)"; VISION_CPUS=$(( CPUS / 2 > 0 ? CPUS / 2 : 1 ))
  export INSTALL_DOMAIN INSTALL_SERVER_IP INSTALL_DB_HOST INSTALL_DB_PORT INSTALL_DB_USER INSTALL_DB_PASSWORD \
         INSTALL_RO_USER INSTALL_RO_PASSWORD INSTALL_RW_USER INSTALL_RW_PASSWORD INSTALL_EMBEDDING_KEYS \
         INSTALL_ALLOW_PRIVILEGED \
         VISION_CPUS NPM_NETWORK_DETECTED
  umask 077
  python3 - <<'PYEOF'
import base64, os, re, secrets
e = os.environ
for key in ("INSTALL_DB_PASSWORD", "INSTALL_RO_PASSWORD", "INSTALL_RW_PASSWORD"):
    if "'" in e.get(key, "") or "\n" in e.get(key, ""):
        raise SystemExit(f"{key}: password tidak boleh mengandung tanda kutip tunggal atau baris baru")

def literal(v):
    # Kutip tunggal = nilai literal bagi docker compose (tanpa interpolasi $).
    return f"'{v}'" if re.search(r"[^A-Za-z0-9_.@:/,+=-]", v) else v

values = {
    "APP_MODE": "production", "DJANGO_DEBUG": "false",
    "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
    "DJANGO_ALLOWED_HOSTS": f"{e['INSTALL_DOMAIN']},{e['INSTALL_SERVER_IP']},web,localhost",
    "CSRF_TRUSTED_ORIGINS": f"https://{e['INSTALL_DOMAIN']}",
    "BEHIND_TLS_PROXY": "true", "SESSION_COOKIE_SECURE": "true", "CSRF_COOKIE_SECURE": "true",
    "NPM_NETWORK": e["NPM_NETWORK_DETECTED"],
    "DB_HOST": e["INSTALL_DB_HOST"], "DB_PORT": e["INSTALL_DB_PORT"],
    "DB_USER": e["INSTALL_DB_USER"], "DB_PASSWORD": literal(e["INSTALL_DB_PASSWORD"]),
    "FACE_EMBEDDING_KEYS": e.get("INSTALL_EMBEDDING_KEYS") or base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "FACE_WORKER_SECRET": secrets.token_urlsafe(32),
    "FACE_DEFAULT_CAMERA": "usb", "GUNICORN_WORKERS": "2", "VISION_CPUS": e["VISION_CPUS"],
    "KHANZA_DB_HOST": e["INSTALL_DB_HOST"], "KHANZA_DB_PORT": e["INSTALL_DB_PORT"],
    "KHANZA_DB_USER": e["INSTALL_RO_USER"], "KHANZA_DB_PASSWORD": literal(e["INSTALL_RO_PASSWORD"]),
    "KHANZA_ALLOW_PRIVILEGED_USER": e["INSTALL_ALLOW_PRIVILEGED"],
    "KHANZA_RW_DB_USER": e["INSTALL_RW_USER"], "KHANZA_RW_DB_PASSWORD": literal(e["INSTALL_RW_PASSWORD"]),
}
text = open(".env.production.example").read()
for k, v in values.items():
    pattern = rf"(?m)^{k}=.*$"
    if re.search(pattern, text):
        text = re.sub(pattern, lambda m: f"{k}={v}", text)
    else:
        text += f"{k}={v}\n"
# RTSP worker opsional: kosongkan contoh URL agar tidak terpakai tanpa sengaja.
text = re.sub(r"(?m)^CAMERA_SOURCE=.*$", "CAMERA_SOURCE=", text)
open(".env.production", "w").write(text)
PYEOF
  chmod 600 "$ENV_FILE"
  echo "$ENV_FILE dibuat (permission 600). Cadangkan FACE_EMBEDDING_KEYS di tempat aman, terpisah dari backup data."
fi

envval() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1 | sed "s/^'\(.*\)'$/\1/"; }
DOMAIN="$(envval CSRF_TRUSTED_ORIGINS | sed 's#https://##; s#,.*##')"
SERVER_IP="${INSTALL_SERVER_IP:-$(envval DJANGO_ALLOWED_HOSTS | tr ',' '\n' | grep -Em1 '^([0-9]{1,3}\.){3}[0-9]{1,3}$' || true)}"
NPM_NETWORK="$(envval NPM_NETWORK)"
[ "$NPM_NETWORK" = "$NPM_NETWORK_DETECTED" ] || warn "NPM_NETWORK di $ENV_FILE ($NPM_NETWORK) berbeda dengan jaringan NPM terdeteksi ($NPM_NETWORK_DETECTED)"

# ---------------------------------------------------------------- 5. folder data
info "Menyiapkan folder data (milik uid 1000 = user di container)"
mkdir -p data/var data/media_private data/static
if [ "$(stat -c %u data/var)" != "1000" ] || [ "$(stat -c %u data/media_private)" != "1000" ]; then
  if [ "$(id -u)" = "1000" ]; then :; elif confirm "chown 1000:1000 data/ (butuh sudo)?"; then
    sudo chown -R 1000:1000 data
  else
    die "folder data harus dimiliki uid 1000"
  fi
fi
chmod 700 data/media_private

# ---------------------------------------------------------------- 6. build & uji koneksi server B
info "Membangun image aplikasi"
"${COMPOSE[@]}" build

info "Menguji koneksi ke database server B dari dalam container"
if ! "${COMPOSE[@]}" run --rm --no-deps web manage face_check_db; then
  IP_A="${SERVER_IP:-<IP-server-A>}"
  cat >&2 <<EOF

Koneksi/pemeriksaan database gagal. Di server B, pastikan:
  - MariaDB mendengarkan di LAN (bind-address bukan 127.0.0.1) dan firewall mengizinkan IP server ini:
      sudo ufw allow from $IP_A to any port 3306 proto tcp
  - user '$(envval DB_USER)' boleh login dari $IP_A (cek: SELECT user,host FROM mysql.user WHERE user='$(envval DB_USER)';
    host '%' atau '$IP_A'), dan password di .env.production benar.
EOF
  if [ "$(envval KHANZA_DB_USER)" != "$(envval DB_USER)" ]; then
    cat >&2 <<EOF
  - user baca Khanza '$(envval KHANZA_DB_USER)' juga boleh login dari $IP_A dengan hak SELECT
    (docs/DEPLOY_PRODUCTION.md bagian 3).
EOF
  fi
  echo "Uji manual dari server ini: mysql -h $(envval DB_HOST) -u $(envval DB_USER) -p -e 'SELECT 1'" >&2
  echo "Lalu jalankan ulang skrip ini." >&2
  exit 1
fi
if [ "$(envval KHANZA_ENABLED)" = "true" ] && [ "${INSTALL_SKIP_KHANZA_CHECK:-0}" != "1" ]; then
  "${COMPOSE[@]}" run --rm --no-deps web manage face_khanza_check \
    || die "pemeriksaan Khanza gagal (user harus baca-saja dan bisa login dari IP server ini). Lihat pesan di atas."
fi

# ---------------------------------------------------------------- 7. sertifikat
if [ -f deploy/certs/server.crt ] && [ -f deploy/certs/server.key ]; then
  info "Sertifikat sudah ada — tidak diubah (perbarui: deploy/make_certs.sh --dns $DOMAIN --ip <IP>)"
else
  info "Membuat sertifikat CA internal untuk $DOMAIN"
  CERT_ARGS=(--dns "$DOMAIN")
  [ -n "$SERVER_IP" ] && CERT_ARGS+=(--ip "$SERVER_IP")
  deploy/make_certs.sh "${CERT_ARGS[@]}"
fi

# ---------------------------------------------------------------- 8. jalankan
info "Menjalankan container (tanpa port publik; masuk jaringan $NPM_NETWORK)"
"${COMPOSE[@]}" up -d
echo "Menunggu aplikasi siap..."
ok=0
for _ in $(seq 1 30); do
  if docker run --rm --network "$NPM_NETWORK" nginx:1.27-alpine \
       wget -q -O /dev/null --header "Host: $DOMAIN" http://facerec_nginx/accounts/login/ 2>/dev/null; then
    ok=1; break
  fi
  sleep 2
done
[ "$ok" = "1" ] || die "facerec_nginx belum merespons dari jaringan NPM. Cek: ${COMPOSE[*]} logs web nginx"
"${COMPOSE[@]}" ps

# ---------------------------------------------------------------- 9. ringkasan
COMPOSE_STR="${COMPOSE[*]}"
cat <<EOF

=====================================================================
 Instalasi selesai. Aplikasi siap diteruskan oleh Nginx Proxy Manager.
=====================================================================
1) NPM UI (http://${SERVER_IP:-<IP-server>}:81)
   a. SSL Certificates -> Add SSL Certificate -> Custom
        Certificate Key : $PROJECT_DIR/deploy/certs/server.key
        Certificate     : $PROJECT_DIR/deploy/certs/server.crt
   b. Hosts -> Proxy Hosts -> Add Proxy Host
        Domain Names    : $DOMAIN
        Scheme/Host/Port: http / facerec_nginx / 80
        Block Common Exploits: ON
        SSL tab         : sertifikat custom di atas, Force SSL ON, HTTP/2 ON
        Advanced        : client_max_body_size 8m; proxy_buffering off; proxy_read_timeout 120s;

2) Migrasi customsik (manual, setelah membaca hasil face_check_db di atas):
     $COMPOSE_STR run --rm web manage migrate --database=customsik face_events

3) Akun administrator lokal (kelola referensi wajah & audit):
     $COMPOSE_STR run --rm web manage createsuperuser
   Petugas loket login dengan akun Khanza (hak Registrasi).

4) DNS: $DOMAIN -> ${SERVER_IP:-<IP-server>} (DNS internal/router), atau pakai -HostsIp di PC loket.

5) PC loket Windows: salin $PROJECT_DIR/deploy/windows/out/ lalu (PowerShell Administrator):
     .\\setup-loket.ps1 -ServerUrl https://$DOMAIN -HostsIp ${SERVER_IP:-<IP-server>} -Station loket-1

Log: $COMPOSE_STR logs -f web vision nginx
EOF
