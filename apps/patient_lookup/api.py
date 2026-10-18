from apps.common.api import ApiError, api_view, ok, rate_limit, require_user
from apps.face_events import audit

from .services import ServiceUnavailable, get_patient_lookup_service


@api_view(("GET",))
@rate_limit("patient-search", 60)
def patient_search(request):
    require_user(request, "face_events.search_patient")
    query = request.GET.get("q", "").strip()
    if len(query) < 2 or len(query) > 50:
        raise ApiError("VALIDATION_ERROR", "Kata kunci 2-50 karakter", 422, {"q": "2-50 karakter"})
    service = get_patient_lookup_service()
    try:
        results = service.search(query)
    except ServiceUnavailable as exc:
        raise ApiError("SERVICE_UNAVAILABLE", f"Layanan pencarian pasien tidak tersedia: {exc}", 503)
    audit.log(request.user, "PATIENT_SEARCH", audit.SUCCESS, "Patient", "", f"hasil={len(results)}", request)
    return ok({"source": service.source, "results": [r.as_dict() for r in results]})
