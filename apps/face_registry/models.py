from django.db import models


class ProcessingBasis(models.TextChoices):
    DEMO_SYNTHETIC = "DEMO_SYNTHETIC", "Data uji sintetis (demo)"
    TEST_PARTICIPANT_CONSENT = "TEST_PARTICIPANT_CONSENT", "Peserta uji dengan persetujuan tertulis"
    PATIENT_CONSENT = "PATIENT_CONSENT", "Persetujuan eksplisit pasien (produksi)"


class PatientFaceReferenceQuerySet(models.QuerySet):
    def active(self, now=None):
        from django.utils import timezone

        now = now or timezone.now()
        return self.filter(is_active=True).filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))


class PatientFaceReference(models.Model):
    """Referensi wajah pasien. Embedding disimpan terenkripsi, foto di penyimpanan privat terenkripsi.

    Nomor rekam medis yang dimasukkan petugas BUKAN bukti identitas terverifikasi.
    """

    patient_name = models.CharField(max_length=100)
    no_rkm_medis = models.CharField(max_length=20, db_index=True)
    reference_image = models.CharField(max_length=255, help_text="path relatif di MEDIA_PRIVATE_ROOT (terenkripsi)")
    embedding_ciphertext = models.TextField()
    model_version = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)
    processing_basis = models.CharField(max_length=32, choices=ProcessingBasis.choices)
    is_test_data = models.BooleanField(default=True)
    registered_by = models.CharField(max_length=150)
    quality_score = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    objects = PatientFaceReferenceQuerySet.as_manager()

    class Meta:
        db_table = "face_patient_reference"
        ordering = ["-created_at"]
        permissions = [
            ("manage_reference", "Dapat mendaftarkan, menonaktifkan, dan menghapus referensi wajah"),
            ("view_candidate_identity", "Dapat melihat nama dan nomor RM kandidat"),
        ]
        indexes = [models.Index(fields=["is_active", "model_version"], name="face_ref_active_model_idx")]

    def __str__(self):
        return f"Referensi #{self.pk} ({'aktif' if self.is_active else 'nonaktif'})"
