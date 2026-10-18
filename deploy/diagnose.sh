#!/usr/bin/env bash
# Diagnostik SIMRS Face Recognition di server (BACA-SAJA, tidak mengubah apa pun).
# Tidak menampilkan password/secret. Kirim seluruh output saat meminta bantuan.
#
#   cd /srv/simrs-face-recognition && deploy/diagnose.sh [domain]
cd "$(dirname "$0")/.." || exit 1
ENV_FILE=".env.production"
envval() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -1 | sed "s/^'\(.*\)'$/\1/"; }
DOMAIN="${1:-$(envval CSRF_TRUSTED_ORIGINS | sed 's#https://##; s#,.*##')}"
NPM="$(docker ps --format '{{.Names}} {{.Image}}' | awk '$2 ~ /nginx-proxy-manager/ {print $1; exit}')"
NPM_NET="$(envval NPM_NETWORK)"
sec() { echo; echo "===== $* ====="; }

sec "Versi kode"
git log --oneline -1 2>/dev/null || echo "(bukan repo git)"

sec "Konfigurasi (tanpa secret)"
for k in APP_MODE DJANGO_ALLOWED_HOSTS CSRF_TRUSTED_ORIGINS BEHIND_TLS_PROXY NPM_NETWORK DB_HOST DB_NAME \
         KHANZA_DB_HOST KHANZA_DB_NAME KHANZA_DB_USER KHANZA_ALLOW_PRIVILEGED_USER FACE_DEFAULT_CAMERA; do
  echo "$k=$(envval "$k")"
done
echo "Domain yang diuji: $DOMAIN"

sec "Container"
docker ps -a --filter name=facerec_ --format 'table {{.Names}}\t{{.Status}}\t{{.CreatedAt}}'
echo "NPM: ${NPM:-TIDAK DITEMUKAN}"

sec "Jaringan"
for c in $NPM facerec_nginx facerec_web facerec_vision; do
  echo "$c: $(docker inspect "$c" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}={{$v.IPAddress}} {{end}}' 2>/dev/null)"
done

sec "Upstream di facerec_nginx (harus facerec_web / facerec_vision)"
docker exec facerec_nginx grep -E "upstream_(web|vision) http" /etc/nginx/conf.d/default.conf 2>&1 | sort -u

sec "Uji HTTP langsung dari container NPM -> facerec_nginx"
if [ -n "$NPM" ]; then
  docker exec "$NPM" sh -c "curl -s -o /dev/null -w 'HTTP %{http_code}\n' -H 'Host: $DOMAIN' http://facerec_nginx/accounts/login/" 2>&1
else
  echo "(lewati: NPM tidak ditemukan)"
fi

sec "Uji HTTP dari jaringan $NPM_NET"
[ -n "$NPM_NET" ] && docker run --rm --network "$NPM_NET" nginx:1.27-alpine \
  wget -qS -O /dev/null --header "Host: $DOMAIN" http://facerec_nginx/accounts/login/ 2>&1 | grep -m1 HTTP/

sec "Stabilitas container (restart / OOM / mulai)"
for c in facerec_web facerec_vision facerec_nginx; do
  echo "$c: $(docker inspect "$c" --format 'restart={{.RestartCount}} oom={{.State.OOMKilled}} mulai={{.State.StartedAt}} exit={{.State.ExitCode}}' 2>&1)"
done
docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' facerec_web facerec_vision facerec_nginx 2>&1
echo "Beban server: $(cut -d' ' -f1-3 /proc/loadavg) (load 1/5/15 menit), CPU: $(nproc), $(free -h | awk '/^Mem:/ {print "RAM terpakai "$3" dari "$2}')"
(dmesg 2>/dev/null | grep -iE "out of memory|oom-kill" | tail -3) || true

sec "Error vision/web 1 jam terakhir (timeout worker, exception, koneksi DB)"
for c in facerec_vision facerec_web; do
  echo "-- $c"
  docker logs --since 1h "$c" 2>&1 | grep -iE "WORKER TIMEOUT|Booting worker|Traceback|Error|exception|killed|refused|gone away|Lost connection" \
    | grep -v "onnxruntime" | tail -15
done
echo "-- jumlah request frame stasiun 10 menit terakhir: $(docker logs --since 10m facerec_vision 2>&1 | grep -c 'stations/')"
echo "-- kode status frame stasiun 10 menit terakhir:"
docker logs --since 10m facerec_vision 2>&1 | grep 'stations/' | grep -oE '" [0-9]{3} ' | sort | uniq -c

if [ -n "$NPM" ] && [ -n "$DOMAIN" ]; then
  sec "Log error NPM untuk $DOMAIN"
  PH="$(docker exec "$NPM" sh -c "grep -l 'server_name.*$DOMAIN' /data/nginx/proxy_host/*.conf 2>/dev/null | head -1")"
  if [ -n "$PH" ]; then
    ID="$(basename "$PH" .conf)"
    echo "Proxy host #$ID"
    docker exec "$NPM" sh -c "tail -n 15 /data/logs/proxy-host-${ID}_error.log 2>/dev/null || echo '(log error kosong)'"
  else
    echo "(proxy host untuk $DOMAIN tidak ditemukan di NPM)"
  fi
fi

sec "Log web (20 baris)"
docker logs --tail 20 facerec_web 2>&1
sec "Log nginx aplikasi (10 baris)"
docker logs --tail 10 facerec_nginx 2>&1

sec "DNS domain $DOMAIN"
(getent hosts "$DOMAIN" || echo "tidak terselesaikan di server ini") 2>&1
command -v dig >/dev/null && echo "DNS publik (1.1.1.1): $(dig +short "$DOMAIN" @1.1.1.1 | tr '\n' ' ')"
echo "IP publik server ini: $(curl -s --max-time 5 https://api.ipify.org || echo '?')"

if [ -n "$NPM" ]; then
  sec "Log NPM (error terbaru)"
  docker logs --tail 200 "$NPM" 2>&1 | grep -iE "error|fail|warn|letsencrypt|certbot|nginx: \[emerg\]" | tail -25
  sec "Uji konfigurasi nginx di NPM"
  docker exec "$NPM" nginx -t 2>&1 | tail -5
  sec "Log Let's Encrypt NPM (terakhir)"
  docker exec "$NPM" sh -c 'tail -n 25 /tmp/letsencrypt-log/letsencrypt.log 2>/dev/null || tail -n 25 /data/logs/letsencrypt.log 2>/dev/null || echo "(log tidak ada)"' 2>&1
fi
echo
echo "Selesai. Kirim seluruh output di atas."
