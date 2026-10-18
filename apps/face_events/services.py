"""Penerimaan kandidat dari AI worker, verifikasi petugas, status kontrol, dan status worker."""
from __future__ import annotations

import logging
import re
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from ai_worker import matcher
from ai_worker.embedding import EMBEDDING_DIM, MODEL_VERSION
from apps.common.api import ApiError
from apps.face_registry.models import PatientFaceReference
from apps.face_registry.services import current_thresholds, reference_cache
from apps.patient_lookup.services import ServiceUnavailable, get_control_schedule_service, get_patient_lookup_service

from . import audit
from .models import ControlStatus, FaceRecognitionEvent, MatchStatus, VerificationStatus

logger = logging.getLogger(__name__)

ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
WORKER_STATUS_KEY = "face_worker_status"
STATUS_RANK = {MatchStatus.ERROR: 0, MatchStatus.NO_MATCH: 1, MatchStatus.NEEDS_REVIEW: 2, MatchStatus.CANDIDATE: 3}


# --- kandidat dari worker ------------------------------------------------------
def validate_candidate_payload(data: dict) -> dict:
    errors = {}
    camera_id = data.get("camera_id")
    track_id = data.get("track_id")
    if not isinstance(camera_id, str) or not ID_RE.match(camera_id):
        errors["camera_id"] = "wajib, 1-64 karakter [A-Za-z0-9_.-]"
    if not isinstance(track_id, str) or not ID_RE.match(track_id):
        errors["track_id"] = "wajib, 1-64 karakter [A-Za-z0-9_.-]"
    if not isinstance(data.get("model_version"), str):
        errors["model_version"] = "wajib"
    embedding = data.get("embedding")
    if not isinstance(embedding, list) or len(embedding) != EMBEDDING_DIM or \
            not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in embedding):
        errors["embedding"] = f"wajib berupa daftar {EMBEDDING_DIM} angka"
    if data.get("quality_status") != "GOOD":
        errors["quality_status"] = "hanya wajah berkualitas GOOD yang boleh dicocokkan"
    if errors:
        raise ApiError("VALIDATION_ERROR", "Payload kandidat tidak valid", 422, errors)
    return data


def ingest_candidate(data: dict) -> FaceRecognitionEvent:
    validate_candidate_payload(data)
    result = matcher.match(
        data["embedding"], reference_cache.get(), current_thresholds(),
        model_version=data["model_version"], expected_version=MODEL_VERSION,
    )
    # Referensi divalidasi ulang di server; ID kandidat tidak pernah diambil dari client.
    reference = None
    if result.reference_id is not None:
        reference = PatientFaceReference.objects.active().filter(pk=result.reference_id).first()
        if reference is None:
            result = matcher.MatchResult(matcher.NO_MATCH, reason="referensi tidak lagi aktif")

    since = timezone.now() - timedelta(seconds=settings.FACE_EVENT_MERGE_SECONDS)
    with transaction.atomic(using=settings.FACE_DB_ALIAS):
        event = (FaceRecognitionEvent.objects.select_for_update()
                 .filter(camera_id=data["camera_id"], track_id=data["track_id"],
                         verification_status=VerificationStatus.PENDING, created_at__gte=since)
                 .order_by("-created_at").first())
        fields = {
            "candidate_reference": reference,
            "match_status": result.status,
            "quality_status": data["quality_status"],
            "similarity": None if result.score is None else round(result.score, 4),
            "second_similarity": None if result.second_score is None else round(result.second_score, 4),
            "model_version": data["model_version"][:64],
            "match_note": result.reason[:255],
        }
        if event is None:
            event = FaceRecognitionEvent.objects.create(camera_id=data["camera_id"], track_id=data["track_id"],
                                                        **fields)
        else:
            event.submissions += 1
            better = (STATUS_RANK[result.status], result.score or -1) > \
                     (STATUS_RANK[MatchStatus(event.match_status)], event.similarity or -1)
            update = ["submissions", "updated_at"]
            if better:
                for key, value in fields.items():
                    setattr(event, key, value)
                update += list(fields.keys())
            event.save(update_fields=update)
    return event


