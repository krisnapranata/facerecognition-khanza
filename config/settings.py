"""Pengaturan Django SIMRS Face Recognition.

Semua nilai sensitif dibaca dari environment (.env). Tidak ada password
atau kunci di source code.

Pembagian database:
- ``default``   : SQLite lokal untuk tabel framework Django (auth, session,
                  admin, contenttypes) milik aplikasi ini. Sengaja TIDAK
                  memakai tabel auth_* / django_* yang sudah ada di
                  ``customsik`` karena tabel itu milik aplikasi SIMRS lain.
- ``customsik`` : MySQL/MariaDB; hanya tabel baru ``face_*``.
- ``khanza``    : opsional, baca-saja, nonaktif pada mode demo.
"""
from pathlib import Path
import os

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=False)
except ImportError:  # pragma: no cover
    pass


def env(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        raise ImproperlyConfigured(f"Environment variable {name} wajib diisi (lihat .env.example)")
    return value


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name, default):
    return float(os.environ.get(name, default))


def env_int(name, default):
    return int(os.environ.get(name, default))


APP_MODE = env("APP_MODE", "demo").lower()  # demo | production
if APP_MODE not in {"demo", "production"}:
    raise ImproperlyConfigured("APP_MODE harus 'demo' atau 'production'")
DEMO_MODE = APP_MODE == "demo"

SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
DEBUG = env_bool("DJANGO_DEBUG", False)
if not DEMO_MODE and DEBUG:
    raise ImproperlyConfigured("DJANGO_DEBUG harus False pada APP_MODE=production")
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]
# Di belakang nginx (Docker): origin HTTPS yang dipakai PC loket, mis. https://facerec.rs.local,https://192.168.1.20
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
if env_bool("BEHIND_TLS_PROXY", False):
    # nginx meneruskan X-Forwarded-Proto=https; hanya aktifkan bila Django tidak terekspos langsung.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.common",
    "apps.face_registry",
    "apps.face_events",
    "apps.dashboard",
    "apps.patient_lookup",
    "apps.integrations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.common.context_processors.app_context",
            ],
        },
    },
]

VAR_DIR = Path(env("APP_VAR_DIR", str(BASE_DIR / "var")))

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": VAR_DIR / "django_local.sqlite3",
    },
    "customsik": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("DB_NAME", "customsik"),
        "USER": env("DB_USER", "sik"),
        "PASSWORD": env("DB_PASSWORD", ""),
        "HOST": env("DB_HOST", "127.0.0.1"),
        "PORT": env("DB_PORT", "3306"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        },
        "TEST": {"NAME": None},
    },
}

KHANZA_ENABLED = env_bool("KHANZA_ENABLED", False)
KHANZA_SCHEMA_VERIFIED = env_bool("KHANZA_SCHEMA_VERIFIED", False)
KHANZA_QUERY_FILE = env("KHANZA_QUERY_FILE", "")
# Izinkan user Khanza yang punya hak tulis (mis. akun 'sik' yang sudah ada) untuk koneksi baca.
# Koneksi tetap dikunci READ ONLY per sesi; face_khanza_check hanya memberi peringatan.
KHANZA_ALLOW_PRIVILEGED_USER = env_bool("KHANZA_ALLOW_PRIVILEGED_USER", False)
if KHANZA_ENABLED:
    DATABASES["khanza"] = {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("KHANZA_DB_NAME", required=True),
        "USER": env("KHANZA_DB_USER", required=True),  # WAJIB user baca-saja
        "PASSWORD": env("KHANZA_DB_PASSWORD", ""),
        "HOST": env("KHANZA_DB_HOST", "127.0.0.1"),
        "PORT": env("KHANZA_DB_PORT", "3306"),
        "OPTIONS": {"charset": "utf8mb4", "init_command": "SET SESSION TRANSACTION READ ONLY"},
    }

# Registrasi kunjungan (reg_periksa) & login akun tabel `user` Khanza memakai koneksi tulis.
# Sesuai keputusan operator memakai akun MySQL yang sama dengan Khanza desktop; aplikasi
# membatasi diri di kode (apps/integrations/khanza_rw.py): hanya INSERT reg_periksa dan
# UPDATE pasien.umur yang diizinkan.
KHANZA_REGISTRATION_ENABLED = KHANZA_ENABLED and env_bool("KHANZA_REGISTRATION_ENABLED", False)
KHANZA_AUTH_ENABLED = KHANZA_ENABLED and env_bool("KHANZA_AUTH_ENABLED", False)
KHANZA_BASENOREG = env("KHANZA_BASENOREG", "booking")  # booking | registrasi (setting/database.xml Khanza)
KHANZA_URUTNOREG = env("KHANZA_URUTNOREG", "dokter + poli")  # poli | dokter | dokter + poli
KHANZA_EXCLUDED_POLI = [p.strip() for p in env("KHANZA_EXCLUDED_POLI", "IGDK").split(",") if p.strip()]
# Pilihan dokter/poli selalu dari tabel jadwal. hari_ini: hanya dokter yang praktik hari ini
# (cek kuota seperti DlgReg saat JADWALDOKTERDIREGISTRASI aktif); semua: semua dokter berjadwal.
KHANZA_JADWAL_MODE = env("KHANZA_JADWAL_MODE", "hari_ini")
if KHANZA_JADWAL_MODE not in {"hari_ini", "semua"}:
    raise ImproperlyConfigured("KHANZA_JADWAL_MODE harus 'hari_ini' atau 'semua'")
