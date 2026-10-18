"""Kamera (tanpa perangkat fisik), tracker, kualitas, server HTTP worker, alur worker end-to-end palsu."""
import threading
import time
from unittest import mock

import numpy as np
import requests
from django.test import SimpleTestCase

from ai_worker import quality
from ai_worker.camera import (STATE_CONNECTED, STATE_RECONNECTING, STATE_STOPPED, STATE_UNAVAILABLE, CameraStream,
                              mask_source)
from ai_worker.config import QualityConfig, WorkerConfig
from ai_worker.errors import ModelUnavailableError
from ai_worker.health import BOUNDARY, FrameBuffer, WorkerHealth, WorkerHTTPServer
from ai_worker.signing import sign, signed_headers, stream_token, verify
from ai_worker.tracker import IoUTracker, iou
from ai_worker.worker import FaceWorker, format_identity

from .helpers import FakeDetector, FakeEmbedder, fake_detection


class FakeCapture:
    def __init__(self, frames=None, fail_after=None, opened=True):
        self.frames = frames
        self.count = 0
        self.fail_after = fail_after
        self.opened = opened
        self.released = False

    def isOpened(self):  # noqa: N802
        return self.opened

    def read(self):
        self.count += 1
        if self.fail_after is not None and self.count > self.fail_after:
            return False, None
        time.sleep(0.005)
        return True, np.full((480, 640, 3), 120, dtype=np.uint8)

    def release(self):
        self.released = True


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class CameraTests(SimpleTestCase):
    def test_camera_unavailable(self):
        cam = CameraStream("0", capture_factory=lambda *a: FakeCapture(opened=False), max_backoff=0.05)
        cam.start()
        self.assertTrue(wait_until(lambda: cam.status().reconnect_attempts >= 2))
        self.assertEqual(cam.status().state, STATE_UNAVAILABLE)
        self.assertFalse(cam.status().connected)
        cam.stop()
        self.assertEqual(cam.status().state, STATE_STOPPED)
        self.assertFalse(cam.is_alive())

    def test_frames_and_bounded_queue(self):
        cam = CameraStream("0", queue_size=2, capture_factory=lambda *a: FakeCapture())
        cam.start()
        self.assertTrue(wait_until(lambda: cam.status().connected))
        time.sleep(0.2)
        self.assertLessEqual(cam.frames.qsize(), 2)
        item = cam.read(timeout=1)
        self.assertEqual(item[1].shape, (480, 640, 3))
        self.assertEqual(cam.status().state, STATE_CONNECTED)
        cam.stop()

    def test_reconnect_after_disconnect(self):
        captures = []

        def factory(*a):
            c = FakeCapture(fail_after=5 if not captures else None)
            captures.append(c)
            return c

        cam = CameraStream("0", capture_factory=factory, read_failure_limit=3, max_backoff=0.05)
        cam.start()
        self.assertTrue(wait_until(lambda: len(captures) >= 2))
        self.assertTrue(captures[0].released)
        self.assertTrue(wait_until(lambda: cam.status().connected))
        cam.stop()

    def test_factory_exception_handled(self):
        def boom(*a):
            raise RuntimeError("device busy")

        cam = CameraStream("0", capture_factory=boom, max_backoff=0.05)
        cam.start()
        self.assertTrue(wait_until(lambda: cam.status().state == STATE_UNAVAILABLE))
        cam.stop()

    def test_rtsp_credentials_masked(self):
        self.assertEqual(mask_source("rtsp://admin:rahasia@10.0.0.5:554/s1"), "rtsp://***@10.0.0.5:554/s1")
        cam = CameraStream("rtsp://admin:rahasia@10.0.0.5/s", capture_factory=lambda *a: FakeCapture(opened=False))
        cam._open()
        self.assertNotIn("rahasia", cam.status().last_error)
        self.assertEqual(cam.status().source_type, "rtsp/ip")
        self.assertNotIn("rahasia", str(cam.status().as_dict()))


