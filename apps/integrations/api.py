"""API registrasi kunjungan ke reg_periksa Khanza dari kejadian identifikasi yang sudah diverifikasi."""
import re

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.api import ApiError, api_view, ok, parse_json, rate_limit, require_user
from apps.face_events import audit
from apps.face_events.models import FaceRecognitionEvent, VerificationStatus

from .khanza_registration import VisitRegistrationService

PERM = "face_events.register_visit"
CODE_RE = re.compile(r"^[A-Za-z0-9._\-]{1,20}$")


def _enabled():
    if not settings.KHANZA_REGISTRATION_ENABLED:
        raise ApiError("FEATURE_DISABLED", "Registrasi kunjungan ke Khanza tidak aktif", 503)


def _verified_event(event_id: int, for_update: bool = False) -> FaceRecognitionEvent:
    qs = FaceRecognitionEvent.objects.select_for_update() if for_update else FaceRecognitionEvent.objects
    event = qs.filter(pk=event_id).first()
    if event is None:
        raise ApiError("NOT_FOUND", "Kejadian tidak ditemukan", 404)
    if event.verification_status not in (VerificationStatus.CONFIRMED, VerificationStatus.MANUAL) \
            or not event.verified_no_rkm_medis:
        raise ApiError("IDENTITY_NOT_VERIFIED", "Identitas pasien harus diverifikasi petugas terlebih dahulu", 409)
    if event.khanza_no_rawat:
        raise ApiError("ALREADY_REGISTERED", f"Kejadian ini sudah didaftarkan (No. Rawat {event.khanza_no_rawat})", 409)
    return event


def _codes(data: dict) -> tuple[str, str]:
    kd_poli, kd_dokter = str(data.get("kd_poli", "")).strip(), str(data.get("kd_dokter", "")).strip()
    errors = {}
    if not CODE_RE.match(kd_poli):
        errors["kd_poli"] = "wajib dipilih"
    if not CODE_RE.match(kd_dokter):
        errors["kd_dokter"] = "wajib dipilih"
    if errors:
        raise ApiError("VALIDATION_ERROR", "Pilih poliklinik dan dokter", 422, errors)
    return kd_poli, kd_dokter


@api_view(("GET",))
@rate_limit("khanza-options", 60)
def registration_options(request, event_id: int):
    require_user(request, PERM)
    _enabled()
    event = _verified_event(event_id)
    data = VisitRegistrationService().options(event.verified_no_rkm_medis)
    data["patient"] = {"no_rkm_medis": event.verified_no_rkm_medis, "nama": event.verified_patient_name}
    return ok(data)


@api_view(("POST",))
@rate_limit("khanza-preview", 60)
def registration_preview(request, event_id: int):
    require_user(request, PERM)
    _enabled()
    event = _verified_event(event_id)
    kd_poli, kd_dokter = _codes(parse_json(request))
    plan = VisitRegistrationService().plan(event.verified_no_rkm_medis, kd_poli, kd_dokter)
    return ok({"plan": plan.as_dict(), "note": "Pratinjau; No. Rawat/No. Reg final ditentukan saat disimpan"})


@api_view(("POST",))
@rate_limit("khanza-register", 20)
def registration_create(request, event_id: int):
    require_user(request, PERM)
    _enabled()
    kd_poli, kd_dokter = _codes(parse_json(request))
    try:
        plan, event = _register_locked(event_id, kd_poli, kd_dokter, request.user)
    except ApiError as exc:
        # Di luar transaksi, agar catatan kegagalan tidak ikut di-rollback.
        audit.log(request.user, "KHANZA_VISIT_REGISTERED", audit.FAILED, "FaceRecognitionEvent", event_id,
                  f"{exc.code} poli={kd_poli} dokter={kd_dokter}", request)
        raise
    audit.log(request.user, "KHANZA_VISIT_REGISTERED", audit.SUCCESS, "reg_periksa", plan.no_rawat,
              f"event={event.pk} poli={kd_poli} dokter={kd_dokter} no_reg={plan.no_reg}", request)
    return ok({"plan": plan.as_dict()}, status=201)


def _register_locked(event_id: int, kd_poli: str, kd_dokter: str, user):
    with transaction.atomic(using=settings.FACE_DB_ALIAS):
        # Kunci baris kejadian agar klik ganda tidak membuat dua kunjungan.
        event = _verified_event(event_id, for_update=True)
        plan = VisitRegistrationService().register(event.verified_no_rkm_medis, kd_poli, kd_dokter)
        event.khanza_no_rawat = plan.no_rawat
        event.khanza_no_reg = plan.no_reg
        event.khanza_registered_by = user.get_username()
        event.khanza_registered_at = timezone.now()
        event.save(update_fields=["khanza_no_rawat", "khanza_no_reg", "khanza_registered_by", "khanza_registered_at",
                                  "updated_at"])
    return plan, event
