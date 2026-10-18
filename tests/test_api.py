"""API: autentikasi antarlayanan, validasi, hak akses, format JSON, verifikasi, status worker."""
import json
import time
from unittest import mock

from ai_worker import signing
from ai_worker.embedding import MODEL_VERSION
from apps.face_events.models import FaceAuditLog, FaceRecognitionEvent
from apps.face_events.services import WORKER_STATUS_KEY

from .helpers import FaceTestCase, make_reference, signed_post, unit_vector, vector_with_similarity

CANDIDATES = "/api/face/v1/candidates/"
HEARTBEAT = "/api/face/v1/worker/heartbeat/"


def candidate_payload(vec, track="abc-1", **extra):
    data = {"camera_id": "laptop-0", "track_id": track, "model_version": MODEL_VERSION,
            "embedding": [float(v) for v in vec], "quality_status": "GOOD", "quality_score": 0.8}
    data.update(extra)
    return data


class ServiceAuthTests(FaceTestCase):
    def test_missing_signature_rejected(self):
        res = self.client.post(CANDIDATES, json.dumps(candidate_payload(unit_vector(1))),
                               content_type="application/json")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json(), {"ok": False, "error": {"code": "SERVICE_AUTH_MISSING",
                                                             "message": "Autentikasi layanan gagal"}})

    def test_wrong_secret_rejected(self):
        res = signed_post(self.client, CANDIDATES, candidate_payload(unit_vector(1)), secret="salah")
        self.assertEqual(res.json()["error"]["code"], "SERVICE_AUTH_INVALID")

    def test_replay_rejected(self):
        body = json.dumps(candidate_payload(unit_vector(1))).encode()
        hdrs = signing.signed_headers("test-worker-secret", "POST", CANDIDATES, body)
        meta = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in hdrs.items()}
        first = self.client.generic("POST", CANDIDATES, body, content_type="application/json", **meta)
        second = self.client.generic("POST", CANDIDATES, body, content_type="application/json", **meta)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.json()["error"]["code"], "SERVICE_AUTH_REPLAY")

    def test_expired_timestamp_rejected(self):
        with mock.patch("ai_worker.signing.time.time", return_value=time.time() - 120):
            body = json.dumps(candidate_payload(unit_vector(1))).encode()
            hdrs = signing.signed_headers("test-worker-secret", "POST", CANDIDATES, body)
        meta = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in hdrs.items()}
        res = self.client.generic("POST", CANDIDATES, body, content_type="application/json", **meta)
        self.assertEqual(res.json()["error"]["code"], "SERVICE_AUTH_EXPIRED")

    def test_tampered_body_rejected(self):
        body = json.dumps(candidate_payload(unit_vector(1))).encode()
        hdrs = signing.signed_headers("test-worker-secret", "POST", CANDIDATES, body)
        meta = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in hdrs.items()}
        tampered = body.replace(b"laptop-0", b"laptop-9")
        res = self.client.generic("POST", CANDIDATES, tampered, content_type="application/json", **meta)
        self.assertEqual(res.json()["error"]["code"], "SERVICE_AUTH_INVALID")

    def test_browser_session_cannot_post_candidates(self):
        self.client.force_login(self.admin)
        res = self.client.post(CANDIDATES, json.dumps(candidate_payload(unit_vector(1))),
                               content_type="application/json")
        self.assertEqual(res.status_code, 401)


