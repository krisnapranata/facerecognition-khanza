"""Registrasi kunjungan ke reg_periksa (pola DlgReg), penjaga SQL tulis, login akun Khanza.

Tanpa koneksi ke Khanza: repository dan pemeriksaan kredensial dipalsukan.
"""
import json
from datetime import date, datetime
from unittest import mock

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.common.api import ApiError
from apps.face_events.models import FaceAuditLog, FaceRecognitionEvent
from apps.integrations import khanza_auth, khanza_rw
from apps.integrations import khanza_registration as kr

from .helpers import FaceTestCase

TODAY = date(2026, 10, 18)
NOW = timezone.make_aware(datetime(2026, 10, 18, 9, 30, 15))


class FakeRepo:
    def __init__(self, **overrides):
        self.inserted = []
        self.fail_inserts = 0
        self.max_reg = {"reg_periksa": 2, "booking_registrasi": 5}
        self.max_rawat = 2
        self.inpatient = False
        self.closing = False
        self.unpaid = 0
        self.existing = None
        self.visited = True
        self.scope_calls = []
        self.locked = False
        self.patient_row = {"no_rkm_medis": "900001", "nm_pasien": "PASIEN CONTOH", "asal": "JL. X, KEL, KEC, KAB",
                            "namakeluarga": "IBU", "keluarga": "IBU", "kd_pj": "BPJ", "png_jawab": "BPJS",
                            "tgl_daftar": date(2019, 11, 26), "tahun": 38, "bulan": 11, "hari": 16}
        self.__dict__.update(overrides)

    def polies(self):
        return [{"kd_poli": "INT", "nm_poli": "Klinik Penyakit Dalam", "registrasi": 30000, "registrasilama": 25000},
                {"kd_poli": "BED", "nm_poli": "Klinik Bedah", "registrasi": 30000, "registrasilama": 30000},
                {"kd_poli": "GIG", "nm_poli": "Klinik Gigi (tanpa jadwal)", "registrasi": 30000, "registrasilama": 30000},
                {"kd_poli": "IGDK", "nm_poli": "Unit IGD", "registrasi": 0, "registrasilama": 0}]

    # TODAY (2026-10-18) adalah Minggu = AKHAD.
    schedule_rows = [
        {"kd_dokter": "D001", "nm_dokter": "dr. A", "kd_poli": "INT", "hari_kerja": "AKHAD",
         "jam_mulai": "08:00:00", "jam_selesai": "12:00:00", "kuota": 20},
        {"kd_dokter": "D001", "nm_dokter": "dr. A", "kd_poli": "INT", "hari_kerja": "SENIN",
         "jam_mulai": "08:00:00", "jam_selesai": "12:00:00", "kuota": 20},
        {"kd_dokter": "D002", "nm_dokter": "dr. B", "kd_poli": "INT", "hari_kerja": "SELASA",
         "jam_mulai": "13:00:00", "jam_selesai": "16:00:00", "kuota": 10},
        {"kd_dokter": "D003", "nm_dokter": "dr. C", "kd_poli": "BED", "hari_kerja": "RABU",
         "jam_mulai": "09:00:00", "jam_selesai": "11:00:00", "kuota": None},
    ]
    counts = {}

    def schedules(self):
        return self.schedule_rows

    def registrations_per_doctor(self, d):
        return self.counts

    def suggestions(self, rm, d):
        return [{"sumber": "surat_kontrol_bpjs", "kd_poli": "INT", "kd_dokter": "D001", "keterangan": "Surat kontrol"},
                {"sumber": "skdp_bpjs", "kd_poli": None, "kd_dokter": "TIDAKADA", "keterangan": "SKDP"}]

    def patient(self, rm):
        return self.patient_row if rm == "900001" else None

    def is_inpatient(self, rm):
        return self.inpatient

    def closing_required(self):
        return self.closing

    def unpaid_visits(self, rm):
        return self.unpaid

    def existing_today(self, rm, kd_poli, d):
        return self.existing

    def visited_poli(self, rm, kd_poli):
        return self.visited

    def max_no_reg(self, table, scope, d):
        self.scope_calls.append((table, tuple(scope)))
        return self.max_reg[table]

    def max_no_rawat(self, d):
        return self.max_rawat

    def insert(self, plan):
        if self.fail_inserts:
            self.fail_inserts -= 1
            self.max_rawat += 1  # nomor dipakai proses lain
            raise IntegrityError("Duplicate entry")
        self.inserted.append(plan)

    def acquire_lock(self):
        self.locked = True
        return True

    def release_lock(self):
        self.locked = False


