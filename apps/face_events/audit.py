"""Pencatatan audit. Hanya metadata; jangan pernah memasukkan password, embedding, atau foto."""
from __future__ import annotations

import logging

from .models import FaceAuditLog

logger = logging.getLogger(__name__)

SUCCESS = "SUCCESS"
DENIED = "DENIED"
FAILED = "FAILED"


def _actor(user_or_name) -> str:
    if user_or_name is None:
        return "system"
    if isinstance(user_or_name, str):
        return user_or_name[:150]
    if getattr(user_or_name, "is_authenticated", False):
        return user_or_name.get_username()[:150]
    return "anonymous"


def log(actor, action: str, outcome: str = SUCCESS, target_type: str = "", target_id="",
        detail: str = "", request=None) -> FaceAuditLog | None:
    ip = request.META.get("REMOTE_ADDR", "") if request is not None else ""
    try:
        return FaceAuditLog.objects.create(
            actor=_actor(actor), action=action[:64], outcome=outcome[:16],
            target_type=target_type[:64], target_id=str(target_id or "")[:64],
            detail=(detail or "")[:255], ip_address=ip[:45],
        )
    except Exception:  # audit tidak boleh menghentikan pelayanan, tetapi wajib tercatat di log
        logger.exception("Gagal menulis audit log untuk aksi %s", action)
        return None
