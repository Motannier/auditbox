"""USB serial Console relay with audit and high-risk command gating."""

from __future__ import annotations

import logging
import os
import select
import socket
from typing import Callable

import serial

from auditbox.audit.logger import AuditLogger
from auditbox.prediction.detector import DETECT_BYTES, detect as detect_device
from auditbox.relay.command_gate import CommandGate

log = logging.getLogger(__name__)


class ConsoleConnectionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ConsoleProxy:
    def __init__(
        self,
        audit: AuditLogger,
        on_device_detected: Callable[[str], None] | None = None,
        on_confirmation_ready=None,
        device_profile: str = "linux",
        input_framing: bool = False,
    ) -> None:
        self._audit = audit
        self._on_device_detected = on_device_detected
        self._on_confirmation_ready = on_confirmation_ready
        self._device_profile = device_profile
        self._input_framing = input_framing

    def connect_and_relay(self, client_sock: socket.socket, params: dict, on_ready=None) -> None:
        path = str(params["host"])
        baud_rate = int(params.get("port") or 9600)
        kwargs = {
            "port": path,
            "baudrate": baud_rate,
            "bytesize": serial.EIGHTBITS,
            "parity": serial.PARITY_NONE,
            "stopbits": serial.STOPBITS_ONE,
            "timeout": 0,
            "write_timeout": 2,
            "xonxoff": False,
            "rtscts": False,
            "dsrdtr": False,
        }
        if os.name == "posix":
            kwargs["exclusive"] = True

        try:
            target = serial.Serial(**kwargs)
        except (OSError, serial.SerialException) as exc:
            log.warning("[%s] Console 打开失败 %s: %s", self._audit.session_id, path, exc)
            message = str(exc).lower()
            if "permission" in message or "access is denied" in message:
                raise ConsoleConnectionError("console_permission_denied", "没有访问 Console 线的权限") from exc
            if "busy" in message or "resource temporarily unavailable" in message:
                raise ConsoleConnectionError("console_busy", "Console 线正在被其他连接使用") from exc
            raise ConsoleConnectionError(
                "console_unavailable",
                "Console 线未连接、已被占用或串口参数不可用",
            ) from exc

        log.info("[%s] Console 连接 %s @ %d", self._audit.session_id, path, baud_rate)
        try:
            if on_ready:
                on_ready()
            self._relay(client_sock, target)
        finally:
            try:
                target.close()
            except (OSError, serial.SerialException):
                pass

    def _relay(self, client_sock: socket.socket, target: serial.Serial) -> None:
        detect_buf = b""
        detected = False

        def serial_send(data: bytes) -> None:
            view = memoryview(data)
            while view:
                written = target.write(view)
                if not written:
                    raise serial.SerialTimeoutException("Console 写入超时")
                view = view[written:]

        command_gate = CommandGate(
            audit=self._audit,
            target_send=serial_send,
            client_send=client_sock.sendall,
            device_profile=self._device_profile,
            input_framing=self._input_framing,
        )
        if self._on_confirmation_ready:
            self._on_confirmation_ready(command_gate.confirm, command_gate.cancel_confirmation)

        while target.is_open:
            try:
                readable, _, _ = select.select([client_sock], [], [], 0.05)
            except (ValueError, OSError):
                break

            if client_sock in readable:
                try:
                    data = client_sock.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                try:
                    command_gate.feed_transport(data)
                except (OSError, serial.SerialException):
                    break

            try:
                waiting = target.in_waiting
                output = target.read(min(max(waiting, 1), 4096)) if waiting else b""
            except (OSError, serial.SerialException):
                break
            if not output:
                try:
                    command_gate.poll()
                except (OSError, serial.SerialException):
                    break
                continue
            try:
                client_sock.sendall(output)
            except OSError:
                break
            command_gate.feed_output(output)
            try:
                command_gate.poll()
            except (OSError, serial.SerialException):
                break

            if not detected and self._on_device_detected:
                detect_buf += output
                if b"\n" in detect_buf or len(detect_buf) >= DETECT_BYTES:
                    detected = True
                    device_type = detect_device(detect_buf.decode(errors="replace"))
                    log.info("[%s] Console 检测到设备类型: %s", self._audit.session_id, device_type)
                    self._on_device_detected(device_type)

        log.info("[%s] Console relay 结束", self._audit.session_id)
