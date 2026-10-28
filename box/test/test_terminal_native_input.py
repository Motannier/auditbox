"""Native CLI editing and confirmation tests. No network/device access."""

import os
import pty
import select
import shutil
import socket
import subprocess
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from auditbox.relay.command_gate import CommandGate
from auditbox.relay.terminal_line import TerminalLineSync, extract_command_from_redraw
from auditbox.relay.telnet_proxy import TelnetProxy
from test_command_gate import FakeAudit


def settle(gate):
    read = gate._line_read
    assert read and read.output_at is not None
    with patch("auditbox.relay.command_gate.time.monotonic", return_value=read.output_at + 0.1):
        gate.poll()


def redraw(gate, command, prompt="[H3C]"):
    gate.feed_output(("\r\x1b[2K" + prompt + command).encode())
    settle(gate)


@pytest.mark.parametrize("prompt", ["[H3C]", "[H3C-GigabitEthernet2/0]", "<H3C>", "[~HUAWEI-GE1/0/1]", "Switch(config-if)# ", "root@box:~$ "])
def test_prompt_and_shell_redirection_are_not_part_of_command(prompt):
    command = "echo value > /tmp/file"
    assert extract_command_from_redraw(prompt + command) == command


def test_terminal_overwrite_backspace_erase_and_split_utf8():
    sync = TerminalLineSync()
    sync.feed(b"\r[H3C]display versx\bion")
    assert sync.command == "display version"
    sync.feed(b"\r[H3C]quit\x1b[K")
    assert sync.command == "quit"
    wire = "\r[H3C]description 测试接口\x1b[K".encode()
    for byte in wire:
        sync.feed(bytes([byte]))
    assert sync.command == "description 测试接口"


def test_incomplete_escape_and_vertical_screen_redraw_are_not_snapshots():
    sync = TerminalLineSync()
    sync.feed(b"\r[H3C]shutdown\x1b[")
    assert sync.command is None
    sync.feed(b"1A")
    assert sync.command is None
    sync.feed(b"\r\n[H3C]display version")
    assert sync.command == "display version"


def test_charset_and_osc_controls_do_not_change_command_text():
    sync = TerminalLineSync()
    sync.feed(b"\x1b]0;not a command\x1b\\\r[H3C]\x1b(Bshutdown")
    assert sync.command == "shutdown"


def test_hard_wrapped_command_tail_cannot_be_mistaken_for_shell_prompt():
    sync = TerminalLineSync()
    sync.feed(b"\rroot@box:~$ echo a very long argument\r\ncontinued > file")
    assert sync.command is None
    sync.feed(b"\r\nroot@box:~$ echo ok")
    assert sync.command == "echo ok"


@pytest.mark.parametrize("profile,prompt,actual", [
    ("h3c_comware", "[H3C]", "interface GigabitEthernet 2/0"),
    ("huawei_vrp", "[HUAWEI]", "interface GigabitEthernet 0/0/1"),
    ("cisco_ios", "Switch(config)# ", "interface GigabitEthernet 0/1"),
    ("zte_zxr10", "ZXR10(config)# ", "interface gei_1/1"),
])
def test_native_tab_uses_full_device_command_and_never_replays_it(profile, prompt, actual):
    audit, sent = FakeAudit(), bytearray()
    gate = CommandGate(audit, sent.extend, lambda _: None, device_profile=profile)
    gate.feed(b"int\tg\t2/0\r")
    assert not audit.confirmations and b"\r" not in sent
    redraw(gate, actual, prompt)
    command, alert, token = audit.confirmations[-1]
    assert command == actual
    assert "组合命令" not in alert["reason"]
    before = bytes(sent)
    gate.feed(b"evil\r")
    assert bytes(sent) == before
    assert gate.confirm(token)
    assert b"\r" not in sent
    redraw(gate, actual, prompt)
    assert bytes(sent).endswith(b"\r")
    assert actual.encode() not in sent  # Only the original keys, probes and Enter.
    assert audit.commands == [actual]
    assert not gate.confirm(token)


def test_fragmented_late_tab_echo_and_immediate_enter_wait_for_full_redraw():
    audit, sent = FakeAudit(), bytearray()
    gate = CommandGate(audit, sent.extend, lambda _: None, device_profile="h3c_comware")
    gate.feed(b"dis\tver\t\r")
    gate.poll()
    assert not audit.commands and b"\r" not in sent
    gate.feed_output(b"\r[H3C]display ver")
    gate.poll()
    assert not audit.commands
    gate.feed_output(b"sion")
    settle(gate)
    assert audit.commands == ["display version"]
    assert bytes(sent) == b"dis\tver\t\x05\x12\r"


def test_history_redraw_then_typed_suffix_is_not_counted_twice():
    audit, sent = FakeAudit(), bytearray()
    gate = CommandGate(audit, sent.extend, lambda _: None, device_profile="h3c_comware")
    gate.feed(b"\x1b[A")
    gate.feed_output(b"\r[H3C]display interface")
    gate.feed(b" brief")
    gate.feed_output(b" brief")
    gate.feed(b"\r")
    redraw(gate, "display interface brief")
    assert audit.commands == ["display interface brief"]


