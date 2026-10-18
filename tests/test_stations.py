"""Stasiun kamera USB di PC loket (frame dari browser) — tanpa kamera fisik, engine AI dipalsukan."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.face_events import stations
from apps.face_events.models import FaceRecognitionEvent

from .helpers import FaceTestCase, install_fake_engine, make_reference, synthetic_jpeg, unit_vector

URL = "/api/face/v1/stations/{}/frame/"


class StationFrameTests(FaceTestCase):
    def setUp(self):
        super().setUp()
        stations.manager.reset()
        self.vec = unit_vector(77)
        install_fake_engine(vector=self.vec)

    def send(self, station="loket-1", data=None):
        return self.client.post(URL.format(station),
                                {"frame": SimpleUploadedFile("f.jpg", data or synthetic_jpeg(), content_type="image/jpeg")})

    def test_requires_login_and_permission(self):
        self.assertEqual(self.send().status_code, 401)
        self.client.force_login(self.nobody)
        self.assertEqual(self.send().status_code, 403)

    def test_frame_returns_boxes_and_matches_once_track_is_stable(self):
        make_reference("UJI-0001", vector=self.vec)
        self.client.force_login(self.operator)
        first = self.send().json()["data"]
        self.assertEqual(len(first["faces"]), 1)
        face = first["faces"][0]
        self.assertTrue(all(0 <= v <= 1 for v in face["bbox"]))
        self.assertEqual(face["quality"], "GOOD")
        self.assertIsNone(face["match_status"])  # track belum cukup lama
        station = stations.manager.get("loket-1")
        station.min_track_age = 0
        station.match_interval = 0
        second = self.send().json()["data"]["faces"][0]
        self.assertEqual(second["track_id"], face["track_id"])  # tracking antarframe
        self.assertEqual(second["match_status"], "CANDIDATE")
        event = FaceRecognitionEvent.objects.get(pk=second["event_id"])
        self.assertEqual(event.camera_id, "usb-loket-1")
        self.assertNotIn("UJI-0001", str(second))  # respons stasiun tidak memuat identitas

    def test_station_listed_in_health(self):
        self.client.force_login(self.operator)
        self.send("loket-2")
        data = self.client.get("/api/face/v1/health/").json()["data"]
        st = next(s for s in data["stations"] if s["station_id"] == "loket-2")
        self.assertTrue(st["online"])
        self.assertEqual(st["user"], "operator_uji")

    def test_validation(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.send("loket 1!").status_code, 422)
        self.assertEqual(self.client.post(URL.format("loket-1")).status_code, 422)
        with override_settings(FACE_STATION_MAX_FRAME_BYTES=100):
            self.assertEqual(self.send().status_code, 413)
        self.assertEqual(self.send(data=b"bukan gambar" * 50).status_code, 415)

    @override_settings(FACE_DETECTOR_MODEL="models/tidak-ada.onnx")
    def test_model_unavailable(self):
        from apps.face_registry import engine

        engine.set_engine_for_tests(None)
        self.client.force_login(self.operator)
        res = self.send()
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["error"]["code"], "MODEL_UNAVAILABLE")

    def test_idle_station_is_dropped(self):
        self.client.force_login(self.operator)
        self.send("loket-3")
        st = stations.manager.get("loket-3")
        st.last_seen -= 10_000
        stations.manager.get("loket-4")  # memicu pembersihan
        self.assertIsNot(stations.manager.get("loket-3"), st)
