from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.dashboard import views as dashboard_views
from apps.face_events import api as events_api
from apps.face_events import views as events_views
from apps.face_registry import api as registry_api
from apps.face_registry import views as registry_views
from apps.integrations import api as khanza_api
from apps.patient_lookup import api as patient_api

api_v1 = [
    path("health/", events_api.health, name="health"),
    path("events/", events_api.events_list, name="events"),
    path("events/<int:event_id>/", events_api.event_detail, name="event-detail"),
    path("events/<int:event_id>/verify/", events_api.event_verify, name="event-verify"),
    path("candidates/", events_api.candidates, name="candidates"),
    path("worker/heartbeat/", events_api.worker_heartbeat, name="worker-heartbeat"),
    # Kamera USB di PC loket (browser). Di Docker, nginx meneruskan path ini ke container "vision".
    path("stations/<str:station_id>/frame/", events_api.station_frame, name="station-frame"),
    path("captures/", registry_api.capture_create, name="captures"),
    path("captures/<str:token>/", registry_api.capture_detail, name="capture-detail"),
    path("references/", registry_api.references_create, name="references"),
    path("references/<int:ref_id>/", registry_api.reference_delete, name="reference-delete"),
]

dashboard_patterns = ([
    path("", dashboard_views.index, name="index"),
    path("stream/", dashboard_views.stream, name="stream"),
    path("registration/", registry_views.registration, name="registration"),
    path("references/", registry_views.reference_list, name="references"),
    path("references/<int:ref_id>/photo/", registry_views.reference_photo, name="reference-photo"),
    path("events/", events_views.event_list, name="events"),
    path("events/<int:event_id>/", events_views.event_detail, name="event-detail"),
    path("audit/", events_views.audit_list, name="audit"),
], "dashboard")

urlpatterns = [
    path("accounts/login/", dashboard_views.RateLimitedLoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("api/face/v1/", include((api_v1, "api"))),
    path("api/patients/search/", patient_api.patient_search, name="patient-search"),
    path("api/khanza/v1/events/<int:event_id>/registration/options/", khanza_api.registration_options,
         name="khanza-registration-options"),
    path("api/khanza/v1/events/<int:event_id>/registration/preview/", khanza_api.registration_preview,
         name="khanza-registration-preview"),
    path("api/khanza/v1/events/<int:event_id>/registration/", khanza_api.registration_create,
         name="khanza-registration"),
    path("", include(dashboard_patterns)),
]
