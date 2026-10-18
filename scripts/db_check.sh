#!/usr/bin/env bash
# Pemeriksaan koneksi customsik dan daftar tabel yang akan dibuat. Tidak mengubah database.
source "$(dirname "$0")/_common.sh"
verify_interpreter
require_env_file
"$PY" manage.py face_check_db
"$PY" manage.py migrate --database=customsik face_events --plan
