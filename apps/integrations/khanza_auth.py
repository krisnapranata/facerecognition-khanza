"""Login memakai akun tabel ``user``/``admin`` Khanza (pola fungsi/akses.java).

    select * from user where user.id_user=AES_ENCRYPT(?,'nur') and user.password=AES_ENCRYPT(?,'windi')

- Password Khanza tidak pernah disimpan; user Django lokal dibuat dengan password
  tidak terpakai (``set_unusable_password``) hanya untuk sesi, izin, dan audit.
- Hanya akun dengan ``user.registrasi='true'`` (atau tabel ``admin`` = Admin Utama)
  yang boleh login. Hak disinkronkan setiap login.
"""
from __future__ import annotations

import logging
import re

from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import Group, User

from apps.face_events import audit

from . import khanza_rw

logger = logging.getLogger(__name__)

USERNAME_PREFIX = "kz."
ID_RE = re.compile(r"^[A-Za-z0-9._@+\-]{1,60}$")
MARKER_GROUP = "khanza_user"
MANAGED_GROUPS = ("face_operator", "face_registrar")

SQL_ADMIN = "SELECT 1 FROM admin WHERE admin.usere=AES_ENCRYPT(%s,'nur') AND admin.passworde=AES_ENCRYPT(%s,'windi') LIMIT 1"
SQL_USER = ("SELECT user.registrasi FROM user WHERE user.id_user=AES_ENCRYPT(%s,'nur') "
            "AND user.password=AES_ENCRYPT(%s,'windi') LIMIT 1")
SQL_NAME = ("SELECT COALESCE((SELECT petugas.nama FROM petugas WHERE petugas.nip=%s LIMIT 1), "
            "(SELECT dokter.nm_dokter FROM dokter WHERE dokter.kd_dokter=%s LIMIT 1), '')")


def check_credentials(id_user: str, password: str) -> dict | None:
    """Kembalikan {'admin': bool, 'registrasi': bool, 'nama': str} atau None jika salah."""
    with khanza_rw.cursor() as cur:
        cur.execute(SQL_ADMIN, [id_user, password])
        is_admin = cur.fetchone() is not None
        registrasi = is_admin
        if not is_admin:
            cur.execute(SQL_USER, [id_user, password])
            row = cur.fetchone()
            if row is None:
                return None
            registrasi = str(row[0]) == "true"
        cur.execute(SQL_NAME, [id_user, id_user])
        nama = (cur.fetchone() or [""])[0] or ""
    return {"admin": is_admin, "registrasi": registrasi, "nama": str(nama)}


def is_khanza_user(user) -> bool:
    return user.is_authenticated and user.groups.filter(name=MARKER_GROUP).exists()


class KhanzaUserBackend(BaseBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password or not ID_RE.match(username):
            return None
        try:
            info = check_credentials(username, password)
        except Exception as exc:
            logger.error("Login Khanza gagal diperiksa: %s", type(exc).__name__)
            return None
        if info is None:
            return None
        if not info["registrasi"]:
            audit.log(username, "LOGIN_KHANZA", audit.DENIED, detail="tidak memiliki hak registrasi", request=request)
            return None
        user, created = User.objects.get_or_create(username=f"{USERNAME_PREFIX}{username}")
        if created:
            user.set_unusable_password()
        user.first_name = info["nama"][:150]
        user.is_active = True
        user.save()
        self._sync_groups(user, info)
        return user

    @staticmethod
    def _sync_groups(user, info):
        wanted = {MARKER_GROUP, "face_operator", "face_registrar"} if info["registrasi"] else {MARKER_GROUP}
        for name in (MARKER_GROUP, *MANAGED_GROUPS):
            group, _ = Group.objects.get_or_create(name=name)
            if name in wanted:
                user.groups.add(group)
            else:
                user.groups.remove(group)
        # Grup lain (mis. face_admin) dikelola manual oleh administrator lewat /admin/.

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
