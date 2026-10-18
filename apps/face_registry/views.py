from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.common import storage
from apps.common.crypto import DecryptionError
from apps.face_events import audit

from .engine import model_status
from .models import PatientFaceReference
from .services import allowed_processing_bases

MANAGE = "face_registry.manage_reference"


@login_required
@permission_required(MANAGE, raise_exception=True)
def registration(request):
    return render(request, "face_registry/registration.html", {
        "bases": [(b.value, b.label) for b in allowed_processing_bases()],
        "khanza_enabled": settings.KHANZA_ENABLED,
        "default_camera": settings.FACE_DEFAULT_CAMERA,
        "model": model_status(),
        "retention_days": settings.FACE_RETENTION_DAYS,
    })


@login_required
@permission_required(MANAGE, raise_exception=True)
def reference_list(request):
    refs = PatientFaceReference.objects.all()
    status = request.GET.get("status")
    if status == "active":
        refs = refs.filter(is_active=True)
    elif status == "inactive":
        refs = refs.filter(is_active=False)
    return render(request, "face_registry/reference_list.html", {"references": refs[:500], "status": status})


@login_required
@permission_required(MANAGE, raise_exception=True)
def reference_photo(request, ref_id: int):
    ref = get_object_or_404(PatientFaceReference, pk=ref_id)
    try:
        data = storage.read_decrypted(ref.reference_image)
    except (FileNotFoundError, ValueError, DecryptionError):
        raise Http404("Foto referensi tidak tersedia")
    audit.log(request.user, "REFERENCE_PHOTO_VIEWED", audit.SUCCESS, "PatientFaceReference", ref.pk,
              request=request)
    response = HttpResponse(data, content_type="image/jpeg")
    response["Cache-Control"] = "no-store, private"
    return response
