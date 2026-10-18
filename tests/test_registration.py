"""Registrasi referensi, penghapusan/pencabutan, model tidak tersedia, audit."""
import io
import json
from datetime import timedelta

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from ai_worker.embedding import MODEL_VERSION
from apps.common import crypto, storage
from apps.face_events.models import FaceAuditLog
from apps.face_registry.models import PatientFaceReference

from .helpers import FaceTestCase, fake_detection, install_fake_engine, make_reference, synthetic_jpeg, unit_vector

CAPTURE = "/api/face/v1/captures/"
REFS = "/api/face/v1/references/"


class RegistrationTests(FaceTestCase):
    def setUp(self):
        super().setUp()
        self.vector = unit_vector(42)
        install_fake_engine(vector=self.vector)
        self.client.force_login(self.admin)

    def capture(self, data=None):
        upload = SimpleUploadedFile("c.jpg", data or synthetic_jpeg(), content_type="image/jpeg")
        return self.client.post(CAPTURE + "?source=upload", {"image": upload})

    def register(self, token, **overrides):
        payload = {"patient_name": "Pasien Uji Satu", "no_rkm_medis": "UJI-0001",
                   "processing_basis": "DEMO_SYNTHETIC", "consent_confirmed": True, "capture_token": token}
        payload.update(overrides)
        return self.client.post(REFS, json.dumps(payload), content_type="application/json")

    def test_full_registration_flow(self):
        res = self.capture()
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()["data"]
        self.assertTrue(data["acceptable"], data)
        token = data["capture_token"]
        preview = self.client.get(f"{CAPTURE}{token}/")
        self.assertEqual(preview["Content-Type"], "image/jpeg")
        self.assertEqual(preview["Cache-Control"], "no-store")

        res = self.register(token)
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()["data"]
        self.assertNotIn("embedding", json.dumps(body))
        ref = PatientFaceReference.objects.get(pk=body["id"])
        self.assertTrue(ref.is_test_data)
        self.assertEqual(ref.model_version, MODEL_VERSION)
        self.assertIsNotNone(ref.expires_at)
        # Embedding terenkripsi dan dapat didekripsi kembali dengan benar.
        self.assertNotIn("[", ref.embedding_ciphertext)
        self.assertAlmostEqual(float(crypto.decrypt_embedding(ref.embedding_ciphertext) @ self.vector), 1.0, places=5)
        # Foto tersimpan terenkripsi dengan nama acak (bukan nomor RM)
        self.assertNotIn("UJI", ref.reference_image)
        self.assertTrue(storage.exists(ref.reference_image))
        # File sementara dibersihkan
        self.assertEqual(self.client.get(f"{CAPTURE}{token}/").status_code, 404)
        self.assertTrue(FaceAuditLog.objects.filter(action="REFERENCE_REGISTERED", outcome="SUCCESS").exists())

    def test_consent_required(self):
        token = self.capture().json()["data"]["capture_token"]
        res = self.register(token, consent_confirmed=False)
        self.assertEqual(res.status_code, 422)
        self.assertIn("consent_confirmed", res.json()["error"]["fields"])
        self.assertEqual(PatientFaceReference.objects.count(), 0)
        self.assertTrue(FaceAuditLog.objects.filter(action="REFERENCE_REGISTERED", outcome="FAILED").exists())

    def test_demo_rm_must_be_test_format(self):
        token = self.capture().json()["data"]["capture_token"]
        res = self.register(token, no_rkm_medis="123456")
        self.assertEqual(res.status_code, 422)
        self.assertIn("no_rkm_medis", res.json()["error"]["fields"])

    def test_production_basis_rejected_in_demo(self):
        token = self.capture().json()["data"]["capture_token"]
        res = self.register(token, processing_basis="PATIENT_CONSENT")
        self.assertEqual(res.status_code, 422)

    def test_poor_quality_cannot_be_saved(self):
        install_fake_engine(detections=[fake_detection(size=40)], vector=self.vector)
        data = self.capture().json()["data"]
        self.assertFalse(data["acceptable"])
        self.assertIn("TOO_SMALL", data["quality"]["reasons"])
        res = self.register(data["capture_token"])
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["error"]["code"], "QUALITY_INSUFFICIENT")

    def test_no_face_and_multiple_faces(self):
        install_fake_engine(detections=[])
        data = self.capture().json()["data"]
        self.assertIsNone(data["capture_token"])
        install_fake_engine(detections=[fake_detection(), fake_detection(x=10)])
        data = self.capture().json()["data"]
        self.assertEqual(data["face_count"], 2)
        self.assertIsNone(data["capture_token"])

    def test_invalid_upload_type_and_size(self):
        res = self.capture(b"GIF89a-bukan-jpeg" + b"0" * 500)
        self.assertEqual(res.status_code, 415)
        with override_settings(FACE_MAX_UPLOAD_BYTES=100):
            self.assertEqual(self.capture().status_code, 413)

    def test_capture_token_is_bound_to_session(self):
        token = self.capture().json()["data"]["capture_token"]
        other = self.client_class()
        other.force_login(self.admin)
        res = other.post(REFS, json.dumps({"patient_name": "Pasien Uji", "no_rkm_medis": "UJI-9",
                                           "processing_basis": "DEMO_SYNTHETIC", "consent_confirmed": True,
                                           "capture_token": token}), content_type="application/json")
        self.assertEqual(res.status_code, 404)

    def test_cancel_discards_temp_file(self):
        token = self.capture().json()["data"]["capture_token"]
        path = self.client.session["face_captures"][token]["path"]
        self.assertTrue(storage.exists(path))
        self.assertEqual(self.client.delete(f"{CAPTURE}{token}/").status_code, 200)
        self.assertFalse(storage.exists(path))

    def test_worker_source_unavailable(self):
        res = self.client.post(CAPTURE + "?source=worker")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["error"]["code"], "WORKER_UNAVAILABLE")