class PureFunctionTests(SimpleTestCase):
    def test_auto_nomer3_matches_khanza(self):
        self.assertEqual(kr.auto_nomer3(0, "", 3), "001")
        self.assertEqual(kr.auto_nomer3(9, "", 3), "010")
        self.assertEqual(kr.auto_nomer3(999, "", 3), "1000")
        self.assertEqual(kr.auto_nomer3(2, "2026/10/18/", 6), "2026/10/18/000003")
        self.assertEqual(kr.auto_nomer3(None, "", 3), "001")

    def test_age_rules(self):
        self.assertEqual(kr.age_from_parts(38, 11, 16), (38, "Th"))
        self.assertEqual(kr.age_from_parts(0, 3, 2), (3, "Bl"))
        self.assertEqual(kr.age_from_parts(0, 0, 5), (5, "Hr"))

    def test_scope_and_day(self):
        self.assertEqual(kr.no_reg_scope("dokter + poli"), ("kd_dokter", "kd_poli"))
        self.assertEqual(kr.no_reg_scope("poli"), ("kd_poli",))
        self.assertEqual(kr.no_reg_scope("lain"), ("kd_dokter",))
        self.assertEqual(kr.hari_kerja(date(2026, 10, 18)), "AKHAD")  # Minggu
        self.assertEqual(kr.hari_kerja(date(2026, 10, 19)), "SENIN")


class WriteGuardTests(SimpleTestCase):
    def test_only_registered_writes_pass(self):
        self.assertTrue(khanza_rw.check_statement(kr.SQL_INSERT))
        self.assertTrue(khanza_rw.check_statement(kr.SQL_UPDATE_UMUR))
        self.assertTrue(khanza_rw.check_statement("SELECT GET_LOCK(%s, 10)"))
        for bad in ["DELETE FROM reg_periksa", "UPDATE reg_periksa SET stts='Batal'",
                    "UPDATE pasien SET nm_pasien='x' WHERE pasien.no_rkm_medis=%s", "DROP TABLE reg_periksa",
                    "SELECT 1; DELETE FROM pasien", "SELECT * FROM pasien INTO OUTFILE '/tmp/x'",
                    "INSERT INTO pasien (no_rkm_medis) VALUES (%s)"]:
            with self.assertRaises(khanza_rw.ForbiddenStatement, msg=bad):
                khanza_rw.check_statement(bad)

    def test_register_write_restricted_to_allowed_targets(self):
        with self.assertRaises(khanza_rw.ForbiddenStatement):
            khanza_rw.register_write("DELETE FROM reg_periksa WHERE no_rawat=%s")
        with self.assertRaises(khanza_rw.ForbiddenStatement):
            khanza_rw.register_write("UPDATE pasien SET umur=1; DROP TABLE pasien")

    def test_insert_columns_match_khanza_order(self):
        self.assertEqual(kr.INSERT_COLUMNS[:3], ("no_reg", "no_rawat", "tgl_registrasi"))
        self.assertEqual(len(kr.INSERT_COLUMNS), 19)


@override_settings(KHANZA_URUTNOREG="dokter + poli", KHANZA_BASENOREG="booking", KHANZA_EXCLUDED_POLI=["IGDK"],
                   KHANZA_JADWAL_MODE="hari_ini")
