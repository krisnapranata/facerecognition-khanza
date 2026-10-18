#!/usr/bin/env bash
# Terapkan migrasi:
#   1) database default (SQLite lokal: auth/session milik aplikasi ini)
#   2) customsik: HANYA app face_registry & face_events (tabel face_*), setelah pemeriksaan lolos.
# Tidak ada DROP/TRUNCATE/FLUSH. Menolak lanjut jika pemeriksaan gagal.
source "$(dirname "$0")/_common.sh"
verify_interpreter
require_env_file
info "Pemeriksaan sebelum migrasi"
"$PY" manage.py face_check_db
"$PY" manage.py migrate --database=customsik face_events --plan
if [ "${1:-}" != "--yes" ]; then
  read -r -p "Lanjutkan migrasi ke alias 'customsik' (hanya tabel face_* di atas)? [y/N] " ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ] || die "Dibatalkan"
fi
info "Migrasi database default (SQLite lokal)"
"$PY" manage.py migrate --database=default
info "Migrasi customsik (face_registry, face_events)"
"$PY" manage.py migrate --database=customsik face_events
"$PY" manage.py face_setup_roles
info "Selesai. Buat akun: .venv/bin/python manage.py createsuperuser"