if KHANZA_REGISTRATION_ENABLED or KHANZA_AUTH_ENABLED:
    DATABASES["khanza_rw"] = {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("KHANZA_RW_DB_NAME", DATABASES["khanza"]["NAME"]),
        "USER": env("KHANZA_RW_DB_USER", required=True),
        "PASSWORD": env("KHANZA_RW_DB_PASSWORD", ""),
        "HOST": env("KHANZA_RW_DB_HOST", DATABASES["khanza"]["HOST"]),
        "PORT": env("KHANZA_RW_DB_PORT", DATABASES["khanza"]["PORT"]),
        "OPTIONS": {"charset": "utf8mb4"},
    }

AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
if KHANZA_AUTH_ENABLED:
    AUTHENTICATION_BACKENDS.append("apps.integrations.khanza_auth.KhanzaUserBackend")

DATABASE_ROUTERS = ["config.routers.FaceDatabaseRouter"]
FACE_DB_ALIAS = "customsik"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "id"
TIME_ZONE = env("TIME_ZONE", "Asia/Jakarta")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = Path(env("STATIC_ROOT", str(VAR_DIR / "staticfiles")))  # disajikan nginx di produksi

# Tidak ada MEDIA_URL: foto biometrik tidak pernah disajikan langsung sebagai file.
MEDIA_PRIVATE_ROOT = Path(env("MEDIA_PRIVATE_ROOT", str(BASE_DIR / "media_private")))

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard:index"
LOGOUT_REDIRECT_URL = "login"

# Nama cookie berbeda dari aplikasi SIMRS lain di host yang sama (cookie tidak dipisah per port).
SESSION_COOKIE_NAME = "facerec_sessionid"
CSRF_COOKIE_NAME = "facerec_csrftoken"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 8 * 3600)
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEMO_MODE)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEMO_MODE)
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

CACHES = {
    "default": {
        # Berbasis file agar status worker, nonce, dan rate limit terbagi antarproses.
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": str(VAR_DIR / "cache"),
        "TIMEOUT": 300,
    }
}

# --- Pengenalan wajah --------------------------------------------------------
FACE_EMBEDDING_KEYS = [k.strip() for k in env("FACE_EMBEDDING_KEYS", required=True).split(",") if k.strip()]
FACE_WORKER_SECRET = env("FACE_WORKER_SECRET", required=True)
FACE_WORKER_URL = env("FACE_WORKER_URL", "http://127.0.0.1:8765").rstrip("/")
# Sumber kamera default di dashboard: "usb" = kamera USB di PC loket (via browser, butuh HTTPS),
# "server" = kamera yang dibaca AI worker (webcam server / IP camera RTSP).
FACE_DEFAULT_CAMERA = env("FACE_DEFAULT_CAMERA", "server")
if FACE_DEFAULT_CAMERA not in {"usb", "server"}:
    raise ImproperlyConfigured("FACE_DEFAULT_CAMERA harus 'usb' atau 'server'")
FACE_STATION_MAX_FPS = env_float("FACE_STATION_MAX_FPS", 6.0)
# Pencocokan & tulis DB dari stasiun kamera USB di background (False hanya untuk pengujian).
FACE_STATION_ASYNC_MATCH = env_bool("FACE_STATION_ASYNC_MATCH", True)
FACE_STATION_IDLE_SECONDS = env_int("FACE_STATION_IDLE_SECONDS", 60)
FACE_STATION_MAX_FRAME_BYTES = env_int("FACE_STATION_MAX_FRAME_BYTES", 1024 * 1024)
FACE_DETECTOR_MODEL = env("FACE_DETECTOR_MODEL", "models/face_detection_yunet_2023mar.onnx")
FACE_EMBEDDING_MODEL = env("FACE_EMBEDDING_MODEL", "models/face_recognition_sface_2021dec.onnx")
FACE_EMBEDDING_BACKEND = env("FACE_EMBEDDING_BACKEND", "onnxruntime")
FACE_MATCH_THRESHOLD = env_float("FACE_MATCH_THRESHOLD", 0.50)
FACE_REVIEW_THRESHOLD = env_float("FACE_REVIEW_THRESHOLD", 0.363)
FACE_MATCH_MARGIN = env_float("FACE_MATCH_MARGIN", 0.08)
FACE_RETENTION_DAYS = env_int("FACE_RETENTION_DAYS", 90)
FACE_EVENT_MERGE_SECONDS = env_int("FACE_EVENT_MERGE_SECONDS", 120)
FACE_WORKER_STALE_SECONDS = env_float("FACE_WORKER_STALE_SECONDS", 6.0)
FACE_MAX_UPLOAD_BYTES = env_int("FACE_MAX_UPLOAD_BYTES", 5 * 1024 * 1024)
FACE_CAPTURE_TTL_SECONDS = env_int("FACE_CAPTURE_TTL_SECONDS", 900)
FACE_REGISTRATION_MIN_FACE_SIZE = env_int("FACE_REGISTRATION_MIN_FACE_SIZE", 80)
# Pada mode demo, nomor RM wajib berawalan UJI- agar tidak menyerupai data produksi.
FACE_DEMO_RM_PATTERN = r"^UJI-[0-9A-Z]{1,12}$"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}
