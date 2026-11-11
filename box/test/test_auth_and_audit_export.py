import csv
import io
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from auditbox.audit import logger as logger_module
from auditbox.audit.logger import AuditLogger
from auditbox.gateway.app import _iter_audit_csv
from auditbox.gateway import auth as auth_module
from auditbox.gateway.auth import (
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    Principal,
    authenticate_credentials,
    decode_access_token,
    issue_access_token,
    require_super_admin,
)


def _request_with_token(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/audit/export",
            "headers": [(b"authorization", f"Bearer {token}".encode("ascii"))],
        }
    )


def test_configured_accounts_have_distinct_roles(monkeypatch) -> None:
    monkeypatch.setattr(auth_module, "AUDITBOX_ADMIN_PASSWORD", "test-only-login-password")
    monkeypatch.setattr(auth_module, "AUDITBOX_SUPER_ADMIN_PASSWORD", "test-only-login-password")
    assert authenticate_credentials("opsadmin", "test-only-login-password") == Principal("opsadmin", ROLE_ADMIN)
    assert authenticate_credentials("superadmin", "test-only-login-password") == Principal(
        "superadmin", ROLE_SUPER_ADMIN
    )
    assert authenticate_credentials("superadmin", "wrong") is None


@pytest.mark.parametrize("username", ["opsadmin", "superadmin"])
def test_unconfigured_passwords_disable_login(monkeypatch, username) -> None:
    monkeypatch.setattr(auth_module, "AUDITBOX_ADMIN_PASSWORD", "")
    monkeypatch.setattr(auth_module, "AUDITBOX_SUPER_ADMIN_PASSWORD", "")
    assert authenticate_credentials(username, "") is None
    assert authenticate_credentials(username, "test-only-login-password") is None


def test_signed_token_rejects_tampering_and_expiry() -> None:
    principal = Principal("superadmin", ROLE_SUPER_ADMIN)
    token = issue_access_token(principal, now=1_000)
    assert decode_access_token(token, now=1_001) == principal

    with pytest.raises(HTTPException) as expired:
        decode_access_token(token, now=1_000_000)
    assert expired.value.status_code == 401

    with pytest.raises(HTTPException) as tampered:
        decode_access_token(token[:-1] + ("A" if token[-1] != "A" else "B"), now=1_001)
    assert tampered.value.status_code == 401


def test_only_super_admin_can_export() -> None:
    admin_token = issue_access_token(Principal("opsadmin", ROLE_ADMIN))
    with pytest.raises(HTTPException) as forbidden:
        require_super_admin(_request_with_token(admin_token))
    assert forbidden.value.status_code == 403

    super_token = issue_access_token(Principal("superadmin", ROLE_SUPER_ADMIN))
    assert require_super_admin(_request_with_token(super_token)).role == ROLE_SUPER_ADMIN


def test_audit_logger_records_account_on_every_event(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(logger_module, "AUDIT_DIR", str(tmp_path))
    audit = AuditLogger(
        session_id="session-account",
        target="192.168.1.1:22",
        protocol="ssh",
        account="superadmin",
    )
    audit.log_confirmation_result("sudo reboot", confirmed=False)
    audit.log_terminal_input(b"password", sensitive=True)
    audit.log_terminal_output(b"output\r\n")
    audit.log_operation("guide_command_submitted", {"actual_command": "tool --token secret"})
    audit.close()

    records = [
        json.loads(line)
        for line in (tmp_path / "session-account.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records
    assert {record["account"] for record in records} == {"superadmin"}
    assert next(record for record in records if record["event"] == "terminal_input")["data"] == "[敏感输入已隐藏]"
    operation = next(record for record in records if record["event"] == "guide_command_submitted")
    assert operation["details"]["actual_command"] == "tool --token [已隐藏]"


def test_csv_export_is_utf8_excel_friendly_and_formula_safe(tmp_path) -> None:
    records = [
        {
            "event": "session_start",
            "session_id": "new-session",
            "account": "superadmin",
            "target": "192.168.1.1:22",
            "protocol": "ssh",
            "ts": "2026-07-14T12:00:00Z",
        },
        {
            "event": "command",
            "account": "superadmin",
            "command": "=HYPERLINK(\"https://invalid.example\")",
            "ts": "2026-07-14T12:00:01Z",
        },
    ]
    path = tmp_path / "new-session.jsonl"
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    (tmp_path / "legacy-session.jsonl").write_text(
        json.dumps({"event": "command", "command": "ls", "ts": "old"}) + "\n",
        encoding="utf-8",
    )

    exported = "".join(_iter_audit_csv(tmp_path))
    assert exported.startswith("\ufeff账号,时间,会话 ID")
    rows = list(csv.reader(io.StringIO(exported.lstrip("\ufeff"))))
    assert any(row[0] == "系统管理员" and row[6].startswith("'=") for row in rows[1:])
    assert any(row[0] == "历史记录" and row[2] == "legacy-session" for row in rows[1:])
