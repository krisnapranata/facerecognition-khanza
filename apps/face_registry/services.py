"""Logika registrasi referensi wajah, pencabutan, retensi, dan cache referensi untuk pencocokan."""
from __future__ import annotations

import logging
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import timedelta

import cv2
import numpy as np
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from ai_worker import matcher, quality
from ai_worker.config import QualityConfig
from ai_worker.embedding import MODEL_VERSION
from apps.common import crypto, storage
from apps.common.api import ApiError
from apps.face_events import audit

from .engine import get_engine
from .models import PatientFaceReference, ProcessingBasis

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[A-Za-zÀ-ɏ .,'\-]{2,100}$")
RM_RE = re.compile(r"^[0-9A-Za-z.\-/]{1,20}$")
SESSION_CAPTURES_KEY = "face_captures"
REGISTRY_VERSION_KEY = "face_registry_version"


# --- validasi --------------------------------------------------------------
def allowed_processing_bases() -> list[ProcessingBasis]:
    if settings.KHANZA_ENABLED or not settings.DEMO_MODE:
        # Identitas pasien nyata dari Khanza: wajib persetujuan eksplisit.
        return [ProcessingBasis.PATIENT_CONSENT, ProcessingBasis.TEST_PARTICIPANT_CONSENT]
    return [ProcessingBasis.DEMO_SYNTHETIC, ProcessingBasis.TEST_PARTICIPANT_CONSENT]


def validate_registration_input(data: dict) -> dict:
    """Validasi input registrasi.

    Jika Khanza aktif, nomor RM wajib ada di tabel pasien Khanza dan nama diambil
    dari Khanza (bukan dari ketikan petugas). Jika tidak, mode demo mewajibkan RM UJI-xxxx.
    """
    errors = {}
    name = str(data.get("patient_name", "")).strip()
    rm = str(data.get("no_rkm_medis", "")).strip()
    basis = str(data.get("processing_basis", "")).strip()
    consent = data.get("consent_confirmed") is True
    if not RM_RE.match(rm):
        errors["no_rkm_medis"] = "Nomor RM 1-20 karakter (huruf, angka, titik, garis miring, tanda hubung)"
    elif not settings.KHANZA_ENABLED:
        rm = rm.upper()
        if settings.DEMO_MODE and not re.match(settings.FACE_DEMO_RM_PATTERN, rm):
            errors["no_rkm_medis"] = "Mode demo: nomor RM uji wajib berformat UJI-XXXX agar tidak menyerupai data produksi"
        if not NAME_RE.match(name):
            errors["patient_name"] = "Nama 2-100 karakter, hanya huruf, spasi, titik, koma, apostrof, tanda hubung"
    if basis not in allowed_processing_bases():
        errors["processing_basis"] = "Dasar pemrosesan tidak valid untuk mode ini"
    if not consent:
        errors["consent_confirmed"] = "Persetujuan/dasar pemrosesan wajib dikonfirmasi sebelum menyimpan biometrik"
    if errors:
        raise ApiError("VALIDATION_ERROR", "Data registrasi tidak valid", 422, errors)

    if settings.KHANZA_ENABLED:
        from apps.patient_lookup.services import ServiceUnavailable, get_patient_lookup_service

        try:
            patient = get_patient_lookup_service().get(rm)
        except ServiceUnavailable as exc:
            raise ApiError("SERVICE_UNAVAILABLE", f"Data pasien Khanza tidak dapat diperiksa: {exc}", 503)
        if patient is None:
            raise ApiError("VALIDATION_ERROR", "Data registrasi tidak valid", 422,
                           {"no_rkm_medis": "Nomor RM tidak ditemukan di data pasien Khanza"})
        rm, name = patient.no_rkm_medis, patient.nama[:100]
    return {"patient_name": name, "no_rkm_medis": rm, "processing_basis": basis}


def decode_image(data: bytes) -> np.ndarray:
    if not data:
        raise ApiError("INVALID_IMAGE", "Gambar kosong")
    if len(data) > settings.FACE_MAX_UPLOAD_BYTES:
        raise ApiError("IMAGE_TOO_LARGE", "Ukuran gambar melebihi batas", 413)
    if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
        raise ApiError("INVALID_IMAGE_TYPE", "Hanya JPEG atau PNG yang diterima", 415)
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ApiError("INVALID_IMAGE", "Gambar tidak dapat dibaca")
    h, w = image.shape[:2]
    if w < 160 or h < 160 or w > 4096 or h > 4096:
        raise ApiError("INVALID_IMAGE", "Resolusi gambar harus 160-4096 piksel")
    return image


def registration_quality_config() -> QualityConfig:
    cfg = QualityConfig.from_env()
    cfg.min_face_size = max(cfg.min_face_size, settings.FACE_REGISTRATION_MIN_FACE_SIZE)
    return cfg


# --- capture sementara --------------------------------------------------------
@dataclass
class CaptureResult:
    token: str
    quality: dict
    face_count: int
    acceptable: bool
    message: str


def _face_crop(image: np.ndarray, bbox, margin: float = 0.35) -> np.ndarray:
    """Potong wajah dengan margin (minimisasi data: tidak menyimpan seluruh frame)."""
    h, w = image.shape[:2]
    x, y, bw, bh = bbox
    mx, my = bw * margin, bh * margin
    x0, y0 = max(0, int(x - mx)), max(0, int(y - my))
    x1, y1 = min(w, int(x + bw + mx)), min(h, int(y + bh + my))
    return image[y0:y1, x0:x1]


def create_capture(request, image_bytes: bytes) -> CaptureResult:
    image = decode_image(image_bytes)
    engine = get_engine()
    detections = engine.detector.detect(image)
    if not detections:
        return CaptureResult("", {}, 0, False, "Wajah tidak terdeteksi. Pastikan wajah terlihat jelas.")
    if len(detections) > 1:
        return CaptureResult("", {}, len(detections), False,
                             "Terdeteksi lebih dari satu wajah. Pastikan hanya satu orang di depan kamera.")
    det = detections[0]
    q = quality.assess(image, det, registration_quality_config())
    # Simpan frame yang sudah di-encode ulang (membuang metadata EXIF) dalam bentuk terenkripsi.
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        raise ApiError("INVALID_IMAGE", "Gambar gagal diproses")
    path = storage.save_encrypted(encoded.tobytes(), storage.TEMP_DIR)
    token = secrets.token_urlsafe(24)
    captures = request.session.get(SESSION_CAPTURES_KEY, {})
    # Hapus capture lama milik sesi ini (hanya satu capture aktif per sesi).
    for old in captures.values():
        storage.delete(old.get("path", ""))
    captures = {token: {
        "path": path,
        "detection": det.raw.tolist(),
        "quality": q.as_dict(),
        "created": timezone.now().timestamp(),
    }}
    request.session[SESSION_CAPTURES_KEY] = captures
    request.session.modified = True
    message = "Kualitas foto baik" if q.ok else "Kualitas foto kurang: " + ", ".join(q.as_dict()["reason_labels"])
    return CaptureResult(token, q.as_dict(), 1, q.ok, message)


def get_capture(request, token: str) -> dict:
    captures = request.session.get(SESSION_CAPTURES_KEY, {})
    entry = captures.get(token) if isinstance(token, str) else None
    if not entry:
        raise ApiError("CAPTURE_NOT_FOUND", "Foto sementara tidak ditemukan atau sudah kedaluwarsa", 404)
    age = timezone.now().timestamp() - float(entry.get("created", 0))
    if age > settings.FACE_CAPTURE_TTL_SECONDS:
        discard_capture(request, token)
        raise ApiError("CAPTURE_EXPIRED", "Foto sementara kedaluwarsa, ambil ulang", 410)
    return entry


def discard_capture(request, token: str) -> bool:
    captures = request.session.get(SESSION_CAPTURES_KEY, {})
    entry = captures.pop(token, None)
    request.session[SESSION_CAPTURES_KEY] = captures
    request.session.modified = True
    if entry:
        storage.delete(entry.get("path", ""))
        return True
    return False


def capture_preview_jpeg(request, token: str) -> bytes:
    entry = get_capture(request, token)
    return storage.read_decrypted(entry["path"])


# --- registrasi --------------------------------------------------------------
def register_reference(request, data: dict) -> PatientFaceReference:
    user = request.user
    cleaned = validate_registration_input(data)
    token = data.get("capture_token")
    entry = get_capture(request, token)
    if entry["quality"].get("status") != quality.GOOD:
        raise ApiError("QUALITY_INSUFFICIENT", "Kualitas foto tidak memenuhi syarat, silakan ambil ulang", 422)

    engine = get_engine()
    image = cv2.imdecode(np.frombuffer(storage.read_decrypted(entry["path"]), np.uint8), cv2.IMREAD_COLOR)
    from ai_worker.detector import Detection

    det = Detection.from_row(np.asarray(entry["detection"], dtype=np.float32))
    vector = engine.embedder.embed(image, det)
    ciphertext = crypto.encrypt_embedding(vector, MODEL_VERSION)
    ok, crop_jpeg = cv2.imencode(".jpg", _face_crop(image, det.bbox), [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise ApiError("INVALID_IMAGE", "Gambar gagal diproses")

    photo_path = storage.save_encrypted(crop_jpeg.tobytes(), storage.REFERENCE_DIR)
    try:
        with transaction.atomic(using=settings.FACE_DB_ALIAS):
            ref = PatientFaceReference.objects.create(
                patient_name=cleaned["patient_name"],
                no_rkm_medis=cleaned["no_rkm_medis"],
                reference_image=photo_path,
                embedding_ciphertext=ciphertext,
                model_version=MODEL_VERSION,
                is_active=True,
                processing_basis=cleaned["processing_basis"],
                is_test_data=settings.DEMO_MODE and not settings.KHANZA_ENABLED,
                registered_by=user.get_username(),
                quality_score=entry["quality"].get("score"),
                expires_at=timezone.now() + timedelta(days=settings.FACE_RETENTION_DAYS)
                if settings.FACE_RETENTION_DAYS > 0 else None,
            )
    except Exception:
        storage.delete(photo_path)
        raise
    finally:
        discard_capture(request, token)
    bump_registry_version()
    audit.log(user, "REFERENCE_REGISTERED", audit.SUCCESS, "PatientFaceReference", ref.pk,
              f"basis={ref.processing_basis}", request)
    return ref


def deactivate_reference(ref: PatientFaceReference, actor, request=None, reason: str = "") -> None:
    ref.is_active = False
    ref.save(update_fields=["is_active", "updated_at"])
    bump_registry_version()
    audit.log(actor, "REFERENCE_DEACTIVATED", audit.SUCCESS, "PatientFaceReference", ref.pk, reason, request)


def delete_reference(ref: PatientFaceReference, actor, request=None, reason: str = "") -> None:
    """Hapus permanen referensi beserta file foto. Riwayat kejadian tetap ada tanpa tautan identitas."""
    ref_id, path = ref.pk, ref.reference_image
    with transaction.atomic(using=settings.FACE_DB_ALIAS):
        ref.delete()
    file_removed = storage.delete(path)
    bump_registry_version()
    audit.log(actor, "REFERENCE_DELETED", audit.SUCCESS, "PatientFaceReference", ref_id,
              f"{reason} file_removed={file_removed}".strip(), request)


def purge_expired(now=None, dry_run: bool = False) -> list[int]:
    now = now or timezone.now()
    expired = list(PatientFaceReference.objects.filter(expires_at__isnull=False, expires_at__lte=now))
    ids = [r.pk for r in expired]
    if not dry_run:
        for ref in expired:
            delete_reference(ref, "system", reason="retensi berakhir")
    return ids


# --- cache referensi untuk pencocokan ------------------------------------------
def bump_registry_version() -> None:
    cache.set(REGISTRY_VERSION_KEY, secrets.token_hex(8), timeout=None)


class ReferenceCache:
    """Embedding referensi aktif yang sudah didekripsi, disimpan di memori proses saja."""

    def __init__(self):
        self._lock = threading.Lock()
        self._version = None
        self._loaded_at = None
        self._vectors: list[matcher.ReferenceVector] = []

    def get(self) -> list[matcher.ReferenceVector]:
        version = cache.get(REGISTRY_VERSION_KEY)
        now = timezone.now()
        with self._lock:
            stale = (self._loaded_at is None or version != self._version
                     or (now - self._loaded_at).total_seconds() > 60)
            if stale:
                self._vectors = self._load(now)
                self._version = version
                self._loaded_at = now
            return self._vectors

    def clear(self) -> None:
        with self._lock:
            self._loaded_at = None
            self._vectors = []

    @staticmethod
    def _load(now) -> list[matcher.ReferenceVector]:
        vectors = []
        rows = PatientFaceReference.objects.active(now).filter(model_version=MODEL_VERSION) \
            .values_list("id", "no_rkm_medis", "embedding_ciphertext")
        for ref_id, rm, ciphertext in rows:
            try:
                vec = crypto.decrypt_embedding(ciphertext, MODEL_VERSION)
                vectors.append(matcher.ReferenceVector(ref_id, rm, matcher.validate_embedding(vec)))
            except Exception:
                logger.error("Referensi #%s tidak dapat didekripsi/divalidasi; dilewati", ref_id)
        return vectors


reference_cache = ReferenceCache()


def current_thresholds() -> matcher.Thresholds:
    return matcher.Thresholds(
        candidate=settings.FACE_MATCH_THRESHOLD,
        review=settings.FACE_REVIEW_THRESHOLD,
        margin=settings.FACE_MATCH_MARGIN,
    )
