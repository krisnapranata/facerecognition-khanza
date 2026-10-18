"""Data pasien SINTETIS untuk mode demo.

Semua nama fiktif dan nomor RM berawalan ``UJI-`` sehingga tidak menyerupai
data produksi. Jadwal kontrol relatif terhadap tanggal hari ini.
"""

SYNTHETIC_PATIENTS = [
    {"no_rkm_medis": "UJI-0001", "nama": "Pasien Uji Satu", "jk": "L", "tahun_lahir": 1980},
    {"no_rkm_medis": "UJI-0002", "nama": "Pasien Uji Dua", "jk": "P", "tahun_lahir": 1975},
    {"no_rkm_medis": "UJI-0003", "nama": "Pasien Uji Tiga", "jk": "L", "tahun_lahir": 1992},
    {"no_rkm_medis": "UJI-0004", "nama": "Pasien Uji Empat", "jk": "P", "tahun_lahir": 1968},
    {"no_rkm_medis": "UJI-0005", "nama": "Pasien Uji Lima", "jk": "L", "tahun_lahir": 2001},
]

# offset_days: 0 = hari ini, 1 = besok, -1 = kemarin
SYNTHETIC_CONTROL_SCHEDULES = [
    {"no_rkm_medis": "UJI-0001", "offset_days": 0, "poli": "Poli Penyakit Dalam (simulasi)", "dokter": "dr. Simulasi A"},
    {"no_rkm_medis": "UJI-0002", "offset_days": 1, "poli": "Poli Saraf (simulasi)", "dokter": "dr. Simulasi B"},
    {"no_rkm_medis": "UJI-0003", "offset_days": 0, "poli": "Poli Jantung (simulasi)", "dokter": "dr. Simulasi C"},
]
