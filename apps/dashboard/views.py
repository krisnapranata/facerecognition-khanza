import logging

import requests
from django.conf import settings
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from ai_worker.signing import stream_token
from apps.common.api import ApiError, check_rate_limit, client_ip
from apps.face_events import audit

logger = logging.getLogger(__name__)


class RateLimitedLoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        try:
            check_rate_limit(f"login:{client_ip(request)}", 10, 60)
        except ApiError:
            return HttpResponse("Terlalu banyak percobaan login. Coba lagi dalam 1 menit.", status=429)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        audit.log(form.get_user(), "LOGIN", audit.SUCCESS, request=self.request)
        return response

    def form_invalid(self, form):
        username = str(self.request.POST.get("username", ""))[:150]
        audit.log(username or "anonymous", "LOGIN", audit.FAILED, request=self.request)
        return super().form_invalid(form)


@login_required
@permission_required("face_events.view_facerecognitionevent", raise_exception=True)
def index(request):
    return render(request, "dashboard/index.html", {
        "thresholds": {
            "candidate": settings.FACE_MATCH_THRESHOLD,
            "review": settings.FACE_REVIEW_THRESHOLD,
            "margin": settings.FACE_MATCH_MARGIN,
        },
        "default_camera": settings.FACE_DEFAULT_CAMERA,
        "station_max_fps": settings.FACE_STATION_MAX_FPS,
    })


@login_required
@permission_required("face_events.view_facerecognitionevent", raise_exception=True)
@require_GET
def stream(request):
    """Proxy MJPEG dari worker lokal; browser tidak pernah melihat URL kamera/RTSP."""
    # Nama/No. RM dibakar ke frame hanya bila pemohon memang berwenang melihat identitas.
    show_identity = request.user.has_perm("face_registry.view_candidate_identity")
    token = stream_token(settings.FACE_WORKER_SECRET, show_identity=show_identity)
    try:
        upstream = requests.get(f"{settings.FACE_WORKER_URL}/stream.mjpg", stream=True, timeout=(2, 10),
                                headers={"X-Worker-Token": token})
    except requests.RequestException:
        return HttpResponse("AI worker tidak berjalan", status=503, content_type="text/plain")
    if upstream.status_code != 200:
        upstream.close()
        return HttpResponse("Stream tidak tersedia", status=503, content_type="text/plain")

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=16384):
                if chunk:
                    yield chunk
        except requests.RequestException:
            return
        finally:
            upstream.close()

    response = StreamingHttpResponse(generate(), content_type=upstream.headers.get("Content-Type"))
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"
    return response
