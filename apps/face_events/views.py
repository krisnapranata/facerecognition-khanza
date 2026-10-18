from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from .models import FaceAuditLog, FaceRecognitionEvent, MatchStatus
from .services import serialize_event


@login_required
@permission_required("face_events.view_facerecognitionevent", raise_exception=True)
def event_list(request):
    qs = FaceRecognitionEvent.objects.select_related("candidate_reference")
    status = request.GET.get("status")
    if status in MatchStatus.values:
        qs = qs.filter(match_status=status)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    rows = [serialize_event(e, request.user) for e in page.object_list]
    return render(request, "face_events/event_list.html", {
        "page": page, "rows": rows, "status": status, "statuses": MatchStatus.choices,
    })


@login_required
@permission_required("face_events.view_facerecognitionevent", raise_exception=True)
def event_detail(request, event_id: int):
    event = get_object_or_404(FaceRecognitionEvent.objects.select_related("candidate_reference"), pk=event_id)
    return render(request, "face_events/event_detail.html", {"e": serialize_event(event, request.user)})


@login_required
@permission_required("face_events.view_faceauditlog", raise_exception=True)
def audit_list(request):
    page = Paginator(FaceAuditLog.objects.all(), 50).get_page(request.GET.get("page"))
    return render(request, "face_events/audit_list.html", {"page": page})