class TrackerTests(SimpleTestCase):
    def test_iou(self):
        self.assertAlmostEqual(iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)
        self.assertEqual(iou((0, 0, 10, 10), (20, 20, 5, 5)), 0.0)

    def test_track_persistence_and_removal(self):
        tr = IoUTracker(max_missed_seconds=0.5, session_id="s")
        visible, _ = tr.update([fake_detection(x=100)], now=0.0)
        tid = visible[0].track_id
        visible, _ = tr.update([fake_detection(x=110)], now=0.1)
        self.assertEqual(visible[0].track_id, tid)
        visible, _ = tr.update([fake_detection(x=100), fake_detection(x=420, y=20, size=120)], now=0.2)
        self.assertEqual(len(visible), 2)
        _, removed = tr.update([], now=1.0)
        self.assertEqual({t.track_id for t in removed}, {tid, f"s-2"})
        self.assertEqual(tr.tracks, {})


class QualityTests(SimpleTestCase):
    def setUp(self):
        rng = np.random.RandomState(0)
        self.frame = (rng.rand(480, 640, 3) * 120 + 60).astype(np.uint8)

    def test_good(self):
        q = quality.assess(self.frame, fake_detection(), QualityConfig())
        self.assertTrue(q.ok, q.reasons)

    def test_too_small_dark_blurry_tilted(self):
        self.assertIn("TOO_SMALL", quality.assess(self.frame, fake_detection(size=30)).reasons)
        dark = np.full_like(self.frame, 10)
        reasons = quality.assess(dark, fake_detection()).reasons
        self.assertIn("TOO_DARK", reasons)
        self.assertIn("BLURRY", reasons)
        det = fake_detection()
        det.landmarks[1][1] += 120  # mata kiri jauh lebih rendah -> miring
        self.assertIn("POSE_ROLL", quality.assess(self.frame, det).reasons)
        det = fake_detection()
        det.landmarks[2][0] += 80  # hidung jauh ke samping -> menoleh
        self.assertIn("POSE_YAW", quality.assess(self.frame, det).reasons)
        self.assertIn("LOW_CONFIDENCE", quality.assess(self.frame, fake_detection(score=0.6)).reasons)
        self.assertIn("OUT_OF_FRAME", quality.assess(self.frame, fake_detection(x=560)).reasons)


class SigningTests(SimpleTestCase):
    def test_sign_verify(self):
        h = signed_headers("s", "POST", "/p/", b"{}")
        ok, code = verify("s", h["X-Face-Timestamp"], h["X-Face-Nonce"], h["X-Face-Signature"], "POST", "/p/", b"{}")
        self.assertTrue(ok, code)
        ok, code = verify("s", h["X-Face-Timestamp"], h["X-Face-Nonce"], h["X-Face-Signature"], "POST", "/q/", b"{}")
        self.assertFalse(ok)
        self.assertEqual(verify("", "1", "n" * 16, "x", "POST", "/", b"")[1], "SERVICE_AUTH_NOT_CONFIGURED")
        with self.assertRaises(ValueError):
            sign("", "1", "n", "POST", "/", b"")


class WorkerHttpTests(SimpleTestCase):
    def test_token_required_and_snapshot(self):
        frames, health = FrameBuffer(), WorkerHealth()
        server = WorkerHTTPServer("127.0.0.1", 0, stream_token("s"), frames, health)
        server.start()
        try:
            base = f"http://127.0.0.1:{server.port}"
            self.assertEqual(requests.get(f"{base}/health", timeout=2).status_code, 403)
            hdr = {"X-Worker-Token": stream_token("s")}
            self.assertEqual(requests.get(f"{base}/snapshot.jpg", headers=hdr, timeout=2).status_code, 503)
            frames.update(b"\xff\xd8annotated", b"\xff\xd8raw")
            res = requests.get(f"{base}/snapshot.jpg", headers=hdr, timeout=2)
            self.assertEqual(res.content, b"\xff\xd8raw")
            self.assertTrue(requests.get(f"{base}/health", headers=hdr, timeout=2).json()["ok"])
        finally:
            server.stop()

    def test_stream_identity_depends_on_token_scope(self):
        frames, health = FrameBuffer(), WorkerHealth()
        server = WorkerHTTPServer("127.0.0.1", 0, stream_token("s"), frames, health,
                                  identity_token=stream_token("s", show_identity=True))
        server.start()
        plain = {"X-Worker-Token": stream_token("s")}
        ident = {"X-Worker-Token": stream_token("s", show_identity=True)}
        try:
            frames.update(b"tanpa-identitas", b"\xff\xd8raw", b"dengan-identitas")
            self.assertEqual(_first_mjpeg_frame(server.port, plain, len(b"tanpa-identitas")), b"tanpa-identitas")
            self.assertEqual(_first_mjpeg_frame(server.port, ident, len(b"dengan-identitas")), b"dengan-identitas")
        finally:
            server.stop()


