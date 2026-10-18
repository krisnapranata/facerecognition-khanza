"""AI worker utama.

Jalankan dengan interpreter venv proyek::

    .venv/bin/python -m ai_worker.worker

Alur: kamera -> deteksi YuNet -> tracking IoU -> penilaian kualitas ->
pemilihan bingkai terbaik per track -> embedding SFace -> kirim ke Django
(POST /api/face/v1/candidates/) yang melakukan pencocokan terhadap
referensi terenkripsi. Worker tidak menyimpan foto atau video ke disk.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

from . import quality as quality_mod
from .camera import CameraStream, mask_source
from .client import DjangoClient
from .config import WorkerConfig
from .detector import DETECTOR_VERSION, FaceDetector
from .embedding import MODEL_VERSION, FaceEmbedder
from .errors import ModelUnavailableError
from .health import FrameBuffer, WorkerHealth, WorkerHTTPServer
from .signing import stream_token
from .tracker import IoUTracker, Track

logger = logging.getLogger("ai_worker")

STATUS_COLORS = {  # BGR
    "": (200, 200, 200),
    "POOR": (0, 165, 255),
    "MATCHING": (255, 160, 0),
    "CANDIDATE": (0, 200, 0),
    "NEEDS_REVIEW": (0, 220, 255),
    "NO_MATCH": (60, 60, 230),
    "ERROR": (128, 0, 128),
}

IDENTITY_MAX_CHARS = 34


def format_identity(candidate: dict | None) -> str | None:
    """Label "Nama (No. RM)" dari kandidat, atau None bila tidak ada."""
    if not candidate:
        return None
    name = str(candidate.get("patient_name") or "").strip()
    rm = str(candidate.get("no_rkm_medis") or "").strip()
    if not name and not rm:
        return None
    label = f"{name} ({rm})" if name and rm else (name or rm)
    return label if len(label) <= IDENTITY_MAX_CHARS else label[:IDENTITY_MAX_CHARS - 1] + "…"


class FaceWorker:
    def __init__(self, cfg: WorkerConfig, camera: CameraStream | None = None,
                 client: DjangoClient | None = None, detector=None, embedder=None,
                 enable_http: bool = True):
        self.cfg = cfg
        self.health = WorkerHealth()
        self.frames = FrameBuffer()
        self.stop_event = threading.Event()
        self.camera = camera or CameraStream(cfg.camera_source, cfg.camera_width, cfg.camera_height)
        self.client = client or DjangoClient(cfg.django_url, cfg.worker_secret)
        self.tracker = IoUTracker(max_missed_seconds=cfg.track_max_missed_seconds)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="worker-io")
        self.model_errors: list[str] = []
        self.detector = detector
        self.embedder = embedder
        if self.detector is None:
            try:
                self.detector = FaceDetector(cfg.detector_model, cfg.detection_score_threshold)
            except ModelUnavailableError as exc:
                self.model_errors.append(str(exc))
        if self.embedder is None:
            try:
                self.embedder = FaceEmbedder(cfg.embedding_model, cfg.embedding_backend)
            except ModelUnavailableError as exc:
                self.model_errors.append(str(exc))
        self.http = None
        if enable_http:
            if not cfg.worker_secret:
                logger.warning("FACE_WORKER_SECRET kosong; server HTTP worker dinonaktifkan")
            else:
                self.http = WorkerHTTPServer(cfg.http_host, cfg.http_port, stream_token(cfg.worker_secret),
                                             self.frames, self.health,
                                             identity_token=stream_token(cfg.worker_secret, show_identity=True))
        self._inference_fps = 0.0
        self._last_heartbeat = 0.0
        self._last_stream = 0.0
        self._heartbeat_inflight = False
        self._django_ok: bool | None = None
        self._last_django_error = ""

    # --- status -----------------------------------------------------------
    @property
    def model_status(self) -> dict:
        return {
            "detector": "READY" if self.detector else "UNAVAILABLE",
            "embedder": "READY" if self.embedder else "UNAVAILABLE",
            "detector_version": DETECTOR_VERSION,
            "model_version": MODEL_VERSION,
            "embedding_backend": getattr(self.embedder, "backend", None),
            "errors": self.model_errors,
        }

    def _status_payload(self, tracks: list[Track], frame_shape) -> dict:
        h, w = (frame_shape[:2] if frame_shape is not None else (0, 0))
        return {
            "camera_id": self.cfg.camera_id,
            "camera": self.camera.status().as_dict(),
            "model": self.model_status,
            "inference_fps": round(self._inference_fps, 1),
            "faces": len(tracks),
            "tracks": [
                {
                    "track_id": t.track_id,
                    "bbox": [round(t.bbox[0] / w, 4), round(t.bbox[1] / h, 4),
                             round(t.bbox[2] / w, 4), round(t.bbox[3] / h, 4)] if w and h else None,
                    "quality": (t.last_quality or {}).get("status"),
                    "match_status": t.match_status or None,
                }
                for t in tracks
            ],
            "sent_at": time.time(),
        }

    # --- Django I/O (di thread executor agar loop kamera tidak terblokir) --
    def _send_heartbeat(self, payload: dict) -> None:
        try:
            result = self.client.heartbeat(payload)
            self._django_ok = bool(result.get("ok"))
            self._last_django_error = "" if self._django_ok else str(result.get("error"))
        except Exception as exc:
            self._django_ok = False
            self._last_django_error = type(exc).__name__
            logger.warning("Heartbeat ke Django gagal: %s", type(exc).__name__)
        finally:
            self._heartbeat_inflight = False

    def _submit(self, track: Track, embedding: np.ndarray, quality: dict) -> None:
        try:
            result = self.client.submit_candidate(self.cfg.camera_id, track.track_id, embedding,
                                                  MODEL_VERSION, quality)
            if result.get("ok"):
                data = result.get("data") or {}
                track.match_status = data.get("match_status", "ERROR")
                track.event_id = data.get("event_id")
                track.identity = format_identity(data.get("candidate"))
            else:
                track.match_status = "ERROR"
        except Exception as exc:
            track.match_status = "ERROR"
            logger.warning("Pengiriman kandidat gagal: %s", type(exc).__name__)
        finally:
            track.pending = False

    # --- pemrosesan frame ---------------------------------------------------
    def process_frame(self, frame: np.ndarray, now: float) -> list[Track]:
        if self.detector is None:
            return []
        detections = self.detector.detect(frame)
        visible, _removed = self.tracker.update(detections, now)
        for track in visible:
            q = quality_mod.assess(frame, track.detection, self.cfg.quality)
            track.last_quality = {"status": q.status, "score": q.score, "reasons": q.reasons}
            if q.ok and self.embedder is not None and q.score > track.best_score:
                track.best_score = q.score
                lit = quality_mod.correct_lighting(frame, track.detection)  # cerahkan wajah redup
                track.best_aligned = self.embedder.align(lit, track.detection)
            self._maybe_submit(track, now)
        return visible

    def _maybe_submit(self, track: Track, now: float) -> None:
        if self.embedder is None or track.pending or track.best_aligned is None:
            return
        if track.match_status == "CANDIDATE":
            return  # kandidat kuat sudah didapat; tunggu verifikasi petugas
        if track.submissions >= self.cfg.max_submissions_per_track:
            return
        if track.age(now) < self.cfg.min_track_age_seconds:
            return
        if now - track.last_submit_at < self.cfg.match_interval_seconds:
            return
        aligned, score = track.best_aligned, track.best_score
        track.best_aligned, track.best_score = None, -1.0  # bingkai berikutnya harus baru
        embedding = self.embedder.embed_aligned(aligned)
        track.pending = True
        track.submissions += 1
        track.last_submit_at = now
        if not track.match_status:
            track.match_status = "MATCHING"
        self.executor.submit(self._submit, track, embedding, {"status": "GOOD", "score": round(score, 3)})

    def annotate(self, frame: np.ndarray, tracks: list[Track], with_identity: bool = False) -> np.ndarray:
        out = frame.copy()
        has_identity = with_identity and any(t.identity for t in tracks)
        for t in tracks:
            x, y, w, h = (int(v) for v in t.bbox)
            label_status = t.match_status
            if not label_status and t.last_quality and t.last_quality.get("status") == "POOR":
                label_status = "POOR"
            color = STATUS_COLORS.get(label_status, STATUS_COLORS[""])
            cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
            # Warna kotak tetap menandai status; teksnya nama + No. RM bila pemohon berwenang.
            if has_identity and t.identity:
                text = t.identity
            else:
                text = f"T{t.track_id.split('-')[-1]} {label_status or 'TRACKING'}"
            cv2.putText(out, text, (x, max(14, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        banner = f"{self.cfg.camera_id}  wajah:{len(tracks)}  inferensi:{self._inference_fps:.1f} fps"
        cv2.putText(out, banner, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
        if self.model_errors:
            cv2.putText(out, "MODEL TIDAK TERSEDIA - pencocokan nonaktif", (8, out.shape[0] - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)
        return out

    def _publish_stream(self, frame: np.ndarray, tracks: list[Track], now: float) -> None:
        if self.http is None or now - self._last_stream < 1.0 / max(self.cfg.stream_fps, 0.5):
            return
        self._last_stream = now
        params = [int(cv2.IMWRITE_JPEG_QUALITY), self.cfg.stream_jpeg_quality]
        ok_a, annotated = cv2.imencode(".jpg", self.annotate(frame, tracks), params)
        if not ok_a:
            return
        # Varian berlabel identitas hanya dibuat bila ada track yang punya nama,
        # sehingga tidak ada biaya encode tambahan saat tidak ada yang perlu ditampilkan.
        with_identity = any(t.identity for t in tracks)
        ok_i, annotated_identity = (cv2.imencode(".jpg", self.annotate(frame, tracks, with_identity=True), params)
                                   if with_identity else (False, None))
        ok_r, raw = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        self.frames.update(annotated.tobytes(), raw.tobytes() if ok_r else None,
                           annotated_identity.tobytes() if ok_i and annotated_identity is not None else None)

    def _maybe_heartbeat(self, tracks: list[Track], frame_shape, now: float) -> None:
        payload = self._status_payload(tracks, frame_shape)
        self.health.update(state="RUNNING", django_reachable=self._django_ok,
                           django_error=self._last_django_error, **payload)
        if self._heartbeat_inflight or now - self._last_heartbeat < self.cfg.heartbeat_interval_seconds:
            return
        self._last_heartbeat = now
        self._heartbeat_inflight = True
        self.executor.submit(self._send_heartbeat, payload)

    # --- lifecycle --------------------------------------------------------
    def stop(self, *_args) -> None:
        self.stop_event.set()

    def run(self, max_seconds: float | None = None) -> None:
        logger.info("Worker mulai: kamera=%s id=%s model=%s", mask_source(self.cfg.camera_source),
                    self.cfg.camera_id, self.model_status)
        if self.model_errors:
            logger.error("Model tidak tersedia: %s. Pencocokan identitas dinonaktifkan.", self.model_errors)
        self.camera.start()
        if self.http:
            self.http.start()
            logger.info("Server HTTP worker di http://%s:%s (token wajib)", self.cfg.http_host, self.http.port)
        started = time.monotonic()
        min_interval = 1.0 / max(self.cfg.max_inference_fps, 0.5)
        frames_done, fps_started = 0, time.monotonic()
        last_tracks: list[Track] = []
        last_shape = None
        try:
            while not self.stop_event.is_set():
                if max_seconds is not None and time.monotonic() - started > max_seconds:
                    break
                item = self.camera.read(timeout=0.5)
                now = time.monotonic()
                if item is None:
                    if not self.camera.status().connected:
                        last_tracks = []
                        self.tracker.update([], now)
                        self.frames.clear()
                    self._maybe_heartbeat(last_tracks, last_shape, now)
                    continue
                _, frame = item
                last_shape = frame.shape
                t0 = time.monotonic()
                last_tracks = self.process_frame(frame, now)
                frames_done += 1
                if now - fps_started >= 2.0:
                    self._inference_fps = frames_done / (now - fps_started)
                    frames_done, fps_started = 0, now
                self._publish_stream(frame, last_tracks, now)
                self._maybe_heartbeat(last_tracks, last_shape, now)
                spent = time.monotonic() - t0
                if spent < min_interval:
                    self.stop_event.wait(min_interval - spent)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        logger.info("Worker berhenti")
        self.stop_event.set()
        self.camera.stop()
        self.health.update(state="STOPPED")
        try:
            payload = self._status_payload([], None)
            payload["stopping"] = True
            self.client.heartbeat(payload)
        except Exception:
            pass
        if self.http:
            self.http.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI worker pengenalan wajah SIMRS")
    parser.add_argument("--camera", help="override CAMERA_SOURCE (index webcam atau URL RTSP)")
    parser.add_argument("--max-seconds", type=float, default=None, help="berhenti otomatis (uji)")
    parser.add_argument("--no-http", action="store_true", help="nonaktifkan server stream lokal")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = WorkerConfig.from_env()
    if args.camera is not None:
        cfg.camera_source = args.camera
    if not cfg.worker_secret:
        logger.error("FACE_WORKER_SECRET belum diatur di .env; worker tidak dapat mengirim hasil ke Django")
        return 2
    worker = FaceWorker(cfg, enable_http=not args.no_http)
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    worker.run(max_seconds=args.max_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
