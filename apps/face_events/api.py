from django.conf import settings
from django.views.decorators.csrf import csrf_exempt

from apps.common.api import ApiError, api_view, ok, parse_json, rate_limit, require_user, service_view
from apps.face_registry.engine import model_status
from apps.integrations import khanza

from . import services
from .models import FaceRecognitionEvent, MatchStatus, VerificationStatus


@api_view(("GET",))
def health(request):
    if not request.user.is_authenticated:
        return ok({"service": "ok"})
    worker = services.worker_status()
    return ok({
        "service": "ok",
        "mode": settings.APP_MODE,
        "worker": worker,
        "camera": worker["camera"],
        "model": {**model_status(), "worker": worker.get("model") or {}},
        "khanza": khanza.health(),
        "stations": _stations_status(),
    })


def _stations_status():
    from .stations import stations_status

    return stations_status()


@api_view(("GET",))
@rate_limit("events", 240)
def events_list(request):
    require_user(request, "face_events.view_facerecognitionevent")
    qs = FaceRecognitionEvent.objects.select_related("candidate_reference")
    status = request.GET.get("status")
    if status:
        if status not in MatchStatus.values:
            raise ApiError("VALIDATION_ERROR", "status tidak valid", 422, {"status": ", ".join(MatchStatus.values)})
        qs = qs.filter(match_status=status)
    verification = request.GET.get("verification")
    if verification:
        if verification not in VerificationStatus.values:
            raise ApiError("VALIDATION_ERROR", "verification tidak valid", 422)
        qs = qs.filter(verification_status=verification)
    try:
        limit = min(max(int(request.GET.get("limit", 20)), 1), 100)
    except ValueError:
        raise ApiError("VALIDATION_ERROR", "limit harus angka", 422, {"limit": "1-100"})
    events = [services.serialize_event(e, request.user) for e in qs[:limit]]
    return ok({"events": events, "count": len(events)})


@api_view(("GET",))
def event_detail(request, event_id: int):
    require_user(request, "face_events.view_facerecognitionevent")
    event = FaceRecognitionEvent.objects.select_related("candidate_reference").filter(pk=event_id).first()
    if event is None:
        raise ApiError("NOT_FOUND", "Kejadian tidak ditemukan", 404)
    return ok(services.serialize_event(event, request.user))


@api_view(("POST",))
@rate_limit("verify", 60)
def event_verify(request, event_id: int):
    require_user(request, "face_events.verify_event")
    event = services.verify_event(event_id, request.user, parse_json(request), request)
    return ok(services.serialize_event(event, request.user))


@api_view(("POST",))
def station_frame(request, station_id: str):
    """Frame dari kamera USB di PC loket (browser). Frame diproses lalu dibuang, tidak disimpan."""
    import time

    from ai_worker.errors import ModelUnavailableError
    from apps.common.api import check_rate_limit
    from apps.face_registry.services import decode_image

    from . import stations

    require_user(request, "face_events.view_facerecognitionevent")
    if not stations.STATION_RE.match(station_id):
        raise ApiError("VALIDATION_ERROR", "Nama stasiun tidak valid", 422, {"station_id": "1-40 karakter [A-Za-z0-9_.-]"})
    # Batas laju per pengguna+stasiun: fps maksimal + toleransi jitter jaringan.
    check_rate_limit(f"station:{request.user.pk}:{station_id}", int(settings.FACE_STATION_MAX_FPS * 10 * 1.5) + 5, 10)
    upload = request.FILES.get("frame")
    if upload is None:
        raise ApiError("VALIDATION_ERROR", "Frame wajib dikirim", 422, {"frame": "wajib"})
    if upload.size > settings.FACE_STATION_MAX_FRAME_BYTES:
        raise ApiError("IMAGE_TOO_LARGE", "Frame terlalu besar; kecilkan resolusi/kualitas JPEG", 413)
    image = decode_image(upload.read())
    started = time.monotonic()
    station = stations.manager.get(station_id)
    try:
        faces = station.process(image, show_identity=request.user.has_perm("face_registry.view_candidate_identity"))
    except ModelUnavailableError as exc:
        raise ApiError("MODEL_UNAVAILABLE", f"Model AI tidak tersedia: {exc}", 503)
    elapsed = (time.monotonic() - started) * 1000
    stations.record_status(station, faces, request.user, elapsed)
    return ok({"station_id": station_id, "camera_id": station.camera_id, "faces": faces,
               "processing_ms": round(elapsed, 1), "server_fps": round(station.fps, 1),
               "max_fps": settings.FACE_STATION_MAX_FPS})


@csrf_exempt  # autentikasi antarlayanan HMAC, bukan cookie
@api_view(("POST",))
@service_view
def candidates(request):
    event = services.ingest_candidate(parse_json(request))
    # Identitas hanya untuk label overlay kamera, dan worker hanya boleh
    # menggambarnya pada koneksi stream yang terikat izin melihat identitas.
    return ok({"event_id": event.pk, "match_status": event.match_status,
               "candidate": services.candidate_identity(event)}, status=201)


@csrf_exempt
@api_view(("POST",))
@service_view
def worker_heartbeat(request):
    services.store_worker_heartbeat(parse_json(request))
    return ok({"received": True})
