"""Pencocokan embedding wajah (tanpa dependensi Django).

Skor similarity adalah cosine similarity antara embedding ter-normalisasi L2.
Skor ini BUKAN persentase kepastian identitas. Ambang bersifat awal dan
wajib divalidasi dengan data lokal sebelum dipakai di produksi.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from .embedding import EMBEDDING_DIM, MODEL_VERSION
from .errors import EmbeddingValidationError

CANDIDATE = "CANDIDATE"
NEEDS_REVIEW = "NEEDS_REVIEW"
NO_MATCH = "NO_MATCH"
ERROR = "ERROR"
STATUSES = (CANDIDATE, NEEDS_REVIEW, NO_MATCH, ERROR)


@dataclass(frozen=True)
class Thresholds:
    # Nilai awal untuk SFace; OpenCV melaporkan ambang cosine 0.363 pada LFW.
    candidate: float = 0.50
    review: float = 0.363
    margin: float = 0.08

    def __post_init__(self):
        if not (-1.0 <= self.review <= self.candidate <= 1.0):
            raise ValueError("ambang harus memenuhi -1 <= review <= candidate <= 1")
        if self.margin < 0:
            raise ValueError("margin tidak boleh negatif")


@dataclass(frozen=True)
class ReferenceVector:
    reference_id: int
    patient_key: str  # mis. no_rkm_medis; beberapa referensi dapat milik pasien yang sama
    vector: np.ndarray


@dataclass(frozen=True)
class MatchResult:
    status: str
    reference_id: int | None = None
    patient_key: str | None = None
    score: float | None = None
    second_score: float | None = None
    reason: str = ""

    @property
    def margin(self) -> float | None:
        if self.score is None:
            return None
        if self.second_score is None:
            return None
        return self.score - self.second_score


def validate_embedding(values: Sequence[float] | np.ndarray, dim: int = EMBEDDING_DIM) -> np.ndarray:
    try:
        vec = np.asarray(values, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError) as exc:
        raise EmbeddingValidationError("embedding harus berupa daftar angka") from exc
    if vec.shape[0] != dim:
        raise EmbeddingValidationError(f"dimensi embedding {vec.shape[0]} != {dim}")
    if not np.all(np.isfinite(vec)):
        raise EmbeddingValidationError("embedding mengandung NaN/Inf")
    norm = float(np.linalg.norm(vec))
    if norm == 0.0 or not math.isfinite(norm):
        raise EmbeddingValidationError("embedding nol")
    return vec / norm


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def match(probe: Sequence[float] | np.ndarray, references: Iterable[ReferenceVector],
          thresholds: Thresholds = Thresholds(), model_version: str = MODEL_VERSION,
          expected_version: str = MODEL_VERSION, dim: int = EMBEDDING_DIM) -> MatchResult:
    if model_version != expected_version:
        return MatchResult(ERROR, reason=f"versi model tidak sesuai: {model_version}")
    try:
        q = validate_embedding(probe, dim)
    except EmbeddingValidationError as exc:
        return MatchResult(ERROR, reason=str(exc))

    # Skor terbaik per pasien, agar beberapa foto milik pasien yang sama tidak
    # dianggap sebagai kandidat yang bersaing.
    best_per_patient: dict[str, tuple[float, int]] = {}
    for ref in references:
        if ref.vector.shape[0] != dim:
            continue
        score = cosine(q, ref.vector)
        current = best_per_patient.get(ref.patient_key)
        if current is None or score > current[0]:
            best_per_patient[ref.patient_key] = (score, ref.reference_id)

    if not best_per_patient:
        return MatchResult(NO_MATCH, reason="tidak ada referensi aktif")

    ranked = sorted(best_per_patient.items(), key=lambda kv: kv[1][0], reverse=True)
    top_key, (top_score, top_ref) = ranked[0]
    second_score = ranked[1][1][0] if len(ranked) > 1 else None

    if top_score < thresholds.review:
        return MatchResult(NO_MATCH, score=top_score, second_score=second_score,
                           reason="skor di bawah ambang review")

    ambiguous = second_score is not None and (top_score - second_score) < thresholds.margin
    if top_score >= thresholds.candidate and not ambiguous:
        status, reason = CANDIDATE, "skor di atas ambang kandidat"
    elif ambiguous:
        status, reason = NEEDS_REVIEW, "dua kandidat memiliki skor berdekatan"
    else:
        status, reason = NEEDS_REVIEW, "skor di antara ambang review dan kandidat"
    return MatchResult(status, reference_id=top_ref, patient_key=top_key, score=top_score,
                       second_score=second_score, reason=reason)
