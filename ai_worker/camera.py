"""Pembacaan kamera (webcam laptop atau RTSP) di thread terpisah.

- Antrian frame dibatasi (frame lama dibuang), sehingga pemrosesan selalu
  memakai frame terbaru dan memori tidak bertambah.
- Koneksi ulang otomatis dengan backoff jika kamera gagal dibuka/terputus.
- URL RTSP tidak pernah ditulis utuh ke log (kredensial disamarkan).
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)

STATE_STARTING = "STARTING"
STATE_CONNECTED = "CONNECTED"
STATE_RECONNECTING = "RECONNECTING"
STATE_UNAVAILABLE = "UNAVAILABLE"
STATE_STOPPED = "STOPPED"


def mask_source(source: str) -> str:
    """Samarkan kredensial pada URL kamera, mis. rtsp://user:pass@host -> rtsp://***@host."""
    return re.sub(r"(//)([^/@]+)@", r"\1***@", str(source))


def describe_source(source: str) -> str:
    return "webcam" if str(source).isdigit() else "rtsp/ip"


def default_capture_factory(source: str, width: int, height: int):
    if str(source).isdigit():
        cap = cv2.VideoCapture(int(source), cv2.CAP_V4L2)
        if not cap.isOpened():  # fallback backend default
            cap.release()
            cap = cv2.VideoCapture(int(source))
    else:
        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


@dataclass
class CameraStatus:
    state: str
    source_type: str
    connected: bool
    last_error: str
    last_frame_age: float | None
    fps: float
    frame_width: int
    frame_height: int
    reconnect_attempts: int

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "source_type": self.source_type,
            "connected": self.connected,
            "last_error": self.last_error,
            "last_frame_age": None if self.last_frame_age is None else round(self.last_frame_age, 2),
            "fps": round(self.fps, 1),
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "reconnect_attempts": self.reconnect_attempts,
        }


class CameraStream(threading.Thread):
    def __init__(self, source: str = "0", width: int = 640, height: int = 480,
                 queue_size: int = 2, capture_factory: Callable | None = None,
                 max_backoff: float = 15.0, read_failure_limit: int = 30):
        super().__init__(name="camera-stream", daemon=True)
        self.source = str(source)
        self.width = width
        self.height = height
        self.frames: queue.Queue[tuple[float, np.ndarray]] = queue.Queue(maxsize=max(1, queue_size))
        self._capture_factory = capture_factory or default_capture_factory
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._max_backoff = max_backoff
        self._read_failure_limit = read_failure_limit
        self._state = STATE_STARTING
        self._last_error = ""
        self._last_frame_at: float | None = None
        self._fps = 0.0
        self._frame_size = (0, 0)
        self._reconnect_attempts = 0
        self._cap = None

    # --- status -----------------------------------------------------------
    def _set_state(self, state: str, error: str = "") -> None:
        with self._lock:
            self._state = state
            if error or state == STATE_CONNECTED:
                self._last_error = error

    def status(self) -> CameraStatus:
        with self._lock:
            age = None if self._last_frame_at is None else time.monotonic() - self._last_frame_at
            connected = self._state == STATE_CONNECTED and age is not None and age < 3.0
            return CameraStatus(
                state=self._state,
                source_type=describe_source(self.source),
                connected=connected,
                last_error=self._last_error,
                last_frame_age=age,
                fps=self._fps,
                frame_width=self._frame_size[0],
                frame_height=self._frame_size[1],
                reconnect_attempts=self._reconnect_attempts,
            )

    # --- frame access -----------------------------------------------------
    def read(self, timeout: float = 1.0) -> tuple[float, np.ndarray] | None:
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def _push(self, frame: np.ndarray) -> None:
        item = (time.monotonic(), frame)
        try:
            self.frames.put_nowait(item)
        except queue.Full:
            try:
                self.frames.get_nowait()  # buang frame terlama
            except queue.Empty:
                pass
            try:
                self.frames.put_nowait(item)
            except queue.Full:
                pass

    # --- lifecycle --------------------------------------------------------
    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=timeout)
        self._release()
        self._set_state(STATE_STOPPED)

    @property
    def stopped(self) -> bool:
        return self._stop_event.is_set()

    def _release(self) -> None:
        cap, self._cap = self._cap, None
        if cap is not None:
            try:
                cap.release()
            except Exception:  # pragma: no cover
                pass

    def _open(self) -> bool:
        self._release()
        try:
            cap = self._capture_factory(self.source, self.width, self.height)
        except Exception as exc:
            self._set_state(STATE_UNAVAILABLE, f"gagal membuka kamera: {type(exc).__name__}")
            return False
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            self._set_state(STATE_UNAVAILABLE, f"kamera {mask_source(self.source)} tidak dapat dibuka")
            return False
        self._cap = cap
        self._set_state(STATE_CONNECTED)
        logger.info("Kamera terhubung: %s", mask_source(self.source))
        return True

    def run(self) -> None:
        backoff = 1.0
        fps_count, fps_started = 0, time.monotonic()
        while not self._stop_event.is_set():
            if self._cap is None:
                if not self._open():
                    with self._lock:
                        self._reconnect_attempts += 1
                    logger.warning("Kamera tidak tersedia, coba lagi dalam %.0f detik", backoff)
                    self._stop_event.wait(backoff)
                    backoff = min(backoff * 2, self._max_backoff)
                    continue
                backoff = 1.0
                failures = 0
            ok, frame = self._cap.read()
            if not ok or frame is None:
                failures += 1
                if failures >= self._read_failure_limit:
                    logger.warning("Kamera terputus, mencoba koneksi ulang")
                    self._set_state(STATE_RECONNECTING, "kamera terputus / frame tidak terbaca")
                    self._release()
                else:
                    self._stop_event.wait(0.02)
                continue
            failures = 0
            now = time.monotonic()
            with self._lock:
                self._last_frame_at = now
                self._frame_size = (int(frame.shape[1]), int(frame.shape[0]))
                if self._state != STATE_CONNECTED:
                    self._state = STATE_CONNECTED
            self._push(frame)
            fps_count += 1
            if now - fps_started >= 2.0:
                with self._lock:
                    self._fps = fps_count / (now - fps_started)
                fps_count, fps_started = 0, now
        self._release()
        self._set_state(STATE_STOPPED)
