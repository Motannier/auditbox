from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from starlette.requests import Request

from auditbox.gateway import app as gateway_app
from auditbox.gateway.app import LoginRequest
from auditbox.gateway.preflight import (
    ChallengeStore,
    PreflightError,
    _canonical_json,
    guardian_policy,
    issue_admission_token,
    synchronize_system_clock,
    validate_admission_token,
    verify_report,
)
from auditbox.gateway.auth import Principal


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _signed_report(
    challenge,
    now: int = 1_000,
    ids: tuple[str, ...] | None = None,
    guardian_version: str = "0.2.1",
) -> dict:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    ids = ids or ("usb_link", "firewall", "external_access")
    payload = {
        "challenge_id": challenge.challenge_id,
        "nonce": challenge.nonce,
        "device_id": hashlib.sha256(public_key).hexdigest()[:32],
        "public_key": _b64(public_key),
        "checked_at": datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z"),
        "overall": "pass",
        "checks": [
            {
                "id": check_id,
                "label": check_id,
                "status": "pass",
                "message": "ok",
                "required": True,
                "details": {},
            }
            for check_id in ids
        ],
        "guardian_version": guardian_version,
        "platform": "windows",
        "policy_digest": hashlib.sha256(_canonical_json(guardian_policy())).hexdigest(),
    }
    return {**payload, "signature": _b64(private_key.sign(_canonical_json(payload)))}


def test_signed_report_issues_ip_bound_admission() -> None:
    store = ChallengeStore()
    challenge = store.issue("192.168.77.28", now=950)
    consumed = store.consume(challenge.challenge_id, "192.168.77.28", now=960)
    report = _signed_report(consumed)
    device_id = verify_report(report, consumed, now=1_000)
    admission = issue_admission_token("192.168.77.28", device_id, now=1_000)
    validate_admission_token(admission, "192.168.77.28", now=1_001)

    with pytest.raises(PreflightError):
        validate_admission_token(admission, "192.168.77.29", now=1_001)
    with pytest.raises(PreflightError):
        validate_admission_token(admission, "192.168.77.28", now=100_000)


def test_outdated_guardian_reports_an_actionable_version_error() -> None:
    challenge = ChallengeStore().issue("192.168.77.28", now=950)
    report = _signed_report(challenge, guardian_version="0.1.0")

    with pytest.raises(PreflightError, match="版本过旧"):
        verify_report(report, challenge, now=1_000)


