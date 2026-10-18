"""Enkripsi ulang embedding dan foto dengan kunci pertama di FACE_EMBEDDING_KEYS.

Langkah rotasi: tambahkan kunci baru di DEPAN daftar FACE_EMBEDDING_KEYS
(kunci lama tetap di belakang), jalankan perintah ini, lalu hapus kunci lama.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.common.crypto import rotate_token
from apps.face_registry.models import PatientFaceReference
from apps.face_events import audit


class Command(BaseCommand):
    help = "Rotasi kunci enkripsi data biometrik"

    def handle(self, *args, **options):
        count = 0
        root = Path(settings.MEDIA_PRIVATE_ROOT)
        for ref in PatientFaceReference.objects.all().iterator():
            ref.embedding_ciphertext = rotate_token(ref.embedding_ciphertext.encode()).decode()
            ref.save(update_fields=["embedding_ciphertext", "updated_at"])
            photo = root / ref.reference_image
            if photo.is_file():
                photo.write_bytes(rotate_token(photo.read_bytes()))
            count += 1
        audit.log("system", "KEYS_ROTATED", audit.SUCCESS, "PatientFaceReference", "", f"jumlah={count}")
        self.stdout.write(self.style.SUCCESS(f"{count} referensi dienkripsi ulang"))
