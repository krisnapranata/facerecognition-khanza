"""Akses tulis TERBATAS ke database Khanza (alias ``khanza_rw``).

Akun MySQL yang dipakai (keputusan operator: sama dengan Khanza desktop) memiliki
hak luas, sehingga pembatasan dilakukan di sini: setiap pernyataan yang bukan
SELECT harus cocok dengan allowlist berikut, selain itu ditolak sebelum dikirim
ke database.

- ``INSERT INTO reg_periksa (...) VALUES (...)``   (pola DlgReg.isRegistrasi)
- ``UPDATE pasien SET umur=... WHERE no_rkm_medis=...``   (pola DlgReg.UpdateUmur)
- ``SELECT GET_LOCK/RELEASE_LOCK`` untuk serialisasi penomoran antarproses aplikasi ini
"""
from __future__ import annotations

import re
from contextlib import contextmanager

from django.db import connections

ALIAS = "khanza_rw"

_WRITE_PREFIXES = ("INSERT INTO reg_periksa (", "UPDATE pasien SET umur=")
_allowed_writes: set[str] = set()
_SELECT = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_FORBIDDEN_IN_SELECT = re.compile(r";|--|/\*|#|\b(into\s+(out|dump)file|for\s+update)\b", re.IGNORECASE)


class ForbiddenStatement(PermissionError):
    pass


def _normalize(sql: str) -> str:
    return " ".join(sql.split())


def register_write(sql: str) -> str:
    """Daftarkan pernyataan tulis yang PERSIS boleh dijalankan (hanya prefix yang diizinkan)."""
    normalized = _normalize(sql)
    if not normalized.startswith(_WRITE_PREFIXES) or ";" in normalized:
        raise ForbiddenStatement("hanya INSERT reg_periksa / UPDATE pasien.umur yang boleh didaftarkan")
    _allowed_writes.add(normalized)
    return sql


def check_statement(sql: str) -> str:
    normalized = _normalize(sql)
    if _SELECT.match(normalized):
        if _FORBIDDEN_IN_SELECT.search(normalized):
            raise ForbiddenStatement("SELECT Khanza mengandung pola terlarang")
        return normalized
    if normalized in _allowed_writes:
        return normalized
    raise ForbiddenStatement(f"pernyataan tulis ke Khanza tidak diizinkan: {normalized[:60]}")


class GuardedCursor:
    """Membungkus cursor Django; hanya meneruskan SQL yang lolos ``check_statement``."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql: str, params=None):
        return self._cursor.execute(check_statement(sql), params)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self):
        return self._cursor.rowcount


@contextmanager
def cursor():
    with connections[ALIAS].cursor() as raw:
        yield GuardedCursor(raw)


def fetch_dicts(cur: GuardedCursor) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
