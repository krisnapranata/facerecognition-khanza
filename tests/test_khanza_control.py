"""Mode simulasi Khanza, adapter baca-saja, penentuan status kontrol, pencarian pasien."""
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.face_events.models import ControlStatus, FaceAuditLog, FaceRecognitionEvent, VerificationStatus
from apps.face_events.services import determine_control_status
from apps.integrations import khanza
from apps.patient_lookup.services import (
    ServiceUnavailable,
    SimulatedControlScheduleService,
    SimulatedPatientLookupService,
    get_control_schedule_service,
    get_patient_lookup_service,
)

from .helpers import FaceTestCase, make_reference


class SimulationModeTests(FaceTestCase):
    def test_default_services_are_simulated_and_no_khanza_alias(self):
        self.assertFalse(settings.KHANZA_ENABLED)
        self.assertNotIn("khanza", settings.DATABASES)
        self.assertIsInstance(get_patient_lookup_service(), SimulatedPatientLookupService)
        self.assertIsInstance(get_control_schedule_service(), SimulatedControlScheduleService)

    def test_search_synthetic_and_registered(self):
        make_reference("UJI-0099", name="Peserta Uji Coba")
        svc = SimulatedPatientLookupService()
        self.assertEqual([p.no_rkm_medis for p in svc.search("uji-0001")], ["UJI-0001"])
        self.assertIn("UJI-0099", [p.no_rkm_medis for p in svc.search("coba")])
        self.assertEqual(svc.search("a"), [])
        self.assertTrue(all(p.no_rkm_medis.startswith("UJI-") for p in svc.search("pasien")))

    def test_search_api(self):
        self.client.force_login(self.operator)
        res = self.client.get("/api/patients/search/?q=Dua")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["data"]["source"], "simulasi")
        self.assertEqual(res.json()["data"]["results"][0]["no_rkm_medis"], "UJI-0002")
        self.assertTrue(FaceAuditLog.objects.filter(action="PATIENT_SEARCH").exists())
        self.assertEqual(self.client.get("/api/patients/search/?q=x").status_code, 422)
        self.client.force_login(self.nobody)
        self.assertEqual(self.client.get("/api/patients/search/?q=Dua").status_code, 403)

    def test_control_schedule_simulation(self):
        svc = SimulatedControlScheduleService()
        self.assertTrue(svc.find_control("UJI-0001").found)
        self.assertFalse(svc.find_control("UJI-0002").found)  # besok, bukan hari ini
        self.assertTrue(svc.find_control("UJI-0002", timezone.localdate() + timedelta(days=1)).found)
        self.assertFalse(svc.find_control("UJI-0005").found)


class ControlStatusTests(FaceTestCase):
    def _event(self, **kw):
        defaults = dict(camera_id="c", track_id="t", match_status="CANDIDATE", quality_status="GOOD")
        defaults.update(kw)
        return FaceRecognitionEvent(**defaults)

    def test_unverified_identity(self):
        status, _ = determine_control_status(self._event())
        self.assertEqual(status, ControlStatus.IDENTITY_NOT_VERIFIED)

    def test_rejected_identity_not_checked(self):
        status, _ = determine_control_status(self._event(verification_status=VerificationStatus.REJECTED))
        self.assertEqual(status, ControlStatus.IDENTITY_NOT_VERIFIED)

    def test_found_and_not_found(self):
        found = self._event(verification_status=VerificationStatus.CONFIRMED, verified_no_rkm_medis="UJI-0001")
        self.assertEqual(determine_control_status(found)[0], ControlStatus.CONTROL_FOUND)
        not_found = self._event(verification_status=VerificationStatus.MANUAL, verified_no_rkm_medis="UJI-0005")
        status, detail = determine_control_status(not_found)
        self.assertEqual(status, ControlStatus.CONTROL_NOT_FOUND)
        self.assertIn("manual", detail.lower())

    def test_service_failure_needs_manual_check(self):
        ev = self._event(verification_status=VerificationStatus.CONFIRMED, verified_no_rkm_medis="UJI-0001")
        with mock.patch("apps.face_events.services.get_control_schedule_service") as get:
            get.return_value.find_control.side_effect = ServiceUnavailable("down")
            self.assertEqual(determine_control_status(ev)[0], ControlStatus.NEEDS_MANUAL_CHECK)
            get.return_value.find_control.side_effect = RuntimeError("boom")
            self.assertEqual(determine_control_status(ev)[0], ControlStatus.NEEDS_MANUAL_CHECK)

    def test_manual_verification_flow(self):
        event = FaceRecognitionEvent.objects.create(camera_id="c", track_id="t", match_status="NO_MATCH",
                                                    quality_status="GOOD")
        self.client.force_login(self.operator)
        url = f"/api/face/v1/events/{event.pk}/verify/"
        res = self.client.post(url, json.dumps({"decision": "CONFIRMED"}), content_type="application/json")
        self.assertEqual(res.status_code, 409)  # tidak ada kandidat untuk dikonfirmasi
        res = self.client.post(url, json.dumps({"decision": "MANUAL", "no_rkm_medis": "UJI-9999"}),
                               content_type="application/json")
        self.assertEqual(res.status_code, 404)
        res = self.client.post(url, json.dumps({"decision": "MANUAL", "no_rkm_medis": "uji-0003"}),
                               content_type="application/json")
        data = res.json()["data"]
        self.assertEqual(data["verification_status"], "MANUAL")
        self.assertEqual(data["verified_identity"]["no_rkm_medis"], "UJI-0003")
        self.assertEqual(data["control_status"], "CONTROL_FOUND")


