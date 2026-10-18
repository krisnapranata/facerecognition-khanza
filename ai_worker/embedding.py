"""Face embedding SFace (OpenCV Zoo, lisensi Apache-2.0).

Penyelarasan wajah memakai cv2.FaceRecognizerSF.alignCrop. Inferensi memakai
ONNX Runtime CPU (default) atau OpenCV DNN; keduanya menghasilkan embedding
yang identik secara numerik (diuji di tests/test_ai_models.py), sehingga
versi model tidak bergantung pada backend.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import cv2
import numpy as np

from .errors import ModelUnavailableError

MODEL_VERSION = "opencv-zoo/sface-2021dec/128"
EMBEDDING_DIM = 128
BACKENDS = ("onnxruntime", "opencv")


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vec))
    if not np.isfinite(norm) or norm == 0.0:
        raise ValueError("embedding tidak dapat dinormalisasi")
    return vec / norm


class FaceEmbedder:
    def __init__(self, model_path: str | Path, backend: str = "onnxruntime", threads: int | None = None):
        self.model_path = Path(model_path)
        if not self.model_path.is_file():
            raise ModelUnavailableError(f"model embedding tidak ditemukan: {self.model_path.name}")
        if backend not in BACKENDS:
            raise ModelUnavailableError(f"backend embedding tidak dikenal: {backend}")
        try:
            # Recognizer OpenCV selalu dipakai untuk alignCrop.
            self._recognizer = cv2.FaceRecognizerSF.create(str(self.model_path), "")
        except cv2.error as exc:
            raise ModelUnavailableError(f"model embedding gagal dimuat: {exc}") from exc
        self._lock = threading.Lock()
        self._session = None
        self.backend = backend
        if backend == "onnxruntime":
            try:
                import onnxruntime as ort
            except ImportError as exc:  # pragma: no cover
                raise ModelUnavailableError("onnxruntime tidak terpasang") from exc
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            opts.intra_op_num_threads = threads or max(1, min(4, os.cpu_count() or 1))
            self._session = ort.InferenceSession(
                str(self.model_path), sess_options=opts, providers=["CPUExecutionProvider"]
            )
            self._input_name = self._session.get_inputs()[0].name
        self.version = MODEL_VERSION
        self.dim = EMBEDDING_DIM

    def align(self, frame: np.ndarray, detection) -> np.ndarray:
        raw = detection.raw if hasattr(detection, "raw") else np.asarray(detection, dtype=np.float32)
        with self._lock:
            return self._recognizer.alignCrop(frame, raw.reshape(1, -1))

    def embed_aligned(self, aligned: np.ndarray) -> np.ndarray:
        if aligned is None or aligned.shape[:2] != (112, 112):
            aligned = cv2.resize(aligned, (112, 112))
        if self._session is not None:
            blob = cv2.dnn.blobFromImage(aligned, 1.0, (112, 112), (0, 0, 0), swapRB=True, crop=False)
            feat = self._session.run(None, {self._input_name: blob})[0]
        else:
            with self._lock:
                feat = self._recognizer.feature(aligned)
        vec = l2_normalize(feat)
        if vec.shape[0] != self.dim:
            raise ModelUnavailableError(f"dimensi embedding tidak sesuai: {vec.shape[0]}")
        return vec

    def embed(self, frame: np.ndarray, detection) -> np.ndarray:
        return self.embed_aligned(self.align(frame, detection))
