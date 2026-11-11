"""Cancelled input must recover or disconnect without submitting leftover bytes."""

import json
import select
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from auditbox.relay.command_gate import CommandGate
from auditbox.audit import logger
from auditbox.relay.console_proxy import ConsoleProxy
from auditbox.relay.ssh_proxy import SSHProxy
from auditbox.relay.telnet_proxy import TelnetProxy
from test_command_gate import FakeAudit


CASES = [
    ("linux", "root@box:~$ ", "sudo reboot", "pwd"),
    ("h3c_comware", "[DiShiPeiWangHuiJu]", "shutdown", "display version"),
    ("huawei_vrp", "[HUAWEI]", "shutdown", "display version"),
    ("cisco_ios", "Switch(config)# ", "shutdown", "show version"),
    ("zte_zxr10", "ZXR10(config)# ", "shutdown", "show version"),
]


def pending_gate(profile="h3c_comware", prompt="[DiShiPeiWangHuiJu]", command="shutdown"):
    audit, sent, messages = FakeAudit(), bytearray(), bytearray()
    audit.operations = []
    audit.log_operation = lambda event, details: audit.operations.append((event, details))
    gate = CommandGate(audit, sent.extend, messages.extend, device_profile=profile)
    gate.feed(command.encode())
    gate.feed_output(("\r" + prompt + command).encode())
    gate.feed(b"\r")
    token = audit.confirmations[-1][2]
    assert gate.cancel_confirmation(token)
    return gate, audit, sent, messages, token


def poll_after(gate, seconds):
    with patch("auditbox.relay.command_gate.time.monotonic", return_value=gate._line_read.started + seconds):
        gate.poll()


@pytest.mark.parametrize("profile,prompt,command,safe", CASES)
def test_cancel_accepts_fresh_empty_prompt_without_requiring_crlf(profile, prompt, command, safe):
    gate, audit, sent, _, token = pending_gate(profile, prompt, command)
    assert gate._line_read and gate._line_read.purpose == "clear"
    assert b"\r" not in sent and b"\n" not in sent
    gate.feed_output(prompt.encode())
    poll_after(gate, 0.1)
    assert gate._line_read is None
    assert not gate.confirm(token)
    gate.feed((safe + "\r").encode())
    assert audit.commands == [safe]
    assert audit.confirmation_results == [(command, False)]


def test_cancel_without_initial_echo_still_requires_empty_prompt_before_reuse():
    audit, sent = FakeAudit(), bytearray()
    gate = CommandGate(audit, sent.extend, lambda _: None, device_profile="h3c_comware")
    gate.feed(b"shutdown\r")
    assert gate.cancel_confirmation(audit.confirmations[-1][2])
    before = bytes(sent)
    gate.feed(b"display version\r")
    assert bytes(sent) == before and not audit.commands
    gate.feed_output(b"\r\n[H3C]")
    poll_after(gate, 0.1)
    gate.feed(b"display version\r")
    assert audit.commands == ["display version"]


def test_ctrl_c_restarts_clear_and_redraw_and_is_audited_as_forwarded():
    gate, audit, sent, _, _ = pending_gate()
    old = gate._line_read
    gate.feed_output(b"\r\x1b[1A[DiShiPeiWangHuiJu]")
    assert gate._line_sync.command is None
    before = bytes(sent)
    gate.feed(b"\x03")
    assert gate._line_read is not old and gate._line_read.output_at is None
    assert bytes(sent) == before + b"\x05\x18\x03\x12"
    assert audit.terminal_inputs[-1] == (b"\x03", False, True)
    gate.feed_output(b"[DiShiPeiWangHuiJu]")
    poll_after(gate, 0.1)
    assert gate._line_read is None
    gate.feed(b"display version\r")
    assert audit.commands == ["display version"]


