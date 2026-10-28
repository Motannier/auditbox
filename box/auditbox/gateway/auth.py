"""Small signed-token authentication layer for the local gateway."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from auditbox.env import (
    AUDITBOX_ADMIN_PASSWORD,
    AUDITBOX_ADMIN_USERNAME,
    AUDITBOX_AUTH_SECRET,
    AUDITBOX_AUTH_TOKEN_TTL,
    AUDITBOX_SUPER_ADMIN_PASSWORD,
    AUDITBOX_SUPER_ADMIN_USERNAME,
)

ROLE_ADMIN = "admin"
ROLE_SUPER_ADMIN = "super_admin"

# When no persistent secret is configured, use a process-local random key. This
# keeps development usable without shipping a universal signing secret; a
# service restart simply requires the browser to log in again.
_SIGNING_SECRET = (AUDITBOX_AUTH_SECRET or secrets.token_urlsafe(48)).encode("utf-8")


@dataclass(frozen=True, slots=True)
class Principal:
    username: str
    role: str
    login_id: str = ""


def authenticate_credentials(username: str, password: str) -> Principal | None:
    supplied_username = username.strip().encode("utf-8")
    supplied_password = password.encode("utf-8")
    accounts = (
        (
            AUDITBOX_SUPER_ADMIN_USERNAME,
            AUDITBOX_SUPER_ADMIN_PASSWORD,
            ROLE_SUPER_ADMIN,
        ),
        (AUDITBOX_ADMIN_USERNAME, AUDITBOX_ADMIN_PASSWORD, ROLE_ADMIN),
    )
    for configured_username, configured_password, role in accounts:
        username_matches = hmac.compare_digest(
            supplied_username, configured_username.encode("utf-8")
        )
        password_matches = hmac.compare_digest(
            supplied_password, configured_password.encode("utf-8")
        )
        if configured_password and username_matches and password_matches:
            return Principal(username=configured_username, role=role)
    return None


def issue_access_token(principal: Principal, now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else now
    payload = {
        "sub": principal.username,
        "role": principal.role,
        "iat": issued_at,
        "exp": issued_at + AUDITBOX_AUTH_TOKEN_TTL,
        "login_id": principal.login_id,
    }
    encoded_payload = _base64url_encode(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    )
    signature = hmac.new(_SIGNING_SECRET, encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded_payload}.{_base64url_encode(signature)}"


def decode_access_token(token: str, now: int | None = None) -> Principal:
    unauthorized = HTTPException(status_code=401, detail="登录状态已失效，请重新登录")
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        supplied_signature = _base64url_decode(encoded_signature)
        if _base64url_encode(supplied_signature) != encoded_signature:
            raise unauthorized
        expected_signature = hmac.new(
            _SIGNING_SECRET, encoded_payload.encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise unauthorized
        payload = json.loads(_base64url_decode(encoded_payload).decode("utf-8"))
        username = str(payload["sub"])
        role = str(payload["role"])
        expires_at = int(payload["exp"])
    except HTTPException:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise unauthorized from exc

    current_time = int(time.time()) if now is None else now
    if expires_at <= current_time:
        raise unauthorized

    configured_roles = {
        AUDITBOX_ADMIN_USERNAME: ROLE_ADMIN,
        AUDITBOX_SUPER_ADMIN_USERNAME: ROLE_SUPER_ADMIN,
    }
    if configured_roles.get(username) != role:
        raise unauthorized
    return Principal(username=username, role=role, login_id=str(payload.get("login_id") or ""))


def require_authenticated(request: Request) -> Principal:
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="请先登录")
    return decode_access_token(token.strip())


def require_super_admin(request: Request) -> Principal:
    principal = require_authenticated(request)
    if principal.role != ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="仅超级管理员可以导出操作日志")
    return principal


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
