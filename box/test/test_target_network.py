import ipaddress
import json
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest

from auditbox.network import client, helper


@pytest.fixture
def unavailable_helper(monkeypatch):
    connection = MagicMock()
    connection.connect.side_effect = FileNotFoundError("helper socket missing")
    socket_factory = MagicMock()
    socket_factory.return_value.__enter__.return_value = connection
    monkeypatch.setattr(client, "AUDITBOX_TARGET_PREPARE_SOCKET", "/run/test-helper.sock")
    monkeypatch.setattr(client.socket, "socket", socket_factory)
    return connection


def test_prepare_entrypoint_reuses_existing_route(unavailable_helper, monkeypatch):
    fallback = MagicMock(return_value={"ok": True, "action": "existing-route", "degraded": True})
    monkeypatch.setattr(client, "_existing_target_route", fallback)

    assert client.prepare_target_network("192.168.88.1")["degraded"] is True
    fallback.assert_called_once_with(ipaddress.ip_address("192.168.88.1"))


def test_prepare_without_route_reports_helper_error(unavailable_helper, monkeypatch):
    monkeypatch.setattr(client, "_existing_target_route", lambda _target: None)

    with pytest.raises(client.NetworkPreparationError, match="没有可复用"):
        client.prepare_target_network("192.168.88.1")


@pytest.mark.parametrize("action", ["get", "set"])
@pytest.mark.parametrize("timeout", [False, True])
def test_settings_never_fall_back_to_a_target_route(
    unavailable_helper, monkeypatch, action, timeout,
):
    fallback = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(client, "_existing_target_route", fallback)
    if timeout:
        unavailable_helper.connect.side_effect = None
        unavailable_helper.recv.side_effect = TimeoutError("helper response timed out")

    with pytest.raises(client.NetworkPreparationError, match="无法确认当前配置"):
        if action == "get":
            client.get_ethernet_settings()
        else:
            client.set_ethernet_settings("static", "192.168.88.2", "255.255.255.0")
    fallback.assert_not_called()


def test_helper_rejection_is_not_hidden_by_route_fallback(unavailable_helper, monkeypatch):
    unavailable_helper.connect.side_effect = None
    unavailable_helper.recv.return_value = b'{"ok": false, "error": "rejected"}\n'
    fallback = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(client, "_existing_target_route", fallback)

    with pytest.raises(client.NetworkPreparationError, match="rejected"):
        client.prepare_target_network("192.168.88.1")
    fallback.assert_not_called()


def test_private_target_validation() -> None:
    assert helper._validate_target("192.168.88.1") == ipaddress.ip_address("192.168.88.1")


@pytest.mark.parametrize(
    "target",
    ["192.168.77.1", "8.8.8.8", "192.168.1.0", "192.168.1.255"],
)
def test_unsafe_targets_are_rejected(target: str) -> None:
    with pytest.raises(helper.PreparationError):
        helper._validate_target(target)


def test_target_matching_old_alias_is_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(helper, "_carrier_state", lambda _interface: "1")
    monkeypatch.setattr(
        helper,
        "_interface_addresses",
        lambda _interface: [ipaddress.ip_interface("192.168.88.2/24")],
    )

    def fake_run(command: list[str], *, check: bool = True) -> CompletedProcess[str]:
        commands.append(command)
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(helper, "_run", fake_run)

    result = helper.prepare_target("192.168.88.2")

    assert ["ip", "addr", "del", "192.168.88.2/24", "dev", "eth0"] in commands
    assert result["source"] == "192.168.88.254"
    assert result["action"] == "added"


def test_existing_target_route_can_be_reused_when_helper_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "AUDITBOX_TARGET_INTERFACE", "eth-target")
    monkeypatch.setattr(
        client.subprocess,
        "run",
        lambda *_args, **_kwargs: CompletedProcess(
            [],
            0,
            json.dumps([{"dev": "eth-target", "prefsrc": "192.168.88.2"}]),
            "",
        ),
    )

    result = client._existing_target_route(ipaddress.ip_address("192.168.88.1"))

    assert result == {
        "ok": True,
        "target": "192.168.88.1",
        "network": "192.168.88.0/24",
        "source": "192.168.88.2",
        "action": "existing-route",
        "degraded": True,
    }


def test_route_on_wrong_interface_is_not_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client, "AUDITBOX_TARGET_INTERFACE", "eth-target")
    monkeypatch.setattr(
        client.subprocess,
        "run",
        lambda *_args, **_kwargs: CompletedProcess(
            [], 0, json.dumps([{"dev": "br-usb", "prefsrc": "192.168.88.2"}]), ""
        ),
    )

    assert client._existing_target_route(ipaddress.ip_address("192.168.88.1")) is None


@pytest.mark.parametrize("target", ["8.8.8.8", "192.168.77.10", "192.168.88.255"])
def test_unsafe_existing_route_is_not_reused(
    target: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "AUDITBOX_TARGET_INTERFACE", "eth-target")
    monkeypatch.setattr(client, "AUDITBOX_MANAGEMENT_NETWORKS", ("192.168.77.0/24",))
    monkeypatch.setattr(
        client.subprocess,
        "run",
        lambda *_args, **_kwargs: CompletedProcess(
            [], 0, json.dumps([{"dev": "eth-target", "prefsrc": target}]), ""
        ),
    )

    assert client._existing_target_route(ipaddress.ip_address(target)) is None
