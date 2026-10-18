"""Helper API JSON: format respons konsisten, autentikasi, otorisasi, rate limit.

Format respons:
    sukses : {"ok": true, "data": {...}}
    gagal  : {"ok": false, "error": {"code": "...", "message": "...", "fields": {...}}}
"""
from __future__ import annotations

import functools
import json
import time

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

from ai_worker import signing


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, fields: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.fields = fields or {}


def ok(data=None, status: int = 200) -> JsonResponse:
    return JsonResponse({"ok": True, "data": data if data is not None else {}}, status=status)


def fail(code: str, message: str, status: int = 400, fields: dict | None = None) -> JsonResponse:
    error = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    response = JsonResponse({"ok": False, "error": error}, status=status)
    response["Cache-Control"] = "no-store"
    return response


def parse_json(request) -> dict:
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiError("INVALID_JSON", "Body harus berupa JSON yang valid")
    if not isinstance(data, dict):
        raise ApiError("INVALID_JSON", "Body JSON harus berupa objek")
    return data


def api_view(methods: tuple[str, ...]):
    """Validasi metode HTTP dan ubah ApiError menjadi respons JSON konsisten."""

    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return fail("METHOD_NOT_ALLOWED", f"Metode {request.method} tidak diizinkan", 405)
            try:
                response = view(request, *args, **kwargs)
            except ApiError as exc:
                return fail(exc.code, exc.message, exc.status, exc.fields)
            response.setdefault("Cache-Control", "no-store")
            return response

        return wrapper

    return decorator


def require_user(request, *perms: str) -> None:
    user = request.user
    if not user.is_authenticated:
        raise ApiError("UNAUTHENTICATED", "Login diperlukan", 401)
    if perms and not user.has_perms(perms):
        raise ApiError("FORBIDDEN", "Anda tidak memiliki izin untuk tindakan ini", 403)


def client_ip(request) -> str:
    # Tidak mempercayai X-Forwarded-For tanpa reverse proxy tepercaya.
    return request.META.get("REMOTE_ADDR", "unknown")


def check_rate_limit(key: str, limit: int, window_seconds: int) -> None:
    bucket = int(time.time() // window_seconds)
    cache_key = f"rl:{key}:{bucket}"
    if cache.add(cache_key, 1, timeout=window_seconds + 1):
        count = 1
    else:
        try:
            count = cache.incr(cache_key)
        except ValueError:
            cache.set(cache_key, 1, timeout=window_seconds + 1)
            count = 1
    if count > limit:
        raise ApiError("RATE_LIMITED", "Terlalu banyak permintaan, coba lagi sebentar lagi", 429)


def rate_limit(scope: str, limit: int, window_seconds: int = 60):
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            ident = f"u{request.user.pk}" if getattr(request, "user", None) and request.user.is_authenticated \
                else f"ip{client_ip(request)}"
            check_rate_limit(f"{scope}:{ident}", limit, window_seconds)
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def verify_service_request(request) -> None:
    """Autentikasi AI worker (HMAC + timestamp + nonce sekali pakai)."""
    timestamp = request.headers.get(signing.HEADER_TIMESTAMP, "")
    nonce = request.headers.get(signing.HEADER_NONCE, "")
    signature = request.headers.get(signing.HEADER_SIGNATURE, "")
    valid, code = signing.verify(settings.FACE_WORKER_SECRET, timestamp, nonce, signature,
                                 request.method, request.path, request.body)
    if not valid:
        raise ApiError(code, "Autentikasi layanan gagal", 401)
    # cache.add atomik per backend: gagal jika nonce sudah pernah dipakai.
    if not cache.add(f"nonce:{nonce}", 1, timeout=signing.MAX_CLOCK_SKEW_SECONDS * 2 + 5):
        raise ApiError("SERVICE_AUTH_REPLAY", "Permintaan duplikat ditolak", 401)


def service_view(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        verify_service_request(request)
        check_rate_limit("service", 600, 60)
        return view(request, *args, **kwargs)

    return wrapper
