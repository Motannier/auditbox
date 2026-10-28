import asyncio
import ipaddress
from subprocess import CompletedProcess

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from auditbox.gateway import app as gateway
from auditbox.gateway.auth import Principal
from auditbox.network import helper
from auditbox.network.client import NetworkPreparationError


@pytest.fixture
def nic(tmp_path, monkeypatch):
    runtime = tmp_path / "run" / "auditbox-network"
    runtime.mkdir(parents=True)
    settings_file = runtime / "ethernet.network"
    link = tmp_path / "05-auditbox-target-user.network"
    link.symlink_to(settings_file)
    monkeypatch.setattr(helper, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(helper, "SETTINGS_LINK", link)
    monkeypatch.setattr(helper, "AUDITBOX_TARGET_INTERFACE", "enP4p65s0")
    monkeypatch.setattr(helper, "AUDITBOX_MANAGEMENT_NETWORKS", ["192.168.77.0/24"])
    factory = ipaddress.IPv4Interface("169.254.200.6/24")
    state = {"addresses": [factory], "carrier": "1", "conflict": False, "fail": 0, "commands": []}
    monkeypatch.setattr(helper, "_carrier_state", lambda _interface: state["carrier"])
    monkeypatch.setattr(helper, "_interface_addresses", lambda _interface: list(state["addresses"]))

    def run(command, *, check=True):
        state["commands"].append(command)
        if command[0] == "arping":
            return CompletedProcess(command, int(state["conflict"]), "", "")
        if command[:2] == ["networkctl", "reconfigure"]:
            if state["fail"]:
                state["fail"] -= 1
                raise helper.PreparationError("simulated reconfigure failure")
            state["addresses"] = [helper._read_static_address() or factory]
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "_run", run)
    return state


def apply(address="192.168.10.2", netmask="255.255.255.0"):
    return helper.configure_ethernet({"mode": "static", "ip_address": address, "netmask": netmask})


def test_default_settings_do_not_write_any_configuration(nic):
    result = helper.ethernet_settings()
    assert result["mode"] == "auto"
    assert result["ip_address"] == "169.254.200.6"
    assert result["default_address"] == "169.254.200.6/24"
    assert result["reset_on_reboot"] is True
    assert nic["commands"] == []
    assert not helper.SETTINGS_FILE.exists()


def test_manual_address_is_runtime_only_and_configures_only_rj45(nic):
    result = apply("172.18.9.3", "255.255.254.0")
    assert result["addresses"] == ["172.18.9.3/23"]
    assert result["mode"] == "static"
    content = helper.SETTINGS_FILE.read_text()
    assert "Address=172.18.9.3/23" in content
    assert "Name=enP4p65s0" in content
    assert "Gateway=" not in content
    assert "DNS=" not in content
    assert "br-usb" not in content
    assert [command for command in nic["commands"] if command[0] == "networkctl"] == [
        ["networkctl", "reload"], ["networkctl", "reconfigure", "enP4p65s0"],
    ]
    assert not any(command[0] == "systemctl" for command in nic["commands"])


def test_settings_can_be_applied_before_cable_is_connected(nic):
    nic["carrier"] = "0"
    result = apply()
    assert result["mode"] == "static"
    assert result["link_up"] is False
    assert result["conflict_checked"] is False
    assert not any(command[0] == "arping" for command in nic["commands"])


@pytest.mark.parametrize("address, mask", [
    ("192.168.77.2", "255.255.255.0"),
    ("192.168.10.2", "255.255.0.0"),
    ("192.168.10.0", "255.255.255.0"),
    ("192.168.10.255", "255.255.255.0"),
    ("192.168.10.2", "255.0.255.0"),
    ("192.168.10.2", "0.0.0.255"),
    ("192.168.10.2", "255.255.255.255"),
    ("192.168.10.2", "0.0.0.0"),
    ("127.0.0.2", "255.255.255.0"),
    ("0.1.2.3", "255.255.255.0"),
    ("224.1.2.3", "255.255.255.0"),
    ("::1", "255.255.255.0"),
    ("192.168.10.2; reboot", "255.255.255.0"),
])
def test_invalid_or_management_overlapping_addresses_are_rejected_without_writes(nic, address, mask):
    with pytest.raises(helper.PreparationError):
        apply(address, mask)
    assert nic["commands"] == []
    assert not helper.SETTINGS_FILE.exists()


def test_occupied_ip_is_rejected_without_changing_settings(nic):
    nic["conflict"] = True
    with pytest.raises(helper.PreparationError, match="占用"):
        apply()
    assert not helper.SETTINGS_FILE.exists()
    assert not any(command[0] == "networkctl" for command in nic["commands"])


def test_failed_application_restores_previous_configuration(nic):
    apply("192.168.10.2")
    original = helper.SETTINGS_FILE.read_bytes()
    nic["fail"] = 1
    with pytest.raises(helper.PreparationError, match="已恢复原配置"):
        apply("192.168.20.2")
    assert helper.SETTINGS_FILE.read_bytes() == original
    assert helper.ethernet_settings()["ip_address"] == "192.168.10.2"


def test_failed_first_application_restores_factory_configuration(nic):
    nic["fail"] = 1
    with pytest.raises(helper.PreparationError, match="已恢复原配置"):
        apply()
    assert helper.ethernet_settings()["ip_address"] == "169.254.200.6"
    assert helper.ethernet_settings()["mode"] == "auto"


