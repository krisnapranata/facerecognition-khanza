#!/usr/bin/env bash
# Jalankan Django (host/port dari FACE_DJANGO_URL, default 127.0.0.1:8020) dan AI worker sebagai proses terpisah. Ctrl+C menghentikan keduanya.
source "$(dirname "$0")/_common.sh"
verify_interpreter
require_env_file
# Host/port diambil dari FACE_DJANGO_URL (.env) agar Django dan AI worker selalu sinkron.
read -r HOST PORT < <("$PY" -c 'from urllib.parse import urlsplit; from ai_worker.config import WorkerConfig as W; u=urlsplit(W.from_env().django_url); print(u.hostname or "127.0.0.1", u.port or 8020)')
if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  holder="$(ss -ltnp 2>/dev/null | grep ":$PORT " | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1)"
  if [ -n "$holder" ] && ps -o args= -p "$holder" | grep -q "manage.py runserver"; then
    echo "Port $PORT dipakai demo yang masih berjalan (PID $holder):"
    ps -o pid=,args= -p "$holder"
    die "Hentikan dulu: kill $holder (dan worker: pgrep -a -f ai_worker.worker), lalu jalankan ulang."
  fi
  die "Port $PORT sudah dipakai proses lain${holder:+ (PID $holder)}. Ubah FACE_DJANGO_URL di .env (mis. http://127.0.0.1:8030)"
fi
"$PY" manage.py check
"$PY" manage.py migrate --database=customsik face_events --check >/dev/null 2>&1 \
  || die "Migrasi customsik belum diterapkan. Jalankan scripts/migrate_customsik.sh"

pids=()
cleanup() {
  echo; info "Menghentikan proses..."
  for p in "${pids[@]}"; do kill -TERM "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

info "Django: http://$HOST:$PORT/"
"$PY" manage.py runserver "$HOST:$PORT" --noreload &
pids+=($!)
sleep 2
info "AI worker (kamera: lihat CAMERA_SOURCE di .env)"
"$PY" -m ai_worker.worker "$@" &
pids+=($!)
wait -n "${pids[@]}"
