"""Uji model AI nyata (dilewati jika file model belum dipasang). Tidak memakai foto wajah sungguhan."""
import unittest

import numpy as np
from django.test import SimpleTestCase

from ai_worker.config import WorkerConfig
from ai_worker.embedding import EMBEDDING_DIM, FaceEmbedder

CFG = WorkerConfig()
MODELS_PRESENT = CFG.detector_model.is_file() and CFG.embedding_model.is_file()


@unittest.skipUnless(MODELS_PRESENT, "file model ONNX belum dipasang (lihat README: Memasang model AI)")
class RealModelTests(SimpleTestCase):
    def test_detector_on_blank_image_finds_no_face(self):
        from ai_worker.detector import FaceDetector

        det = FaceDetector(CFG.detector_model)
        self.assertEqual(det.detect(np.zeros((480, 640, 3), np.uint8)), [])

    def test_onnxruntime_matches_opencv_backend(self):
        img = (np.random.RandomState(0).rand(112, 112, 3) * 255).astype(np.uint8)
        a = FaceEmbedder(CFG.embedding_model, "onnxruntime").embed_aligned(img)
        b = FaceEmbedder(CFG.embedding_model, "opencv").embed_aligned(img)
        self.assertEqual(a.shape, (EMBEDDING_DIM,))
        self.assertAlmostEqual(float(np.linalg.norm(a)), 1.0, places=5)
        self.assertGreater(float(a @ b), 0.9999)

    def test_embedding_is_deterministic(self):
        # Gambar noise bukan wajah, sehingga hanya determinisme & perbedaan input yang diuji di sini;
        # akurasi pengenalan wajib divalidasi dengan peserta uji yang memberi persetujuan.
        emb = FaceEmbedder(CFG.embedding_model)
        rng = np.random.RandomState(1)
        img1 = (rng.rand(112, 112, 3) * 255).astype(np.uint8)
        img2 = (rng.rand(112, 112, 3) * 255).astype(np.uint8)
        e1, e1b, e2 = emb.embed_aligned(img1), emb.embed_aligned(img1), emb.embed_aligned(img2)
        self.assertAlmostEqual(float(e1 @ e1b), 1.0, places=5)
        self.assertLess(float(e1 @ e2), 0.999)
