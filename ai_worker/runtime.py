"""Penyetelan thread inferensi CPU sesuai kuota CPU container.

OpenCV dan ONNX Runtime default membuat thread sebanyak core HOST. Di container yang dibatasi
(`deploy.resources.limits.cpus` / VISION_CPUS) hal ini menyebabkan CPU throttling dan latensi
melonjak (terukur ±3x lebih lambat pada kuota 2 CPU). Modul ini menghitung CPU efektif dari
cgroup lalu membatasi jumlah thread.
"""
from __future__ import annotations

import math
import os
import threading
from pathlib import Path

MAX_THREADS = 4
_lock = threading.Lock()
_configured: int | None = None


def effective_cpus() -> float:
    """CPU yang benar-benar boleh dipakai proses ini (kuota cgroup v2/v1, lalu afinitas CPU)."""
    candidates = []
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            candidates.append(int(quota) / int(period))
    except (OSError, ValueError):
        try:
            quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
            period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
            if quota > 0 and period > 0:
                candidates.append(quota / period)
        except (OSError, ValueError):
            pass
    try:
        candidates.append(len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        candidates.append(os.cpu_count() or 1)
    return max(1.0, min(candidates))


def ai_threads() -> int:
    """Jumlah thread inferensi: FACE_AI_THREADS bila diisi, selain itu floor(CPU efektif), maks 4."""
    override = os.environ.get("FACE_AI_THREADS", "").strip()
    if override.isdigit() and int(override) > 0:
        return int(override)
    return max(1, min(MAX_THREADS, math.floor(effective_cpus())))


def configure_cv2_threads() -> int:
    """Set jumlah thread OpenCV sekali per proses; kembalikan nilai yang dipakai."""
    global _configured
    with _lock:
        if _configured is None:
            import cv2

            _configured = ai_threads()
            cv2.setNumThreads(_configured)
        return _configured
