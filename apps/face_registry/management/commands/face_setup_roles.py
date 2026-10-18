"""Membuat grup peran:

- face_operator : melihat dashboard/kejadian, melihat identitas kandidat, verifikasi, cari pasien
- face_registrar: mendaftarkan kunjungan ke reg_periksa Khanza (diberikan otomatis ke akun
                  Khanza dengan user.registrasi='true')
- face_admin    : semua hak operator + kelola referensi wajah + lihat audit log + daftarkan kunjungan
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError

OPERATOR = [
    "face_events.view_facerecognitionevent",
    "face_events.verify_event",
    "face_events.search_patient",
    "face_registry.view_candidate_identity",
]
ADMIN = OPERATOR + [
    "face_registry.manage_reference",
    "face_registry.view_patientfacereference",
    "face_events.view_faceauditlog",
    "face_events.register_visit",
]
REGISTRAR = ["face_events.register_visit"]


def resolve(perms):
    result = []
    for full in perms:
        app_label, codename = full.split(".")
        perm = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
        if perm is None:
            raise CommandError(f"Permission {full} belum ada. Jalankan 'manage.py migrate' (database default) dulu.")
        result.append(perm)
    return result


class Command(BaseCommand):
    help = "Buat/perbarui grup face_operator, face_registrar, dan face_admin"

    def handle(self, *args, **options):
        for name, perms in (("face_operator", OPERATOR), ("face_registrar", REGISTRAR), ("face_admin", ADMIN)):
            group, created = Group.objects.get_or_create(name=name)
            group.permissions.set(resolve(perms))
            self.stdout.write(self.style.SUCCESS(f"Grup {name} {'dibuat' if created else 'diperbarui'} "
                                                 f"({len(perms)} izin)"))
