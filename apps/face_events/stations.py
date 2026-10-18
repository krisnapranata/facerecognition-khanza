"""Stasiun kamera browser (kamera USB di PC loket).

Browser di PC Windows membaca kamera USB (getUserMedia), mengirim frame JPEG kecil
ke ``POST /api/face/v1/stations/<id>/frame/``. Server menjalankan pipeline yang sama
dengan AI worker: deteksi YuNet -> tracking IoU -> kualitas -> bingkai terbaik ->
embedding SFace -> pencocokan (ingest_candidate). Frame tidak pernah disimpan.

State tracker disimpan di memori proses, sehingga endpoint ini harus dilayani oleh
SATU proses (container ``vision``: gunicorn 1 worker, banyak thread). Lihat
docs/DEPLOY_PRODUCTION.md.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.cache import cache

from ai_worker import quality as quality_mod
from ai_worker.config import QualityConfig, WorkerConfig
from ai_worker.embedding import MODEL_VERSION
from ai_worker.tracker import IoUTracker, Track
from ai_worker.worker import format_identity
from apps.face_registry.engine import get_engine

from . import services

logger = logging.getLogger(__name__)

STATION_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,40}$")
STATIONS_KEY = "face_stations"


class Station:
    def __init__(self, station_id: str):
        cfg = WorkerConfig.from_env()
        self.station_id = station_id
        self.camera_id = f"usb-{station_id}"[:64]
        self.lock = threading.Lock()
        self.tracker = IoUTracker(max_missed_seconds=cfg.track_max_missed_seconds)
        self.match_interval = cfg.match_interval_seconds
        self.min_track_age = cfg.min_track_age_seconds
        self.max_submissions = cfg.max_submissions_per_track
        self.quality_cfg = QualityConfig.from_env()
        self.last_seen = 0.0
        self._fps_count = 0
        self._fps_started = time.monotonic()
        self.fps = 0.0

    def _tick_fps(self, now: float) -> None:
        self._fps_count += 1
        if now - self._fps_started >= 2.0:
            self.fps = self._fps_count / (now - self._fps_started)
            self._fps_count, self._fps_started = 0, now

    def process(self, image, show_identity: bool = False) -> list[dict]:
        """``show_identity``: sertakan label "Nama (No. RM)" kandidat (hanya untuk pengguna berizin)."""
        engine = get_engine()
        now = time.monotonic()
        with self.lock:
            self.last_seen = now
            self._tick_fps(now)
            visible, _removed = self.tracker.update(engine.detector.detect(image), now)
            h, w = image.shape[:2]
            faces = []
            for track in visible:
                q = quality_mod.assess(image, track.detection, self.quality_cfg)
                track.last_quality = {"status": q.status, "score": q.score, "reasons": q.reasons}
                if q.ok and q.score > track.best_score:
                    track.best_score = q.score
                    track.best_aligned = engine.embedder.align(image, track.detection)
                self._maybe_match(track, now, engine)
                x, y, bw, bh = track.bbox
                faces.append({
                    "track_id": track.track_id,
                    "bbox": [round(x / w, 4), round(y / h, 4), round(bw / w, 4), round(bh / h, 4)],
                    "quality": q.status,
                    "reasons": q.as_dict()["reason_labels"],
                    "match_status": track.match_status or None,
                    "identity": track.identity if show_identity else None,
                    "event_id": track.event_id,
                })
            return faces

    def _maybe_match(self, track: Track, now: float, engine) -> None:
        if track.pending or track.best_aligned is None or track.match_status == "CANDIDATE":
            return
        if track.submissions >= self.max_submissions or track.age(now) < self.min_track_age:
            return
        if now - track.last_submit_at < self.match_interval:
            return
        aligned, score = track.best_aligned, track.best_score
        track.best_aligned, track.best_score = None, -1.0
        track.submissions += 1
        track.last_submit_at = now
        if not track.match_status:
            track.match_status = "MATCHING"
        if settings.FACE_STATION_ASYNC_MATCH:
            # Embedding + pencocokan + tulis DB (server database lain) di background agar frame
            # berikutnya tidak tertahan; hasil tampil pada frame selanjutnya.
            track.pending = True
            _match_executor.submit(self._match_job, track, aligned, score, engine)
        else:
            self._match_job(track, aligned, score, engine)

    def _match_job(self, track: Track, aligned, score: float, engine) -> None:
        from django.db import close_old_connections

        close_old_connections()
        try:
            embedding = engine.embedder.embed_aligned(aligned)
            event = services.ingest_candidate({
                "camera_id": self.camera_id, "track_id": track.track_id, "model_version": MODEL_VERSION,
                "embedding": [float(v) for v in embedding], "quality_status": "GOOD",
                "quality_score": round(score, 3),
            })
            track.match_status = event.match_status
            track.event_id = event.pk
            track.identity = format_identity(services.candidate_identity(event))
        except Exception:
            logger.exception("Pencocokan stasiun %s gagal", self.station_id)
            track.match_status = "ERROR"
        finally:
            track.pending = False
            if settings.FACE_STATION_ASYNC_MATCH:
                close_old_connections()


# Pencocokan berjalan di thread terpisah (maks. 2 bersamaan) agar respons frame tetap cepat.
_match_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="station-match")


class StationManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._stations: dict[str, Station] = {}

    def get(self, station_id: str) -> Station:
        with self._lock:
            self._cleanup()
            station = self._stations.get(station_id)
            if station is None:
                station = self._stations[station_id] = Station(station_id)
            return station

    def _cleanup(self) -> None:
        cutoff = time.monotonic() - settings.FACE_STATION_IDLE_SECONDS
        for sid in [s for s, st in self._stations.items() if st.last_seen and st.last_seen < cutoff]:
            del self._stations[sid]  # tracker & crop wajah dibuang dari memori

    def reset(self) -> None:
        with self._lock:
            self._stations.clear()


manager = StationManager()


def record_status(station: Station, faces: list[dict], user, processing_ms: float) -> None:
    stations = cache.get(STATIONS_KEY) or {}
    now = time.time()
    stations = {k: v for k, v in stations.items() if now - v.get("last_seen", 0) < 3600}
    stations[station.station_id] = {
        "station_id": station.station_id, "camera_id": station.camera_id, "last_seen": now,
        "fps": round(station.fps, 1), "faces": len(faces), "processing_ms": round(processing_ms, 1),
        "user": user.get_username(),
    }
    cache.set(STATIONS_KEY, stations, timeout=3600)


def stations_status() -> list[dict]:
    now = time.time()
    out = []
    for st in (cache.get(STATIONS_KEY) or {}).values():
        age = now - st.get("last_seen", 0)
        out.append({**st, "last_seen_seconds": round(age, 1), "online": age <= settings.FACE_WORKER_STALE_SECONDS})
    return sorted(out, key=lambda s: s["station_id"])
