"""Helper uji: data sintetis saja (tanpa foto wajah sungguhan), engine AI palsu, pengguna per peran."""
from __future__ import annotations

import io
import json
import zlib

import cv2
import numpy as np
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase

from ai_worker import signing
from ai_worker.detector import Detection
from ai_worker.embedding import EMBEDDING_DIM, MODEL_VERSION
from apps.common import crypto
from apps.face_registry import engine as engine_mod
from apps.face_registry.models import PatientFaceReference, ProcessingBasis
from apps.face_registry.services import reference_cache


def unit_vector(seed: int, dim: int = EMBEDDING_DIM) -> np.ndarray:
    v = np.random.RandomState(seed).randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def vector_with_similarity(base: np.ndarray, target: float, seed: int = 99) -> np.ndarray:
    """Buat vektor dengan cosine similarity tepat ``target`` terhadap ``base``."""
    rnd = np.random.RandomState(seed).randn(base.shape[0]).astype(np.float32)
    ortho = rnd - np.dot(rnd, base) * base
    ortho /= np.linalg.norm(ortho)
    v = target * base + np.sqrt(max(0.0, 1 - target ** 2)) * ortho
    return (v / np.linalg.norm(v)).astype(np.float32)


def synthetic_jpeg(width: int = 480, height: int = 480, seed: int = 0) -> bytes:
    """Gambar noise bertekstur (bukan wajah); deteksi dipalsukan oleh FakeDetector."""
    rng = np.random.RandomState(seed)
    img = (rng.rand(height, width, 3) * 120 + 60).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def fake_detection(x=140, y=120, size=200, score=0.95) -> Detection:
    cx = x + size / 2
    row = np.array([
        x, y, size, size,
        cx - size * 0.2, y + size * 0.38,  # mata kanan
        cx + size * 0.2, y + size * 0.38,  # mata kiri
        cx, y + size * 0.55,               # hidung
        cx - size * 0.15, y + size * 0.75,
        cx + size * 0.15, y + size * 0.75,
        score,
    ], dtype=np.float32)
    return Detection.from_row(row)


class FakeDetector:
    def __init__(self, detections=None):
        self.detections = [fake_detection()] if detections is None else detections
        self.version = "fake"

    def detect(self, frame):
        return list(self.detections)


class FakeEmbedder:
    version = MODEL_VERSION
    dim = EMBEDDING_DIM
    backend = "fake"

    def __init__(self, vector=None):
        self.vector = unit_vector(1) if vector is None else vector

    def align(self, frame, det):
        return np.zeros((112, 112, 3), dtype=np.uint8)

    def embed_aligned(self, aligned):
        return self.vector

    def embed(self, frame, det):
        return self.vector


def install_fake_engine(detections=None, vector=None):
    engine_mod.set_engine_for_tests(engine_mod.FaceEngine(FakeDetector(detections), FakeEmbedder(vector)))


def make_reference(rm="UJI-0001", name="Pasien Uji Satu", vector=None, active=True, **extra) -> PatientFaceReference:
    vec = unit_vector(zlib.crc32(rm.encode()) % 100000) if vector is None else vector
    ref = PatientFaceReference.objects.create(
        patient_name=name, no_rkm_medis=rm, reference_image="",
        embedding_ciphertext=crypto.encrypt_embedding(vec, MODEL_VERSION),
        model_version=MODEL_VERSION, is_active=active,
        processing_basis=ProcessingBasis.DEMO_SYNTHETIC, registered_by="tester", **extra,
    )
    reference_cache.clear()
    return ref


def signed_post(client, path: str, payload: dict, secret: str | None = None, headers: dict | None = None):
    body = json.dumps(payload).encode()
    hdrs = signing.signed_headers(secret or settings.FACE_WORKER_SECRET, "POST", path, body)
    if headers:
        hdrs.update(headers)
    return client.generic("POST", path, body, content_type="application/json",
                          **{f"HTTP_{k.upper().replace('-', '_')}": v for k, v in hdrs.items()})


class FaceTestCase(TestCase):
    databases = "__all__"

    @classmethod
    def setUpTestData(cls):
        call_command("face_setup_roles", stdout=io.StringIO())
        cls.admin = User.objects.create_user("admin_uji", password="pw-uji-12345")
        cls.admin.groups.add(Group.objects.get(name="face_admin"))
        cls.operator = User.objects.create_user("operator_uji", password="pw-uji-12345")
        cls.operator.groups.add(Group.objects.get(name="face_operator"))
        cls.nobody = User.objects.create_user("tanpa_izin", password="pw-uji-12345")

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        reference_cache.clear()
        engine_mod.set_engine_for_tests(None)

    def tearDown(self):
        engine_mod.set_engine_for_tests(None)
