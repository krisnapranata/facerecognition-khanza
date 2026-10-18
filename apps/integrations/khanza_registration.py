"""Registrasi kunjungan rawat jalan ke ``reg_periksa`` Khanza, mengikuti pola
``simrskhanza/DlgReg.java`` (isCekPasien, isNumber, isRegistrasi, UpdateUmur).

Aturan yang ditiru:
- no_rawat  : ``yyyy/MM/dd/`` + 6 digit (MAX 6 digit terakhir pada tanggal itu + 1)
- no_reg    : 3 digit, MAX + 1 menurut ``URUTNOREG`` (poli | dokter | dokter + poli); jika
              ``BASENOREG=booking`` diambil yang lebih besar dari booking_registrasi/reg_periksa
- stts_daftar: 'Baru' bila pasien.tgl_daftar = tanggal registrasi, selain itu 'Lama'
- status_poli: 'Lama' bila pasien pernah terdaftar di poli tsb, selain itu 'Baru'
- biaya_reg : poliklinik.registrasi (Baru) / poliklinik.registrasilama (Lama)
- p_jawab, almt_pj, hubunganpj, kd_pj dari data pasien; umur Th/Bl/Hr seperti Khanza
- stts='Belum', status_lanjut='Ralan', status_bayar='Belum Bayar'
- INSERT dicoba ulang (maks. 5x) dengan nomor baru bila bentrok; lalu UpdateUmur
- Validasi: pasien tidak sedang rawat inap; tagihan belum closing bila
  set_validasi_registrasi.wajib_closing_kasir='Yes'

Tambahan aplikasi ini: hanya untuk identitas yang sudah diverifikasi petugas, menolak
registrasi ganda pada poli yang sama di hari yang sama, dan serialisasi penomoran
dengan GET_LOCK antarproses aplikasi ini.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.common.api import ApiError

from . import khanza_rw

logger = logging.getLogger(__name__)

HARI = ("SENIN", "SELASA", "RABU", "KAMIS", "JUMAT", "SABTU", "AKHAD")  # enum jadwal.hari_kerja
MAX_ATTEMPTS = 5
LOCK_NAME = "facerec_reg_periksa"

# Ekspresi umur identik dengan DlgReg.isCekPasien / UpdateUmur.
_AGE_COLUMNS = (
    "TIMESTAMPDIFF(YEAR, p.tgl_lahir, CURDATE()) AS tahun, "
    "(TIMESTAMPDIFF(MONTH, p.tgl_lahir, CURDATE()) - ((TIMESTAMPDIFF(MONTH, p.tgl_lahir, CURDATE()) div 12) * 12)) AS bulan, "
    "TIMESTAMPDIFF(DAY, DATE_ADD(DATE_ADD(p.tgl_lahir, INTERVAL TIMESTAMPDIFF(YEAR, p.tgl_lahir, CURDATE()) YEAR), "
    "INTERVAL TIMESTAMPDIFF(MONTH, p.tgl_lahir, CURDATE()) - ((TIMESTAMPDIFF(MONTH, p.tgl_lahir, CURDATE()) div 12) * 12) MONTH), "
    "CURDATE()) AS hari"
)
SQL_PATIENT = (
    "SELECT p.no_rkm_medis, p.nm_pasien, CONCAT(p.alamat, ', ', kel.nm_kel, ', ', kec.nm_kec, ', ', kab.nm_kab) AS asal, "
    "p.namakeluarga, p.keluarga, p.kd_pj, pj.png_jawab, p.tgl_daftar, " + _AGE_COLUMNS + " "
    "FROM pasien p INNER JOIN kelurahan kel ON p.kd_kel = kel.kd_kel INNER JOIN kecamatan kec ON p.kd_kec = kec.kd_kec "
    "INNER JOIN kabupaten kab ON p.kd_kab = kab.kd_kab INNER JOIN penjab pj ON p.kd_pj = pj.kd_pj "
    "WHERE p.no_rkm_medis = %s"
)
SQL_UPDATE_UMUR = (
    "UPDATE pasien SET umur=CONCAT(CONCAT(CONCAT(TIMESTAMPDIFF(YEAR, tgl_lahir, CURDATE()), ' Th '),"
    "CONCAT(TIMESTAMPDIFF(MONTH, tgl_lahir, CURDATE()) - ((TIMESTAMPDIFF(MONTH, tgl_lahir, CURDATE()) div 12) * 12), ' Bl ')),"
    "CONCAT(TIMESTAMPDIFF(DAY, DATE_ADD(DATE_ADD(tgl_lahir,INTERVAL TIMESTAMPDIFF(YEAR, tgl_lahir, CURDATE()) YEAR), "
    "INTERVAL TIMESTAMPDIFF(MONTH, tgl_lahir, CURDATE()) - ((TIMESTAMPDIFF(MONTH, tgl_lahir, CURDATE()) div 12) * 12) MONTH), "
    "CURDATE()), ' Hr')) WHERE pasien.no_rkm_medis=%s"
)
INSERT_COLUMNS = ("no_reg", "no_rawat", "tgl_registrasi", "jam_reg", "kd_dokter", "no_rkm_medis", "kd_poli", "p_jawab",
                  "almt_pj", "hubunganpj", "biaya_reg", "stts", "stts_daftar", "status_lanjut", "kd_pj", "umurdaftar",
                  "sttsumur", "status_bayar", "status_poli")
SQL_INSERT = khanza_rw.register_write(f"INSERT INTO reg_periksa ({', '.join(INSERT_COLUMNS)}) "
                                      f"VALUES ({', '.join(['%s'] * len(INSERT_COLUMNS))})")
khanza_rw.register_write(SQL_UPDATE_UMUR)


# --- fungsi murni (diuji tanpa database) ---------------------------------------------
def auto_nomer3(max_value, prefix: str, length: int) -> str:
    """Padanan Valid.autoNomer3: MAX+1, dipad nol di depan sampai ``length``."""
    s = str(int(max_value or 0) + 1)
    return prefix + "0" * max(0, length - len(s)) + s


def age_from_parts(tahun: int, bulan: int, hari: int) -> tuple[int, str]:
    """Aturan DlgReg: tahun>0 -> Th; tahun==0 & bulan>0 -> Bl; selain itu hari -> Hr."""
    tahun, bulan, hari = int(tahun or 0), int(bulan or 0), int(hari or 0)
    if tahun > 0:
        return tahun, "Th"
    if bulan > 0:
        return bulan, "Bl"
    return hari, "Hr"


def no_reg_scope(urut: str) -> tuple[str, ...]:
    return {"poli": ("kd_poli",), "dokter": ("kd_dokter",), "dokter + poli": ("kd_dokter", "kd_poli")}.get(
        urut, ("kd_dokter",))


def hari_kerja(d: date) -> str:
    return HARI[d.weekday()]


# --- data ----------------------------------------------------------------------------
@dataclass
class RegistrationPlan:
    no_rkm_medis: str
    nm_pasien: str
    tgl_registrasi: str
    jam_reg: str
    kd_poli: str
    nm_poli: str
    kd_dokter: str
    nm_dokter: str
    no_reg: str
    no_rawat: str
    p_jawab: str
    almt_pj: str
    hubunganpj: str
    biaya_reg: float
    stts_daftar: str
    status_poli: str
    kd_pj: str
    png_jawab: str
    umurdaftar: int
    sttsumur: str
    warnings: list[str] = field(default_factory=list)

    def insert_values(self) -> list:
        return [self.no_reg, self.no_rawat, self.tgl_registrasi, self.jam_reg, self.kd_dokter, self.no_rkm_medis,
                self.kd_poli, self.p_jawab, self.almt_pj, self.hubunganpj, self.biaya_reg, "Belum", self.stts_daftar,
                "Ralan", self.kd_pj, self.umurdaftar, self.sttsumur, "Belum Bayar", self.status_poli]

    def as_dict(self) -> dict:
        return asdict(self)


# --- repository (SQL MySQL; dipalsukan dalam pengujian) ----------------------------------
class KhanzaRegistrationRepository:
    def _all(self, sql, params=()):
        with khanza_rw.cursor() as cur:
            cur.execute(sql, list(params))
            return khanza_rw.fetch_dicts(cur)

    def _scalar(self, sql, params=()):
        with khanza_rw.cursor() as cur:
            cur.execute(sql, list(params))
            row = cur.fetchone()
            return row[0] if row else None

    def polies(self) -> list[dict]:
        return self._all("SELECT kd_poli, nm_poli, registrasi, registrasilama FROM poliklinik "
                         "WHERE status = '1' ORDER BY nm_poli")

    def schedules(self) -> list[dict]:
        """Seluruh jadwal praktik (tabel jadwal) untuk dokter & poli yang aktif."""
        # kd_dokter '-' adalah baris placeholder Khanza, bukan dokter sungguhan.
        return self._all(
            "SELECT j.kd_dokter, d.nm_dokter, j.kd_poli, j.hari_kerja, j.jam_mulai, j.jam_selesai, j.kuota "
            "FROM jadwal j INNER JOIN dokter d ON d.kd_dokter = j.kd_dokter "
            "INNER JOIN poliklinik p ON p.kd_poli = j.kd_poli "
            "WHERE d.status = '1' AND p.status = '1' AND d.kd_dokter <> '-' "
            "ORDER BY d.nm_dokter, FIELD(j.hari_kerja, 'SENIN', 'SELASA', 'RABU', 'KAMIS', 'JUMAT', 'SABTU', 'AKHAD'), "
            "j.jam_mulai")

    def registrations_per_doctor(self, d: date) -> dict[str, int]:
        """Jumlah registrasi per dokter pada tanggal tsb (dasar cek kuota DlgReg)."""
        rows = self._all("SELECT kd_dokter, COUNT(no_rawat) AS n FROM reg_periksa WHERE tgl_registrasi = %s "
                         "GROUP BY kd_dokter", [d])
        return {r["kd_dokter"]: int(r["n"]) for r in rows}

    def suggestions(self, rm: str, d: date) -> list[dict]:
        out = []
        for r in self._all("SELECT kd_poli, kd_dokter, status FROM booking_registrasi WHERE no_rkm_medis = %s "
                           "AND tanggal_periksa = %s AND status IN ('Terdaftar', 'Belum')", [rm, d]):
            out.append({"sumber": "booking_registrasi", "kd_poli": r["kd_poli"], "kd_dokter": r["kd_dokter"],
                        "keterangan": f"Booking ({r['status']})"})
        for r in self._all(
                "SELECT k.no_surat, k.nm_poli_bpjs, k.nm_dokter_bpjs, mp.kd_poli_rs, md.kd_dokter "
                "FROM bridging_surat_kontrol_bpjs k INNER JOIN bridging_sep s ON s.no_sep = k.no_sep "
                "LEFT JOIN maping_poli_bpjs mp ON mp.kd_poli_bpjs = k.kd_poli_bpjs "
                "LEFT JOIN maping_dokter_dpjpvclaim md ON md.kd_dokter_bpjs = k.kd_dokter_bpjs "
                "WHERE s.nomr = %s AND k.tgl_rencana = %s", [rm, d]):
            out.append({"sumber": "surat_kontrol_bpjs", "kd_poli": r["kd_poli_rs"], "kd_dokter": r["kd_dokter"],
                        "keterangan": f"Surat kontrol {r['no_surat']}: {r['nm_poli_bpjs']} / {r['nm_dokter_bpjs']}"})
        for r in self._all("SELECT kd_dokter FROM skdp_bpjs WHERE no_rkm_medis = %s AND tanggal_datang >= %s "
                           "AND tanggal_datang < DATE_ADD(%s, INTERVAL 1 DAY) AND status = 'Menunggu'", [rm, d, d]):
            out.append({"sumber": "skdp_bpjs", "kd_poli": None, "kd_dokter": r["kd_dokter"], "keterangan": "SKDP"})
        return out

    def patient(self, rm: str) -> dict | None:
        rows = self._all(SQL_PATIENT, [rm])
        return rows[0] if rows else None

    def is_inpatient(self, rm: str) -> bool:
        return bool(self._scalar(
            "SELECT COUNT(p.no_rkm_medis) FROM pasien p INNER JOIN reg_periksa r ON r.no_rkm_medis = p.no_rkm_medis "
            "INNER JOIN kamar_inap k ON r.no_rawat = k.no_rawat WHERE k.stts_pulang = '-' AND p.no_rkm_medis = %s",
            [rm]))

    def closing_required(self) -> bool:
        return str(self._scalar("SELECT wajib_closing_kasir FROM set_validasi_registrasi LIMIT 1") or "No") == "Yes"

    def unpaid_visits(self, rm: str) -> int:
        return int(self._scalar("SELECT COUNT(no_rkm_medis) FROM reg_periksa WHERE no_rkm_medis = %s "
                                "AND status_bayar = 'Belum Bayar' AND stts <> 'Batal'", [rm]) or 0)

    def existing_today(self, rm: str, kd_poli: str, d: date) -> dict | None:
        rows = self._all("SELECT no_rawat, no_reg FROM reg_periksa WHERE no_rkm_medis = %s AND kd_poli = %s "
                         "AND tgl_registrasi = %s AND stts <> 'Batal' LIMIT 1", [rm, kd_poli, d])
        return rows[0] if rows else None

    def visited_poli(self, rm: str, kd_poli: str) -> bool:
        return bool(self._scalar("SELECT COUNT(no_rkm_medis) FROM reg_periksa WHERE no_rkm_medis = %s AND kd_poli = %s",
                                 [rm, kd_poli]))

    def max_no_reg(self, table: str, scope: dict, d: date) -> int:
        date_col = {"reg_periksa": "tgl_registrasi", "booking_registrasi": "tanggal_periksa"}[table]
        where = " AND ".join(f"{col} = %s" for col in scope)  # kolom dari allowlist no_reg_scope
        sql = f"SELECT IFNULL(MAX(CONVERT(no_reg, SIGNED)), 0) FROM {table} WHERE {where} AND {date_col} = %s"
        return int(self._scalar(sql, [*scope.values(), d]) or 0)

    def max_no_rawat(self, d: date) -> int:
        return int(self._scalar("SELECT IFNULL(MAX(CONVERT(RIGHT(no_rawat, 6), SIGNED)), 0) FROM reg_periksa "
                                "WHERE tgl_registrasi = %s", [d]) or 0)

    @transaction.atomic(using=khanza_rw.ALIAS)
    def insert(self, plan: RegistrationPlan) -> None:
        with khanza_rw.cursor() as cur:
            cur.execute(SQL_INSERT, plan.insert_values())
            cur.execute(SQL_UPDATE_UMUR, [plan.no_rkm_medis])

    def acquire_lock(self) -> bool:
        return bool(self._scalar("SELECT GET_LOCK(%s, 10)", [LOCK_NAME]))

    def release_lock(self) -> None:
        self._scalar("SELECT RELEASE_LOCK(%s)", [LOCK_NAME])


# --- service ---------------------------------------------------------------------------
class VisitRegistrationService:
    def __init__(self, repo: KhanzaRegistrationRepository | None = None):
        self.repo = repo or KhanzaRegistrationRepository()

    def _schedule_index(self, d: date) -> dict:
        """Kelompokkan tabel jadwal: {kd_poli: {kd_dokter: {...}}} dengan status hari ini & kuota."""
        today = hari_kerja(d)
        strict = settings.KHANZA_JADWAL_MODE == "hari_ini"
        counts = self.repo.registrations_per_doctor(d)
        index: dict[str, dict[str, dict]] = {}
        for s in self.repo.schedules():
            doc = index.setdefault(s["kd_poli"], {}).setdefault(s["kd_dokter"], {
                "kd_dokter": s["kd_dokter"], "nm_dokter": " ".join(str(s["nm_dokter"]).split()), "jadwal": [], "hari_ini": [],
                "kuota": None, "terisi": counts.get(s["kd_dokter"], 0)})
            slot = {"hari": s["hari_kerja"], "jam": f"{str(s['jam_mulai'])[:5]}-{str(s['jam_selesai'])[:5]}"}
            doc["jadwal"].append(slot)
            if s["hari_kerja"] == today:
                doc["hari_ini"].append(slot["jam"])
                if s["kuota"]:
                    doc["kuota"] = max(doc["kuota"] or 0, int(s["kuota"]))
        for doctors in index.values():
            for doc in doctors.values():
                full = bool(doc["kuota"]) and doc["terisi"] >= doc["kuota"]
                if strict and not doc["hari_ini"]:
                    doc["selectable"], doc["reason"] = False, "Tidak praktik hari ini"
                elif strict and full:
                    doc["selectable"], doc["reason"] = False, "Kuota hari ini penuh"
                else:
                    doc["selectable"], doc["reason"] = True, "" if doc["hari_ini"] else "Di luar jadwal hari ini"
        return index

    def options(self, rm: str, d: date | None = None) -> dict:
        d = d or timezone.localdate()
        excluded = set(settings.KHANZA_EXCLUDED_POLI)
        index = self._schedule_index(d)
        polies = []
        for p in self.repo.polies():
            if p["kd_poli"] in excluded or p["kd_poli"] not in index:
                continue  # hanya poli yang memiliki jadwal dokter
            doctors = sorted(index[p["kd_poli"]].values(),
                             key=lambda x: (not x["selectable"], not x["hari_ini"], x["nm_dokter"]))
            polies.append({"kd_poli": p["kd_poli"], "nm_poli": p["nm_poli"], "doctors": doctors,
                           "praktik_hari_ini": sum(1 for x in doctors if x["hari_ini"]),
                           "selectable": any(x["selectable"] for x in doctors)})
        polies.sort(key=lambda x: (not x["selectable"], -x["praktik_hari_ini"], x["nm_poli"]))
        suggestions = []
        for s in self.repo.suggestions(rm, d):
            doc = index.get(s["kd_poli"] or "", {}).get(s["kd_dokter"] or "")
            if s["kd_poli"] not in index:
                s["kd_poli"] = None
            s["kd_dokter"] = s["kd_dokter"] if doc and doc["selectable"] else None
            suggestions.append(s)
        return {"tanggal": d.isoformat(), "hari": hari_kerja(d), "mode_jadwal": settings.KHANZA_JADWAL_MODE,
                "polies": polies, "suggestions": suggestions}

    def plan(self, rm: str, kd_poli: str, kd_dokter: str, now: datetime | None = None) -> RegistrationPlan:
        now = timezone.localtime(now) if now else timezone.localtime()
        d = now.date()
        poli = next((p for p in self.repo.polies() if p["kd_poli"] == kd_poli), None)
        if poli is None or kd_poli in settings.KHANZA_EXCLUDED_POLI:
            raise ApiError("VALIDATION_ERROR", "Poliklinik tidak valid/aktif", 422, {"kd_poli": "tidak valid"})
        dokter = self._schedule_index(d).get(kd_poli, {}).get(kd_dokter)
        if dokter is None:
            raise ApiError("VALIDATION_ERROR", "Dokter tidak memiliki jadwal di poliklinik ini", 422,
                           {"kd_dokter": "tidak ada di tabel jadwal untuk poli ini"})
        if not dokter["selectable"]:
            raise ApiError("SCHEDULE_UNAVAILABLE", f"{dokter['nm_dokter']}: {dokter['reason']}", 409,
                           {"kd_dokter": dokter["reason"]})
        pasien = self.repo.patient(rm)
        if pasien is None:
            raise ApiError("PATIENT_INCOMPLETE", "Data pasien/alamat/cara bayar di Khanza tidak lengkap", 422)
        if self.repo.is_inpatient(rm):
            raise ApiError("INPATIENT", "Pasien sedang dalam masa perawatan di kamar inap", 409)
        if self.repo.closing_required() and self.repo.unpaid_visits(rm) > 0:
            raise ApiError("UNPAID_PREVIOUS", "Kunjungan sebelumnya memiliki tagihan yang belum di-closing kasir", 409)
        existing = self.repo.existing_today(rm, kd_poli, d)
        if existing:
            raise ApiError("ALREADY_REGISTERED",
                           f"Pasien sudah terdaftar hari ini di poli ini (No. Rawat {existing['no_rawat']})", 409)

        stts_daftar = "Baru" if pasien["tgl_daftar"] == d else "Lama"
        biaya = poli["registrasi"] if stts_daftar == "Baru" else poli["registrasilama"]
        umur, sttsumur = age_from_parts(pasien["tahun"], pasien["bulan"], pasien["hari"])
        no_reg, no_rawat = self._numbers(kd_poli, kd_dokter, d)
        return RegistrationPlan(
            no_rkm_medis=pasien["no_rkm_medis"], nm_pasien=pasien["nm_pasien"], tgl_registrasi=d.isoformat(),
            jam_reg=now.strftime("%H:%M:%S"), kd_poli=kd_poli, nm_poli=poli["nm_poli"], kd_dokter=kd_dokter,
            nm_dokter=dokter["nm_dokter"], no_reg=no_reg, no_rawat=no_rawat, p_jawab=pasien["namakeluarga"] or "",
            almt_pj=pasien["asal"] or "", hubunganpj=pasien["keluarga"] or "", biaya_reg=float(biaya or 0),
            stts_daftar=stts_daftar, status_poli="Lama" if self.repo.visited_poli(rm, kd_poli) else "Baru",
            kd_pj=pasien["kd_pj"], png_jawab=pasien["png_jawab"], umurdaftar=umur, sttsumur=sttsumur,
        )

    def _numbers(self, kd_poli: str, kd_dokter: str, d: date) -> tuple[str, str]:
        values = {"kd_poli": kd_poli, "kd_dokter": kd_dokter}
        scope = {col: values[col] for col in no_reg_scope(settings.KHANZA_URUTNOREG)}
        max_reg = self.repo.max_no_reg("reg_periksa", scope, d)
        if settings.KHANZA_BASENOREG == "booking":
            max_reg = max(max_reg, self.repo.max_no_reg("booking_registrasi", scope, d))
        no_reg = auto_nomer3(max_reg, "", 3)
        no_rawat = auto_nomer3(self.repo.max_no_rawat(d), d.strftime("%Y/%m/%d/"), 6)
        return no_reg, no_rawat

    def register(self, rm: str, kd_poli: str, kd_dokter: str) -> RegistrationPlan:
        if not self.repo.acquire_lock():
            raise ApiError("BUSY", "Penomoran sedang dipakai proses lain, coba lagi", 503)
        try:
            last_error = None
            for _attempt in range(MAX_ATTEMPTS):
                plan = self.plan(rm, kd_poli, kd_dokter)  # nomor dihitung ulang setiap percobaan
                try:
                    self.repo.insert(plan)
                    return plan
                except IntegrityError as exc:  # no_rawat bentrok (mis. diisi bersamaan oleh Khanza desktop)
                    last_error = exc
                    logger.warning("No. rawat %s bentrok, mencoba nomor baru", plan.no_rawat)
            raise ApiError("REGISTRATION_FAILED", f"Gagal menyimpan registrasi setelah {MAX_ATTEMPTS} percobaan", 500) \
                from last_error
        finally:
            self.repo.release_lock()
