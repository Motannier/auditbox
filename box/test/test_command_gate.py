import unittest
from unittest.mock import patch

from auditbox.relay.command_gate import CommandGate
from auditbox.relay.terminal_line import TerminalLineSync, extract_command_from_redraw


class FakeAudit:
    session_id = "test-session"

    def __init__(self):
        self.commands = []
        self.blocked = []
        self.confirmations = []
        self.confirmation_results = []
        self.command_results = []
        self.terminal_inputs = []
        self.terminal_outputs = []

    def log_command(self, command):
        self.commands.append(command)

    def log_blocked_command(self, command, alert):
        self.blocked.append((command, alert))

    def log_confirmation_required(self, command, alert, confirmation_id):
        self.confirmations.append((command, alert, confirmation_id))

    def log_confirmation_result(self, command, confirmed):
        self.confirmation_results.append((command, confirmed))

    def log_command_result(self, command, *, success):
        self.command_results.append((command, success))

    def log_terminal_input(self, data, *, sensitive=False, forwarded=True):
        self.terminal_inputs.append((data, sensitive, forwarded))

    def log_terminal_output(self, data):
        self.terminal_outputs.append(data)


def _bash_echo(command: str) -> bytes:
    return f"\r\x1b[Kuser@host:~$ {command}".encode()


def _redraw(gate, command, prompt="user@host:~$ "):
    gate.feed_output(f"\r\x1b[2K{prompt}{command}".encode())
    with patch("auditbox.relay.command_gate.time.monotonic", return_value=gate._line_read.output_at + 0.1):
        gate.poll()


class TerminalLineSyncTest(unittest.TestCase):
    def test_parses_bash_history_redraw(self):
        sync = TerminalLineSync()
        sync.feed(_bash_echo("ls -la"))
        sync.finalize_pending()
        self.assertEqual(sync.line, "ls -la")

    def test_parses_cisco_prompt(self):
        sync = TerminalLineSync()
        sync.feed(b"\rRouter# show running-config")
        sync.finalize_pending()
        self.assertEqual(sync.line, "show running-config")

    def test_ignores_osc_title_before_history_redraw(self):
        sync = TerminalLineSync()
        sync.feed(b"\x1b]0;root@IOTRouter: ~/tmp\x07root@IOTRouter:~/tmp# rm -rf *")
        sync.finalize_pending()
        self.assertEqual(sync.line, "rm -rf *")


class ExtractCommandTest(unittest.TestCase):
    def test_strips_bash_prompt(self):
        self.assertEqual(
            extract_command_from_redraw("user@host:~$ rm -rf /"),
            "rm -rf /",
        )