def test_mid_line_edit_is_audited_from_remote_editor_not_appended_keys():
    audit, sent = FakeAudit(), bytearray()
    gate = CommandGate(audit, sent.extend, lambda _: None, device_profile="h3c_comware")
    gate.feed(b"display version\x1b[D\x7fn\r")
    redraw(gate, "display version")
    assert audit.commands == ["display version"]


def test_changed_line_requires_new_confirmation_not_old_approval():
    audit, sent = FakeAudit(), bytearray()
    gate = CommandGate(audit, sent.extend, lambda _: None, device_profile="h3c_comware")
    gate.feed(b"shut\t\r")
    redraw(gate, "shutdown")
    old = audit.confirmations[-1][2]
    assert gate.confirm(old)
    redraw(gate, "save")
    assert b"\r" not in sent and not audit.commands
    assert audit.confirmation_results == [("shutdown", False)]
    command, _, new = audit.confirmations[-1]
    assert command == "save" and new != old
    assert not gate.confirm(old)
    assert gate.confirm(new)
    redraw(gate, "save")
    assert audit.commands == ["save"]
    assert sent.count(b"\r") == 1


def test_confirmation_uses_late_echo_even_when_dialog_preceded_the_echo():
    audit, sent = FakeAudit(), bytearray()
    gate = CommandGate(audit, sent.extend, lambda _: None, device_profile="h3c_comware")
    gate.feed(b"shutdown\r")
    token = audit.confirmations[-1][2]
    gate.feed_output(b"\r[H3C]shutdown")
    assert gate.confirm(token)
    assert b"\r" not in sent
    redraw(gate, "shutdown")
    assert audit.commands == ["shutdown"]


def test_empty_native_snapshot_does_not_submit_an_unreviewed_enter():
    audit, sent = FakeAudit(), bytearray()
    gate = CommandGate(audit, sent.extend, lambda _: None, device_profile="h3c_comware")
    gate.feed(b"shut\t\r")
    redraw(gate, "")
    assert b"\r" not in sent and not audit.commands and not audit.confirmations


def test_cancel_never_sends_enter_and_waits_until_device_input_is_empty():
    audit, sent = FakeAudit(), bytearray()
    gate = CommandGate(audit, sent.extend, lambda _: None, device_profile="h3c_comware")
    gate.feed(b"shut\t\r")
    redraw(gate, "shutdown")
    assert gate.cancel_confirmation(audit.confirmations[-1][2])
    assert bytes(sent).endswith(b"\x05\x18\x03") and b"\r" not in sent
    # Simulate a device which ignored the clear key: it must not receive Enter.
    redraw(gate, "shutdown")
    before = bytes(sent)
    gate.feed(b"\r")
    assert bytes(sent) == before
    gate.feed(b"\x03")
    assert bytes(sent).endswith(b"\x03")
    redraw(gate, "")
    gate.feed(b"display version\r")
    assert audit.commands == ["display version"]
    assert audit.confirmation_results == [("shutdown", False)]


@pytest.mark.parametrize("output", [b"", b"\r\n---- More ----", b"\r\nCPU usage: 5%", b"\r[H3C]shut\x1b[1A"])
def test_snapshot_timeout_never_executes_partial_input_or_late_output(output):
    audit, sent, messages = FakeAudit(), bytearray(), bytearray()
    gate = CommandGate(audit, sent.extend, messages.extend, device_profile="h3c_comware")
    gate.feed(b"shut\t\r")
    if output:
        gate.feed_output(output)
    with patch("auditbox.relay.command_gate.time.monotonic", return_value=gate._line_read.started + 3):
        gate.poll()
    assert not audit.commands and not audit.confirmations and b"\r" not in sent
    gate.feed_output(b"\r[H3C]shutdown")
    gate.poll()
    assert not audit.commands and b"\r" not in sent
    assert "未发送回车" in messages.decode()
    gate.feed(b"\x03")
    gate.feed_output(b"\r\n[H3C]")
    gate.feed(b"display version\r")
    assert audit.commands == ["display version"]


class H3CEditor:
    """Actual mutable editor state, including a deliberately broken Ctrl+X."""

    def __init__(self):
        self.line = ""
        self.executed = []
        self.gate = None

    def send(self, data):
        for byte in data:
            if byte == 9:
                self.line = {"int": "interface ", "interface g": "interface GigabitEthernet "}.get(self.line, self.line)
                self.gate.feed_output(("\r[H3C]" + self.line + "\x1b[K").encode())
            elif byte == 18:
                self.gate.feed_output(("\r[H3C]" + self.line + "\x1b[K").encode())
            elif byte == 3:
                self.line = ""
                self.gate.feed_output(b"\r\n[H3C]")
            elif byte in (13, 10):
                self.executed.append(self.line)
                self.line = ""
                self.gate.feed_output(b"\r\n[H3C]")
            elif byte >= 32:
                self.line += chr(byte)
                self.gate.feed_output(bytes([byte]))


