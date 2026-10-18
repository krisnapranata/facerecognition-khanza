"""Enkripsi/dekripsi embedding & foto, penyimpanan privat."""
import os
import stat

import numpy as np
from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from ai_worker.embedding import MODEL_VERSION
from apps.common import crypto, storage

from .helpers import unit_vector


class CryptoTests(SimpleTestCase):
    def test_roundtrip(self):
        vec = unit_vector(3)
        ct = crypto.encrypt_embedding(vec, MODEL_VERSION)
        out = crypto.decrypt_embedding(ct, MODEL_VERSION)
        np.testing.assert_allclose(out, vec, rtol=0, atol=0)

    def test_ciphertext_does_not_contain_plaintext(self):
        vec = unit_vector(4)
        ct = crypto.encrypt_embedding(vec, MODEL_VERSION)
        self.assertNotIn(str(round(float(vec[0]), 4)), ct)
        self.assertNotIn(MODEL_VERSION, ct)
        # Enkripsi non-deterministik (IV acak)
        self.assertNotEqual(ct, crypto.encrypt_embedding(vec, MODEL_VERSION))

    def test_wrong_key_fails(self):
        ct = crypto.encrypt_embedding(unit_vector(5), MODEL_VERSION)
        crypto._fernet_for.cache_clear()
        with override_settings(FACE_EMBEDDING_KEYS=[Fernet.generate_key().decode()]):
            with self.assertRaises(crypto.DecryptionError):
                crypto.decrypt_embedding(ct)

    def test_model_version_bound(self):
        ct = crypto.encrypt_embedding(unit_vector(6), MODEL_VERSION)
        with self.assertRaises(crypto.DecryptionError):
            crypto.decrypt_embedding(ct, "model-lain")

    def test_key_rotation(self):
        old = Fernet.generate_key().decode()
        new = Fernet.generate_key().decode()
        with override_settings(FACE_EMBEDDING_KEYS=[old]):
            ct = crypto.encrypt_embedding(unit_vector(7), MODEL_VERSION)
        with override_settings(FACE_EMBEDDING_KEYS=[new, old]):
            rotated = crypto.rotate_token(ct.encode()).decode()
        with override_settings(FACE_EMBEDDING_KEYS=[new]):
            self.assertEqual(crypto.decrypt_embedding(rotated).shape[0], 128)


class StorageTests(SimpleTestCase):
    def test_encrypted_private_file(self):
        rel = storage.save_encrypted(b"\xff\xd8\xffdata-gambar", storage.REFERENCE_DIR)
        self.assertRegex(rel, r"^references/[0-9a-f]{32}\.bin$")
        from django.conf import settings

        path = settings.MEDIA_PRIVATE_ROOT / rel
        raw = path.read_bytes()
        self.assertNotIn(b"data-gambar", raw)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(storage.read_decrypted(rel), b"\xff\xd8\xffdata-gambar")
        self.assertTrue(storage.delete(rel))
        self.assertFalse(storage.exists(rel))

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            storage.read_decrypted("../../etc/passwd")
        self.assertFalse(storage.delete("references/../../x.bin"))