class PlanTests(SimpleTestCase):
    def test_plan_follows_dlgreg(self):
        repo = FakeRepo()
        plan = kr.VisitRegistrationService(repo).plan("900001", "INT", "D001", now=NOW)
        self.assertEqual(plan.no_rawat, "2026/10/18/000003")
        self.assertEqual(plan.no_reg, "006")  # booking (5) > reg_periksa (2) -> 6
        self.assertEqual(repo.scope_calls, [("reg_periksa", ("kd_dokter", "kd_poli")),
                                            ("booking_registrasi", ("kd_dokter", "kd_poli"))])
        self.assertEqual((plan.stts_daftar, plan.biaya_reg, plan.status_poli), ("Lama", 25000.0, "Lama"))
        self.assertEqual((plan.umurdaftar, plan.sttsumur), (38, "Th"))
        self.assertEqual((plan.kd_pj, plan.p_jawab, plan.hubunganpj), ("BPJ", "IBU", "IBU"))
        self.assertEqual((plan.tgl_registrasi, plan.jam_reg), ("2026-10-18", "09:30:15"))
        values = plan.insert_values()
        self.assertEqual(values[11:14], ["Belum", "Lama", "Ralan"])
        self.assertEqual(values[17:], ["Belum Bayar", "Lama"])

    def test_new_patient_uses_registrasi_fee(self):
        repo = FakeRepo(visited=False)
        repo.patient_row = {**repo.patient_row, "tgl_daftar": TODAY}
        plan = kr.VisitRegistrationService(repo).plan("900001", "INT", "D001", now=NOW)
        self.assertEqual((plan.stts_daftar, plan.biaya_reg, plan.status_poli), ("Baru", 30000.0, "Baru"))

    @override_settings(KHANZA_BASENOREG="registrasi", KHANZA_URUTNOREG="poli")
    def test_registrasi_base_ignores_booking(self):
        repo = FakeRepo()
        plan = kr.VisitRegistrationService(repo).plan("900001", "INT", "D001", now=NOW)
        self.assertEqual(plan.no_reg, "003")
        self.assertEqual(repo.scope_calls, [("reg_periksa", ("kd_poli",))])

    def test_validations(self):
        svc = lambda **kw: kr.VisitRegistrationService(FakeRepo(**kw))  # noqa: E731
        cases = [
            (dict(), ("900001", "IGDK", "D001"), "VALIDATION_ERROR"),
            (dict(), ("900001", "XXX", "D001"), "VALIDATION_ERROR"),
            (dict(), ("900001", "INT", "D999"), "VALIDATION_ERROR"),
            (dict(), ("900001", "BED", "D001"), "VALIDATION_ERROR"),  # D001 tidak berjadwal di BED
            (dict(), ("900001", "INT", "D002"), "SCHEDULE_UNAVAILABLE"),  # D002 tidak praktik hari Minggu
            (dict(counts={"D001": 20}), ("900001", "INT", "D001"), "SCHEDULE_UNAVAILABLE"),  # kuota penuh
            (dict(), ("999999", "INT", "D001"), "PATIENT_INCOMPLETE"),
            (dict(inpatient=True), ("900001", "INT", "D001"), "INPATIENT"),
            (dict(closing=True, unpaid=2), ("900001", "INT", "D001"), "UNPAID_PREVIOUS"),
            (dict(existing={"no_rawat": "2026/10/18/000001", "no_reg": "001"}), ("900001", "INT", "D001"),
             "ALREADY_REGISTERED"),
        ]
        for kw, args, code in cases:
            with self.assertRaises(ApiError, msg=code) as ctx:
                svc(**kw).plan(*args, now=NOW)
            self.assertEqual(ctx.exception.code, code)
        # Tagihan belum closing diabaikan bila validasi closing nonaktif (seperti Khanza).
        self.assertTrue(svc(closing=False, unpaid=3).plan("900001", "INT", "D001", now=NOW))

    def test_register_retries_with_new_number_and_releases_lock(self):
        repo = FakeRepo(fail_inserts=2)
        plan = kr.VisitRegistrationService(repo).register("900001", "INT", "D001")
        self.assertEqual(len(repo.inserted), 1)
        self.assertTrue(plan.no_rawat.endswith("000005"))
        self.assertFalse(repo.locked)

    def test_register_gives_up_after_five_attempts(self):
        repo = FakeRepo(fail_inserts=10)
        with self.assertRaises(ApiError) as ctx:
            kr.VisitRegistrationService(repo).register("900001", "INT", "D001")
        self.assertEqual(ctx.exception.code, "REGISTRATION_FAILED")
        self.assertFalse(repo.locked)

    def test_options_only_from_jadwal_table(self):
        data = kr.VisitRegistrationService(FakeRepo()).options("900001", TODAY)
        # GIG tanpa jadwal & IGDK dikecualikan tidak muncul; poli yang praktik hari ini di atas.
        self.assertEqual([p["kd_poli"] for p in data["polies"]], ["INT", "BED"])
        self.assertEqual(data["hari"], "AKHAD")
        intp = data["polies"][0]
        self.assertEqual(intp["praktik_hari_ini"], 1)
        a, b = intp["doctors"]
        self.assertEqual((a["kd_dokter"], a["hari_ini"], a["selectable"], a["kuota"]), ("D001", ["08:00-12:00"], True, 20))
        self.assertEqual([s["hari"] for s in a["jadwal"]], ["AKHAD", "SENIN"])
        self.assertEqual((b["kd_dokter"], b["selectable"], b["reason"]), ("D002", False, "Tidak praktik hari ini"))
        self.assertFalse(data["polies"][1]["selectable"])  # BED: tidak ada praktik hari ini
        self.assertEqual(data["suggestions"][0]["kd_dokter"], "D001")
        self.assertIsNone(data["suggestions"][1]["kd_dokter"])  # kode tidak dikenal dibuang

    @override_settings(KHANZA_JADWAL_MODE="semua")
    def test_mode_semua_allows_any_scheduled_doctor(self):
        repo = FakeRepo(counts={"D001": 99})
        data = kr.VisitRegistrationService(repo).options("900001", TODAY)
        b = data["polies"][0]["doctors"][1]
        self.assertTrue(b["selectable"])
        self.assertEqual(b["reason"], "Di luar jadwal hari ini")
        plan = kr.VisitRegistrationService(repo).plan("900001", "INT", "D002", now=NOW)
        self.assertEqual(plan.nm_dokter, "dr. B")
        with self.assertRaises(ApiError):  # tetap wajib berjadwal di poli tsb
            kr.VisitRegistrationService(repo).plan("900001", "BED", "D001", now=NOW)