class ModelUnavailableTests(FaceTestCase):
    @override_settings(FACE_DETECTOR_MODEL="models/tidak-ada.onnx")
    def test_capture_reports_model_unavailable(self):
        self.client.force_login(self.admin)
        upload = SimpleUploadedFile("c.jpg", synthetic_jpeg(), content_type="image/jpeg")
        res = self.client.post(CAPTURE + "?source=upload", {"image": upload})
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["error"]["code"], "MODEL_UNAVAILABLE")

    @override_settings(FACE_EMBEDDING_MODEL="models/tidak-ada.onnx")
    def test_health_reports_missing_model(self):
        self.client.force_login(self.operator)
        data = self.client.get("/api/face/v1/health/").json()["data"]
        self.assertEqual(data["model"]["embedding_file"], "MISSING")
        self.assertFalse(data["model"]["available"])


class ReferenceDeletionTests(FaceTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def _ref_with_photo(self):
        path = storage.save_encrypted(b"\xff\xd8\xff-foto", storage.REFERENCE_DIR)
        ref = make_reference()
        ref.reference_image = path
        ref.save()
        return ref, path

    def test_deactivate(self):
        ref, path = self._ref_with_photo()
        res = self.client.delete(f"{REFS}{ref.pk}/?mode=deactivate")
        self.assertEqual(res.status_code, 200)
        ref.refresh_from_db()
        self.assertFalse(ref.is_active)
        self.assertTrue(storage.exists(path))
        self.assertTrue(FaceAuditLog.objects.filter(action="REFERENCE_DEACTIVATED", target_id=str(ref.pk)).exists())

    def test_delete_removes_file_and_row(self):
        ref, path = self._ref_with_photo()
        res = self.client.delete(f"{REFS}{ref.pk}/?mode=delete")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(PatientFaceReference.objects.filter(pk=ref.pk).exists())
        self.assertFalse(storage.exists(path))
        log = FaceAuditLog.objects.get(action="REFERENCE_DELETED")
        self.assertIn("file_removed=True", log.detail)

    def test_invalid_mode_and_missing(self):
        ref, _ = self._ref_with_photo()
        self.assertEqual(self.client.delete(f"{REFS}{ref.pk}/?mode=wipe").status_code, 422)
        self.assertEqual(self.client.delete(f"{REFS}99999/").status_code, 404)

    def test_operator_cannot_delete(self):
        ref, _ = self._ref_with_photo()
        self.client.force_login(self.operator)
        self.assertEqual(self.client.delete(f"{REFS}{ref.pk}/?mode=delete").status_code, 403)

    def test_retention_purge(self):
        ref, path = self._ref_with_photo()
        ref.expires_at = timezone.now() - timedelta(minutes=1)
        ref.save()
        keep = make_reference("UJI-0002")
        call_command("face_purge_expired", stdout=io.StringIO())
        self.assertFalse(PatientFaceReference.objects.filter(pk=ref.pk).exists())
        self.assertTrue(PatientFaceReference.objects.filter(pk=keep.pk).exists())
        self.assertFalse(storage.exists(path))

    def test_photo_view_requires_permission_and_is_audited(self):
        ref, _ = self._ref_with_photo()
        res = self.client.get(f"/references/{ref.pk}/photo/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b"\xff\xd8\xff-foto")
        self.assertIn("no-store", res["Cache-Control"])
        self.assertTrue(FaceAuditLog.objects.filter(action="REFERENCE_PHOTO_VIEWED").exists())
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(f"/references/{ref.pk}/photo/").status_code, 403)

    def test_media_root_not_served_publicly(self):
        ref, path = self._ref_with_photo()
        self.client.logout()
        self.assertEqual(self.client.get(f"/media_private/{path}").status_code, 404)
        self.assertNotIn("media_private", str(getattr(settings, "MEDIA_URL", "")))
