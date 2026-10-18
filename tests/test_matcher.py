"""Pencocokan: kandidat, ambigu, tidak cocok, error, false match / false non-match (vektor sintetis)."""
import numpy as np
from django.test import SimpleTestCase

from ai_worker import matcher
from ai_worker.embedding import MODEL_VERSION
from ai_worker.errors import EmbeddingValidationError

from .helpers import unit_vector, vector_with_similarity

T = matcher.Thresholds(candidate=0.5, review=0.363, margin=0.08)


def refs(*items):
    return [matcher.ReferenceVector(i, key, vec) for i, (key, vec) in enumerate(items, start=1)]


class MatcherTests(SimpleTestCase):
    def setUp(self):
        self.a = unit_vector(10)
        self.b = unit_vector(20)
        self.c = unit_vector(30)

    def test_strong_match_is_candidate(self):
        probe = vector_with_similarity(self.a, 0.8)
        r = matcher.match(probe, refs(("UJI-A", self.a), ("UJI-B", self.b)), T)
        self.assertEqual(r.status, matcher.CANDIDATE)
        self.assertEqual(r.patient_key, "UJI-A")
        self.assertAlmostEqual(r.score, 0.8, places=4)

    def test_ambiguous_close_scores_need_review(self):
        # Dua pasien berbeda dengan skor berdekatan -> NEEDS_REVIEW walau di atas ambang kandidat.
        base = self.a
        other = vector_with_similarity(base, 0.98, seed=5)
        probe = vector_with_similarity(base, 0.75, seed=7)
        r = matcher.match(probe, refs(("UJI-A", base), ("UJI-B", other)), T)
        self.assertEqual(r.status, matcher.NEEDS_REVIEW)
        self.assertLess(r.margin, T.margin)

    def test_same_patient_multiple_refs_not_ambiguous(self):
        second_photo = vector_with_similarity(self.a, 0.97, seed=3)
        probe = vector_with_similarity(self.a, 0.8, seed=4)
        r = matcher.match(probe, refs(("UJI-A", self.a), ("UJI-A", second_photo), ("UJI-B", self.b)), T)
        self.assertEqual(r.status, matcher.CANDIDATE)

    def test_mid_score_needs_review(self):
        probe = vector_with_similarity(self.a, 0.42)
        r = matcher.match(probe, refs(("UJI-A", self.a)), T)
        self.assertEqual(r.status, matcher.NEEDS_REVIEW)

    def test_low_score_no_match(self):
        probe = vector_with_similarity(self.a, 0.1)
        r = matcher.match(probe, refs(("UJI-A", self.a)), T)
        self.assertEqual(r.status, matcher.NO_MATCH)
        self.assertIsNone(r.reference_id)

    def test_no_references_no_match(self):
        r = matcher.match(self.a, [], T)
        self.assertEqual(r.status, matcher.NO_MATCH)

    def test_dimension_mismatch_error(self):
        r = matcher.match([0.1] * 64, refs(("UJI-A", self.a)), T)
        self.assertEqual(r.status, matcher.ERROR)

    def test_model_version_mismatch_error(self):
        r = matcher.match(self.a, refs(("UJI-A", self.a)), T, model_version="model-lain/v1")
        self.assertEqual(r.status, matcher.ERROR)

    def test_nan_rejected(self):
        bad = self.a.copy()
        bad[0] = np.nan
        with self.assertRaises(EmbeddingValidationError):
            matcher.validate_embedding(bad)

    def test_normalization_applied(self):
        r = matcher.match(self.a * 7.5, refs(("UJI-A", self.a)), T)
        self.assertAlmostEqual(r.score, 1.0, places=4)

    def test_invalid_thresholds_rejected(self):
        with self.assertRaises(ValueError):
            matcher.Thresholds(candidate=0.3, review=0.5)

    def test_false_match_and_false_non_match_rates_synthetic(self):
        """Simulasi: 50 identitas, probe genuine (sim 0.55-0.9) dan impostor (orang tak terdaftar)."""
        gallery = refs(*[(f"UJI-{i:04d}", unit_vector(1000 + i)) for i in range(50)])
        rng = np.random.RandomState(0)
        false_non_match = 0
        for i, ref in enumerate(gallery):
            probe = vector_with_similarity(ref.vector, float(rng.uniform(0.55, 0.9)), seed=i)
            r = matcher.match(probe, gallery, T)
            if r.status == matcher.NO_MATCH or (r.patient_key and r.patient_key != ref.patient_key):
                false_non_match += 1
        false_match = 0
        for j in range(200):
            impostor = unit_vector(50000 + j)
            r = matcher.match(impostor, gallery, T)
            if r.status == matcher.CANDIDATE:
                false_match += 1
        self.assertEqual(false_non_match, 0)
        self.assertEqual(false_match, 0)
        # Catatan: angka ini dari vektor acak sintetis, BUKAN validasi akurasi model nyata.

    def test_version_constant(self):
        self.assertTrue(MODEL_VERSION.startswith("opencv-zoo/sface"))
