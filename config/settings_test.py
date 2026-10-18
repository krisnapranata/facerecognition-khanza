"""Pengaturan uji: semua alias memakai SQLite in-memory.

Pengujian tidak menyentuh MySQL customsik maupun database Khanza dan tidak
membutuhkan kamera fisik.
"""
import os
import tempfile

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key-not-for-production-0123456789")
os.environ.setdefault("FACE_EMBEDDING_KEYS", "q0lXwVZ2s1o5mY3L9XyFJ8rC4sQe6T1uV7wZ2aB3cD0=")
os.environ.setdefault("FACE_WORKER_SECRET", "test-worker-secret")
os.environ["KHANZA_ENABLED"] = "false"
os.environ["KHANZA_JADWAL_MODE"] = "hari_ini"  # jangan bergantung pada .env lokal
os.environ["APP_MODE"] = "demo"

from .settings import *  # noqa: E402,F401,F403

_TMP = tempfile.mkdtemp(prefix="facerec-test-")
DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    "customsik": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}
MEDIA_PRIVATE_ROOT = Path(_TMP) / "media_private"  # noqa: F405
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
FACE_WORKER_URL = "http://127.0.0.1:9"  # port tertutup: worker dianggap mati
FACE_STATION_ASYNC_MATCH = False  # hasil pencocokan langsung tersedia dalam respons uji
LOGGING = {"version": 1, "disable_existing_loggers": False, "root": {"level": "CRITICAL"}}
