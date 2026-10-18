"""PatientLookupService dan ControlScheduleService.

Mode demo memakai data sintetis dan tidak membaca maupun menulis database
Khanza. Adapter Khanza hanya dipakai jika KHANZA_ENABLED dan
KHANZA_SCHEMA_VERIFIED bernilai true (lihat apps/integrations/khanza.py).

Penentuan status kontrol dipisahkan dari pengenalan wajah: wajah tidak
menentukan apakah pasien datang untuk kontrol. Status kontrol hanya
diperiksa setelah identitas diverifikasi petugas.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.utils import timezone

from .demo_data import SYNTHETIC_CONTROL_SCHEDULES, SYNTHETIC_PATIENTS

logger = logging.getLogger(__name__)


@dataclass
class PatientRecord:
    no_rkm_medis: str
    nama: str
    source: str
    is_test_data: bool = True
    # Hanya untuk membedakan pasien bernama sama saat verifikasi manual.
    jk: str = ""
    tgl_lahir: str = ""

    def as_dict(self) -> dict:
        return {"no_rkm_medis": self.no_rkm_medis, "nama": self.nama, "source": self.source,
                "is_test_data": self.is_test_data, "jk": self.jk, "tgl_lahir": self.tgl_lahir}


@dataclass
class ControlResult:
    found: bool
    detail: str
    schedules: list[dict]


class ServiceUnavailable(Exception):
    pass


class SimulatedPatientLookupService:
    source = "simulasi"

    def search(self, query: str, limit: int = 20) -> list[PatientRecord]:
        q = query.strip().lower()
        if len(q) < 2:
            return []
        results: dict[str, PatientRecord] = {}
        for p in SYNTHETIC_PATIENTS:
            if q in p["nama"].lower() or q in p["no_rkm_medis"].lower():
                results[p["no_rkm_medis"]] = PatientRecord(p["no_rkm_medis"], p["nama"], "simulasi")
        # Pasien uji yang sudah terdaftar referensi wajahnya juga dapat dicari.
        from apps.face_registry.models import PatientFaceReference

        refs = PatientFaceReference.objects.filter(is_test_data=True).values_list("no_rkm_medis", "patient_name")
        for rm, name in refs:
            if rm not in results and (q in rm.lower() or q in name.lower()):
                results[rm] = PatientRecord(rm, name, "referensi-uji")
        return list(results.values())[:limit]

    def get(self, no_rkm_medis: str) -> PatientRecord | None:
        rm = no_rkm_medis.strip().upper()
        for p in SYNTHETIC_PATIENTS:
            if p["no_rkm_medis"] == rm:
                return PatientRecord(p["no_rkm_medis"], p["nama"], "simulasi")
        from apps.face_registry.models import PatientFaceReference

        ref = PatientFaceReference.objects.filter(no_rkm_medis=rm, is_test_data=True).first()
        if ref:
            return PatientRecord(ref.no_rkm_medis, ref.patient_name, "referensi-uji")
        return None


class SimulatedControlScheduleService:
    source = "simulasi"

    def find_control(self, no_rkm_medis: str, on_date: date | None = None) -> ControlResult:
        base = timezone.localdate()
        target = on_date or base
        matches = []
        for s in SYNTHETIC_CONTROL_SCHEDULES:
            scheduled = base + timedelta(days=s["offset_days"])
            if s["no_rkm_medis"] == no_rkm_medis and scheduled == target:
                matches.append({"tanggal": scheduled.isoformat(), "poli": s["poli"], "dokter": s["dokter"]})
        if matches:
            return ControlResult(True, f"Jadwal kontrol {matches[0]['tanggal']}: {matches[0]['poli']}", matches)
        return ControlResult(False, "Data kontrol belum ditemukan pada sistem simulasi. Periksa manual.", [])


def get_patient_lookup_service():
    if settings.KHANZA_ENABLED:
        from apps.integrations.khanza import KhanzaPatientLookupService

        return KhanzaPatientLookupService()
    return SimulatedPatientLookupService()


def get_control_schedule_service():
    if settings.KHANZA_ENABLED:
        from apps.integrations.khanza import KhanzaControlScheduleService

        return KhanzaControlScheduleService()
    return SimulatedControlScheduleService()
