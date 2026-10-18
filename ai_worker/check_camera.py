"""Pemeriksaan kamera tanpa menyimpan gambar apa pun.

    .venv/bin/python -m ai_worker.check_camera --seconds 5
"""
from __future__ import annotations

import argparse
import sys
import time

from .camera import CameraStream, mask_source
from .config import WorkerConfig
from .detector import FaceDetector
from .errors import ModelUnavailableError


def main(argv=None) -> int:
    cfg = WorkerConfig.from_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", default=cfg.camera_source)
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args(argv)

    print(f"Sumber kamera : {mask_source(args.camera)}")
    detector = None
    try:
        detector = FaceDetector(cfg.detector_model, cfg.detection_score_threshold)
        print("Model detektor: OK")
    except ModelUnavailableError as exc:
        print(f"Model detektor: TIDAK TERSEDIA ({exc}) - hanya uji kamera")

    cam = CameraStream(args.camera, cfg.camera_width, cfg.camera_height)
    cam.start()
    frames, faces_max, det_time = 0, 0, 0.0
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        item = cam.read(timeout=1.0)
        if item is None:
            continue
        frames += 1
        if detector:
            t0 = time.monotonic()
            faces_max = max(faces_max, len(detector.detect(item[1])))
            det_time += time.monotonic() - t0
    status = cam.status()
    cam.stop()
    print(f"Status        : {status.state} {status.last_error}")
    print(f"Resolusi      : {status.frame_width}x{status.frame_height}")
    print(f"Frame diproses: {frames} (~{frames / args.seconds:.1f} fps termasuk deteksi)")
    if detector and frames:
        print(f"Deteksi rata2 : {1000 * det_time / frames:.1f} ms/frame; wajah maks: {faces_max}")
    if frames == 0:
        print("GAGAL: tidak ada frame. Periksa izin grup 'video', kamera dipakai aplikasi lain, atau CAMERA_SOURCE.")
        return 1
    print("OK: kamera berfungsi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