class KhanzaAuthTests(FaceTestCase):
    def _auth(self, info, username="12345", password="rahasia"):
        with mock.patch("apps.integrations.khanza_auth.check_credentials", return_value=info):
            return khanza_auth.KhanzaUserBackend().authenticate(None, username=username, password=password)

    def test_login_with_registrasi_right(self):
        user = self._auth({"admin": False, "registrasi": True, "nama": "Petugas Loket"})
        self.assertEqual(user.username, "kz.12345")
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.first_name, "Petugas Loket")
        self.assertTrue(user.has_perm("face_events.register_visit"))
        self.assertTrue(user.has_perm("face_events.verify_event"))
        self.assertFalse(user.has_perm("face_registry.manage_reference"))

    def test_login_denied_without_registrasi_right(self):
        self.assertIsNone(self._auth({"admin": False, "registrasi": False, "nama": "X"}))
        self.assertTrue(FaceAuditLog.objects.filter(action="LOGIN_KHANZA", outcome="DENIED").exists())
        self.assertFalse(User.objects.filter(username="kz.12345").exists())

    def test_wrong_password_and_invalid_id(self):
        self.assertIsNone(self._auth(None))
        self.assertIsNone(self._auth({"registrasi": True, "nama": ""}, username="x' OR 1=1 --"))

    def test_rights_resynced_each_login(self):
        user = self._auth({"admin": False, "registrasi": True, "nama": "A"})
        user.groups.add(*[g for g in []])
        with mock.patch("apps.integrations.khanza_auth.check_credentials",
                        return_value={"admin": False, "registrasi": False, "nama": "A"}):
            self.assertIsNone(khanza_auth.KhanzaUserBackend().authenticate(None, username="12345", password="p"))

    def test_db_error_returns_none(self):
        with mock.patch("apps.integrations.khanza_auth.check_credentials", side_effect=RuntimeError("down")):
            self.assertIsNone(khanza_auth.KhanzaUserBackend().authenticate(None, username="1", password="p"))

    def test_credentials_query_uses_khanza_aes_keys(self):
        self.assertIn("AES_ENCRYPT(%s,'nur')", khanza_auth.SQL_USER)
        self.assertIn("AES_ENCRYPT(%s,'windi')", khanza_auth.SQL_USER)