class CandidateIngestTests(FaceTestCase):
    def test_candidate_identity_returned_for_overlay_label(self):
        # Identitas dikirim ke worker hanya untuk label overlay kamera, dan worker
        # hanya menggambarnya pada stream yang tokennya terikat izin identitas.
        ref = make_reference("UJI-0001", vector=unit_vector(7))
        make_reference("UJI-0002", vector=unit_vector(8))
        res = signed_post(self.client, CANDIDATES, candidate_payload(vector_with_similarity(unit_vector(7), 0.8)))
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()["data"]
        self.assertEqual(data["match_status"], "CANDIDATE")
        self.assertEqual(data["candidate"]["no_rkm_medis"], "UJI-0001")
        self.assertEqual(data["candidate"]["patient_name"], ref.patient_name)
        event = FaceRecognitionEvent.objects.get(pk=data["event_id"])
        self.assertEqual(event.candidate_reference_id, ref.pk)

    def test_no_candidate_means_no_identity_in_response(self):
        make_reference("UJI-0001", vector=unit_vector(7))
        res = signed_post(self.client, CANDIDATES, candidate_payload(unit_vector(3)))
        self.assertEqual(res.status_code, 201, res.content)
        data = res.json()["data"]
        self.assertEqual(data["match_status"], "NO_MATCH")
        self.assertIsNone(data["candidate"])
        self.assertNotIn("UJI-0001", res.content.decode())

    def test_validation_errors(self):
        cases = [
            ({"embedding": [0.1] * 10}, "embedding"),
            ({"camera_id": "bad id!"}, "camera_id"),
            ({"quality_status": "POOR"}, "quality_status"),
            ({"embedding": [True] * 128}, "embedding"),
        ]
        for override, field in cases:
            res = signed_post(self.client, CANDIDATES, candidate_payload(unit_vector(1), **override))
            self.assertEqual(res.status_code, 422, field)
            self.assertIn(field, res.json()["error"]["fields"])

    def test_invalid_json(self):
        body = b"{bukan json"
        hdrs = signing.signed_headers("test-worker-secret", "POST", CANDIDATES, body)
        meta = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in hdrs.items()}
        res = self.client.generic("POST", CANDIDATES, body, content_type="application/json", **meta)
        self.assertEqual(res.json()["error"]["code"], "INVALID_JSON")

    def test_client_supplied_candidate_id_ignored(self):
        make_reference("UJI-0001", vector=unit_vector(7))
        other = make_reference("UJI-0002", vector=unit_vector(8))
        payload = candidate_payload(unit_vector(500), candidate_reference_id=other.pk)
        res = signed_post(self.client, CANDIDATES, payload)
        event = FaceRecognitionEvent.objects.get(pk=res.json()["data"]["event_id"])
        self.assertEqual(event.match_status, "NO_MATCH")
        self.assertIsNone(event.candidate_reference_id)

    def test_wrong_model_version_is_error_event(self):
        make_reference("UJI-0001", vector=unit_vector(7))
        res = signed_post(self.client, CANDIDATES, candidate_payload(unit_vector(7), model_version="lain/v2"))
        self.assertEqual(res.json()["data"]["match_status"], "ERROR")

    def test_inactive_reference_not_matched(self):
        make_reference("UJI-0001", vector=unit_vector(7), active=False)
        res = signed_post(self.client, CANDIDATES, candidate_payload(unit_vector(7)))
        self.assertEqual(res.json()["data"]["match_status"], "NO_MATCH")

    def test_same_track_merges_into_one_event(self):
        make_reference("UJI-0001", vector=unit_vector(7))
        r1 = signed_post(self.client, CANDIDATES, candidate_payload(vector_with_similarity(unit_vector(7), 0.42)))
        r2 = signed_post(self.client, CANDIDATES, candidate_payload(vector_with_similarity(unit_vector(7), 0.8)))
        self.assertEqual(r1.json()["data"]["event_id"], r2.json()["data"]["event_id"])
        event = FaceRecognitionEvent.objects.get()
        self.assertEqual(event.match_status, "CANDIDATE")
        self.assertEqual(event.submissions, 2)


