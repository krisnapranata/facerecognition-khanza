"""Model database, migrasi aman, router."""
from django.apps import apps
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations import AddField, AddIndex, AlterModelOptions, CreateModel
from django.test import SimpleTestCase

from config.routers import FACE_APP_LABELS, FaceDatabaseRouter
from apps.face_events.models import FaceAuditLog, FaceRecognitionEvent

from .helpers import FaceTestCase, make_reference

# Label app yang SUDAH ada di django_migrations customsik (hasil audit) — tidak boleh dipakai.
EXISTING_CUSTOMSIK_LABELS = {"admin", "auth", "berkasdigital", "contenttypes", "dashboard", "idrgapp", "keuangan",
                             "klaimidrg", "master", "operasi", "pelayanan", "penunjang", "registrasi", "satusehat",
                             "sessions", "telegrambot"}


class MigrationSafetyTests(SimpleTestCase):
    def test_face_migrations_only_create_face_tables(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        for (app_label, name), migration in loader.disk_migrations.items():
            if app_label not in FACE_APP_LABELS:
                continue
            for op in migration.operations:
                self.assertIsInstance(op, (CreateModel, AddIndex, AddField, AlterModelOptions), f"{app_label}.{name}: {op}")
                if isinstance(op, CreateModel):
                    self.assertTrue(op.options["db_table"].startswith("face_"))

    def test_table_names(self):
        tables = {m._meta.db_table for label in FACE_APP_LABELS for m in apps.get_app_config(label).get_models()}
        self.assertEqual(tables, {"face_patient_reference", "face_recognition_event", "face_audit_log"})

    def test_app_labels_do_not_collide_with_customsik(self):
        ours = {cfg.label for cfg in apps.get_app_configs() if cfg.name.startswith("apps.")}
        self.assertFalse(ours & EXISTING_CUSTOMSIK_LABELS, ours & EXISTING_CUSTOMSIK_LABELS)

    def test_router(self):
        r = FaceDatabaseRouter()
        self.assertTrue(r.allow_migrate("customsik", "face_registry"))
        self.assertFalse(r.allow_migrate("default", "face_registry"))
        self.assertFalse(r.allow_migrate("customsik", "auth"))
        self.assertFalse(r.allow_migrate("customsik", "sessions"))
        self.assertTrue(r.allow_migrate("default", "auth"))
        self.assertFalse(r.allow_migrate("khanza", "face_events"))
        self.assertFalse(r.allow_migrate("khanza", "auth"))
        self.assertEqual(r.db_for_write(FaceRecognitionEvent), "customsik")
        self.assertNotEqual(r.db_for_write(FaceAuditLog), "khanza")


class ModelTests(FaceTestCase):
    def test_create_reference_event_audit(self):
        ref = make_reference()
        event = FaceRecognitionEvent.objects.create(camera_id="cam", track_id="t-1", candidate_reference=ref,
                                                    match_status="CANDIDATE", quality_status="GOOD")
        self.assertEqual(event.verification_status, "PENDING")
        self.assertEqual(event.control_status, "NOT_CHECKED")
        log = FaceAuditLog.objects.create(actor="x", action="TEST", outcome="SUCCESS")
        self.assertIsNotNone(log.timestamp)
        # Event tidak memiliki kolom embedding/frame.
        names = {f.name for f in FaceRecognitionEvent._meta.get_fields()}
        self.assertFalse({"embedding", "embedding_ciphertext", "frame", "image"} & names)

    def test_reference_delete_keeps_event_history(self):
        ref = make_reference()
        event = FaceRecognitionEvent.objects.create(camera_id="cam", track_id="t-1", candidate_reference=ref,
                                                    match_status="CANDIDATE", quality_status="GOOD")
        ref.delete()
        event.refresh_from_db()
        self.assertIsNone(event.candidate_reference)

    def test_active_queryset_excludes_expired(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.face_registry.models import PatientFaceReference

        make_reference("UJI-0001")
        make_reference("UJI-0002", expires_at=timezone.now() - timedelta(days=1))
        make_reference("UJI-0003", active=False)
        self.assertEqual(list(PatientFaceReference.objects.active().values_list("no_rkm_medis", flat=True)),
                         ["UJI-0001"])
