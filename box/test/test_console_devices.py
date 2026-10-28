from types import SimpleNamespace

import pytest

from auditbox.console import devices


def test_console_port_discovery_uses_usb_adapter_metadata(monkeypatch) -> None:
    fake_port = SimpleNamespace(
        device="/dev/ttyUSB0",
        description="FT232R USB UART",
        manufacturer="FTDI",
        product="FT232R USB UART",
        serial_number="ABCD1234",
        vid=0x0403,
        pid=0x6001,
    )
    monkeypatch.setattr(devices.list_ports, "comports", lambda: [fake_port])
    monkeypatch.setattr(
        devices,
        "_stable_device_path",
        lambda _device: "/dev/serial/by-id/usb-FTDI_ABCD1234-if00-port0",
    )

    result = devices.list_console_ports()

    assert len(result) == 1
    assert result[0].device == "/dev/ttyUSB0"
    assert result[0].path.startswith("/dev/serial/by-id/")
    assert result[0].label == "FT232R USB UART（ttyUSB0）"
    assert result[0].vid == 0x0403
    assert result[0].pid == 0x6001


def test_console_path_presence_check_uses_discovery_without_opening_device(monkeypatch) -> None:
    port = devices.ConsolePort(
        path="/dev/serial/by-id/usb-Test",
        device="/dev/ttyUSB0",
        label="Test",
        description="Test",
        manufacturer="",
        product="",
        serial_number="",
        vid=None,
        pid=None,
    )
    monkeypatch.setattr(devices, "list_console_ports", lambda: [port])

    assert devices.validate_console_path(port.path, require_connected=True) == port.path


def test_console_path_rejects_non_usb_and_nested_device_paths() -> None:
    with pytest.raises(ValueError, match="仅支持"):
        devices.validate_console_path("/dev/ttyS0")

    with pytest.raises(ValueError, match="仅支持"):
        devices.validate_console_path("/dev/ttyUSB0/../../etc/passwd")