# --- verifikasi petugas ----------------------------------------------------------
def determine_control_status(event: FaceRecognitionEvent) -> tuple[str, str]:
    """Status kontrol hanya ditentukan setelah identitas terverifikasi."""
    if event.verification_status not in (VerificationStatus.CONFIRMED, VerificationStatus.MANUAL):
        return ControlStatus.IDENTITY_NOT_VERIFIED, "Identitas belum diverifikasi petugas"
    rm = event.verified_no_rkm_medis
    if not rm:
        return ControlStatus.NEEDS_MANUAL_CHECK, "Nomor RM terverifikasi tidak tersedia"
    try:
        result = get_control_schedule_service().find_control(rm)
    except ServiceUnavailable as exc:
        return ControlStatus.NEEDS_MANUAL_CHECK, f"Layanan jadwal tidak tersedia: {exc}"[:255]
    except Exception:
        logger.exception("Pemeriksaan jadwal kontrol gagal")
        return ControlStatus.NEEDS_MANUAL_CHECK, "Pemeriksaan jadwal gagal; periksa manual"
    if result.found:
        return ControlStatus.CONTROL_FOUND, result.detail[:255]
    return ControlStatus.CONTROL_NOT_FOUND, result.detail[:255]


def verify_event(event_id: int, user, data: dict, request=None) -> FaceRecognitionEvent:
    decision = str(data.get("decision", "")).upper()
    note = str(data.get("note", "")).strip()[:255]
    if decision not in (VerificationStatus.CONFIRMED, VerificationStatus.REJECTED, VerificationStatus.MANUAL):
        raise ApiError("VALIDATION_ERROR", "Keputusan verifikasi tidak valid", 422,
                       {"decision": "CONFIRMED, REJECTED, atau MANUAL"})
    with transaction.atomic(using=settings.FACE_DB_ALIAS):
        event = FaceRecognitionEvent.objects.select_for_update().filter(pk=event_id).first()
        if event is None:
            raise ApiError("NOT_FOUND", "Kejadian tidak ditemukan", 404)
        if event.verification_status != VerificationStatus.PENDING:
            raise ApiError("ALREADY_VERIFIED", "Kejadian sudah diverifikasi", 409)

        if decision == VerificationStatus.CONFIRMED:
            ref = event.candidate_reference
            if ref is None or event.match_status not in (MatchStatus.CANDIDATE, MatchStatus.NEEDS_REVIEW):
                raise ApiError("NO_CANDIDATE", "Tidak ada kandidat yang dapat dikonfirmasi", 409)
            event.verified_no_rkm_medis = ref.no_rkm_medis
            event.verified_patient_name = ref.patient_name
        elif decision == VerificationStatus.MANUAL:
            rm = str(data.get("no_rkm_medis", "")).strip().upper()
            if not rm:
                raise ApiError("VALIDATION_ERROR", "Nomor RM wajib untuk identifikasi manual", 422,
                               {"no_rkm_medis": "wajib"})
            patient = get_patient_lookup_service().get(rm)
            if patient is None:
                raise ApiError("PATIENT_NOT_FOUND", "Pasien tidak ditemukan", 404, {"no_rkm_medis": "tidak ditemukan"})
            event.verified_no_rkm_medis = patient.no_rkm_medis
            event.verified_patient_name = patient.nama[:100]

        event.verification_status = decision
        event.verified_by = user.get_username()
        event.verified_at = timezone.now()
        event.verification_note = note
        event.control_status, event.control_detail = determine_control_status(event)
        event.save()
    audit.log(user, "EVENT_VERIFIED", audit.SUCCESS, "FaceRecognitionEvent", event.pk,
              f"decision={decision} control={event.control_status}", request)
    return event


# --- serialisasi untuk browser (tanpa embedding) ----------------------------------
def identity_visible(event: FaceRecognitionEvent, user) -> bool:
    return (event.match_status in (MatchStatus.CANDIDATE, MatchStatus.NEEDS_REVIEW)
            and event.candidate_reference_id is not None
            and user.has_perm("face_registry.view_candidate_identity"))


