import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from auditbox.gateway import app as gateway
from auditbox.gateway.auth import Principal
from auditbox.inference import routing, server


def test_linux_only_warmup_never_contacts_h3c(monkeypatch):
    monkeypatch.setattr(routing, "AUDITBOX_INFERENCE_PROFILES", ["linux"])
    calls = []
    monkeypatch.setattr(server, "_warm_backend", lambda name, url, payload: calls.append((name, url, payload)))
    server.warm_configured_backends()
    assert len(calls) == 2
    assert all("h3c" not in name and ":8083" not in url for name, url, _ in calls)
    assert routing.enabled_inference_profiles() == ("linux",)
    with pytest.raises(ValueError, match="disabled"):
        server._route("/review/v1/chat/completions", "h3c_comware")


def test_default_board_still_warms_both_profiles(monkeypatch):
    monkeypatch.setattr(routing, "AUDITBOX_INFERENCE_PROFILES", ["linux", "h3c_comware"])
    calls = []
    monkeypatch.setattr(server, "_warm_backend", lambda name, url, payload: calls.append(name))
    server.warm_configured_backends()
    assert calls == ["linux assistant", "h3c_comware assistant", "linux review", "h3c_comware review"]


def test_bad_model_profile_is_not_silently_accepted(monkeypatch):
    monkeypatch.setattr(routing, "AUDITBOX_INFERENCE_PROFILES", ["linux", "invalid"])
    with pytest.raises(ValueError, match="unsupported"):
        routing.enabled_inference_profiles()


def test_server_browser_time_never_changes_host_clock(monkeypatch):
    monkeypatch.setattr(gateway, "AUDITBOX_BROWSER_CLOCK_SYNC_ENABLED", False)
    monkeypatch.setattr(gateway, "synchronize_system_clock", lambda _: pytest.fail("host clock changed"))
    result = gateway._synchronize_browser_clock(1789520000000)
    assert result["synchronized"] is False
    assert result["enabled"] is False
    assert "error" not in result


def test_server_network_read_and_write_never_call_helper(monkeypatch):
    monkeypatch.setattr(gateway, "AUDITBOX_NETWORK_SETTINGS_ENABLED", False)
    monkeypatch.setattr(gateway, "require_authenticated", lambda _: Principal("opsadmin", "admin"))
    monkeypatch.setattr(gateway, "get_ethernet_settings", lambda: pytest.fail("read helper called"))
    monkeypatch.setattr(gateway, "set_ethernet_settings", lambda *_: pytest.fail("host network changed"))
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    assert asyncio.run(gateway.read_ethernet_settings(request))["supported"] is False
    with pytest.raises(HTTPException) as exc:
        asyncio.run(gateway.update_ethernet_settings(gateway.EthernetSettingsRequest(mode="auto"), request))
    assert exc.value.status_code == 403
