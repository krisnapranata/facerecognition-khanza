from django.apps import AppConfig


class FaceRegistryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.face_registry"
    # Label diberi prefix agar tidak bentrok dengan app lain di customsik (mis. 'dashboard').
    label = "face_registry"
    verbose_name = "Registri referensi wajah"