class BrowserApiTests(FaceTestCase):
    def setUp(self):
        super().setUp()
        self.ref = make_reference("UJI-0001", vector=unit_vector(7))
        res = signed_post(self.client, CANDIDATES, candidate_payload(vector_with_similarity(unit_vector(7), 0.8)))
        self.event_id = res.json()["data"]["event_id"]

    def test_events_require_login(self):
        res = self.client.get("/api/face/v1/events/")
        self.assertEqual(res.status_code, 401)
        self.assertFalse(res.json()["ok"])

    def test_events_forbidden_without_permission(self):
        self.client.force_login(self.nobody)
        self.assertEqual(self.client.get("/api/face/v1/events/").status_code, 403)
        self.assertEqual(self.client.get("/").status_code, 403)

    def test_operator_sees_identity_but_no_embedding(self):
        self.client.force_login(self.operator)
        res = self.client.get("/api/face/v1/events/")
        body = res.json()
        self.assertTrue(body["ok"])
        event = body["data"]["events"][0]
        self.assertEqual(event["candidate"]["no_rkm_medis"], "UJI-0001")
        self.assertNotIn("embedding", res.content.decode())
        detail = self.client.get(f"/api/face/v1/events/{self.event_id}/").json()["data"]
        self.assertEqual(detail["id"], self.event_id)

    def test_identity_hidden_without_identity_permission(self):
        from django.contrib.auth.models import Permission

        self.nobody.user_permissions.add(Permission.objects.get(codename="view_facerecognitionevent"))
        self.client.force_login(self.nobody)
        event = self.client.get("/api/face/v1/events/").json()["data"]["events"][0]
        self.assertIsNone(event["candidate"])

    def test_no_match_identity_never_shown(self):
        signed_post(self.client, CANDIDATES, candidate_payload(unit_vector(900), track="zzz-9"))
        self.client.force_login(self.operator)
        events = self.client.get("/api/face/v1/events/?status=NO_MATCH").json()["data"]["events"]
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["candidate"])

    def test_events_filter_validation(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get("/api/face/v1/events/?status=XYZ").status_code, 422)
        self.assertEqual(self.client.get("/api/face/v1/events/?limit=abc").status_code, 422)
        self.assertEqual(self.client.get("/api/face/v1/events/99999/").status_code, 404)

    def test_method_not_allowed(self):
        self.client.force_login(self.operator)
        res = self.client.post("/api/face/v1/events/")
        self.assertEqual(res.status_code, 405)

    def test_verify_confirm_and_audit(self):
        self.client.force_login(self.operator)
        res = self.client.post(f"/api/face/v1/events/{self.event_id}/verify/",
                               json.dumps({"decision": "CONFIRMED", "note": "KTP dicek"}),
                               content_type="application/json")
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()["data"]
        self.assertEqual(data["verification_status"], "CONFIRMED")
        self.assertEqual(data["verified_by"], "operator_uji")
        self.assertEqual(data["control_status"], "CONTROL_FOUND")  # UJI-0001 punya jadwal hari ini (simulasi)
        self.assertTrue(FaceAuditLog.objects.filter(action="EVENT_VERIFIED", target_id=str(self.event_id)).exists())
        again = self.client.post(f"/api/face/v1/events/{self.event_id}/verify/",
                                 json.dumps({"decision": "REJECTED"}), content_type="application/json")
        self.assertEqual(again.status_code, 409)

    def test_verify_requires_csrf_for_session(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        res = client.post(f"/api/face/v1/events/{self.event_id}/verify/", json.dumps({"decision": "REJECTED"}),
                          content_type="application/json")
        self.assertEqual(res.status_code, 403)

    def test_verify_invalid_decision_and_permission(self):
        self.client.force_login(self.operator)
        res = self.client.post(f"/api/face/v1/events/{self.event_id}/verify/", json.dumps({"decision": "MAYBE"}),
                               content_type="application/json")
        self.assertEqual(res.status_code, 422)
        self.client.force_login(self.nobody)
        res = self.client.post(f"/api/face/v1/events/{self.event_id}/verify/",
                               json.dumps({"decision": "REJECTED"}), content_type="application/json")
        self.assertEqual(res.status_code, 403)

    def test_rate_limit(self):
        from apps.common.api import ApiError, check_rate_limit

        for _ in range(3):
            check_rate_limit("unit", 3, 60)
        with self.assertRaises(ApiError) as ctx:
            check_rate_limit("unit", 3, 60)
        self.assertEqual(ctx.exception.status, 429)


class WorkerStatusTests(FaceTestCase):
    def test_no_worker_means_camera_not_active(self):
        self.client.force_login(self.operator)
        data = self.client.get("/api/face/v1/health/").json()["data"]
        self.assertFalse(data["worker"]["alive"])
        self.assertFalse(data["camera"]["connected"])

    def test_heartbeat_then_stale(self):
        payload = {"camera_id": "laptop-0", "camera": {"state": "CONNECTED", "connected": True},
                   "model": {"detector": "READY", "embedder": "READY"}, "faces": 2, "tracks": [], "inference_fps": 9}
        self.assertEqual(signed_post(self.client, HEARTBEAT, payload).status_code, 200)
        self.client.force_login(self.operator)
        data = self.client.get("/api/face/v1/health/").json()["data"]
        self.assertTrue(data["worker"]["alive"])
        self.assertTrue(data["camera"]["connected"])
        self.assertEqual(data["worker"]["faces"], 2)
        # AI worker berhenti: heartbeat basi -> dashboard tidak mengklaim kamera aktif.
        from django.core.cache import cache

        stored = cache.get(WORKER_STATUS_KEY)
        stored["received_at"] -= 60
        cache.set(WORKER_STATUS_KEY, stored)
        data = self.client.get("/api/face/v1/health/").json()["data"]
        self.assertFalse(data["worker"]["alive"])
        self.assertFalse(data["camera"]["connected"])
        self.assertEqual(data["worker"]["faces"], 0)

    def test_worker_stopping_flag(self):
        signed_post(self.client, HEARTBEAT, {"camera": {"connected": True}, "stopping": True})
        self.client.force_login(self.operator)
        data = self.client.get("/api/face/v1/health/").json()["data"]
        self.assertFalse(data["worker"]["alive"])
        self.assertEqual(data["worker"]["message"], "AI worker berhenti")

    def test_health_anonymous_minimal(self):
        data = self.client.get("/api/face/v1/health/").json()["data"]
        self.assertEqual(data, {"service": "ok"})

    def test_stream_proxy_when_worker_down(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get("/stream/").status_code, 503)
