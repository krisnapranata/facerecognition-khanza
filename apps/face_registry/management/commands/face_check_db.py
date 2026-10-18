"""Pemeriksaan database sebelum migrasi (hanya baca).

    .venv/bin/python manage.py face_check_db

Menampilkan alias dan database target, koneksi, migrasi yang akan
diterapkan, serta tabel yang akan dibuat. Keluar dengan kode != 0 jika
rencana migrasi menyentuh selain tabel baru ``face_*`` atau jika tabel
target sudah ada dengan cara yang tidak terduga.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.operations import AddField, AddIndex, AlterModelOptions, CreateModel

from config.routers import FACE_APP_LABELS

# Hanya menambah tabel/kolom/indeks pada tabel face_* milik aplikasi ini (tidak pernah menghapus/mengubah).
ALLOWED_OPERATIONS = (CreateModel, AddIndex, AddField, AlterModelOptions)


def plan_for(alias: str):
    connection = connections[alias]
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    return executor, executor.migration_plan(targets)


def tables_created(plan) -> list[str]:
    tables = []
    for migration, backwards in plan:
        if backwards:
            continue
        for op in migration.operations:
            if isinstance(op, CreateModel):
                tables.append(op.options.get("db_table") or f"{migration.app_label}_{op.name.lower()}")
    return tables


class Command(BaseCommand):
    help = "Periksa koneksi dan rencana migrasi customsik tanpa mengubah apa pun"

    def handle(self, *args, **options):
        alias = settings.FACE_DB_ALIAS
        db = settings.DATABASES[alias]
        self.stdout.write(self.style.MIGRATE_HEADING("== Target database aplikasi pengenalan wajah =="))
        self.stdout.write(f"Alias      : {alias}")
        self.stdout.write(f"Engine     : {db['ENGINE']}")
        self.stdout.write(f"Database   : {db['NAME']} @ {db.get('HOST')}:{db.get('PORT')} user={db.get('USER')}")
        self.stdout.write("Password   : (disembunyikan)")

        connection = connections[alias]
        try:
            with connection.cursor() as cursor:
                if connection.vendor == "mysql":
                    cursor.execute("SELECT VERSION(), DATABASE(), @@character_set_database")
                    version, current_db, charset = cursor.fetchone()
                    self.stdout.write(f"Server     : {version}; database aktif={current_db}; charset={charset}")
                existing = set(connection.introspection.table_names(cursor))
        except Exception as exc:
            raise CommandError(f"Koneksi ke alias '{alias}' gagal: {type(exc).__name__}: {exc}. Migrasi dihentikan.")
        self.stdout.write(self.style.SUCCESS(f"Koneksi OK. Jumlah tabel saat ini: {len(existing)}"))

        executor, plan = plan_for(alias)
        problems = []
        face_plan = []
        for migration, backwards in plan:
            if backwards:
                problems.append(f"rencana mundur tidak diizinkan: {migration}")
            elif migration.app_label in FACE_APP_LABELS:
                face_plan.append(migration)
                for op in migration.operations:
                    if not isinstance(op, ALLOWED_OPERATIONS):
                        problems.append(f"operasi {type(op).__name__} di {migration} bukan penambahan tabel/kolom/indeks")
            else:
                # Router mencegah operasi app lain di customsik, tetapi Django akan
                # mencatatnya di django_migrations. Tolak agar customsik tidak berubah.
                problems.append(f"migrasi app non-face akan dicatat di customsik: {migration.app_label}.{migration.name}")

        new_tables = tables_created([(m, False) for m in face_plan])
        self.stdout.write(self.style.MIGRATE_HEADING("== Migrasi yang akan diterapkan =="))
        for m in face_plan:
            self.stdout.write(f"  {m.app_label}.{m.name}")
        if not face_plan:
            self.stdout.write("  (tidak ada; semua migrasi face_* sudah diterapkan)")
        self.stdout.write(self.style.MIGRATE_HEADING("== Tabel baru yang akan dibuat =="))
        for t in new_tables:
            flag = "SUDAH ADA!" if t in existing else "baru"
            self.stdout.write(f"  {t} ({flag})")
            if not t.startswith("face_"):
                problems.append(f"nama tabel {t} tidak berawalan face_")
            if t in existing:
                problems.append(f"tabel {t} sudah ada; tidak akan ditimpa")
        already = sorted(t for t in existing if t.startswith("face_"))
        if already:
            self.stdout.write(f"Tabel face_* yang sudah ada: {', '.join(already)}")

        if problems:
            for p in problems:
                self.stderr.write(self.style.ERROR(f"MASALAH: {p}"))
            raise CommandError("Pemeriksaan gagal. Migrasi TIDAK boleh dijalankan.")
        self.stdout.write(self.style.SUCCESS("Pemeriksaan lolos: migrasi hanya menambah tabel face_* baru."))
