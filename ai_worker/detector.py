"""Detektor wajah YuNet (OpenCV Zoo, lisensi MIT) via cv2.FaceDetectorYN (CPU)."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .errors import ModelUnavailableError

DETECTOR_VERSION = "opencv-zoo/yunet-2023mar"


@dataclass
class Detection:
    """Satu wajah: bbox (x, y, w, h), 5 landmark, skor deteksi, dan baris mentah YuNet."""

    bbox: tuple[float, float, float, float]
    landmarks: np.ndarray  # (5, 2): mata kanan, mata kiri, hidung, mulut kanan, mulut kiri
    score: float
    raw: np.ndarray  # (15,) dipakai FaceRecognizerSF.alignCrop

    @classmethod
    def from_row(cls, row: np.ndarray) -> "Detection":
        row = np.asarray(row, dtype=np.float32).reshape(-1)
        return cls(
            bbox=(float(row[0]), float(row[1]), float(row[2]), float(row[3])),
            landmarks=row[4:14].reshape(5, 2).copy(),
            score=float(row[14]),
            raw=row[:15].copy(),
        )

    @property
    def area(self) -> float:
        return max(0.0, self.bbox[2]) * max(0.0, self.bbox[3])


class FaceDetector:
    def __init__(self, model_path: str | Path, score_threshold: float = 0.8,
                 nms_threshold: float = 0.3, top_k: int = 50):
        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise ModelUnavailableError(f"model detektor tidak ditemukan: {self.model_path.name}")
        try:
            self._detector = cv2.FaceDetectorYN.create(
                str(self.model_path), "", (320, 320), score_threshold, nms_threshold, top_k
            )
        except cv2.error as exc:
            raise ModelUnavailableError(f"model detektor gagal dimuat: {exc}") from exc
        self._lock = threading.Lock()
        self.version = DETECTOR_VERSION

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if frame is None or frame.size == 0:
            return []
        h, w = frame.shape[:2]
        with self._lock:  # instance OpenCV DNN tidak thread-safe
            self._detector.setInputSize((w, h))
            _, faces = self._detector.detect(frame)
        if faces is None:
            return []
        return [Detection.from_row(row) for row in faces]