def _first_mjpeg_frame(port: int, headers: dict, payload_len: int) -> bytes:
    """Baca satu frame MJPEG. Panjang frame diketahui supaya tidak menunggu stream yang tak pernah EOF."""
    res = requests.get(f"http://127.0.0.1:{port}/stream.mjpg", headers=headers, stream=True, timeout=5)
    try:
        head_len = len(f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {payload_len}\r\n\r\n")
        raw = res.raw.read(head_len + payload_len + 2)
    finally:
        res.close()
    assert raw.startswith(b"--" + BOUNDARY.encode()), raw
    return raw[head_len:head_len + payload_len]


class WorkerPipelineTests(SimpleTestCase):
    def _cfg(self):
        cfg = WorkerConfig()
        cfg.worker_secret = "s"
        cfg.min_track_age_seconds = 0.0
        cfg.match_interval_seconds = 0.0
        return cfg

    def test_identity_label_only_when_django_returns_candidate(self):
        client = mock.Mock()
        client.submit_candidate.return_value = {"ok": True, "data": {
            "event_id": 5, "match_status": "CANDIDATE",
            "candidate": {"patient_name": "Budi Santoso", "no_rkm_medis": "0001234"}}}
        worker = FaceWorker(self._cfg(), camera=mock.Mock(), client=client,
                            detector=FakeDetector(), embedder=FakeEmbedder(), enable_http=False)
        track = worker.tracker.update([fake_detection()], now=1.0)[0][0]
        worker._submit(track, np.zeros(4, dtype=np.float32), {"status": "GOOD"})
        self.assertEqual(track.identity, "Budi Santoso (0001234)")
        frame = np.zeros((120, 320, 3), dtype=np.uint8)
        without = worker.annotate(frame, [track])
        with_id = worker.annotate(frame, [track], with_identity=True)
        self.assertFalse(np.array_equal(without, with_id))
        # Tanpa kandidat dari Django tidak ada label nama sama sekali.
        track.identity = None
        self.assertTrue(np.array_equal(worker.annotate(frame, [track], with_identity=True),
                                       worker.annotate(frame, [track])))

    def test_pipeline_submits_good_faces_and_handles_django_down(self):
        client = mock.Mock()
        client.submit_candidate.return_value = {"ok": True, "data": {"event_id": 5, "match_status": "CANDIDATE"}}
        client.heartbeat.side_effect = requests.ConnectionError("django mati")
        cam = CameraStream("0", capture_factory=lambda *a: FakeCapture())
        # frame bertekstur agar lolos penilaian kualitas
        rng = np.random.RandomState(1)
        textured = (rng.rand(480, 640, 3) * 120 + 60).astype(np.uint8)
        worker = FaceWorker(self._cfg(), camera=cam, client=client, detector=FakeDetector(),
                            embedder=FakeEmbedder(), enable_http=False)
        tracks = worker.process_frame(textured, now=1.0)
        self.assertEqual(len(tracks), 1)
        self.assertTrue(wait_until(lambda: client.submit_candidate.called))
        self.assertTrue(wait_until(lambda: tracks[0].match_status == "CANDIDATE"))
        # CANDIDATE -> tidak dikirim lagi untuk track yang sama
        worker.process_frame(textured, now=2.0)
        time.sleep(0.1)
        self.assertEqual(client.submit_candidate.call_count, 1)
        # Annotasi default tetap memakai ID track & status; identitas hanya bila diminta.
        self.assertEqual(worker.annotate(textured, tracks).shape, textured.shape)
        self.assertEqual(worker.annotate(textured, tracks, with_identity=True).shape, textured.shape)
        # Heartbeat gagal (Django mati) tidak menghentikan worker
        t = threading.Thread(target=worker.run, kwargs={"max_seconds": 0.5})
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertFalse(cam.is_alive())

    def test_poor_quality_not_submitted(self):
        client = mock.Mock()
        worker = FaceWorker(self._cfg(), camera=mock.Mock(), client=client,
                            detector=FakeDetector([fake_detection(size=30)]), embedder=FakeEmbedder(),
                            enable_http=False)
        worker.process_frame(np.full((480, 640, 3), 120, np.uint8), now=1.0)
        time.sleep(0.1)
        client.submit_candidate.assert_not_called()

    def test_model_unavailable_disables_matching(self):
        cfg = self._cfg()
        cfg.detector_model = cfg.detector_model.with_name("tidak-ada.onnx")
        cfg.embedding_model = cfg.embedding_model.with_name("tidak-ada2.onnx")
        worker = FaceWorker(cfg, camera=mock.Mock(), client=mock.Mock(), enable_http=False)
        self.assertEqual(worker.model_status["detector"], "UNAVAILABLE")
        self.assertEqual(worker.model_status["embedder"], "UNAVAILABLE")
        self.assertEqual(len(worker.model_errors), 2)
        self.assertEqual(worker.process_frame(np.zeros((10, 10, 3), np.uint8), 0.0), [])

    def test_model_classes_raise_when_missing(self):
        from ai_worker.detector import FaceDetector
        from ai_worker.embedding import FaceEmbedder

        with self.assertRaises(ModelUnavailableError):
            FaceDetector("/tidak/ada.onnx")
        with self.assertRaises(ModelUnavailableError):
            FaceEmbedder("/tidak/ada.onnx")


class LightingQualityTests(SimpleTestCase):
    """Wajah redup tapi tajam tidak boleh dinilai buram; yang redup dicerahkan sebelum embedding."""

    def setUp(self):
        import cv2

        rng = np.random.RandomState(0)
        base = cv2.GaussianBlur((rng.rand(480, 640) * 255).astype(np.float32), (0, 0), 1.5)
        self.base = cv2.normalize(base, None, 60, 190, cv2.NORM_MINMAX)
        self.det = fake_detection()

    def frame(self, gain):
        g = np.clip(self.base * gain, 0, 255).astype(np.uint8)
        return np.dstack([g, g, g])

    def test_dim_but_sharp_is_not_blurry(self):
        normal = quality.assess(self.frame(1.0), self.det)
        dim = quality.assess(self.frame(0.45), self.det)
        self.assertNotIn("BLURRY", normal.reasons)
        self.assertNotIn("BLURRY", dim.reasons)        # dulu: ketajaman 85 -> 22 = "buram"
        self.assertNotIn("TOO_DARK", dim.reasons)      # kecerahan ±55 >= 30
        self.assertAlmostEqual(dim.metrics["blur_variance"], normal.metrics["blur_variance"],
                               delta=normal.metrics["blur_variance"] * 0.25)

    def test_really_dark_still_rejected(self):
        self.assertIn("TOO_DARK", quality.assess(self.frame(0.15), self.det).reasons)

    def test_truly_blurry_still_rejected(self):
        import cv2

        blurred = cv2.GaussianBlur(self.frame(1.0), (0, 0), 6)
        self.assertIn("BLURRY", quality.assess(blurred, self.det).reasons)

    def test_correct_lighting(self):
        import cv2

        dim = self.frame(0.45)
        out = quality.correct_lighting(dim, self.det)
        x, y, w, h = (int(v) for v in self.det.bbox)
        before = cv2.cvtColor(dim[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY).mean()
        after = cv2.cvtColor(out[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY).mean()
        self.assertGreater(after, before + 25)
        bright = self.frame(1.0)
        self.assertIs(quality.correct_lighting(bright, self.det), bright)  # wajah terang tidak diubah