@pytest.mark.parametrize("response", [b"", b"\r\x1b[1A[H3C]", b"\r[H3C]shutdown"])
def test_unverified_clear_retries_once_then_ends_only_this_connection(response):
    gate, audit, sent, messages, token = pending_gate()
    other_audit, other_sent = FakeAudit(), bytearray()
    other = CommandGate(other_audit, other_sent.extend, lambda _: None)
    if response:
        gate.feed_output(response)
    poll_after(gate, 2.1)
    assert gate._line_read and gate._line_read.notified
    assert bytes(sent).endswith(b"\x05\x18\x03\x12")
    if response:
        gate.feed_output(response)
    with pytest.raises(ConnectionError, match="输入恢复失败"):
        poll_after(gate, 2.1)
    before = bytes(sent)
    gate.feed(b"display version\r")
    assert bytes(sent) == before and b"\r" not in sent and b"\n" not in sent
    assert not audit.commands and not gate.confirm(token)
    assert "重新连接" in messages.decode() and "无需刷新页面" in messages.decode()
    assert any(event == "security_terminal_input_recovery_failed" for event, _ in audit.operations)
    other.feed(b"pwd\r")
    assert other_audit.commands == ["pwd"] and bytes(other_sent) == b"pwd\r"


def test_empty_prompt_after_automatic_retry_recovers_without_replaying_cancelled_command():
    gate, audit, sent, _, _ = pending_gate()
    poll_after(gate, 2.1)
    gate.feed_output(b"[DiShiPeiWangHuiJu]")
    poll_after(gate, 0.1)
    assert gate._line_read is None
    gate.feed(b"display version\r")
    assert audit.commands == ["display version"]
    assert bytes(sent).count(b"shutdown") == 1




class SocketConsole:
    is_open = True

    def __init__(self, stream):
        self.stream = stream

    @property
    def in_waiting(self):
        return 4096 if select.select([self.stream], [], [], 0)[0] else 0

    def read(self, count):
        return self.stream.recv(count)

    def write(self, data):
        return self.stream.send(data)


class SocketChannel:
    closed = False

    def __init__(self, stream):
        self.stream = stream

    def fileno(self):
        return self.stream.fileno()

    def get_transport(self):
        return SimpleNamespace(is_active=lambda: True)

    def recv_ready(self):
        return bool(select.select([self.stream], [], [], 0)[0])

    def recv(self, count):
        return self.stream.recv(count)

    def sendall(self, data):
        self.stream.sendall(data)

    def exit_status_ready(self):
        return False


@pytest.mark.parametrize("protocol", ["ssh", "telnet", "console"])
def test_relays_stop_after_recovery_timeout_instead_of_hanging(protocol, monkeypatch):
    monkeypatch.setattr("auditbox.relay.command_gate._SNAPSHOT_TIMEOUT", 0.1)
    monkeypatch.setattr("auditbox.relay.command_gate._SNAPSHOT_SETTLE", 0.005)
    browser, client = socket.socketpair()
    target, device = socket.socketpair()
    audit, handlers, wire, errors = FakeAudit(), [], bytearray(), []
    cls = {"ssh": SSHProxy, "telnet": TelnetProxy, "console": ConsoleProxy}[protocol]
    relay = cls(audit, device_profile="h3c_comware",
                on_confirmation_ready=lambda yes, no: handlers.extend([yes, no]))
    endpoint = SocketChannel(target) if protocol == "ssh" else SocketConsole(target) if protocol == "console" else target

    def pump():
        try:
            relay._relay(client, endpoint)
        except Exception as exc:
            errors.append(exc)

    def emulate():
        silent = False
        try:
            device.sendall(b"\r\n[H3C]")
            while True:
                data = device.recv(4096)
                if not data:
                    return
                wire.extend(data)
                if b"\x03" in data:
                    silent = True
                if not silent:
                    device.sendall(data)
        except OSError:
            pass

    threads = [threading.Thread(target=pump, daemon=True), threading.Thread(target=emulate, daemon=True)]
    try:
        for thread in threads:
            thread.start()
        browser.sendall(b"shutdown\r")
        deadline = time.monotonic() + 2
        while not audit.confirmations and time.monotonic() < deadline:
            time.sleep(0.01)
        assert audit.confirmations, errors
        assert handlers[1](audit.confirmations[-1][2])
        threads[0].join(timeout=2)
        assert not threads[0].is_alive(), f"{protocol} relay remained stuck"
        assert not errors and not audit.commands
        assert b"\r" not in wire and b"\n" not in wire
        browser.settimeout(0.2)
        output = browser.recv(32768).decode()
        assert "无需刷新页面" in output
    finally:
        for stream in (browser, client, target, device):
            stream.close()
        for thread in threads:
            thread.join(timeout=1)
