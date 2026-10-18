"""Konfigurasi AI worker dari environment / file .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:  # python-dotenv opsional saat pengujian
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=False)
except ImportError:  # pragma: no cover
    pass


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else BASE_DIR / path


@dataclass
class QualityConfig:
    min_face_size: int = 64
    min_blur_variance: float = 40.0
    min_brightness: float = 50.0
    max_brightness: float = 210.0
    max_roll_degrees: float = 25.0
    max_yaw_ratio: float = 0.35
    min_detection_score: float = 0.85

    @classmethod
    def from_env(cls, prefix: str = "FACE_") -> "QualityConfig":
        return cls(
            min_face_size=env_int(f"{prefix}MIN_FACE_SIZE", cls.min_face_size),
            min_blur_variance=env_float(f"{prefix}MIN_BLUR_VARIANCE", cls.min_blur_variance),
            min_brightness=env_float(f"{prefix}MIN_BRIGHTNESS", cls.min_brightness),
            max_brightness=env_float(f"{prefix}MAX_BRIGHTNESS", cls.max_brightness),
            max_roll_degrees=env_float(f"{prefix}MAX_ROLL_DEGREES", cls.max_roll_degrees),
            max_yaw_ratio=env_float(f"{prefix}MAX_YAW_RATIO", cls.max_yaw_ratio),
            min_detection_score=env_float(f"{prefix}MIN_DETECTION_SCORE", cls.min_detection_score),
        )


@dataclass
class WorkerConfig:
    camera_source: str = "0"
    camera_id: str = "laptop-0"
    camera_width: int = 640
    camera_height: int = 480
    detector_model: Path = field(default_factory=lambda: resolve_path("models/face_detection_yunet_2023mar.onnx"))
    embedding_model: Path = field(default_factory=lambda: resolve_path("models/face_recognition_sface_2021dec.onnx"))
    embedding_backend: str = "onnxruntime"
    detection_score_threshold: float = 0.8
    max_inference_fps: float = 10.0
    match_interval_seconds: float = 2.0
    min_track_age_seconds: float = 0.4
    max_submissions_per_track: int = 5
    track_max_missed_seconds: float = 1.5
    heartbeat_interval_seconds: float = 2.0
    django_url: str = "http://127.0.0.1:8020"
    worker_secret: str = ""
    http_host: str = "127.0.0.1"
    http_port: int = 8765
    stream_fps: float = 10.0
    stream_jpeg_quality: int = 75
    quality: QualityConfig = field(default_factory=QualityConfig)

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        return cls(
            camera_source=env_str("CAMERA_SOURCE", "0"),
            camera_id=env_str("CAMERA_ID", "laptop-0"),
            camera_width=env_int("CAMERA_WIDTH", 640),
            camera_height=env_int("CAMERA_HEIGHT", 480),
            detector_model=resolve_path(env_str("FACE_DETECTOR_MODEL", "models/face_detection_yunet_2023mar.onnx")),
            embedding_model=resolve_path(env_str("FACE_EMBEDDING_MODEL", "models/face_recognition_sface_2021dec.onnx")),
            embedding_backend=env_str("FACE_EMBEDDING_BACKEND", "onnxruntime"),
            detection_score_threshold=env_float("FACE_DETECTION_SCORE_THRESHOLD", 0.8),
            max_inference_fps=env_float("WORKER_MAX_INFERENCE_FPS", 10.0),
            match_interval_seconds=env_float("WORKER_MATCH_INTERVAL_SECONDS", 2.0),
            min_track_age_seconds=env_float("WORKER_MIN_TRACK_AGE_SECONDS", 0.4),
            max_submissions_per_track=env_int("WORKER_MAX_SUBMISSIONS_PER_TRACK", 5),
            track_max_missed_seconds=env_float("WORKER_TRACK_MAX_MISSED_SECONDS", 1.5),
            heartbeat_interval_seconds=env_float("WORKER_HEARTBEAT_SECONDS", 2.0),
            django_url=env_str("FACE_DJANGO_URL", "http://127.0.0.1:8020").rstrip("/"),
            worker_secret=env_str("FACE_WORKER_SECRET", ""),
            http_host=env_str("WORKER_HTTP_HOST", "127.0.0.1"),
            http_port=env_int("WORKER_HTTP_PORT", 8765),
            stream_fps=env_float("WORKER_STREAM_FPS", 10.0),
            stream_jpeg_quality=env_int("WORKER_STREAM_JPEG_QUALITY", 75),
            quality=QualityConfig.from_env(),
        )
