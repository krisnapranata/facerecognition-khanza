"""Klien API internal Django untuk AI worker (ditandatangani HMAC)."""
from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

import requests

from .signing import signed_headers

logger = logging.getLogger(__name__)

CANDIDATES_PATH = "/api/face/v1/candidates/"
HEARTBEAT_PATH = "/api/face/v1/worker/heartbeat/"


class DjangoClient:
    def __init__(self, base_url: str, secret: str, timeout: float = 5.0, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.timeout = timeout
        self.session = session or requests.Session()
        self._prefix = urlsplit(self.base_url).path.rstrip("/")

    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json"}
        headers.update(signed_headers(self.secret, "POST", self._prefix + path, body))
        resp = self.session.post(self.base_url + path, data=body, headers=headers, timeout=self.timeout)
        try:
            data = resp.json()
        except ValueError:
            data = {"ok": False, "error": {"code": "BAD_RESPONSE", "message": f"HTTP {resp.status_code}"}}
        if resp.status_code >= 400:
            logger.warning("API %s gagal: HTTP %s %s", path, resp.status_code, data.get("error"))
        return data

    def submit_candidate(self, camera_id: str, track_id: str, embedding, model_version: str,
                         quality: dict) -> dict:
        return self._post(CANDIDATES_PATH, {
            "camera_id": camera_id,
            "track_id": track_id,
            "model_version": model_version,
            "embedding": [round(float(v), 6) for v in embedding],
            "quality_status": quality.get("status", "GOOD"),
            "quality_score": quality.get("score"),
        })

    def heartbeat(self, payload: dict) -> dict:
        return self._post(HEARTBEAT_PATH, payload)
