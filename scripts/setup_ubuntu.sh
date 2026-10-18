#!/usr/bin/env bash
# Setup proyek di Ubuntu 24.04. Hanya memasang paket Python ke .venv proyek.
# Tidak memakai sudo pip, tidak memakai --break-system-packages, tidak menyentuh database.
source "$(dirname "$0")/_common.sh"

info "Memeriksa dependensi sistem (tidak dipasang otomatis)"
missing=()
for pkg in python3-venv python3-dev build-essential pkg-config default-libmysqlclient-dev; do
  dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "Paket sistem belum terpasang: ${missing[*]}"
  echo "Pasang dengan: sudo apt install ${missing[*]}"
  exit 1
fi

ensure_venv
info "Memasang dependensi Python ke venv"
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements.txt
"$PY" -m pip check

info "Memeriksa impor paket penting"
"$PY" - <<'PYEOF'
import cv2, django, numpy, onnxruntime, cryptography, MySQLdb
print("OpenCV", cv2.__version__, "| Django", django.get_version(), "| ONNX Runtime", onnxruntime.__version__)
assert hasattr(cv2, "FaceDetectorYN") and hasattr(cv2, "FaceRecognizerSF"), "OpenCV tanpa modul face"
PYEOF

if [ ! -f .env ]; then
  info "Membuat .env dari .env.example dengan secret acak"
  SK="$("$PY" -c 'import secrets;print(secrets.token_urlsafe(50))')"
  FK="$("$PY" -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
  WS="$("$PY" -c 'import secrets;print(secrets.token_urlsafe(32))')"
  umask 077
  sed -e "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$SK|" \
      -e "s|^FACE_EMBEDDING_KEYS=.*|FACE_EMBEDDING_KEYS=$FK|" \
      -e "s|^FACE_WORKER_SECRET=.*|FACE_WORKER_SECRET=$WS|" \
      -e "s|^TIME_ZONE=.*|TIME_ZONE=$(cat /etc/timezone 2>/dev/null || echo Asia/Jakarta)|" .env.example > .env
  read -r -s -p "Password MySQL untuk user DB (tidak ditampilkan, kosongkan untuk mengisi manual nanti): " DBPW; echo
  if [ -n "$DBPW" ]; then
    "$PY" - "$DBPW" <<'PYEOF'
import re, sys
p = ".env"; s = open(p).read()
s = re.sub(r"(?m)^DB_PASSWORD=.*$", lambda m: "DB_PASSWORD=" + sys.argv[1], s)
open(p, "w").write(s)
PYEOF
  fi
  chmod 600 .env
  echo "PENTING: cadangkan FACE_EMBEDDING_KEYS secara aman; tanpa kunci ini embedding tidak dapat didekripsi."
else
  info ".env sudah ada, tidak diubah"
fi

info "Mengunduh model AI (OpenCV Zoo) jika belum ada"
"$PROJECT_DIR/scripts/download_models.sh"

mkdir -p var media_private && chmod 700 var media_private
"$PY" manage.py check
echo
echo "Setup selesai. Langkah berikutnya:"
echo "  scripts/db_check.sh              # cek koneksi & rencana migrasi customsik (baca-saja)"
echo "  scripts/migrate_customsik.sh     # terapkan migrasi (hanya tabel face_*)"
echo "  scripts/check_camera.sh          # uji kamera"
echo "  scripts/run_demo.sh              # jalankan Django + AI worker"
