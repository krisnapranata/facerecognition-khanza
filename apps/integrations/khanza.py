"""Adapter database Khanza — BACA-SAJA, aktif hanya jika KHANZA_ENABLED dan KHANZA_SCHEMA_VERIFIED.

Prinsip:
- Tidak ada nama tabel/kolom Khanza yang ditulis di kode Python. Query disediakan
  operator dalam file JSON (KHANZA_QUERY_FILE) SETELAH memverifikasi skema
  database Khanza yang sebenarnya (lihat docs/KHANZA_INTEGRATION.md).
- Hanya pernyataan SELECT tunggal (boleh UNION ALL) yang diizinkan, dengan
  parameter (%(nama)s) — tanpa penggabungan string.
- Koneksi alias ``khanza`` memakai user baca-saja dan
  ``SET SESSION TRANSACTION READ ONLY``; router menolak migrasi/penulisan.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path

from django.conf import settings
from django.db import connections

from apps.patient_lookup.services import ControlResult, PatientRecord, ServiceUnavailable

REQUIRED_QUERIES = ("patient_search", "patient_get", "control_schedule")
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|replace|drop|alter|create|truncate|grant|revoke|lock|call|load|into|flush|set"
    r"|handler|rename|outfile|dumpfile|sleep|benchmark)\b",
    re.IGNORECASE,
)
MAX_ROWS = 20
SOURCE_LABELS = {
    "booking_registrasi": "Booking registrasi",
    "surat_kontrol_bpjs": "Surat kontrol BPJS",
    "skdp_bpjs": "SKDP BPJS",
}


class KhanzaNotConfigured(ServiceUnavailable):
    pass


def validate_select(sql: str) -> str:
    stripped = sql.strip()
    if not stripped.lower().startswith("select"):
        raise KhanzaNotConfigured("query Khanza harus diawali SELECT")
    if ";" in stripped or "--" in stripped or "/*" in stripped or "#" in stripped:
        raise KhanzaNotConfigured("query Khanza tidak boleh berisi ';' atau komentar")
    if FORBIDDEN.search(stripped):
        raise KhanzaNotConfigured("query Khanza mengandung kata kunci yang tidak diizinkan")
    return stripped


def query_file_path() -> Path:
    path = Path(settings.KHANZA_QUERY_FILE or "")
    return path if path.is_absolute() else Path(settings.BASE_DIR) / path


_cache: dict = {"mtime": None, "queries": None}


def load_queries() -> dict[str, dict]:
    if not settings.KHANZA_ENABLED:
        raise KhanzaNotConfigured("integrasi Khanza tidak aktif")
    if not settings.KHANZA_SCHEMA_VERIFIED:
        raise KhanzaNotConfigured("skema Khanza belum diverifikasi (KHANZA_SCHEMA_VERIFIED=false)")
    path = query_file_path()
    if not settings.KHANZA_QUERY_FILE or not path.is_file():
        raise KhanzaNotConfigured("KHANZA_QUERY_FILE tidak ditemukan")
    mtime = path.stat().st_mtime
    if _cache["mtime"] == mtime and _cache["queries"] is not None:
        return _cache["queries"]
    try:
        spec = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise KhanzaNotConfigured(f"KHANZA_QUERY_FILE bukan JSON valid: {exc}") from exc
    queries = {}
    for name in REQUIRED_QUERIES:
        item = spec.get(name)
        if not item or "sql" not in item or "columns" not in item:
            raise KhanzaNotConfigured(f"query '{name}' belum didefinisikan")
        queries[name] = {"sql": validate_select(item["sql"]), "columns": dict(item["columns"])}
    _cache.update(mtime=mtime, queries=queries)
    return queries


def _run(name: str, params: dict) -> tuple[list[dict], dict]:
    q = load_queries()[name]
    try:
        with connections["khanza"].cursor() as cursor:
            cursor.execute(q["sql"], params)
            cols = [c[0] for c in cursor.description]
            rows = cursor.fetchmany(MAX_ROWS)
    except KhanzaNotConfigured:
        raise
    except Exception as exc:
        raise ServiceUnavailable(f"database Khanza tidak dapat diakses ({type(exc).__name__})") from exc
    return [dict(zip(cols, row)) for row in rows], q["columns"]


def _record(row: dict, cols: dict) -> PatientRecord:
    tgl = row.get(cols.get("tgl_lahir", ""), None)
    return PatientRecord(
        no_rkm_medis=str(row[cols["no_rkm_medis"]]),
        nama=str(row[cols["nama"]]),
        source="khanza",
        is_test_data=False,
        jk=str(row.get(cols.get("jk", ""), "") or ""),
        tgl_lahir=tgl.isoformat() if isinstance(tgl, date) else (str(tgl) if tgl else ""),
    )


class KhanzaPatientLookupService:
    source = "khanza"

    def search(self, query: str, limit: int = 20) -> list[PatientRecord]:
        term = query.strip()
        rows, cols = _run("patient_search", {"exact": term, "q": f"%{term}%", "limit": min(limit, MAX_ROWS)})
        return [_record(r, cols) for r in rows]

    def get(self, no_rkm_medis: str) -> PatientRecord | None:
        rows, cols = _run("patient_get", {"no_rkm_medis": no_rkm_medis.strip()})
        return _record(rows[0], cols) if rows else None


class KhanzaControlScheduleService:
    source = "khanza"

    def find_control(self, no_rkm_medis: str, on_date=None) -> ControlResult:
        from django.utils import timezone

        target = on_date or timezone.localdate()
        rows, cols = _run("control_schedule", {"no_rkm_medis": no_rkm_medis, "tanggal": target.isoformat()})
        if not rows:
            return ControlResult(False, f"Data kontrol {target.isoformat()} belum ditemukan di Khanza. Periksa manual.",
                                 [])
        schedules = []
        for r in rows:
            item = {}
            for key, col in cols.items():
                value = r.get(col)
                item[key] = value.isoformat() if isinstance(value, date) else ("" if value is None else str(value))
            schedules.append(item)
        first = schedules[0]
        label = SOURCE_LABELS.get(first.get("sumber", ""), first.get("sumber", "Khanza"))
        detail = f"{label} {first.get('tanggal', '')}: {first.get('poli') or '-'} / {first.get('dokter') or '-'}"
        if len(schedules) > 1:
            detail += f" (+{len(schedules) - 1} data lain)"
        return ControlResult(True, detail, schedules)


_health_cache: dict = {"at": 0.0, "value": None}


def health(max_age: float = 60.0) -> dict:
    """Status integrasi untuk dashboard; ping database paling sering sekali per menit."""
    if not settings.KHANZA_ENABLED:
        return {"enabled": False, "status": "DISABLED", "message": "Mode simulasi (data sintetis)"}
    now = time.monotonic()
    if _health_cache["value"] is not None and now - _health_cache["at"] < max_age:
        return _health_cache["value"]
    try:
        load_queries()
        with connections["khanza"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        value = {"enabled": True, "status": "READY", "message": "Terhubung (baca-saja)"}
    except ServiceUnavailable as exc:
        value = {"enabled": True, "status": "ERROR", "message": str(exc)}
    except Exception as exc:
        value = {"enabled": True, "status": "ERROR", "message": f"koneksi gagal ({type(exc).__name__})"}
    _health_cache.update(at=now, value=value)
    return value
