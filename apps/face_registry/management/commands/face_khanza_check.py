"""Pemeriksaan integrasi Khanza (baca-saja). Tidak mencetak nama/identitas pasien.

    .venv/bin/python manage.py face_khanza_check
    .venv/bin/python manage.py face_khanza_check --rm 000123 --date 2024-11-08
"""
import re
import time
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from apps.integrations import khanza

WRITE_PRIVS = re.compile(r"\b(ALL PRIVILEGES|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|INDEX|GRANT OPTION|"
                         r"EXECUTE|FILE|SUPER|TRIGGER|EVENT|LOCK TABLES|REFERENCES)\b", re.IGNORECASE)


class Command(BaseCommand):
    help = "Periksa koneksi, hak akses baca-saja, dan query Khanza"

    def add_arguments(self, parser):
        parser.add_argument("--rm", help="nomor RM untuk uji query pasien & jadwal kontrol (hasil disamarkan)")
        parser.add_argument("--date", help="tanggal uji jadwal kontrol YYYY-MM-DD (default hari ini)")

    def handle(self, *args, rm=None, date=None, **options):
        if not settings.KHANZA_ENABLED:
            raise CommandError("KHANZA_ENABLED=false — integrasi tidak aktif")
        db = settings.DATABASES["khanza"]
        self.stdout.write(f"Database : {db['NAME']} @ {db['HOST']}:{db['PORT']} user={db['USER']} (password disembunyikan)")
        queries = khanza.load_queries()
        self.stdout.write(self.style.SUCCESS(f"Query file OK: {khanza.query_file_path()} ({', '.join(queries)})"))

        with connections["khanza"].cursor() as cursor:
            cursor.execute("SELECT CURRENT_USER()")
            user = cursor.fetchone()[0]
            # MariaDB <11.1 memakai tx_read_only, MySQL 8 / MariaDB baru memakai transaction_read_only.
            cursor.execute("SHOW SESSION VARIABLES WHERE Variable_name IN ('tx_read_only', 'transaction_read_only')")
            read_only = any(str(v).upper() in ("1", "ON") for _, v in cursor.fetchall())
            cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
            grants = [g[0] for g in cursor.fetchall()]
        self.stdout.write(f"User aktif: {user}; sesi READ ONLY={bool(read_only)}")
        bad = [g for g in grants if WRITE_PRIVS.search(g.split(" TO ")[0])]
        for g in grants:
            self.stdout.write("  " + re.sub(r"IDENTIFIED BY PASSWORD '[^']*'", "IDENTIFIED BY PASSWORD ***", g))
        if bad:
            raise CommandError("User Khanza memiliki hak tulis/administratif. Gunakan user baca-saja (SELECT saja).")
        if not read_only:
            raise CommandError("Sesi tidak dalam mode READ ONLY")
        self.stdout.write(self.style.SUCCESS("Hak akses: hanya SELECT"))

        search = khanza.KhanzaPatientLookupService()
        control = khanza.KhanzaControlScheduleService()
        t0 = time.monotonic()
        n = len(search.search("__tidak_ada__"))
        self.stdout.write(f"patient_search  OK ({n} hasil untuk kata kunci dummy, {1000 * (time.monotonic() - t0):.0f} ms)")
        t0 = time.monotonic()
        self.stdout.write(f"patient_get     OK (dummy ditemukan={search.get('__tidak_ada__') is not None}, "
                          f"{1000 * (time.monotonic() - t0):.0f} ms)")
        t0 = time.monotonic()
        control.find_control("__tidak_ada__")
        self.stdout.write(f"control_schedule OK ({1000 * (time.monotonic() - t0):.0f} ms)")

        if rm:
            target = _parse_date(date)
            patient = search.get(rm)
            self.stdout.write(f"RM uji: pasien ditemukan={patient is not None}"
                              + (f" (panjang nama {len(patient.nama)} karakter, disamarkan)" if patient else ""))
            result = control.find_control(rm, target)
            self.stdout.write(f"Jadwal kontrol {target}: ditemukan={result.found}; {len(result.schedules)} baris")
            for s in result.schedules:
                self.stdout.write(f"  sumber={s.get('sumber')} tanggal={s.get('tanggal')} poli={s.get('poli') or '-'}")
        self.stdout.write(self.style.SUCCESS("Integrasi Khanza siap (baca-saja)."))


def _parse_date(value):
    if not value:
        from django.utils import timezone

        return timezone.localdate()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise CommandError("--date harus YYYY-MM-DD")
