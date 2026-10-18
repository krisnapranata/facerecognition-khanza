"""Enkripsi data biometrik (embedding dan foto) dengan Fernet (AES-128-CBC + HMAC-SHA256).

Kunci dibaca dari FACE_EMBEDDING_KEYS (dipisah koma). Kunci pertama dipakai
untuk enkripsi; kunci lain tetap dapat mendekripsi sehingga rotasi kunci
dapat dilakukan dengan ``manage.py face_rotate_keys``.
"""
from __future__ import annotations

import base64
import json
from functools import lru_cache

import numpy as np
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class DecryptionError(Exception):
    pass


@lru_cache(maxsize=4)
def _fernet_for(keys: tuple[str, ...]) -> MultiFernet:
    if not keys:
        raise ImproperlyConfigured("FACE_EMBEDDING_KEYS belum diatur")
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured("FACE_EMBEDDING_KEYS tidak valid (harus kunci Fernet base64)") from exc


def get_fernet() -> MultiFernet:
    return _fernet_for(tuple(settings.FACE_EMBEDDING_KEYS))


def encrypt_bytes(data: bytes) -> bytes:
    return get_fernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    try:
        return get_fernet().decrypt(token)
    except InvalidToken as exc:
        raise DecryptionError("data terenkripsi tidak dapat didekripsi (kunci salah/data rusak)") from exc


def rotate_token(token: bytes) -> bytes:
    return get_fernet().rotate(token)


def encrypt_embedding(vector: np.ndarray, model_version: str) -> str:
    vec = np.asarray(vector, dtype="<f4").reshape(-1)
    payload = json.dumps({
        "v": 1,
        "model": model_version,
        "dim": int(vec.shape[0]),
        "data": base64.b64encode(vec.tobytes()).decode("ascii"),
    }).encode()
    return encrypt_bytes(payload).decode("ascii")


def decrypt_embedding(ciphertext: str, expected_model: str | None = None) -> np.ndarray:
    payload = json.loads(decrypt_bytes(ciphertext.encode("ascii")))
    if expected_model is not None and payload.get("model") != expected_model:
        raise DecryptionError("versi model embedding tidak sesuai")
    vec = np.frombuffer(base64.b64decode(payload["data"]), dtype="<f4").astype(np.float32)
    if vec.shape[0] != int(payload["dim"]):
        raise DecryptionError("dimensi embedding tidak sesuai")
    return vec
