# Fungsi bersama untuk script proyek. Di-source oleh script lain.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PY="$VENV_DIR/bin/python"
cd "$PROJECT_DIR"

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

require_system_python() {
  command -v python3 >/dev/null 2>&1 || die "python3 tidak ditemukan. Pasang: sudo apt install python3 python3-venv"
  python3 -c "import venv, ensurepip" 2>/dev/null || die "modul venv/ensurepip tidak ada. Pasang: sudo apt install python3-venv"
}

ensure_venv() {
  require_system_python
  if [ ! -x "$PY" ]; then
    info "Membuat virtual environment di $VENV_DIR"
    python3 -m venv "$VENV_DIR"
  fi
  verify_interpreter
}

verify_interpreter() {
  [ -x "$PY" ] || die "venv belum ada. Jalankan scripts/setup_ubuntu.sh"
  local exe prefix
  exe="$("$PY" -c 'import sys; print(sys.executable)')"
  prefix="$("$PY" -c 'import sys; print(sys.prefix)')"
  case "$exe" in
    "$VENV_DIR"/*) ;;
    *) die "Interpreter bukan dari venv proyek: $exe" ;;
  esac
  "$PY" -c 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)' || die "Interpreter tidak berjalan di dalam venv"
  info "Interpreter: $exe ($("$PY" --version 2>&1)), prefix=$prefix"
}

require_env_file() {
  [ -f "$PROJECT_DIR/.env" ] || die ".env belum ada. Jalankan scripts/setup_ubuntu.sh atau salin .env.example"
}