class CommandGateTest(unittest.TestCase):
    def test_high_risk_command_requires_one_time_confirmation(self):
        audit = FakeAudit()
        target = bytearray()
        client = bytearray()
        gate = CommandGate(audit, target.extend, client.extend)

        gate.feed(b"sudo reboot\r")

        self.assertEqual(bytes(target), b"sudo reboot")
        self.assertEqual(audit.commands, [])
        self.assertEqual(audit.blocked, [])
        token = audit.confirmations[0][2]

        self.assertTrue(gate.confirm(token))
        self.assertEqual(bytes(target), b"sudo reboot\r")
        self.assertFalse(gate.confirm(token))
        self.assertEqual(audit.commands, ["sudo reboot"])
        self.assertEqual(audit.confirmation_results, [("sudo reboot", True)])
        self.assertIn("二次确认".encode(), bytes(client))

    def test_same_high_risk_command_requires_confirmation_every_time(self):
        audit = FakeAudit()
        target = bytearray()
        client = bytearray()
        gate = CommandGate(audit, target.extend, client.extend)

        gate.feed(b"rm -rf *\r")
        first_token = audit.confirmations[-1][2]
        self.assertTrue(gate.confirm(first_token))

        gate.feed(b"rm -rf *\r")
        second_token = audit.confirmations[-1][2]

        self.assertEqual(len(audit.confirmations), 2)
        self.assertNotEqual(first_token, second_token)
        self.assertEqual(audit.commands, ["rm -rf *"])
        self.assertTrue(gate.confirm(second_token))
        self.assertEqual(audit.commands, ["rm -rf *", "rm -rf *"])

    def test_pending_confirmation_blocks_input_until_cancelled(self):
        audit = FakeAudit()
        target = bytearray()
        client = bytearray()
        gate = CommandGate(audit, target.extend, client.extend)

        gate.feed(b"rm -rf *\r")
        token = audit.confirmations[-1][2]
        target_before_blocked_input = bytes(target)

        gate.feed(b"echo must-not-run\r")
        self.assertEqual(bytes(target), target_before_blocked_input)
        self.assertEqual(audit.commands, [])

        self.assertTrue(gate.cancel_confirmation(token))
        self.assertEqual(bytes(target), target_before_blocked_input + b"\x05\x15\x03")
        gate.feed(b"echo safe\r")
        self.assertEqual(audit.commands, ["echo safe"])

    def test_history_recall_with_osc_title_still_requires_confirmation(self):
        audit = FakeAudit()
        target = bytearray()
        client = bytearray()
        gate = CommandGate(audit, target.extend, client.extend)

        gate.feed(b"\x1b[A")
        gate.feed_output(
            b"\x1b]0;root@IOTRouter: ~/tmp\x07root@IOTRouter:~/tmp# rm -rf *"
        )
        gate.feed(b"\r")

        _redraw(gate, "rm -rf *", "root@IOTRouter:~/tmp# ")

        self.assertEqual(len(audit.confirmations), 1)
        self.assertEqual(audit.confirmations[0][0], "rm -rf *")
        self.assertEqual(audit.commands, [])

    def test_danger_command_blocks_enter_and_clears_remote_line(self):
        audit = FakeAudit()
        target = bytearray()
        client = bytearray()
        gate = CommandGate(audit, target.extend, client.extend)

        gate.feed(b"rm -rf /\r")

        self.assertEqual(bytes(target), b"rm -rf /\x05\x15")
        self.assertEqual(audit.commands, [])
        self.assertEqual(audit.blocked[0][0], "rm -rf /")
        self.assertIn("安全审查".encode(), bytes(client))

    def test_h3c_confirmation_keeps_input_and_cancel_uses_ctrl_x_without_enter(self):
        audit = FakeAudit()
        target = bytearray()
        gate = CommandGate(
            audit,
            target.extend,
            bytearray().extend,
            device_profile="h3c_comware",
        )

        gate.feed(b"shutdown\r")

        self.assertEqual(bytes(target), b"shutdown")
        self.assertEqual(audit.confirmations[0][0], "shutdown")
        self.assertEqual(audit.confirmations[0][1]["reason"], "关闭当前接口或协议功能")
        self.assertTrue(gate.cancel_confirmation(audit.confirmations[0][2]))
        self.assertEqual(bytes(target), b"shutdown\x05\x18\x03")

    def test_sudo_is_not_blanket_blocked(self):
        audit = FakeAudit()
        target = bytearray()
        client = bytearray()
        gate = CommandGate(audit, target.extend, client.extend)

        gate.feed(b"sudo ls\r")

        self.assertEqual(bytes(target), b"sudo ls\r")
        self.assertEqual(audit.commands, ["sudo ls"])
        self.assertEqual(audit.blocked, [])

    def test_invalid_confirmation_token_does_not_execute(self):
        audit = FakeAudit()
        target = bytearray()
        client = bytearray()
        gate = CommandGate(audit, target.extend, client.extend)

        gate.feed(b"sudo systemctl restart nginx\r")

        self.assertFalse(gate.confirm("wrong-token"))
        self.assertEqual(bytes(target), b"sudo systemctl restart nginx")
        self.assertEqual(audit.commands, [])

    def test_history_recall_uses_remote_echo_for_safe_command(self):
        audit = FakeAudit()
        target = bytearray()
        client = bytearray()
        gate = CommandGate(audit, target.extend, client.extend)

        gate.feed(b"\x1b[A")
        gate.feed_output(_bash_echo("ls -la"))
        gate.feed(b"\r")

        _redraw(gate, "ls -la")

        self.assertEqual(bytes(target), b"\x1b[A\x05\x01\x05\r")
        self.assertEqual(audit.commands, ["ls -la"])
        self.assertEqual(audit.blocked, [])

    def test_history_recall_blocks_remote_dangerous_command(self):
        audit = FakeAudit()
        target = bytearray()
        client = bytearray()
        gate = CommandGate(audit, target.extend, client.extend)

        gate.feed(b"\x1b[A")
        gate.feed_output(_bash_echo("rm -rf /"))
        gate.feed(b"\r")

        _redraw(gate, "rm -rf /")

        self.assertEqual(bytes(target), b"\x1b[A\x05\x01\x05\x05\x15\x01\x05")
        self.assertEqual(audit.commands, [])
        self.assertEqual(audit.blocked[0][0], "rm -rf /")
        self.assertIn("安全审查".encode(), bytes(client))

    def test_history_recall_without_echo_does_not_guess_or_forward_enter(self):
        audit = FakeAudit()
        target = bytearray()
        client = bytearray()
        gate = CommandGate(audit, target.extend, client.extend)

        gate.feed(b"\x1b[A\r")

        with patch("auditbox.relay.command_gate.time.monotonic", return_value=gate._line_read.started + 3):
            gate.poll()
        self.assertEqual(bytes(target), b"\x1b[A\x05\x01\x05")
        self.assertEqual(audit.commands, [])
        self.assertEqual(audit.blocked, [])

    def test_empty_enter_after_pager_output_is_not_audited_as_a_command(self):
        audit = FakeAudit()
        target = bytearray()
        gate = CommandGate(
            audit,
            target.extend,
            bytearray().extend,
            device_profile="h3c_comware",
        )

        gate.feed_output(b"GigabitEthernet1/0/1 UP UP\r\n---- More ----")
        gate.feed(b"\r")

        self.assertEqual(bytes(target), b"\r")
        self.assertEqual(audit.commands, [])
        self.assertEqual(audit.blocked, [])

    def test_pager_control_key_does_not_leak_into_the_next_command(self):
        audit = FakeAudit()
        target = bytearray()
        gate = CommandGate(
            audit,
            target.extend,
            bytearray().extend,
            device_profile="h3c_comware",
        )

        gate.feed_output(b"GigabitEthernet1/0/1 UP UP\r\n---- More ----")
        gate.feed(b"q")
        gate.feed_output(b"\r\n<switch>")
        gate.feed(b"display version\r")

        self.assertEqual(bytes(target), b"qdisplay version\r")
        self.assertEqual(audit.commands, ["display version"])

    def test_h3c_prompt_reports_success_and_explicit_comware_error(self):
        audit = FakeAudit()
        target = bytearray()
        gate = CommandGate(
            audit,
            target.extend,
            bytearray().extend,
            device_profile="h3c_comware",
        )

        gate.feed(b"system-view\r")
        gate.feed_output(b"\r\n[Switch]")
        gate.feed(b"interface invalid\r")
        self.assertEqual(audit.commands, ["system-view"])
        self.assertTrue(gate.confirm(audit.confirmations[-1][2]))
        _redraw(gate, "interface invalid", "[Switch]")
        gate.feed_output(
            b"\r\n% Wrong parameter found at '^' position.\r\n[Switch]"
        )

        self.assertEqual(audit.command_results, [
            ("system-view", True),
            ("interface invalid", False),
        ])

    def test_linux_prompt_does_not_use_comware_result_parser(self):
        audit = FakeAudit()
        gate = CommandGate(audit, bytearray().extend, bytearray().extend)

        gate.feed(b"echo hello\r")
        gate.feed_output(b"\r\nuser@host:~$ ")

        self.assertEqual(audit.command_results, [])

    def test_typing_after_history_navigation_blocks_when_combined_command_is_dangerous(self):
        audit = FakeAudit()
        target = bytearray()
        client = bytearray()
        gate = CommandGate(audit, target.extend, client.extend)

        gate.feed(b"\x1b[A")
        gate.feed_output(_bash_echo("rm -rf /"))
        gate.feed(b" --help\r")

        _redraw(gate, "rm -rf / --help")

        self.assertEqual(bytes(target), b"\x1b[A --help\x05\x01\x05\x05\x15\x01\x05")
        self.assertEqual(audit.commands, [])
        self.assertEqual(audit.blocked[0][0], "rm -rf / --help")

    def test_typing_after_history_navigation_allows_safe_combined_command(self):
        audit = FakeAudit()
        target = bytearray()
        client = bytearray()
        gate = CommandGate(audit, target.extend, client.extend)

        gate.feed(b"\x1b[A")
        gate.feed_output(_bash_echo("echo hello"))
        gate.feed(b" > /tmp/out\r")

        _redraw(gate, "echo hello > /tmp/out")

        self.assertEqual(bytes(target), b"\x1b[A > /tmp/out\x05\x01\x05\r")
        self.assertEqual(audit.commands, ["echo hello > /tmp/out"])
        self.assertEqual(audit.blocked, [])

    def test_password_prompt_masks_input_and_does_not_log_it_as_a_command(self):
        audit = FakeAudit()
        target = bytearray()
        gate = CommandGate(audit, target.extend, bytearray().extend)

        gate.feed_output(b"[sudo] password for operator: ")
        gate.feed(b"not-a-real-secret")
        gate.feed(b"\r")

        self.assertEqual(bytes(target), b"not-a-real-secret\r")
        self.assertEqual(audit.commands, [])
        self.assertEqual(audit.terminal_outputs, [b"[sudo] password for operator: "])
        self.assertTrue(all(sensitive for _, sensitive, _ in audit.terminal_inputs))

    def test_split_password_prompt_masks_input(self):
        audit = FakeAudit()
        target = bytearray()
        gate = CommandGate(audit, target.extend, bytearray().extend)

        gate.feed_output(b"\r")
        gate.feed_output(b"Password:")
        gate.feed(b"split-secret\r")

        self.assertEqual(bytes(target), b"split-secret\r")
        self.assertEqual(audit.commands, [])
        self.assertEqual(audit.terminal_inputs[-1], (b"split-secret\r", True, True))

    def test_input_during_confirmation_is_audited_as_not_forwarded(self):
        audit = FakeAudit()
        target = bytearray()
        gate = CommandGate(audit, target.extend, bytearray().extend)

        gate.feed(b"sudo reboot\r")
        gate.feed(b"echo blocked\r")

        self.assertEqual(audit.terminal_inputs[-1], (b"echo blocked\r", False, False))


if __name__ == "__main__":
    unittest.main()
