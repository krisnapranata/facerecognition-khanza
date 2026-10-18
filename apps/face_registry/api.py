import requests
from django.conf import settings
from django.http import HttpResponse

from ai_worker.errors import ModelUnavailableError
from ai_worker.signing import stream_token
from apps.common.api import ApiError, api_view, ok, parse_json, rate_limit, require_user
from apps.face_events import audit

from . import services
from .models import PatientFaceReference

MANAGE = "face_registry.manage_reference"


def fetch_worker_snapshot() -> bytes:
    """Ambil frame terbaru dari worker (server-ke-server, token lokal)."""
    try:
        resp = requests.get(f"{settings.FACE_WORKER_URL}/snapshot.jpg", timeout=3,
                            headers={"X-Worker-Token": stream_token(settings.FACE_WORKER_SECRET)})
    except requests.RequestException:
        raise ApiError("WORKER_UNAVAILABLE", "AI worker/kamera tidak tersedia. Gunakan kamera browser.", 503)
    if resp.status_code != 200:
        raise ApiError("WORKER_UNAVAILABLE", "Frame kamera belum tersedia dari AI worker", 503)
    return resp.content


@api_view(("POST",))
@rate_limit("capture", 30)
def capture_create(request):
    require_user(request, MANAGE)
    source = request.GET.get("source") or request.POST.get("source") or "worker"
    if source == "worker":
        image_bytes = fetch_worker_snapshot()
    elif source == "upload":
        upload = request.FILES.get("image")
        if upload is None:
            raise ApiError("VALIDATION_ERROR", "File gambar wajib", 422, {"image": "wajib"})
        if upload.size > settings.FACE_MAX_UPLOAD_BYTES:
            raise ApiError("IMAGE_TOO_LARGE", "Ukuran gambar melebihi batas", 413)
        image_bytes = upload.read()
    else:
        raise ApiError("VALIDATION_ERROR", "source harus 'worker' atau 'upload'", 422)
    try:
        result = services.create_capture(request, image_bytes)
    except ModelUnavailableError as exc:
        raise ApiError("MODEL_UNAVAILABLE", f"Model AI tidak tersedia: {exc}", 503)
    return ok({
        "capture_token": result.token or None,
        "face_count": result.face_count,
        "acceptable": result.acceptable,
        "quality": result.quality,
        "message": result.message,
    })


@api_view(("GET", "DELETE"))
def capture_detail(request, token: str):
    require_user(request, MANAGE)
    if request.method == "DELETE":
        services.discard_capture(request, token)
        return ok({"discarded": True})
    response = HttpResponse(services.capture_preview_jpeg(request, token), content_type="image/jpeg")
    response["Cache-Control"] = "no-store"
    return response


@api_view(("POST",))
@rate_limit("register", 20)
def references_create(request):
    require_user(request, MANAGE)
    try:
        ref = services.register_reference(request, parse_json(request))
    except ModelUnavailableError as exc:
        audit.log(request.user, "REFERENCE_REGISTERED", audit.FAILED, "PatientFaceReference", "",
                  "model tidak tersedia", request)
        raise ApiError("MODEL_UNAVAILABLE", f"Model AI tidak tersedia: {exc}", 503)
    except ApiError as exc:
        audit.log(request.user, "REFERENCE_REGISTERED", audit.FAILED, "PatientFaceReference", "", exc.code, request)
        raise
    return ok({"id": ref.pk, "patient_name": ref.patient_name, "no_rkm_medis": ref.no_rkm_medis,
               "model_version": ref.model_version, "expires_at": ref.expires_at.isoformat() if ref.expires_at else None},
              status=201)


@api_view(("DELETE",))
@rate_limit("reference-delete", 30)
def reference_delete(request, ref_id: int):
    require_user(request, MANAGE)
    mode = request.GET.get("mode", "deactivate")
    if mode not in ("deactivate", "delete"):
        raise ApiError("VALIDATION_ERROR", "mode harus 'deactivate' atau 'delete'", 422)
    ref = PatientFaceReference.objects.filter(pk=ref_id).first()
    if ref is None:
        raise ApiError("NOT_FOUND", "Referensi tidak ditemukan", 404)
    if mode == "deactivate":
        services.deactivate_reference(ref, request.user, request)
    else:
        services.delete_reference(ref, request.user, request, reason="permintaan petugas")
    return ok({"id": ref_id, "mode": mode})
