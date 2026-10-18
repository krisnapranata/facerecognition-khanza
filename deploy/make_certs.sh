#!/usr/bin/env bash
# Membuat CA internal rumah sakit + sertifikat HTTPS server (offline, openssl).
# Browser hanya mengizinkan kamera USB pada HTTPS, sehingga PC loket harus mempercayai CA ini
# (pasang ca.crt sekali di tiap PC Windows: deploy/windows/setup-loket.ps1).
#
#   deploy/make_certs.sh --ip 192.168.1.20 [--ip ...] [--dns facerec.rs.local] [--days 825]
#
# CA dibuat sekali dan dipakai ulang; jalankan lagi untuk memperbarui sertifikat server
# (mis. IP berubah) tanpa perlu memasang ulang CA di PC loket.
set -euo pipefail
cd "$(dirname "$0")"
OUT="certs"
DAYS=825
IPS=()
DNS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --ip) IPS+=("$2"); shift 2 ;;
    --dns) DNS+=("$2"); shift 2 ;;
    --days) DAYS="$2"; shift 2 ;;
    *) echo "argumen tidak dikenal: $1" >&2; exit 1 ;;
  esac
done
[ ${#IPS[@]} -gt 0 ] || [ ${#DNS[@]} -gt 0 ] || { echo "minimal satu --ip atau --dns (alamat server A yang dibuka PC loket)" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl tidak ditemukan" >&2; exit 1; }
mkdir -p "$OUT" windows/out
umask 077

if [ ! -f "$OUT/ca.key" ]; then
  echo "==> Membuat CA internal (10 tahun)"
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
    -keyout "$OUT/ca.key" -out "$OUT/ca.crt" \
    -subj "/O=SIMRS Rumah Sakit/CN=SIMRS Internal CA" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"
else
  echo "==> Memakai CA yang sudah ada ($OUT/ca.crt)"
fi

SAN="DNS:localhost"
for d in "${DNS[@]}"; do SAN+=",DNS:$d"; done
for i in "${IPS[@]}"; do SAN+=",IP:$i"; done
CN="${DNS[0]:-${IPS[0]}}"

echo "==> Membuat sertifikat server ($CN; $SAN; $DAYS hari)"
openssl req -newkey rsa:2048 -sha256 -nodes -keyout "$OUT/server.key" -out "$OUT/server.csr" \
  -subj "/O=SIMRS Rumah Sakit/CN=$CN"
cat > "$OUT/server.ext" <<EOF
basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$SAN
EOF
openssl x509 -req -in "$OUT/server.csr" -CA "$OUT/ca.crt" -CAkey "$OUT/ca.key" -CAcreateserial \
  -out "$OUT/server.crt" -days "$DAYS" -sha256 -extfile "$OUT/server.ext"
rm -f "$OUT/server.csr" "$OUT/server.ext"
chmod 644 "$OUT/ca.crt" "$OUT/server.crt"
chmod 640 "$OUT/server.key"   # dibaca nginx di container
cp "$OUT/ca.crt" windows/out/simrs-ca.crt
cp windows/setup-loket.ps1 windows/out/setup-loket.ps1
chmod 644 windows/out/simrs-ca.crt windows/out/setup-loket.ps1

openssl verify -CAfile "$OUT/ca.crt" "$OUT/server.crt"
echo
echo "Selesai."
echo "  Server : $OUT/server.crt + $OUT/server.key (dipakai nginx)"
echo "  PC loket: salin folder deploy/windows/out/ (simrs-ca.crt + setup-loket.ps1) ke tiap PC Windows"
echo "  RAHASIAKAN $OUT/ca.key (jangan disalin ke PC loket)."
