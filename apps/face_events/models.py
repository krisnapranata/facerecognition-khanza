from django.db import models


class MatchStatus(models.TextChoices):
    CANDIDATE = "CANDIDATE", "Kandidat"
    NEEDS_REVIEW = "NEEDS_REVIEW", "Perlu review"
    NO_MATCH = "NO_MATCH", "Tidak ada kecocokan"
    ERROR = "ERROR", "Error"


class VerificationStatus(models.TextChoices):
    PENDING = "PENDING", "Belum diverifikasi"
    CONFIRMED = "CONFIRMED", "Dikonfirmasi petugas"
    REJECTED = "REJECTED", "Ditolak petugas"
    MANUAL = "MANUAL", "Identitas dipilih manual"


class ControlStatus(models.TextChoices):
    NOT_CHECKED = "NOT_CHECKED", "Belum diperiksa"
    IDENTITY_NOT_VERIFIED = "IDENTITY_NOT_VERIFIED", "Identitas belum diverifikasi"
    CONTROL_FOUND = "CONTROL_FOUND", "Data kontrol ditemukan"
    CONTROL_NOT_FOUND = "CONTROL_NOT_FOUND", "Data kontrol belum ditemukan"
    NEEDS_MANUAL_CHECK = "NEEDS_MANUAL_CHECK", "Perlu pemeriksaan manual"


class FaceRecognitionEvent(models.Model):
    """Kejadian identifikasi. Tidak menyimpan embedding maupun frame kamera."""

    camera_id = models.CharField(max_length=64)
    track_id = models.CharField(max_length=64)
    candidate_reference = models.ForeignKey(
        "face_registry.PatientFaceReference", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="events",
    )
    match_status = models.CharField(max_length=16, choices=MatchStatus.choices)
    quality_status = models.CharField(max_length=16)
    similarity = models.FloatField(null=True, blank=True, help_text="cosine similarity, bukan persentase kepastian")
    second_similarity = models.FloatField(null=True, blank=True)
    model_version = models.CharField(max_length=64, blank=True)
    match_note = models.CharField(max_length=255, blank=True)
    submissions = models.PositiveIntegerField(default=1)
    verification_status = models.CharField(max_length=16, choices=VerificationStatus.choices,
                                           default=VerificationStatus.PENDING)
    verified_by = models.CharField(max_length=150, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_no_rkm_medis = models.CharField(max_length=20, blank=True)
    verified_patient_name = models.CharField(max_length=100, blank=True)
    verification_note = models.CharField(max_length=255, blank=True)
    control_status = models.CharField(max_length=24, choices=ControlStatus.choices,
                                      default=ControlStatus.NOT_CHECKED)
    control_detail = models.CharField(max_length=255, blank=True)
    # Tautan ke kunjungan Khanza (reg_periksa) yang dibuat dari kejadian ini.
    khanza_no_rawat = models.CharField(max_length=17, blank=True)
    khanza_no_reg = models.CharField(max_length=8, blank=True)
    khanza_registered_by = models.CharField(max_length=150, blank=True)
    khanza_registered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "face_recognition_event"
        ordering = ["-created_at", "-id"]
        permissions = [
            ("verify_event", "Dapat memverifikasi kandidat identifikasi"),
            ("search_patient", "Dapat mencari pasien secara manual"),
            ("register_visit", "Dapat mendaftarkan kunjungan ke reg_periksa Khanza"),
        ]
        indexes = [models.Index(fields=["camera_id", "track_id"], name="face_event_cam_track_idx")]

    def __str__(self):
        return f"Kejadian #{self.pk} {self.match_status}"


class FaceAuditLog(models.Model):
    """Audit aktivitas sensitif. Tidak boleh berisi password, embedding, atau foto."""

    actor = models.CharField(max_length=150)
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    outcome = models.CharField(max_length=16)
    detail = models.CharField(max_length=255, blank=True)
    ip_address = models.CharField(max_length=45, blank=True)

    class Meta:
        db_table = "face_audit_log"
        ordering = ["-timestamp", "-id"]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.actor} {self.action} {self.outcome}"
