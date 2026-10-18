"""Penilaian kualitas wajah sebelum pencocokan.

Wajah tidak dicocokkan jika terlalu kecil, buram, terlalu miring, sebagian
di luar bingkai, kemungkinan tertutup (skor deteksi rendah), atau
pencahayaannya tidak memadai.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import QualityConfig
from .detector import Detection

REF_CONTRAST = 40.0   # kontras (std abu-abu) acuan untuk normalisasi ketajaman
MIN_CONTRAST = 4.0    # di bawah ini wajah dianggap tanpa detail (buram/gelap total)
LIGHT_TARGET = 115.0  # kecerahan tujuan koreksi cahaya
LIGHT_CORRECT_BELOW = 90.0  # hanya wajah lebih redup dari ini yang dikoreksi

GOOD = "GOOD"
POOR = "POOR"

REASON_LABELS = {
    "TOO_SMALL": "wajah terlalu kecil / terlalu jauh",
    "BLURRY": "gambar buram",
    "TOO_DARK": "pencahayaan terlalu gelap",
    "TOO_BRIGHT": "pencahayaan terlalu terang",
    "POSE_ROLL": "kepala terlalu miring",
    "POSE_YAW": "wajah tidak menghadap kamera",
    "LOW_CONFIDENCE": "wajah mungkin tertutup / kurang jelas",
    "OUT_OF_FRAME": "wajah terpotong tepi bingkai",
}


@dataclass
class QualityResult:
    status: str
    score: float
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == GOOD

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "score": round(self.score, 3),
            "reasons": self.reasons,
            "reason_labels": [REASON_LABELS.get(r, r) for r in self.reasons],
            "metrics": {k: round(v, 3) if isinstance(v, float) else v for k, v in self.metrics.items()},
        }


def _crop(frame: np.ndarray, bbox) -> np.ndarray:
    h, w = frame.shape[:2]
    x, y, bw, bh = bbox
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(w, int(x + bw)), min(h, int(y + bh))
    if x1 <= x0 or y1 <= y0:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    return frame[y0:y1, x0:x1]


def assess(frame: np.ndarray, det: Detection, cfg: QualityConfig | None = None) -> QualityResult:
    cfg = cfg or QualityConfig()
    h, w = frame.shape[:2]
    x, y, bw, bh = det.bbox
    reasons: list[str] = []

    face_size = float(min(bw, bh))
    if face_size < cfg.min_face_size:
        reasons.append("TOO_SMALL")

    margin = 2
    if x < -margin or y < -margin or x + bw > w + margin or y + bh > h + margin:
        reasons.append("OUT_OF_FRAME")

    crop = _crop(frame, det.bbox)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    # Normalisasi ukuran agar ukuran wajah tidak memengaruhi ukuran ketajaman.
    norm = cv2.resize(gray, (112, 112), interpolation=cv2.INTER_AREA) if gray.size > 1 else gray
    raw_blur = float(cv2.Laplacian(norm, cv2.CV_64F).var()) if norm.size > 1 else 0.0
    brightness = float(norm.mean()) if norm.size else 0.0
    # Laplacian variance sebanding dengan kontras^2: wajah tajam yang redup ikut bernilai rendah
    # (terukur: cahaya separuh -> 85 menjadi 22). Normalkan ke kontras acuan agar yang dinilai
    # benar-benar ketajaman; wajah tanpa detail (std sangat kecil) tetap dianggap buram.
    contrast = float(norm.std()) if norm.size > 1 else 0.0
    blur = 0.0 if contrast < MIN_CONTRAST else min(raw_blur * (REF_CONTRAST / contrast) ** 2, 10000.0)
    if blur < cfg.min_blur_variance:
        reasons.append("BLURRY")
    if brightness < cfg.min_brightness:
        reasons.append("TOO_DARK")
    elif brightness > cfg.max_brightness:
        reasons.append("TOO_BRIGHT")

    right_eye, left_eye, nose = det.landmarks[0], det.landmarks[1], det.landmarks[2]
    dx, dy = float(left_eye[0] - right_eye[0]), float(left_eye[1] - right_eye[1])
    eye_dist = math.hypot(dx, dy)
    roll = abs(math.degrees(math.atan2(dy, dx))) if eye_dist > 0 else 90.0
    if roll > 90:
        roll = 180 - roll
    eye_mid_x = float((left_eye[0] + right_eye[0]) / 2)
    yaw_ratio = abs(float(nose[0]) - eye_mid_x) / eye_dist if eye_dist > 0 else 1.0
    if roll > cfg.max_roll_degrees:
        reasons.append("POSE_ROLL")
    if yaw_ratio > cfg.max_yaw_ratio:
        reasons.append("POSE_YAW")

    if det.score < cfg.min_detection_score:
        reasons.append("LOW_CONFIDENCE")

    # Skor 0..1 untuk memilih bingkai terbaik dalam satu track (bukan probabilitas).
    size_s = min(1.0, face_size / (cfg.min_face_size * 2.0))
    blur_s = min(1.0, blur / (cfg.min_blur_variance * 4.0))
    light_s = max(0.0, 1.0 - abs(brightness - 128.0) / 128.0)
    pose_s = max(0.0, 1.0 - yaw_ratio / max(cfg.max_yaw_ratio * 2, 1e-6)) * max(
        0.0, 1.0 - roll / max(cfg.max_roll_degrees * 2, 1e-6))
    score = 0.25 * size_s + 0.25 * blur_s + 0.15 * light_s + 0.2 * pose_s + 0.15 * min(1.0, det.score)

    return QualityResult(
        status=POOR if reasons else GOOD,
        score=float(score),
        reasons=reasons,
        metrics={
            "face_size": face_size,
            "blur_variance": blur,
            "blur_variance_raw": raw_blur,
            "contrast": contrast,
            "brightness": brightness,
            "roll_degrees": roll,
            "yaw_ratio": yaw_ratio,
            "detection_score": float(det.score),
        },
    )


def correct_lighting(frame: np.ndarray, det: Detection) -> np.ndarray:
    """Cerahkan frame dengan koreksi gamma bila wajah redup, untuk embedding yang lebih stabil.

    Wajah yang sudah cukup terang (>= LIGHT_CORRECT_BELOW) dikembalikan apa adanya, sehingga
    referensi yang didaftarkan di tempat terang tidak berubah. Dipakai identik di stasiun kamera
    USB, AI worker, dan registrasi.
    """
    crop = _crop(frame, det.bbox)
    if crop.size <= 3:
        return frame
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    mean = float(gray.mean())
    if mean >= LIGHT_CORRECT_BELOW or mean < 1.0:
        return frame
    # gamma sehingga kecerahan rata-rata wajah ~LIGHT_TARGET; dibatasi agar derau tidak berlebihan.
    gamma = math.log(LIGHT_TARGET / 255.0) / math.log(mean / 255.0)
    gamma = max(0.35, min(1.0, gamma))
    lut = np.clip(((np.arange(256) / 255.0) ** gamma) * 255.0, 0, 255).astype(np.uint8)
    return cv2.LUT(frame, lut)
