import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from auditbox.gateway.app import DeviceWriteRequest, SessionCreateRequest, suggest_commands


def test_device_request_accepts_ipv4() -> None:
    payload = DeviceWriteRequest(
        address="192.168.88.1",
        port=22,
        protocol="ssh",
        device_type="server",
        device_profile="linux",
    )
    assert payload.address == "192.168.88.1"
    assert payload.device_type == "server"


@pytest.mark.parametrize("address", ["266.266.266.266", "192.168.1", "2001:db8::1", "server.local"])
def test_device_request_rejects_non_ipv4(address: str) -> None:
    with pytest.raises(ValidationError):
        DeviceWriteRequest(
            address=address,
            port=22,
            protocol="ssh",
            device_type="server",
            device_profile="linux",
        )


@pytest.mark.parametrize("device_type", [None, "router", "linux"])
def test_device_request_requires_supported_device_type(device_type: str | None) -> None:
    values = {
        "address": "192.168.88.1",
        "port": 22,
        "protocol": "ssh",
        "device_profile": "linux",
    }
    if device_type is not None:
        values["device_type"] = device_type
    with pytest.raises(ValidationError):
        DeviceWriteRequest(**values)


def test_session_request_carries_saved_device_id() -> None:
    payload = SessionCreateRequest(
        address="192.168.88.1",
        port=22,
        protocol="ssh",
        device_type="switch",
        device_profile="h3c_comware",
        username="admin",
        target_password="secret",
        device_id="saved-device-id",
    )
    assert payload.device_id == "saved-device-id"
    assert payload.device_type == "switch"


def test_console_request_accepts_usb_serial_path_and_baud_rate() -> None:
    payload = DeviceWriteRequest(
        address="/dev/serial/by-id/usb-FTDI_Test-if00-port0",
        port=115200,
        protocol="console",
        device_type="switch",
        device_profile="h3c_comware",
        remark="核心交换机 Console",
    )
    assert payload.address.startswith("/dev/serial/by-id/")
    assert payload.port == 115200


@pytest.mark.parametrize("address", ["/etc/passwd", "ttyUSB0", "/dev/ttyS0", "/dev/../etc/passwd"])
def test_console_request_rejects_non_usb_serial_paths(address: str) -> None:
    with pytest.raises(ValidationError):
        DeviceWriteRequest(
            address=address,
            port=9600,
            protocol="console",
            device_type="switch",
            device_profile="h3c_comware",
        )


def test_device_request_requires_compatible_device_profile() -> None:
    with pytest.raises(ValidationError):
        DeviceWriteRequest(
            address="192.168.88.1",
            port=22,
            protocol="ssh",
            device_type="switch",
        )
    with pytest.raises(ValidationError, match="不匹配"):
        DeviceWriteRequest(
            address="192.168.88.1",
            port=22,
            protocol="ssh",
            device_type="switch",
            device_profile="linux",
        )


def test_command_completion_is_isolated_by_device_profile() -> None:
    switch_items = asyncio.run(suggest_commands(
        prefix="dis",
        target="",
        protocol="console",
        device_type="switch",
        device_profile="h3c_comware",
        limit=8,
    ))
    switch_linux_probe = asyncio.run(suggest_commands(
        prefix="ls",
        target="",
        protocol="console",
        device_type="switch",
        device_profile="h3c_comware",
        limit=8,
    ))
    switch_config_items = asyncio.run(suggest_commands(
        prefix="sys",
        target="",
        protocol="console",
        device_type="switch",
        device_profile="h3c_comware",
        limit=8,
    ))
    server_items = asyncio.run(suggest_commands(
        prefix="l",
        target="",
        protocol="ssh",
        device_type="server",
        device_profile="linux",
        limit=8,
    ))

    assert switch_items
    assert all(item.command.startswith("display ") for item in switch_items)
    assert switch_linux_probe
    assert all(item.command.startswith(("lsa", "lsdb", "lsr")) for item in switch_linux_probe)
    assert all(item.command != "ls" for item in switch_linux_probe)
    assert switch_config_items[0].command == "system-view"
    assert switch_config_items[0].behavior_label == "系统访问与命令行"
    assert switch_config_items[0].view == "用户视图"
    assert server_items
    assert any(not item.command.startswith("display ") for item in server_items)


def test_command_completion_rejects_mismatched_profile() -> None:
    with pytest.raises(HTTPException) as captured:
        asyncio.run(suggest_commands(
            prefix="dis",
            target="",
            protocol="console",
            device_type="switch",
            device_profile="linux",
            limit=8,
        ))

    assert captured.value.status_code == 422
