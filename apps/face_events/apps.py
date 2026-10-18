from django.apps import AppConfig


class FaceEventsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.face_events"
    # Label diberi prefix agar tidak bentrok dengan app lain di customsik (mis. 'dashboard').
    label = "face_events"
    verbose_name = "Kejadian identifikasi dan audit"
