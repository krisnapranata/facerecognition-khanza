from django.conf import settings


def app_context(request):
    user = getattr(request, "user", None)
    perms = {}
    if user is not None and user.is_authenticated:
        perms = {
            "can_manage_reference": user.has_perm("face_registry.manage_reference"),
            "can_view_events": user.has_perm("face_events.view_facerecognitionevent"),
            "can_verify": user.has_perm("face_events.verify_event"),
            "can_view_identity": user.has_perm("face_registry.view_candidate_identity"),
            "can_search_patient": user.has_perm("face_events.search_patient"),
            "can_view_audit": user.has_perm("face_events.view_faceauditlog"),
            "can_register_visit": settings.KHANZA_REGISTRATION_ENABLED
            and user.has_perm("face_events.register_visit"),
        }
    return {"DEMO_MODE": settings.DEMO_MODE, "APP_MODE": settings.APP_MODE, "KHANZA_ENABLED": settings.KHANZA_ENABLED,
            "KHANZA_AUTH_ENABLED": settings.KHANZA_AUTH_ENABLED,
            "face_perms": perms}
