"""Status worker dan server HTTP lokal (hanya 127.0.0.1) untuk stream MJPEG.

Endpoint (semua wajib header ``X-Worker-Token``):
- ``/health``        status JSON worker
- ``/stream.mjpg``   video beranotasi (bounding box + ID track + status, tanpa nama)
- ``/snapshot.jpg``  frame terbaru tanpa anotasi, dipakai Django untuk registrasi

Token dibedakan dua cakupan: token biasa dan token yang diikat izin melihat
identitas. Hanya koneksi dengan token identitas yang menerima varian video
berlabel nama/No. RM; snapshot dan health tidak pernah memuat identitas.

Browser tidak mengakses server ini secara langsung; Django mem-proxy
stream setelah memeriksa login dan izin pengguna.
"""
from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)

BOUNDARY = "facerecframe"


class FrameBuffer:
    """Menyimpan JPEG terbaru (beranotasi dan mentah) untuk dibagikan ke klien HTTP.

    Dua varian beranotasi disimpan berdampingan: tanpa identitas dan dengan
    nama/No. RM. Klien yang tidak berwenang hanya menerima varian tanpa identitas.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._annotated: bytes | None = None
        self._annotated_identity: bytes | None = None
        self._raw: bytes | None = None
        self._seq = 0
        self._updated_at = 0.0

    def update(self, annotated: bytes, raw: bytes | None = None,
               annotated_identity: bytes | None = None) -> None:
        with self._cond:
            self._annotated = annotated
            self._annotated_identity = annotated_identity
            if raw is not None:
                self._raw = raw
            self._seq += 1
            self._updated_at = time.monotonic()
            self._cond.notify_all()

    def clear(self) -> None:
        with self._cond:
            self._annotated = None
            self._annotated_identity = None
            self._raw = None
            self._cond.notify_all()

    def wait_next(self, last_seq: int, timeout: float = 2.0,
                  with_identity: bool = False) -> tuple[int, bytes | None]:
        with self._cond:
            self._cond.wait_for(lambda: self._seq != last_seq, timeout=timeout)
            jpeg = self._annotated_identity if with_identity else self._annotated
            if with_identity and jpeg is None:
                jpeg = self._annotated  # belum ada varian identitas: jangan tampilkan apa pun
            return self._seq, jpeg

    def raw(self, max_age: float = 2.0) -> bytes | None:
        with self._cond:
            if self._raw is None or time.monotonic() - self._updated_at > max_age:
                return None
            return self._raw


class WorkerHealth:
    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict = {"started_at": time.time(), "state": "STARTING"}

    def update(self, **values) -> None:
        with self._lock:
            self._data.update(values)

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data, default=str))


def make_handler(tokens: dict[str, str], frames: FrameBuffer, health: WorkerHealth,
                 stop_event: threading.Event):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FaceWorker/1.0"

        def log_message(self, fmt, *args):  # jangan banjiri log
            logger.debug("worker-http %s", fmt % args)

        def _authorized(self) -> str | None:
            """Kembalikan cakupan token, atau None bila tidak sah."""
            supplied = self.headers.get("X-Worker-Token", "")
            for scope, expected in tokens.items():
                if expected and hmac.compare_digest(supplied, expected):
                    return scope
            return None

        def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            scope = self._authorized()
            if scope is None:
                self._send(403, b'{"ok":false,"error":"forbidden"}')
                return
            path = self.path.split("?", 1)[0]
            if path == "/health":
                self._send(200, json.dumps({"ok": True, "data": health.snapshot()}).encode())
            elif path == "/snapshot.jpg":
                raw = frames.raw()
                if raw is None:
                    self._send(503, b'{"ok":false,"error":"no_frame"}')
                else:
                    self._send(200, raw, "image/jpeg")
            elif path == "/stream.mjpg":
                self._stream(show_identity=scope == "identity")
            else:
                self._send(404, b'{"ok":false,"error":"not_found"}')

        def _stream(self, show_identity: bool = False):
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seq = -1
            try:
                while not stop_event.is_set():
                    seq, jpeg = frames.wait_next(seq, timeout=2.0, with_identity=show_identity)
                    if jpeg is None:
                        continue
                    self.wfile.write(
                        f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                    )
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

    return Handler


class WorkerHTTPServer:
    def __init__(self, host: str, port: int, token: str, frames: FrameBuffer, health: WorkerHealth,
                 identity_token: str | None = None):
        if host not in ("127.0.0.1", "localhost", "::1"):
            logger.warning("Server HTTP worker tidak di-bind ke localhost (%s); pastikan jaringan tepercaya", host)
        self._stop = threading.Event()
        tokens = {"plain": token}
        if identity_token:
            tokens["identity"] = identity_token
        self.httpd = ThreadingHTTPServer((host, port), make_handler(tokens, frames, health, self._stop))
        self.httpd.daemon_threads = True
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="worker-http", daemon=True)

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()
