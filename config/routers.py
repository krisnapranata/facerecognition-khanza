"""Router database.

- App ``face_registry`` dan ``face_events`` -> alias ``customsik``.
- App framework Django -> ``default`` (SQLite lokal), tidak pernah ke ``customsik``.
- Alias ``khanza``/``khanza_rw`` tidak pernah dimigrasi; penulisan Khanza hanya lewat
  apps/integrations/khanza_rw.py (INSERT reg_periksa, UPDATE pasien.umur).
"""
from django.conf import settings

FACE_APP_LABELS = frozenset({"face_registry", "face_events"})
READ_ONLY_ALIASES = frozenset({"khanza", "khanza_rw"})  # tidak pernah dimigrasi


class FaceDatabaseRouter:
    @property
    def face_alias(self) -> str:
        return getattr(settings, "FACE_DB_ALIAS", "customsik")

    def db_for_read(self, model, **hints):
        if model._meta.app_label in FACE_APP_LABELS:
            return self.face_alias
        return "default"

    def db_for_write(self, model, **hints):
        if model._meta.app_label in FACE_APP_LABELS:
            return self.face_alias
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        a = obj1._meta.app_label in FACE_APP_LABELS
        b = obj2._meta.app_label in FACE_APP_LABELS
        return a == b

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db in READ_ONLY_ALIASES:
            return False
        if app_label in FACE_APP_LABELS:
            return db == self.face_alias
        return db == "default"
