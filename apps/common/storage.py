"""Penyimpanan privat untuk foto biometrik.

- Direktori di luar static/media publik, izin 0700, file 0600.
- Nama file acak (UUID), tidak memuat NIK atau nomor rekam medis.
- Isi file selalu terenkripsi (Fernet), sehingga salinan cadangan tetap terlindungi.
"""
from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path

from django.conf import settings

from .crypto import decrypt_bytes, encrypt_bytes

REFERENCE_DIR = "references"
TEMP_DIR = "tmp"
_NAME_RE = re.compile(r"^[0-9a-f]{32}\.bin$")


def _root() -> Path:
    return Path(settings.MEDIA_PRIVATE_ROOT)


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _safe_path(relative: str) -> Path:
    parts = Path(relative).parts
    if len(parts) != 2 or parts[0] not in (REFERENCE_DIR, TEMP_DIR) or not _NAME_RE.match(parts[1]):
        raise ValueError("path penyimpanan privat tidak valid")
    return _root() / parts[0] / parts[1]


def save_encrypted(data: bytes, folder: str = REFERENCE_DIR) -> str:
    if folder not in (REFERENCE_DIR, TEMP_DIR):
        raise ValueError("folder tidak valid")
    directory = _ensure_dir(_ensure_dir(_root()) / folder)
    name = f"{uuid.uuid4().hex}.bin"
    path = directory / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(encrypt_bytes(data))
    return f"{folder}/{name}"


def read_decrypted(relative: str) -> bytes:
    return decrypt_bytes(_safe_path(relative).read_bytes())


def delete(relative: str) -> bool:
    if not relative:
        return False
    try:
        path = _safe_path(relative)
    except ValueError:
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def exists(relative: str) -> bool:
    try:
        return _safe_path(relative).is_file()
    except ValueError:
        return False


def purge_temp(max_age_seconds: int) -> int:
    directory = _root() / TEMP_DIR
    if not directory.is_dir():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in directory.iterdir():
        if path.is_file() and _NAME_RE.match(path.name) and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