def test_installer_metadata_requires_matching_manifest(monkeypatch, tmp_path) -> None:
    installer = tmp_path / "AuditBoxGuardian-Setup.exe"
    manifest = tmp_path / "AuditBoxGuardian-Setup.json"
    installer.write_bytes(b"validated-installer")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    manifest.write_text(
        json.dumps(
            {
                "version": "0.2.1",
                "filename": installer.name,
                "sha256": digest,
                "size": installer.stat().st_size,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gateway_app, "AUDITBOX_GUARDIAN_DOWNLOAD_PATH", installer)
    monkeypatch.setattr(gateway_app, "AUDITBOX_GUARDIAN_MANIFEST_PATH", manifest)

    assert gateway_app._guardian_installer_metadata()["verified"] is True

    manifest.write_text(
        json.dumps(
            {
                "version": "0.2.1",
                "filename": "another.exe",
                "sha256": digest,
                "size": installer.stat().st_size,
            }
        ),
        encoding="utf-8",
    )
    assert gateway_app._guardian_installer_metadata()["verified"] is False


def test_legacy_guardian_report_for_equivalent_three_categories_is_accepted() -> None:
    challenge = ChallengeStore().issue("192.168.77.28", now=950)
    legacy_ids = (
        "usb_link",
        "management_network",
        "default_routes",
        "vpn",
        "proxy",
        "firewall",
        "public_connections",
        "public_probe",
    )

    report = _signed_report(challenge, ids=legacy_ids)

    assert verify_report(report, challenge, now=1_000) == report["device_id"]


def test_fresh_challenge_accepts_signed_report_when_machine_clocks_differ() -> None:
    store = ChallengeStore()
    challenge = store.issue("192.168.77.28", now=950)
    consumed = store.consume(challenge.challenge_id, "192.168.77.28", now=960)

    report = _signed_report(consumed, now=100_000)

    assert verify_report(report, consumed, now=1_000) == report["device_id"]


def test_expired_challenge_is_rejected_before_report_verification() -> None:
    store = ChallengeStore()
    challenge = store.issue("192.168.77.28", now=100)

    with pytest.raises(PreflightError, match="检查挑战已失效"):
        store.consume(challenge.challenge_id, "192.168.77.28", now=10_000)


def test_verified_guardian_time_calibrates_box_clock() -> None:
    expected = int(datetime(2026, 7, 16, tzinfo=timezone.utc).timestamp())
    changes: list[int] = []

    changed = synchronize_system_clock(
        datetime.fromtimestamp(expected, timezone.utc).isoformat(),
        now=1_000,
        setter=changes.append,
    )

    assert changed is True
    assert changes == [expected]


def test_clock_calibration_is_a_noop_within_two_seconds() -> None:
    expected = int(datetime(2026, 7, 16, tzinfo=timezone.utc).timestamp())
    changes: list[int] = []

    changed = synchronize_system_clock(
        datetime.fromtimestamp(expected, timezone.utc).isoformat(),
        now=expected + 2,
        setter=changes.append,
    )

    assert changed is False
    assert changes == []


def test_challenge_is_single_use_and_bound_to_client_ip() -> None:
    store = ChallengeStore()
    challenge = store.issue("192.168.77.28", now=100)
    with pytest.raises(PreflightError):
        store.consume(challenge.challenge_id, "192.168.77.29", now=101)
    with pytest.raises(PreflightError):
        store.consume(challenge.challenge_id, "192.168.77.28", now=102)


def test_tampered_or_incomplete_report_is_rejected() -> None:
    store = ChallengeStore()
    challenge = store.issue("192.168.77.28", now=950)
    report = _signed_report(challenge)
    report["checks"][0]["status"] = "fail"
    with pytest.raises(PreflightError):
        verify_report(report, challenge, now=1_000)

    report = _signed_report(challenge)
    report["guardian_version"] = "tampered"
    with pytest.raises(PreflightError):
        verify_report(report, challenge, now=1_000)


def test_login_requires_valid_admission_when_guardian_is_enabled(monkeypatch) -> None:
    from auditbox.gateway import auth

    monkeypatch.setattr(auth, "AUDITBOX_ADMIN_PASSWORD", "test-only-login-password")
    monkeypatch.setattr(gateway_app, "AUDITBOX_GUARDIAN_REQUIRED", True)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": [],
            "client": ("192.168.77.28", 50000),
            "server": ("192.168.77.1", 80),
        }
    )
    with pytest.raises(HTTPException) as blocked:
        asyncio.run(
            gateway_app.login(
                LoginRequest(username="opsadmin", password="test-only-login-password"),
                request,
            )
        )
    assert blocked.value.status_code == 403

    admission = issue_admission_token("192.168.77.28", "guardian-device")
    response = asyncio.run(
        gateway_app.login(
            LoginRequest(
                username="opsadmin",
                password="test-only-login-password",
                admission_token=admission,
            ),
            request,
        )
    )
    assert response.username == "opsadmin"


@pytest.mark.parametrize("user_agent", ["Windows NT 10.0", "Macintosh", "Linux", "Android", "iPhone", "unknown", ""])
def test_guardian_disabled_login_only_requires_account_password(monkeypatch, user_agent) -> None:
    monkeypatch.setattr(gateway_app, "AUDITBOX_GUARDIAN_REQUIRED", False)
    monkeypatch.setattr(
        gateway_app, "authenticate_credentials",
        lambda username, password: Principal(username="opsadmin", role="admin")
        if (username, password) == ("opsadmin", "test-password") else None,
    )

    def unexpected_guardian_validation(*_args):
        pytest.fail("Guardian validation must not run when disabled")

    monkeypatch.setattr(gateway_app, "validate_admission_token", unexpected_guardian_validation)
    request = Request({
        "type": "http", "method": "POST", "path": "/api/auth/login",
        "headers": [(b"user-agent", user_agent.encode("ascii"))],
        "client": ("192.168.77.28", 50000),
    })
    assert asyncio.run(gateway_app.security_preflight())["required"] is False
    response = asyncio.run(gateway_app.login(
        LoginRequest(username="opsadmin", password="test-password"), request,
    ))
    assert response.username == "opsadmin"
    assert response.token
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(gateway_app.login(
            LoginRequest(username="opsadmin", password="wrong-password"), request,
        ))
    assert rejected.value.status_code == 401


def test_authenticated_session_can_refresh_admission_without_rerunning_guardian(monkeypatch) -> None:
    monkeypatch.setattr(
        gateway_app,
        "require_authenticated",
        lambda _request: Principal(username="admin", role="admin"),
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/security/admission/refresh",
            "headers": [],
            "client": ("192.168.77.28", 50000),
            "server": ("192.168.77.1", 80),
        }
    )

    response = asyncio.run(gateway_app.refresh_security_admission(request))

    assert response["expires_in"] == gateway_app.AUDITBOX_GUARDIAN_ADMISSION_TTL
    validate_admission_token(response["admission_token"], "192.168.77.28")