def candidate_identity(event: FaceRecognitionEvent) -> dict | None:
    """Identitas kandidat untuk worker (label di overlay kamera).

    Worker tidak pernah memakai ini untuk pencocokan atau penyimpanan, dan
    hanya menggambarnya bila token stream-nya terikat izin melihat identitas.
    """
    if event.match_status not in (MatchStatus.CANDIDATE, MatchStatus.NEEDS_REVIEW):
        return None
    ref = event.candidate_reference
    if ref is None:
        return None
    return {"patient_name": ref.patient_name, "no_rkm_medis": ref.no_rkm_medis,
            "is_test_data": ref.is_test_data}


def _iso(dt):
    return timezone.localtime(dt).isoformat(timespec="seconds") if dt else None


def serialize_event(event: FaceRecognitionEvent, user) -> dict:
    data = {
        "id": event.pk,
        "camera_id": event.camera_id,
        "track_id": event.track_id,
        "match_status": event.match_status,
        "match_status_label": event.get_match_status_display(),
        "quality_status": event.quality_status,
        "similarity": event.similarity,
        "second_similarity": event.second_similarity,
        "match_note": event.match_note,
        "submissions": event.submissions,
        "verification_status": event.verification_status,
        "verification_status_label": event.get_verification_status_display(),
        "verified_by": event.verified_by,
        "verified_at": _iso(event.verified_at),
        "control_status": event.control_status,
        "control_status_label": event.get_control_status_display(),
        "control_detail": event.control_detail,
        "created_at": _iso(event.created_at),
        "updated_at": _iso(event.updated_at),
        "candidate": None,
        "verified_identity": None,
        "khanza_visit": {"no_rawat": event.khanza_no_rawat, "no_reg": event.khanza_no_reg,
                         "registered_by": event.khanza_registered_by,
                         "registered_at": _iso(event.khanza_registered_at)} if event.khanza_no_rawat else None,
    }
    if identity_visible(event, user):
        ref = event.candidate_reference
        data["candidate"] = {
            "reference_id": ref.pk,
            "patient_name": ref.patient_name,
            "no_rkm_medis": ref.no_rkm_medis,
            "is_test_data": ref.is_test_data,
            "reference_active": ref.is_active,
        }
    if event.verified_no_rkm_medis and user.has_perm("face_registry.view_candidate_identity"):
        data["verified_identity"] = {"no_rkm_medis": event.verified_no_rkm_medis,
                                     "patient_name": event.verified_patient_name}
    return data


# --- status worker -----------------------------------------------------------------
def store_worker_heartbeat(payload: dict) -> None:
    allowed = {k: payload.get(k) for k in ("camera_id", "camera", "model", "inference_fps", "faces", "tracks",
                                           "stopping")}
    tracks = allowed.get("tracks") or []
    allowed["tracks"] = tracks[:20] if isinstance(tracks, list) else []
    allowed["received_at"] = time.time()
    cache.set(WORKER_STATUS_KEY, allowed, timeout=3600)


def worker_status() -> dict:
    data = cache.get(WORKER_STATUS_KEY)
    if not data:
        return {"alive": False, "state": "OFFLINE", "message": "AI worker belum pernah terhubung",
                "camera": {"state": "UNKNOWN", "connected": False}, "faces": 0, "tracks": []}
    age = time.time() - float(data.get("received_at", 0))
    alive = age <= settings.FACE_WORKER_STALE_SECONDS and not data.get("stopping")
    camera = data.get("camera") or {}
    result = {
        "alive": alive,
        "state": "RUNNING" if alive else "OFFLINE",
        "last_seen_seconds": round(age, 1),
        "camera_id": data.get("camera_id"),
        "model": data.get("model") or {},
        "inference_fps": data.get("inference_fps") if alive else 0,
        # Dashboard tidak boleh mengklaim kamera aktif jika worker tidak berjalan.
        "camera": camera if alive else {"state": "UNKNOWN", "connected": False},
        "faces": data.get("faces", 0) if alive else 0,
        "tracks": data.get("tracks", []) if alive else [],
    }
    if not alive:
        result["message"] = "AI worker berhenti" if data.get("stopping") else \
            f"AI worker tidak merespons sejak {int(age)} detik"
    return result
