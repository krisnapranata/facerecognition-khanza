from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    # Label diberi prefix agar tidak bentrok dengan app lain di customsik (mis. 'dashboard').
    label = "face_integrations"
    verbose_name = "Integrasi Khanza (baca-saja)"