class KhanzaAdapterTests(SimpleTestCase):
    def test_disabled_by_default(self):
        with self.assertRaises(khanza.KhanzaNotConfigured):
            khanza.load_queries()

    @override_settings(KHANZA_ENABLED=True, KHANZA_SCHEMA_VERIFIED=False)
    def test_requires_schema_verification(self):
        with self.assertRaisesMessage(khanza.KhanzaNotConfigured, "belum diverifikasi"):
            khanza.load_queries()

    def test_only_select_allowed(self):
        bad = [
            "UPDATE x SET y=1",
            "SELECT * FROM a; DELETE FROM a",
            "SELECT * FROM a -- komentar",
            "SELECT * INTO OUTFILE '/tmp/x' FROM a",
            "INSERT INTO a VALUES (1)",
            "SELECT 1 /* c */",
        ]
        for sql in bad:
            with self.assertRaises(khanza.KhanzaNotConfigured, msg=sql):
                khanza.validate_select(sql)
        self.assertTrue(khanza.validate_select("SELECT a, b FROM t WHERE c = %(q)s LIMIT %(limit)s"))

    def test_query_file_validated(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "q.json"
            path.write_text(json.dumps({
                "patient_search": {"sql": "DELETE FROM t", "columns": {}},
                "patient_get": {"sql": "SELECT 1", "columns": {}},
                "control_schedule": {"sql": "SELECT 1", "columns": {}},
            }))
            with override_settings(KHANZA_ENABLED=True, KHANZA_SCHEMA_VERIFIED=True, KHANZA_QUERY_FILE=str(path)):
                with self.assertRaises(khanza.KhanzaNotConfigured):
                    khanza.load_queries()


class KhanzaQueryFileTests(SimpleTestCase):
    def test_project_query_file_passes_validation(self):
        spec = json.loads((Path(settings.BASE_DIR) / "config" / "khanza_queries.json").read_text())
        for name in khanza.REQUIRED_QUERIES:
            self.assertTrue(khanza.validate_select(spec[name]["sql"]), name)
        with override_settings(KHANZA_ENABLED=True, KHANZA_SCHEMA_VERIFIED=True,
                               KHANZA_QUERY_FILE="config/khanza_queries.json"):
            khanza._cache.update(mtime=None, queries=None)
            self.assertEqual(set(khanza.load_queries()), set(khanza.REQUIRED_QUERIES))

    def test_health_disabled(self):
        self.assertEqual(khanza.health()["status"], "DISABLED")


class FakeCursor:
    def __init__(self, rows, cols):
        self.rows, self.description = rows, [(c,) for c in cols]
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchmany(self, n):
        return self.rows[:n]


@override_settings(KHANZA_ENABLED=True, KHANZA_SCHEMA_VERIFIED=True, KHANZA_QUERY_FILE="config/khanza_queries.json")
class KhanzaServiceTests(SimpleTestCase):
    def setUp(self):
        khanza._cache.update(mtime=None, queries=None)

    def _patch(self, rows, cols):
        cursor = FakeCursor(rows, cols)
        conn = mock.MagicMock()
        conn.cursor.return_value = cursor
        return mock.patch("apps.integrations.khanza.connections", {"khanza": conn}), cursor

    def test_patient_get_and_search_mapping(self):
        from datetime import date as d

        patcher, cursor = self._patch([("000123", "NAMA PASIEN", "L", d(1980, 1, 2))],
                                      ["no_rkm_medis", "nama", "jk", "tgl_lahir"])
        with patcher:
            p = khanza.KhanzaPatientLookupService().get("000123")
            self.assertEqual((p.no_rkm_medis, p.nama, p.jk, p.tgl_lahir), ("000123", "NAMA PASIEN", "L", "1980-01-02"))
            self.assertFalse(p.is_test_data)
            khanza.KhanzaPatientLookupService().search("nama")
        sql, params = cursor.executed[-1]
        self.assertEqual(params["exact"], "nama")
        self.assertEqual(params["q"], "%nama%")
        self.assertNotIn("nama", sql.lower().split("like")[1][:20])  # nilai tidak digabung ke SQL

    def test_control_found_detail(self):
        from datetime import date as d

        patcher, _ = self._patch([("surat_kontrol_bpjs", d(2024, 11, 8), "Bedah", "dr. X", "S1")],
                                 ["sumber", "tanggal", "poli", "dokter", "keterangan"])
        with patcher:
            r = khanza.KhanzaControlScheduleService().find_control("000123", d(2024, 11, 8))
        self.assertTrue(r.found)
        self.assertIn("Surat kontrol BPJS 2024-11-08: Bedah", r.detail)

    def test_control_not_found_and_db_error(self):
        patcher, _ = self._patch([], ["sumber", "tanggal", "poli", "dokter", "keterangan"])
        with patcher:
            self.assertFalse(khanza.KhanzaControlScheduleService().find_control("000123").found)
        conn = mock.MagicMock()
        conn.cursor.side_effect = RuntimeError("down")
        with mock.patch("apps.integrations.khanza.connections", {"khanza": conn}):
            with self.assertRaises(ServiceUnavailable):
                khanza.KhanzaPatientLookupService().get("1")


@override_settings(KHANZA_ENABLED=True)
class KhanzaRegistrationTests(FaceTestCase):
    def setUp(self):
        super().setUp()
        from django.core.files.uploadedfile import SimpleUploadedFile

        from .helpers import install_fake_engine, synthetic_jpeg

        install_fake_engine()
        self.client.force_login(self.admin)
        upload = SimpleUploadedFile("c.jpg", synthetic_jpeg(), content_type="image/jpeg")
        self.token = self.client.post("/api/face/v1/captures/?source=upload", {"image": upload}).json()["data"][
            "capture_token"]

    def _register(self, rm="000123", basis="PATIENT_CONSENT"):
        return self.client.post("/api/face/v1/references/", json.dumps({
            "patient_name": "nama diketik salah", "no_rkm_medis": rm, "processing_basis": basis,
            "consent_confirmed": True, "capture_token": self.token}), content_type="application/json")

    def test_name_comes_from_khanza(self):
        from apps.face_registry.models import PatientFaceReference
        from apps.patient_lookup.services import PatientRecord

        with mock.patch("apps.patient_lookup.services.get_patient_lookup_service") as get:
            get.return_value.get.return_value = PatientRecord("000123", "NAMA DARI KHANZA", "khanza", False)
            res = self._register()
        self.assertEqual(res.status_code, 201, res.content)
        ref = PatientFaceReference.objects.get()
        self.assertEqual((ref.no_rkm_medis, ref.patient_name, ref.is_test_data), ("000123", "NAMA DARI KHANZA", False))

    def test_unknown_rm_rejected(self):
        with mock.patch("apps.patient_lookup.services.get_patient_lookup_service") as get:
            get.return_value.get.return_value = None
            res = self._register()
        self.assertEqual(res.status_code, 422)
        self.assertIn("no_rkm_medis", res.json()["error"]["fields"])

    def test_khanza_down_returns_503(self):
        with mock.patch("apps.patient_lookup.services.get_patient_lookup_service") as get:
            get.return_value.get.side_effect = ServiceUnavailable("down")
            self.assertEqual(self._register().status_code, 503)

    def test_synthetic_basis_not_allowed_with_real_patients(self):
        self.assertEqual(self._register(basis="DEMO_SYNTHETIC").status_code, 422)
