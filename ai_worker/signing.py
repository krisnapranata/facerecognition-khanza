"""Autentikasi antarlayanan worker <-> Django berbasis HMAC-SHA256.

Setiap permintaan membawa timestamp, nonce acak, dan tanda tangan atas
metode, path, serta hash body. Server menolak timestamp kedaluwarsa dan
nonce yang pernah dipakai (perlindungan pengiriman ulang).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

HEADER_TIMESTAMP = "X-Face-Timestamp"
HEADER_NONCE = "X-Face-Nonce"
HEADER_SIGNATURE = "X-Face-Signature"
MAX_CLOCK_SKEW_SECONDS = 30


def canonical_message(timestamp: str, nonce: str, method: str, path: str, body: bytes) -> bytes:
    body_hash = hashlib.sha256(body or b"").hexdigest()
    return "\n".join([timestamp, nonce, method.upper(), path, body_hash]).encode()


def sign(secret: str, timestamp: str, nonce: str, method: str, path: str, body: bytes) -> str:
    if not secret:
        raise ValueError("FACE_WORKER_SECRET belum dikonfigurasi")
    message = canonical_message(timestamp, nonce, method, path, body)
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def signed_headers(secret: str, method: str, path: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    return {
        HEADER_TIMESTAMP: timestamp,
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: sign(secret, timestamp, nonce, method, path, body),
    }


def verify(secret: str, timestamp: str, nonce: str, signature: str, method: str, path: str,
           body: bytes, now: float | None = None) -> tuple[bool, str]:
    """Kembalikan (valid, kode_error). Pemeriksaan nonce dilakukan pemanggil."""
    if not secret:
        return False, "SERVICE_AUTH_NOT_CONFIGURED"
    if not (timestamp and nonce and signature):
        return False, "SERVICE_AUTH_MISSING"
    try:
        ts = int(timestamp)
    except ValueError:
        return False, "SERVICE_AUTH_INVALID"
    now = time.time() if now is None else now
    if abs(now - ts) > MAX_CLOCK_SKEW_SECONDS:
        return False, "SERVICE_AUTH_EXPIRED"
    if len(nonce) < 16 or len(nonce) > 64:
        return False, "SERVICE_AUTH_INVALID"
    expected = sign(secret, timestamp, nonce, method, path, body)
    if not hmac.compare_digest(expected, signature):
        return False, "SERVICE_AUTH_INVALID"
    return True, ""


def stream_token(secret: str, show_identity: bool = False) -> str:
    """Token turunan untuk akses HTTP lokal worker (stream/snapshot).

    ``show_identity`` mengikat token ke satu izin pemohon sehingga worker hanya
    menggambar nama/No. RM pada koneksi yang memang diotorisasi untuk itu.
    """
    scope = b"worker-local-http:identity" if show_identity else b"worker-local-http"
    return hmac.new(secret.encode(), scope, hashlib.sha256).hexdigest()
