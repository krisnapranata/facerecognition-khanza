#!/usr/bin/env bash
# Unduh model OpenCV Zoo dan verifikasi SHA-256. YuNet: MIT. SFace: Apache-2.0.
source "$(dirname "$0")/_common.sh"
mkdir -p models
BASE="https://github.com/opencv/opencv_zoo/raw/main/models"
download() {
  local path="$1" file="$2" sha="$3"
  if [ -f "models/$file" ] && echo "$sha  models/$file" | sha256sum -c --status; then
    echo "OK   $file (sudah ada)"; return
  fi
  echo "GET  $file"
  curl -fL --retry 3 -o "models/$file.part" "$BASE/$path/$file" || die "gagal mengunduh $file (cek koneksi internet)"
  echo "$sha  models/$file.part" | sha256sum -c --status || { rm -f "models/$file.part"; die "checksum $file tidak cocok"; }
  mv "models/$file.part" "models/$file"
  echo "OK   $file"
}
download face_detection_yunet face_detection_yunet_2023mar.onnx 8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4
download face_recognition_sface face_recognition_sface_2021dec.onnx 0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79
