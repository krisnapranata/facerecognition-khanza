#!/bin/sh
# Peran container:
#   web    : halaman & API (gunicorn, beberapa worker)
#   vision : frame kamera USB dari browser loket (gunicorn 1 proses, banyak thread — state tracker di memori)
#   worker : AI worker untuk kamera server / IP camera RTSP
#   manage : menjalankan manage.py (mis. face_check_db, migrate, createsuperuser)
# Migrasi ke database customsik TIDAK dijalankan otomatis (lihat docs/DEPLOY_PRODUCTION.md).
set -e
umask 0077
cd /app
role="${1:-web}"
[ $# -gt 0 ] && shift

mkdir -p "$APP_VAR_DIR" "$MEDIA_PRIVATE_ROOT" "$STATIC_ROOT"

for f in "${FACE_DETECTOR_MODEL:-models/face_detection_yunet_2023mar.onnx}" "${FACE_EMBEDDING_MODEL:-models/face_recognition_sface_2021dec.onnx}"; do
  [ -f "$f" ] || echo "PERINGATAN: model $f tidak ditemukan — pencocokan wajah nonaktif (lihat scripts/download_models.sh)" >&2
done

case "$role" in
  web)
    umask 0022
    python manage.py collectstatic --noinput >/dev/null
    # File static dibaca nginx (user lain, uid 101): folder 755, file 644, apa pun umask host.
    chmod -R u=rwX,go=rX "$STATIC_ROOT" 2>/dev/null || echo "PERINGATAN: gagal chmod $STATIC_ROOT (CSS/JS bisa 403)" >&2
    umask 0077
    # Hanya database lokal aplikasi (SQLite: user, sesi). customsik dimigrasi manual.
    python manage.py migrate --database=default --noinput
    python manage.py face_setup_roles
    exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-3}" --threads "${GUNICORN_THREADS:-4}" \
      --timeout 120 --graceful-timeout 30 --access-logfile - "$@"
    ;;
  vision)
    exec gunicorn config.wsgi:application --bind 0.0.0.0:8001 \
      --workers 1 --threads "${VISION_THREADS:-8}" --timeout 60 --graceful-timeout 20 \
      --access-logfile - "$@"
    ;;
  worker)
    exec python -m ai_worker.worker "$@"
    ;;
  manage)
    exec python manage.py "$@"
    ;;
  *)
    exec "$role" "$@"
    ;;
esac
