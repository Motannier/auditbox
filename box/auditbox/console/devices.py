"""Discover USB serial adapters suitable for network-device Console access."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from serial.tools import list_ports

_USB_SERIAL_PREFIXES = ("/dev/ttyUSB", "/dev/ttyACM")
_SERIAL_BY_ID = Path("/dev/serial/by-id")
_USB_TTY_PATTERN = re.compile(r"^/dev/tty(?:USB|ACM)\d+$")


@dataclass(frozen=True)
class ConsolePort:
    path: str
    device: str
    label: str
    description: str
    manufacturer: str
    product: str
    serial_number: str
    vid: int | None
    pid: int | None


def list_console_ports() -> list[ConsolePort]:
    ports: list[ConsolePort] = []
    for port in list_ports.comports():
        device = str(port.device or "")
        if not _is_usb_serial_device(device, port):
            continue
        stable_path = _stable_device_path(device)
        description = str(port.description or "USB Console")
        manufacturer = str(port.manufacturer or "")
        product = str(port.product or "")
        serial_number = str(port.serial_number or "")
        identity = product or description or Path(device).name
        suffix = f"（{Path(device).name}）" if stable_path != device else ""
        ports.append(ConsolePort(
            path=stable_path,
            device=device,
            label=f"{identity}{suffix}",
            description=description,
            manufacturer=manufacturer,
            product=product,
            serial_number=serial_number,
            vid=port.vid,
            pid=port.pid,
        ))
    return sorted(ports, key=lambda item: (item.label.lower(), item.path))


def validate_console_path(path: str, *, require_connected: bool = False) -> str:
    value = str(path or "").strip()
    if not value.startswith("/dev/") or "\x00" in value:
        raise ValueError("请选择盒子识别到的 Console 设备")
    path_value = Path(value)
    is_usb_tty = _USB_TTY_PATTERN.fullmatch(value) is not None
    is_stable_usb_path = path_value.parent == _SERIAL_BY_ID and bool(path_value.name)
    if not (is_usb_tty or is_stable_usb_path):
        raise ValueError("仅支持盒子 USB-A 口连接的 Console 设备")
    if require_connected:
        ports = list_console_ports()
        available = {item.path for item in ports} | {item.device for item in ports}
        if value not in available:
            raise ValueError("Console 线未连接或已更换，请重新选择设备")
    return value


def _is_usb_serial_device(device: str, port: object) -> bool:
    if device.startswith(_USB_SERIAL_PREFIXES):
        return True
    return getattr(port, "vid", None) is not None and device.startswith("/dev/")


def _stable_device_path(device: str, by_id_dir: Path = _SERIAL_BY_ID) -> str:
    try:
        resolved_device = os.path.realpath(device)
        for candidate in by_id_dir.iterdir():
            if candidate.is_symlink() and os.path.realpath(candidate) == resolved_device:
                return str(candidate)
    except OSError:
        pass
    return device
