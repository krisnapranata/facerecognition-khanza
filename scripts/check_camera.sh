#!/usr/bin/env bash
# Uji kamera dengan OpenCV tanpa menyimpan gambar. Hentikan AI worker dulu jika sedang memakai kamera.
source "$(dirname "$0")/_common.sh"
verify_interpreter
info "Perangkat video:"
ls -l /dev/video* 2>/dev/null || echo "Tidak ada /dev/video* (kamera tidak terdeteksi kernel)"
id -nG | grep -qw video || echo "Catatan: user tidak di grup 'video' (ACL desktop biasanya tetap memberi akses)."
if command -v fuser >/dev/null 2>&1; then
  fuser /dev/video0 2>/dev/null && echo "Peringatan: /dev/video0 sedang dipakai proses lain (PID di atas)." || true
fi
"$PY" -m ai_worker.check_camera "$@"
