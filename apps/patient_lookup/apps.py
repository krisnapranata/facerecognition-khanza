from django.apps import AppConfig


class PatientLookupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.patient_lookup"
    # Label diberi prefix agar tidak bentrok dengan app lain di customsik (mis. 'dashboard').
    label = "face_patient_lookup"
    verbose_name = "Pencarian pasien (simulasi Khanza)"