def test_manual_mode_honors_mask_and_never_changes_user_address(nic):
    apply("172.18.9.3", "255.255.254.0")
    nic["commands"].clear()
    result = helper.prepare_target("172.18.8.255")
    assert result["source"] == "172.18.9.3"
    assert result["network"] == "172.18.8.0/23"
    assert result["action"] == "manual"
    for target in ("172.18.9.3", "172.18.9.255", "172.18.10.1", "192.168.77.2"):
        with pytest.raises(helper.PreparationError):
            helper.prepare_target(target)
    assert nic["commands"] == []


def test_switching_to_automatic_restores_factory_network(nic):
    apply()
    result = helper.configure_ethernet({"mode": "auto"})
    assert result["mode"] == "auto"
    assert result["addresses"] == ["169.254.200.6/24"]
    assert helper.SETTINGS_FILE.read_bytes() == b""


def test_same_owned_ip_does_not_claim_an_arp_conflict_check(nic):
    result = apply("169.254.200.6")
    assert result["conflict_checked"] is False
    assert not any(command[0] == "arping" for command in nic["commands"])


def test_failed_reset_keeps_previous_manual_configuration(nic):
    apply()
    nic["fail"] = 1
    with pytest.raises(helper.PreparationError, match="已恢复原配置"):
        helper.configure_ethernet({"mode": "auto"})
    result = helper.ethernet_settings()
    assert result["mode"] == "static"
    assert result["addresses"] == ["192.168.10.2/24"]


def test_reboot_clears_temporary_mode_and_returns_to_factory(nic):
    apply()
    assert helper._read_static_address() == ipaddress.IPv4Interface("192.168.10.2/24")
    # Model reboot: /run disappears, kernel addresses are rebuilt from the base image.
    helper.SETTINGS_FILE.unlink()
    nic["addresses"] = [ipaddress.IPv4Interface("169.254.200.6/24")]
    result = helper.ethernet_settings()
    assert result["mode"] == "auto"
    assert result["addresses"] == [result["default_address"]]


def test_usb_interface_cannot_be_selected(nic, monkeypatch):
    monkeypatch.setattr(helper, "AUDITBOX_TARGET_INTERFACE", "br-usb")
    with pytest.raises(helper.PreparationError, match="USB"):
        apply()
    assert nic["commands"] == []


def test_unknown_helper_action_is_rejected(nic):
    with pytest.raises(helper.PreparationError):
        helper.handle_request({"action": "reboot"})
    assert nic["commands"] == []


def request():
    return Request({"type": "http", "method": "PUT", "path": "/api/settings/ethernet", "headers": []})


def test_network_settings_require_authentication(monkeypatch):
    def forbidden(*_args):
        pytest.fail("Unauthenticated requests must not reach the network helper")
    monkeypatch.setattr(gateway, "get_ethernet_settings", forbidden)
    monkeypatch.setattr(gateway, "set_ethernet_settings", forbidden)
    with pytest.raises(HTTPException) as read_error:
        asyncio.run(gateway.read_ethernet_settings(request()))
    with pytest.raises(HTTPException) as write_error:
        asyncio.run(gateway.update_ethernet_settings(gateway.EthernetSettingsRequest(mode="auto"), request()))
    assert read_error.value.status_code == write_error.value.status_code == 401


def test_authenticated_settings_use_the_scoped_helper(monkeypatch):
    monkeypatch.setattr(gateway, "AUDITBOX_NETWORK_SETTINGS_ENABLED", True)
    monkeypatch.setattr(gateway, "require_authenticated", lambda _request: Principal("opsadmin", "admin"))
    calls = []
    def save(mode, address, netmask):
        calls.append((mode, address, netmask))
        return {"mode": mode, "ip_address": address, "netmask": netmask}
    monkeypatch.setattr(gateway, "set_ethernet_settings", save)
    payload = gateway.EthernetSettingsRequest(mode="static", ip_address="192.168.10.2", netmask="255.255.255.0")
    result = asyncio.run(gateway.update_ethernet_settings(payload, request()))
    assert result["ip_address"] == "192.168.10.2"
    assert calls == [("static", "192.168.10.2", "255.255.255.0")]


def test_helper_failure_is_reported_to_the_user(monkeypatch):
    monkeypatch.setattr(gateway, "AUDITBOX_NETWORK_SETTINGS_ENABLED", True)
    monkeypatch.setattr(gateway, "require_authenticated", lambda _request: Principal("opsadmin", "admin"))
    def unavailable(*_args):
        raise NetworkPreparationError("test unavailable")
    monkeypatch.setattr(gateway, "get_ethernet_settings", unavailable)
    monkeypatch.setattr(gateway, "set_ethernet_settings", unavailable)
    with pytest.raises(HTTPException) as read_error:
        asyncio.run(gateway.read_ethernet_settings(request()))
    with pytest.raises(HTTPException) as write_error:
        asyncio.run(gateway.update_ethernet_settings(gateway.EthernetSettingsRequest(mode="auto"), request()))
    assert read_error.value.status_code == 503
    assert write_error.value.status_code == 409


def test_request_cannot_override_interface_or_inject_commands():
    with pytest.raises(ValidationError):
        gateway.EthernetSettingsRequest(mode="auto", interface="br-usb")