@override_settings(KHANZA_REGISTRATION_ENABLED=True)
class RegistrationApiTests(FaceTestCase):
    def setUp(self):
        super().setUp()
        self.event = FaceRecognitionEvent.objects.create(
            camera_id="c", track_id="t", match_status="CANDIDATE", quality_status="GOOD",
            verification_status="CONFIRMED", verified_no_rkm_medis="900001", verified_patient_name="PASIEN CONTOH")
        self.url = f"/api/khanza/v1/events/{self.event.pk}/registration/"
        self.repo = FakeRepo()
        patcher = mock.patch("apps.integrations.api.VisitRegistrationService",
                             side_effect=lambda: kr.VisitRegistrationService(self.repo))
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, path, body):
        return self.client.post(path, json.dumps(body), content_type="application/json")

    def test_permission_required(self):
        self.client.force_login(self.operator)  # operator lokal tanpa register_visit
        self.assertEqual(self.client.get(self.url + "options/").status_code, 403)

    def test_full_flow_and_double_click_protection(self):
        self.client.force_login(self.admin)
        opts = self.client.get(self.url + "options/").json()["data"]
        self.assertEqual(opts["patient"]["no_rkm_medis"], "900001")
        prev = self.post(self.url + "preview/", {"kd_poli": "INT", "kd_dokter": "D001"})
        self.assertEqual(prev.status_code, 200, prev.content)
        self.assertEqual(self.repo.inserted, [])  # pratinjau tidak menulis
        res = self.post(self.url, {"kd_poli": "INT", "kd_dokter": "D001"})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(len(self.repo.inserted), 1)
        self.event.refresh_from_db()
        self.assertEqual(self.event.khanza_no_rawat, res.json()["data"]["plan"]["no_rawat"])
        self.assertEqual(self.event.khanza_registered_by, "admin_uji")
        self.assertTrue(FaceAuditLog.objects.filter(action="KHANZA_VISIT_REGISTERED", outcome="SUCCESS").exists())
        again = self.post(self.url, {"kd_poli": "INT", "kd_dokter": "D001"})
        self.assertEqual(again.status_code, 409)
        self.assertEqual(len(self.repo.inserted), 1)
        detail = self.client.get(f"/api/face/v1/events/{self.event.pk}/").json()["data"]
        self.assertEqual(detail["khanza_visit"]["no_reg"], self.event.khanza_no_reg)

    def test_unverified_event_rejected(self):
        self.event.verification_status = "PENDING"
        self.event.save()
        self.client.force_login(self.admin)
        res = self.post(self.url, {"kd_poli": "INT", "kd_dokter": "D001"})
        self.assertEqual(res.json()["error"]["code"], "IDENTITY_NOT_VERIFIED")
        self.assertEqual(self.repo.inserted, [])

    def test_validation_and_failure_audit(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.post(self.url, {"kd_poli": "", "kd_dokter": "D001"}).status_code, 422)
        self.repo.inpatient = True
        res = self.post(self.url, {"kd_poli": "INT", "kd_dokter": "D001"})
        self.assertEqual(res.json()["error"]["code"], "INPATIENT")
        self.assertTrue(FaceAuditLog.objects.filter(action="KHANZA_VISIT_REGISTERED", outcome="FAILED").exists())

    @override_settings(KHANZA_REGISTRATION_ENABLED=False)
    def test_feature_disabled(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url + "options/").json()["error"]["code"], "FEATURE_DISABLED")