def test_original_screenshot_scenario_executes_exactly_once_without_clear_key_dependency():
    editor, audit = H3CEditor(), FakeAudit()
    gate = CommandGate(audit, editor.send, lambda _: None, device_profile="h3c_comware")
    editor.gate = gate
    gate.feed_output(b"\r\n[H3C]")
    gate.feed(b"int\tg\t2/0\r")
    settle(gate)
    actual = "interface GigabitEthernet 2/0"
    assert editor.line == actual and editor.executed == []
    assert audit.confirmations[-1][0] == actual
    assert gate.confirm(audit.confirmations[-1][2])
    settle(gate)
    assert editor.executed == [actual]
    assert audit.commands == [actual]


@pytest.mark.parametrize("confirm", [True, False])
def test_telnet_relay_pumps_native_snapshots_and_confirmation_without_real_device(confirm):
    browser, proxy_client = socket.socketpair()
    proxy_target, device = socket.socketpair()
    audit, handlers, editor = FakeAudit(), [], H3CEditor()
    editor.gate = SimpleNamespace(feed_output=device.sendall)
    relay = TelnetProxy(audit, device_profile="h3c_comware",
                        on_confirmation_ready=lambda yes, no: handlers.extend([yes, no]))

    def emulate():
        try:
            device.sendall(b"\r\n[H3C]")
            while True:
                data = device.recv(4096)
                if not data:
                    return
                editor.send(data)
        except OSError:
            return

    threads = [threading.Thread(target=relay._relay, args=(proxy_client, proxy_target), daemon=True),
               threading.Thread(target=emulate, daemon=True)]

    def wait_for(predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("Local relay did not process the editor response")

    try:
        for thread in threads:
            thread.start()
        browser.sendall(b"int\tg\t2/0\r")
        wait_for(lambda: audit.confirmations)
        assert editor.executed == []
        assert audit.confirmations[-1][0] == "interface GigabitEthernet 2/0"
        assert handlers[0 if confirm else 1](audit.confirmations[-1][2])
        if confirm:
            wait_for(lambda: editor.executed)
            assert editor.executed == ["interface GigabitEthernet 2/0"]
        else:
            wait_for(lambda: not editor.line)
            assert editor.executed == []
            time.sleep(0.12)  # The relay verifies the empty prompt before reuse.
            browser.sendall(b"display version\r")
            wait_for(lambda: editor.executed)
            assert editor.executed == ["display version"]
    finally:
        for stream in (browser, proxy_client, proxy_target, device):
            stream.close()
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()


@pytest.fixture
def readline_shell(tmp_path):
    """Local PTY, not a real server; tests execute only cat and printf."""
    if not shutil.which("bash"):
        pytest.skip("A local readline shell is required")
    (tmp_path / "auditbox-native.txt").write_text("NATIVE_TAB_OK\n")
    master, slave = pty.openpty()
    environment = {**os.environ, "PS1": "auditbox-test$ ", "TERM": "xterm", "HISTFILE": "/dev/null", "INPUTRC": "/dev/null"}
    process = subprocess.Popen([shutil.which("bash"), "--noprofile", "--norc", "-i"],
                               stdin=slave, stdout=slave, stderr=slave, cwd=tmp_path,
                               env=environment, start_new_session=True)
    os.close(slave)
    audit, output = FakeAudit(), bytearray()
    gate = CommandGate(audit, lambda data: os.write(master, data), output.extend)

    def pump(predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.02)[0]:
                data = os.read(master, 4096)
                output.extend(data)
                gate.feed_output(data)
            gate.poll()
            if predicate():
                return
        raise AssertionError(repr(output.decode(errors="replace")))

    try:
        pump(lambda: b"auditbox-test$ " in output)
        yield gate, audit, output, pump
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        os.close(master)


def test_real_bash_tab_completion_and_cursor_probe_execute_only_the_complete_command(readline_shell):
    gate, audit, output, pump = readline_shell
    gate.feed(b"cat auditbox-n\t\r")
    pump(lambda: b"NATIVE_TAB_OK" in output)
    assert audit.commands == ["cat auditbox-native.txt"]
    assert b"reverse-i-search" not in output


def test_real_bash_confirm_only_releases_enter_and_cancel_recovers_prompt(readline_shell):
    gate, audit, output, pump = readline_shell
    # The existing Linux rule sees the word reboot, but printf cannot reboot.
    gate.feed(b"printf reboot")
    pump(lambda: gate._line_sync.command == "printf reboot")
    gate.feed(b"\r")
    token = audit.confirmations[-1][2]
    assert gate.confirm(token)
    pump(lambda: audit.commands == ["printf reboot"])
    pump(lambda: gate._line_sync.command == "")
    gate.feed(b"printf reboot")
    pump(lambda: gate._line_sync.command == "printf reboot")
    gate.feed(b"\r")
    assert gate.cancel_confirmation(audit.confirmations[-1][2])
    pump(lambda: gate._line_read is None)
    gate.feed(b"cat auditbox-native.txt\r")
    pump(lambda: b"NATIVE_TAB_OK" in output)
    assert audit.commands == ["printf reboot", "cat auditbox-native.txt"]
