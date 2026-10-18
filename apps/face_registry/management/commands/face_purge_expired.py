from django.conf import settings
from django.core.management.base import BaseCommand

from apps.common import storage
from apps.face_registry.services import purge_expired


class Command(BaseCommand):
    help = "Hapus referensi wajah yang masa retensinya berakhir (beserta foto) dan file sementara lama"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **options):
        ids = purge_expired(dry_run=dry_run)
        verb = "akan dihapus" if dry_run else "dihapus"
        self.stdout.write(f"Referensi kedaluwarsa {verb}: {len(ids)} {ids if ids else ''}")
        if not dry_run:
            removed = storage.purge_temp(settings.FACE_CAPTURE_TTL_SECONDS)
            self.stdout.write(f"File sementara dihapus: {removed}")
