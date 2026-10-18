"""Pelacakan wajah antarbingkai berbasis IoU.

ID track hanya penanda sementara selama wajah berada di area kamera dan
BUKAN identitas pasien.
"""
from __future__ import annotations

import itertools
import secrets
from dataclasses import dataclass, field

import numpy as np

from .detector import Detection


def iou(a, b) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ax1, ay1, bx1, by1 = ax0 + aw, ay0 + ah, bx0 + bw, by0 + bh
    iw = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    track_id: str
    detection: Detection
    first_seen: float
    last_seen: float
    hits: int = 1
    visible: bool = True
    best_score: float = -1.0
    best_aligned: np.ndarray | None = None
    last_quality: dict | None = None
    last_submit_at: float = 0.0
    submissions: int = 0
    pending: bool = False
    match_status: str = ""  # status terakhir dari Django (tanpa nama pasien)
    event_id: int | None = None
    identity: str | None = None  # label "Nama (No. RM)" dari Django; hanya digambar bila diizinkan
    extra: dict = field(default_factory=dict)

    @property
    def bbox(self):
        return self.detection.bbox

    def age(self, now: float) -> float:
        return now - self.first_seen


class IoUTracker:
    def __init__(self, iou_threshold: float = 0.3, max_missed_seconds: float = 1.5,
                 session_id: str | None = None):
        self.iou_threshold = iou_threshold
        self.max_missed_seconds = max_missed_seconds
        self.session_id = session_id or secrets.token_hex(3)
        self._counter = itertools.count(1)
        self.tracks: dict[str, Track] = {}

    def _new_id(self) -> str:
        return f"{self.session_id}-{next(self._counter)}"

    def update(self, detections: list[Detection], now: float) -> tuple[list[Track], list[Track]]:
        """Perbarui track. Mengembalikan (track_terlihat, track_dihapus)."""
        for track in self.tracks.values():
            track.visible = False

        pairs = []
        for tid, track in self.tracks.items():
            for di, det in enumerate(detections):
                score = iou(track.bbox, det.bbox)
                if score >= self.iou_threshold:
                    pairs.append((score, tid, di))
        pairs.sort(reverse=True)
        used_tracks, used_dets = set(), set()
        for _, tid, di in pairs:
            if tid in used_tracks or di in used_dets:
                continue
            track = self.tracks[tid]
            track.detection = detections[di]
            track.last_seen = now
            track.hits += 1
            track.visible = True
            used_tracks.add(tid)
            used_dets.add(di)

        for di, det in enumerate(detections):
            if di not in used_dets:
                tid = self._new_id()
                self.tracks[tid] = Track(track_id=tid, detection=det, first_seen=now, last_seen=now)

        removed = [t for t in self.tracks.values() if now - t.last_seen > self.max_missed_seconds]
        for track in removed:
            # Hapus juga crop wajah terbaik dari memori ketika wajah meninggalkan area.
            track.best_aligned = None
            del self.tracks[track.track_id]
        visible = [t for t in self.tracks.values() if t.visible]
        return visible, removed
