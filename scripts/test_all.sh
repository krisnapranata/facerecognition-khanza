#!/usr/bin/env bash
# Pemeriksaan & test suite. Tidak butuh kamera fisik, MySQL, maupun Khanza (memakai SQLite in-memory).
source "$(dirname "$0")/_common.sh"
ensure_venv
"$PY" -m pip check
export DJANGO_SETTINGS_MODULE=config.settings_test
info "Django system check"
"$PY" manage.py check
info "Memastikan tidak ada perubahan model tanpa migrasi"
"$PY" manage.py makemigrations --check --dry-run
info "Kompilasi sintaks"
"$PY" -m compileall -q ai_worker apps config tests
info "Test suite"
"$PY" manage.py test tests "$@"
