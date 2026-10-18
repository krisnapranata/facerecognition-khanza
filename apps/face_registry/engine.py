"""Pemuatan model AI di proses Django (lazy, thread-safe) untuk registrasi wajah."""
from __future__ import annotations

import threading

from django.conf import settings

from ai_worker.config import resolve_path
from ai_worker.detector import FaceDetector
from ai_worker.embedding import FaceEmbedder
from ai_worker.errors import ModelUnavailableError

_lock = threading.Lock()
_engine: "FaceEngine | None" = None
_last_error: str = ""


class FaceEngine:
    def __init__(self, detector, embedder):
        self.detector = detector
        self.embedder = embedder


def get_engine() -> FaceEngine:
    """Kembalikan engine atau lempar ModelUnavailableError (tanpa fallback ke deteksi saja)."""
    global _engine, _last_error
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is None:
            try:
                detector = FaceDetector(resolve_path(settings.FACE_DETECTOR_MODEL))
                embedder = FaceEmbedder(resolve_path(settings.FACE_EMBEDDING_MODEL),
                                        settings.FACE_EMBEDDING_BACKEND)
            except ModelUnavailableError as exc:
                _last_error = str(exc)
                raise
            _engine = FaceEngine(detector, embedder)
            _last_error = ""
    return _engine


def model_status() -> dict:
    detector_ok = resolve_path(settings.FACE_DETECTOR_MODEL).is_file()
    embedder_ok = resolve_path(settings.FACE_EMBEDDING_MODEL).is_file()
    return {
        "detector_file": "OK" if detector_ok else "MISSING",
        "embedding_file": "OK" if embedder_ok else "MISSING",
        "loaded": _engine is not None,
        "available": detector_ok and embedder_ok and not _last_error,
        "error": _last_error,
        "backend": settings.FACE_EMBEDDING_BACKEND,
    }


def set_engine_for_tests(engine: FaceEngine | None) -> None:
    global _engine, _last_error
    _engine = engine
    _last_error = ""
