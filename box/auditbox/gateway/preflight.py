"""Signed local-environment preflight for browser login admission."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from auditbox.env import (
    AUDITBOX_GUARDIAN_ADMISSION_TTL,
    AUDITBOX_GUARDIAN_CHALLENGE_TTL,
    AUDITBOX_GUARDIAN_MANAGEMENT_TRANSPORT,
    AUDITBOX_GUARDIAN_MANAGEMENT_GATEWAY,
    AUDITBOX_GUARDIAN_MANAGEMENT_NETWORK,
    AUDITBOX_GUARDIAN_MIN_VERSION,
    AUDITBOX_GUARDIAN_SECRET,
    AUDITBOX_GUARDIAN_USB_ADAPTER_KEYWORDS,
    AUDITBOX_GUARDIAN_USB_PRODUCT_ID,
    AUDITBOX_GUARDIAN_USB_VENDOR_ID,
    AUDITBOX_PUBLIC_URL,
)
from auditbox.branding import product_name

GUARDIAN_PROTOCOL_VERSION = 1

REQUIRED_CHECKS = frozenset(
    {
        "usb_link",
        "firewall",
        "external_access",
    }
)
LEGACY_REQUIRED_CHECKS = frozenset(
    {
        "usb_link",
        "management_network",
        "firewall",
        "public_connections",
        "public_probe",
    }
)
_SIGNING_SECRET = (AUDITBOX_GUARDIAN_SECRET or secrets.token_urlsafe(48)).encode("utf-8")
_MIN_TRUSTED_CLOCK = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
_MAX_TRUSTED_CLOCK = int(datetime(2100, 1, 1, tzinfo=timezone.utc).timestamp())


class PreflightError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Challenge:
    challenge_id: str
    nonce: str
    client_ip: str
    expires_at: int
    policy_digest: str


class ChallengeStore:
    def __init__(self) -> None:
        self._items: dict[str, Challenge] = {}
        self._lock = threading.Lock()

    def issue(self, client_ip: str, now: int | None = None) -> Challenge:
        current = int(time.time()) if now is None else now
        challenge = Challenge(
            challenge_id=secrets.token_urlsafe(24),
            nonce=secrets.token_urlsafe(32),
            client_ip=client_ip,
            expires_at=current + AUDITBOX_GUARDIAN_CHALLENGE_TTL,
            policy_digest=_policy_digest(guardian_policy()),
        )
        with self._lock:
            self._cleanup_locked(current)
            self._items[challenge.challenge_id] = challenge
        return challenge

    def consume(self, challenge_id: str, client_ip: str, now: int | None = None) -> Challenge:
        current = int(time.time()) if now is None else now
        with self._lock:
            self._cleanup_locked(current)
            challenge = self._items.pop(challenge_id, None)
        if challenge is None:
            raise PreflightError("检查挑战已失效，请重新检测")
        if challenge.client_ip != client_ip:
            raise PreflightError("检查结果与当前终端不匹配")
        return challenge

    def _cleanup_locked(self, now: int) -> None:
        for challenge_id in [
            key for key, item in self._items.items() if item.expires_at <= now
        ]:
            self._items.pop(challenge_id, None)


def guardian_policy() -> dict[str, Any]:
    return {
        "management_transport": AUDITBOX_GUARDIAN_MANAGEMENT_TRANSPORT,
        "management_network": AUDITBOX_GUARDIAN_MANAGEMENT_NETWORK,
        "management_gateway": AUDITBOX_GUARDIAN_MANAGEMENT_GATEWAY,
        "box_url": AUDITBOX_PUBLIC_URL,
        "usb_vendor_id": AUDITBOX_GUARDIAN_USB_VENDOR_ID,
        "usb_product_id": AUDITBOX_GUARDIAN_USB_PRODUCT_ID,
        "usb_adapter_keywords": AUDITBOX_GUARDIAN_USB_ADAPTER_KEYWORDS,
        "require_firewall": True,
        # Compatibility fields keep 0.2.0 policy digests aligned while
        # disabling the three checks retired by 0.2.1.
        "block_proxy": False,
        "block_vpn": False,
        "block_extra_default_routes": False,
        "block_public_connections": True,
        "probe_urls": [
            {
                "url": "http://www.msftconnecttest.com/connecttest.txt",
                "expect": "Microsoft Connect Test",
            }
        ],
        "vpn_keywords": [],
    }


def verify_report(report: dict[str, Any], challenge: Challenge, now: int | None = None) -> str:
    if report.get("challenge_id") != challenge.challenge_id or report.get("nonce") != challenge.nonce:
        raise PreflightError("检查结果的随机挑战不匹配")
    require_guardian_version(report.get("guardian_version"))
    if not hmac.compare_digest(str(report.get("policy_digest") or ""), challenge.policy_digest):
        raise PreflightError(f"检查策略与{product_name()}要求不一致")
    if report.get("platform") != "windows" or report.get("overall") != "pass":
        raise PreflightError("本机网络环境检查未通过")

    # The box and the Windows workstation do not share a reliable wall clock:
    # after a power loss the box can boot with a stale system time. Freshness is
    # already enforced by the short-lived, single-use, client-IP-bound challenge
    # consumed by the gateway immediately before this function is called. Keep
    # validating the signed timestamp's format, but do not compare independent
    # machine clocks and reject a freshly generated report because of clock skew.
    _parse_timestamp(report.get("checked_at"))

    checks = report.get("checks")
    if not isinstance(checks, list):
        raise PreflightError("检查结果格式不完整")
    passed_ids = {
        str(item.get("id"))
        for item in checks
        if isinstance(item, dict)
        and item.get("status") == "pass"
        and item.get("required", True) is True
    }
    if not (
        REQUIRED_CHECKS.issubset(passed_ids)
        or LEGACY_REQUIRED_CHECKS.issubset(passed_ids)
    ):
        raise PreflightError("必要的网络环境检查未全部通过")

    public_key_text = _required_text(report, "public_key", 128)
    device_id = _required_text(report, "device_id", 64)
    signature_text = _required_text(report, "signature", 256)
    try:
        public_key_bytes = _base64url_decode(public_key_text)
        signature = _base64url_decode(signature_text)
        expected_device_id = hashlib.sha256(public_key_bytes).hexdigest()[:32]
        if not hmac.compare_digest(device_id, expected_device_id):
            raise PreflightError("卫士设备身份无效")
        payload = {key: value for key, value in report.items() if key != "signature"}
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature,
            _canonical_json(payload),
        )
    except PreflightError:
        raise
    except (InvalidSignature, ValueError, TypeError, binascii.Error) as exc:
        raise PreflightError("卫士检查报告签名无效") from exc
    return device_id


def require_guardian_version(value: Any) -> str:
    version = str(value or "").strip()
    if _semantic_version(version) < _semantic_version(AUDITBOX_GUARDIAN_MIN_VERSION):
        raise PreflightError(
            f"智维卫士版本过旧，请下载安装 {AUDITBOX_GUARDIAN_MIN_VERSION} 或更高版本"
        )
    return version


def _semantic_version(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$", str(value or "").strip())
    if not match:
        return (-1, -1, -1)
    return tuple(int(part) for part in match.groups())


def issue_admission_token(client_ip: str, device_id: str, now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else now
    payload = {
        "kind": "guardian_admission",
        "ip": client_ip,
        "device": device_id,
        "iat": issued_at,
        "exp": issued_at + AUDITBOX_GUARDIAN_ADMISSION_TTL,
    }
    encoded = _base64url_encode(_canonical_json(payload))
    signature = hmac.new(_SIGNING_SECRET, encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_base64url_encode(signature)}"


def validate_admission_token(token: str, client_ip: str, now: int | None = None) -> None:
    try:
        encoded, supplied_text = token.split(".", 1)
        supplied = _base64url_decode(supplied_text)
        expected = hmac.new(_SIGNING_SECRET, encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(supplied, expected):
            raise PreflightError("本机环境检查凭证无效")
        payload = json.loads(_base64url_decode(encoded).decode("utf-8"))
        current = int(time.time()) if now is None else now
        if (
            payload.get("kind") != "guardian_admission"
            or payload.get("ip") != client_ip
            or int(payload.get("exp", 0)) <= current
        ):
            raise PreflightError("本机环境检查凭证已失效")
    except PreflightError:
        raise
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise PreflightError("本机环境检查凭证无效") from exc


def synchronize_system_clock(
    checked_at: Any,
    *,
    now: int | None = None,
    setter: Any = None,
) -> bool:
    """Set the clock from an authenticated browser or a verified Guardian report."""
    timestamp = _parse_timestamp(checked_at)
    if not _MIN_TRUSTED_CLOCK <= timestamp <= _MAX_TRUSTED_CLOCK:
        raise PreflightError("客户端时间超出允许范围")

    current = int(time.time()) if now is None else now
    if abs(current - timestamp) <= 2:
        return False

    try:
        if setter is not None:
            setter(timestamp)
        else:
            time.clock_settime(time.CLOCK_REALTIME, timestamp)
    except (AttributeError, OSError, PermissionError) as exc:
        raise PreflightError("盒子时间校准失败") from exc
    return True


def _parse_timestamp(value: Any) -> int:
    try:
        text = str(value)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (TypeError, ValueError, OverflowError) as exc:
        raise PreflightError("检查时间无效") from exc


def _required_text(value: dict[str, Any], key: str, maximum: int) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item or len(item) > maximum:
        raise PreflightError("检查结果格式不完整")
    return item


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _policy_digest(policy: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(policy)).hexdigest()


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    decoded = base64.b64decode(
        value + "=" * (-len(value) % 4),
        altchars=b"-_",
        validate=True,
    )
    if _base64url_encode(decoded) != value:
        raise ValueError("non-canonical base64url")
    return decoded
