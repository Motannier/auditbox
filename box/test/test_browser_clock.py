import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from auditbox.gateway import app as gateway
from auditbox.gateway.auth import Principal
from auditbox.gateway.preflight import PreflightError


def request():
    return Request({"type": "http", "method": "POST", "path": "/api/system/clock/sync", "headers": []})


@pytest.mark.parametrize("value", [0, True, "1789520000000", 4102444800000, -1])
def test_reject_invalid_client_clock(value):
    with pytest.raises(ValidationError):
        gateway.ClockSyncRequest(client_time_ms=value)


def test_clock_endpoint_requires_authentication_before_changing_time(monkeypatch):
    monkeypatch.setattr(gateway, "_synchronize_browser_clock", lambda _: pytest.fail("unauthorized clock write"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(gateway.synchronize_browser_clock(gateway.ClockSyncRequest(client_time_ms=1789520000000), request()))
    assert exc.value.status_code == 401


def test_login_calibrates_only_after_credentials_and_before_token(monkeypatch):
    calls = []
    monkeypatch.setattr(gateway, "AUDITBOX_GUARDIAN_REQUIRED", False)
    monkeypatch.setattr(gateway, "authenticate_credentials", lambda *_: Principal("opsadmin", "admin"))
    monkeypatch.setattr(gateway, "_synchronize_browser_clock", lambda _: calls.append("clock") or {"synchronized": True})
    monkeypatch.setattr(gateway, "issue_access_token", lambda _: calls.append("token") or "new-token")
    payload = gateway.LoginRequest(username="opsadmin", password="test", client_time_ms=1789520000000)
    result = asyncio.run(gateway.login(payload, request()))
    assert calls == ["clock", "token"]
    assert result.token == "new-token"
    monkeypatch.setattr(gateway, "authenticate_credentials", lambda *_: None)
    calls.clear()
    with pytest.raises(HTTPException):
        asyncio.run(gateway.login(payload, request()))
    assert calls == []


@pytest.mark.parametrize("changed", [False, True])
def test_clock_change_reissues_token_without_reauthenticating_after_jump(monkeypatch, changed):
    calls = []
    monkeypatch.setattr(gateway, "require_authenticated", lambda _: calls.append("auth") or Principal("opsadmin", "admin"))
    monkeypatch.setattr(gateway, "_synchronize_browser_clock", lambda _: calls.append("clock") or {"synchronized": changed})
    monkeypatch.setattr(gateway, "issue_access_token", lambda _: calls.append("token") or "new-token")
    result = asyncio.run(gateway.synchronize_browser_clock(gateway.ClockSyncRequest(client_time_ms=1789520000000), request()))
    assert calls == (["auth", "clock", "token"] if changed else ["auth", "clock"])
    assert ("token" in result) is changed


def test_clock_sync_converts_utc_and_surfaces_missing_permission(monkeypatch):
    monkeypatch.setattr(gateway, "AUDITBOX_BROWSER_CLOCK_SYNC_ENABLED", True)
    values = []
    monkeypatch.setattr(gateway, "synchronize_system_clock", lambda v: values.append(v) or True)
    assert gateway._synchronize_browser_clock(1789520000000)["synchronized"]
    assert datetime.fromisoformat(values[0]).tzinfo == timezone.utc
    def denied(_):
        raise PreflightError("no CAP_SYS_TIME")
    monkeypatch.setattr(gateway, "synchronize_system_clock", denied)
    assert gateway._synchronize_browser_clock(1789520000000)["error"]
