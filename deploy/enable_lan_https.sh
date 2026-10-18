#!/usr/bin/env bash
# Aktifkan akses LAN langsung lewat IP dengan HTTPS, mis. https://192.168.1.176:8020
# (domain publik lewat Nginx Proxy Manager tetap berjalan seperti biasa).
#
#   cd /srv/simrs-face-recognition && deploy/enable_lan_https.sh 192.168.1.176 [8020]
#
# Yang dilakukan (aman diulang):
#   1. .env.production: FACE_LAN_HTTPS_PORT, IP ke DJANGO_ALLOWED_HOSTS, https://IP:PORT ke CSRF_TRUSTED_ORIGINS
#   2. Sertifikat CA internal: pastikan memuat IP ini (dibuat ulang bila perlu; CA lama tetap dipakai)
#   3. Menjalankan ulang container dengan docker-compose.lan.yml (port PORT -> nginx 8443)
#   4. Menguji https://IP:PORT dari server ini
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE=".env.production"
die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo; echo "==> $*"; }

IP="${1:-}"
PORT="${2:-8020}"
[[ "$IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || die "pemakaian: deploy/enable_lan_https.sh <IP-server> [port]  (mis. 192.168.1.176 8020)"
[[ "$PORT" =~ ^[0-9]{2,5}$ ]] && [ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || die "port tidak valid: $PORT"
[ -f "$ENV_FILE" ] || die "$ENV_FILE belum ada — jalankan deploy/install_server.sh dulu"
command -v openssl >/dev/null || die "openssl tidak ditemukan"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.npm.yml -f docker-compose.lan.yml --env-file "$ENV_FILE")

info "Memeriksa port $PORT"
if ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q .; then
  docker port facerec_nginx 2>/dev/null | grep -qE "[:.]$PORT$" \
    || die "port $PORT sudah dipakai aplikasi lain (cek: sudo ss -ltnp 'sport = :$PORT'). Pilih port lain."
  echo "Port $PORT sudah dipakai facerec_nginx (instalasi ini) — lanjut."
else
  echo "Port $PORT bebas."
fi

info "Memperbarui $ENV_FILE"
envval() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; }
setkv() {  # setkv KEY VALUE
  if grep -q "^$1=" "$ENV_FILE"; then sed -i "s#^$1=.*#$1=$2#" "$ENV_FILE"; else echo "$1=$2" >> "$ENV_FILE"; fi
}
addlist() {  # addlist KEY ITEM  (daftar dipisah koma, tanpa duplikat)
  local cur; cur="$(envval "$1")"
  if [[ ",$cur," == *",$2,"* ]]; then echo "  $1 sudah memuat $2"; return; fi
  setkv "$1" "${cur:+$cur,}$2"; echo "  $1 += $2"
}
setkv FACE_LAN_HTTPS_PORT "$PORT"; echo "  FACE_LAN_HTTPS_PORT=$PORT"
addlist DJANGO_ALLOWED_HOSTS "$IP"
addlist CSRF_TRUSTED_ORIGINS "https://$IP:$PORT"

info "Memeriksa sertifikat untuk IP $IP"
san() { openssl x509 -in deploy/certs/server.crt -noout -ext subjectAltName 2>/dev/null | tail -n +2 | tr ',' '\n' | sed 's/^ *//'; }
if [ -f deploy/certs/server.crt ] && san | grep -qx "IP Address:$IP"; then
  echo "Sertifikat sudah memuat IP $IP — tidak diubah."
else
  ARGS=()
  if [ -f deploy/certs/server.crt ]; then
    while read -r entry; do
      case "$entry" in
        DNS:localhost) ;;
        DNS:*) ARGS+=(--dns "${entry#DNS:}") ;;
        "IP Address:"*) ARGS+=(--ip "${entry#IP Address:}") ;;
      esac
    done < <(san)
  fi
  ARGS+=(--ip "$IP")
  echo "Membuat ulang sertifikat server (${ARGS[*]}); CA internal yang ada tetap dipakai."
  rm -f deploy/certs/server.crt deploy/certs/server.key
  deploy/make_certs.sh "${ARGS[@]}"
fi

info "Menjalankan ulang container dengan pintu LAN :$PORT"
"${COMPOSE[@]}" up -d --force-recreate web vision
"${COMPOSE[@]}" up -d --force-recreate --no-deps nginx

info "Menguji https://$IP:$PORT"
ok=0
for _ in $(seq 1 30); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --cacert deploy/certs/ca.crt "https://$IP:$PORT/accounts/login/" || true)"
  [ "$code" = "200" ] && { ok=1; break; }
  sleep 2
done
[ "$ok" = "1" ] || die "https://$IP:$PORT belum merespons 200 (terakhir: ${code:-?}). Cek: ${COMPOSE[*]} logs --tail 30 nginx web"
echo "https://$IP:$PORT/accounts/login/ -> 200 OK (sertifikat terverifikasi CA internal)"
"${COMPOSE[@]}" ps

cat <<EOF

=====================================================================
 Akses LAN aktif: https://$IP:$PORT   (domain publik tetap jalan)
=====================================================================
Perintah compose selanjutnya WAJIB menyertakan docker-compose.lan.yml:
  alias fr='docker compose -f docker-compose.yml -f docker-compose.npm.yml -f docker-compose.lan.yml --env-file .env.production'

Firewall server (bila ufw aktif), izinkan dari jaringan RS saja:
  sudo ufw allow from 192.168.0.0/16 to any port $PORT proto tcp

PC loket yang memakai alamat IP ini (sekali per PC, PowerShell Administrator):
  salin $(pwd)/deploy/windows/out/  (simrs-ca.crt + setup-loket.ps1), lalu:
  .\\setup-loket.ps1 -ServerUrl https://$IP:$PORT -Station loket-1
  (TANPA -NoCa: CA internal harus terpasang agar tidak ada peringatan sertifikat)
EOF
