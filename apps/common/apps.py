from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    # Label diberi prefix agar tidak bentrok dengan app lain di customsik (mis. 'dashboard').
    label = "face_common"
    verbose_name = "Utilitas bersama"
