"""Production terminal state transitions; no target device or network required."""
import json
from unittest.mock import patch

import pytest

from auditbox.relay.command_gate import CommandGate
from auditbox.prediction.engine import PredictionEngine
from test_command_gate import FakeAudit


def terminal(profile="huawei_vrp"):
    audit = FakeAudit()
    states, target, client = [], bytearray(), bytearray()
    audit.log_terminal_state = states.append
    gate = CommandGate(audit, target.extend, client.extend, device_profile=profile,
                       require_command_prompt=True)
    return gate, audit, target, client, states


@pytest.mark.parametrize("prompt", ["Username: ", "Login: ", "User name: ", "用户名：", "Please enter username: "])
def test_username_is_never_a_command_even_with_risky_account_name(prompt):
    gate, audit, target, _, states = terminal()
    for byte in prompt.encode():
        gate.feed_output(bytes([byte]))
    gate.feed(b"admin\r")
    gate.feed(b"admin\r")  # Login retry before another output packet.
    assert bytes(target) == b"admin\r"
    assert not audit.commands and not audit.confirmations
    assert states[-1]["mode"] == "auth"
    assert all(sensitive for _, sensitive, _ in audit.terminal_inputs)


def test_login_retry_password_change_and_real_prompt_restore_audit():
    gate, audit, target, _, states = terminal()
    for prompt, reply in [(b"Username: ", b"admin\r"), (b"\r\nPassword: ", b"private\r"),
                          (b"\r\nNew password: ", b"new-private\r"),
                          (b"\r\nRetype password: ", b"new-private\r")]:
        gate.feed_output(prompt)
        gate.feed(reply)
        assert not audit.commands and not audit.confirmations
    gate.feed_output(b"\r\n<HUAWEI>")
    assert states[-1]["mode"] == "command"
    gate.feed(b"display version\r")
    assert audit.commands == ["display version"]
    assert states[-1]["mode"] == "running"
    gate.feed_output(b"display version\r\nversion output\r\n<HUAWEI>")
    assert states[-1]["mode"] == "command"
    assert all(set(s) == {"mode", "version"} for s in states)


@pytest.mark.parametrize("prompt", [b"---- More ----", b"--More--(50%)", b"--More--\x08\x08", "————more————".encode()])
def test_paging_stays_active_until_a_real_command_prompt(prompt):
    gate, audit, target, _, states = terminal()
    gate.feed_output(b"rows\r\n" + prompt)
    for key in [b"q", b"q", b" ", b"\r", b"\x1b[B"]:
        gate.feed(key)
    assert not audit.commands and not audit.confirmations
    assert states[-1]["mode"] == "pager"
    gate.feed_output(b"\r\n<HUAWEI>")
    gate.feed(b"display version\r")
    assert audit.commands == ["display version"]
    assert bytes(target).startswith(b"qdisplay version\r")


def test_target_confirmation_and_empty_enter_do_not_become_commands():
    gate, audit, _, _, states = terminal()
    gate.feed_output(b"<HUAWEI>")
    gate.feed(b"\r")
    assert states[-1]["mode"] == "running"
    gate.feed_output(b"\r\n<HUAWEI>")
    assert states[-1]["mode"] == "command"
    gate.feed_output(b"\r\nContinue? [Y/N]: ")
    gate.feed(b"y\r")
    assert not audit.commands and not audit.confirmations
    gate.feed_output(b"\r\n<HUAWEI>")
    assert states[-1]["mode"] == "command"


def test_unknown_and_shell_continuation_never_bypass_execution_control():
    gate, audit, target, client, states = terminal("linux")
    gate.feed_output(b"> ")
    gate.feed(b"sudo reboot\r")
    gate.feed(b"\r")  # A second Enter must not submit the held device input.
    assert bytes(target) == b"sudo reboot"  # Enter stays withheld.
    assert not audit.confirmations and not audit.commands
    assert "尚未识别" in client.decode()


@pytest.mark.parametrize("prompt,wire,expected", [
    (b"Password: ", b"private\rreboot\r", b"private\r"),
    (b"Username: ", b"admin\rreboot\r", b"admin\r"),
    (b"--More--", b"qreboot\r", b"q"),
    (b"Continue [Y/N]: ", b"y\rreboot\r", b"y\r"),
])
def test_interactive_reply_never_forwards_pasted_following_commands(prompt, wire, expected):
    gate, audit, target, _, _ = terminal()
    gate.feed_output(prompt)
    gate.feed(wire)
    gate.feed(b"reboot\r")
    assert bytes(target) == expected
    assert not audit.commands and not audit.confirmations
    assert any(not forwarded for _, _, forwarded in audit.terminal_inputs)


def test_changed_configuration_view_restores_readiness():
    gate, _, _, _, states = terminal()
    gate.feed_output(b"<HUAWEI>system-view\r\n[HUAWEI]")
    assert states[-1]["mode"] == "command"


@pytest.mark.parametrize("profile,prompt", [("h3c_comware", b"[H3C-GigabitEthernet1/0/1]"),
    ("huawei_vrp", b"[HUAWEI]"), ("cisco_ios", b"Switch(config-if)# "), ("zte_zxr10", b"ZXR10(config)# ")])
def test_configuration_views_are_command_prompts(profile, prompt):
    gate, _, _, _, states = terminal(profile)
    gate.feed_output(prompt)
    assert states[-1]["mode"] == "command"


@pytest.mark.parametrize("prompt", [b"[Y/N]: ", b"[yes/no] ", b"[confirm]"])
def test_standalone_device_confirmation_is_not_a_network_cli_view(prompt):
    gate, audit, target, _, states = terminal()
    gate.feed_output(prompt)
    gate.feed(b"\r")
    assert states[-1]["mode"] == "interaction"
    assert bytes(target) == b"\r" and not audit.commands


def test_risk_confirmation_survives_waiting_and_uses_existing_snapshot_checks():
    gate, audit, target, _, states = terminal("linux")
    gate.feed_output(b"root@box:~# ")
    gate.feed(b"sudo reboot")
    gate.feed_output(b"sudo reboot")
    gate.feed(b"\r")
    assert states[-1]["mode"] == "confirmation"
    token = audit.confirmations[-1][2]
    gate.feed_output(b"\r\x1b[2Kroot@box:~# sudo reboot")
    assert states[-1]["mode"] == "confirmation"
    assert gate.confirm(token)
    gate.feed_output(b"\r\x1b[2Kroot@box:~# sudo reboot")
    with patch("auditbox.relay.command_gate.time.monotonic", return_value=gate._line_read.output_at + 0.1):
        gate.poll()
    assert bytes(target).endswith(b"\r")
    assert audit.commands == ["sudo reboot"]
    assert states[-1]["mode"] == "running"


def test_late_state_subscriber_gets_latest_state_and_versions_are_monotonic(tmp_path, monkeypatch):
    from auditbox.prediction import engine as module
    monkeypatch.setattr(module, "AUDITBOX_HISTORY_REVIEW_ENABLED", False)
    engine = PredictionEngine(tmp_path / "state.db")
    messages = []
    class Socket:
        def sendall(self, data): messages.append(json.loads(data))
        def close(self): pass
    try:
        engine.register_session("one", "ssh")
        engine.on_terminal_state("one", {"mode": "auth", "version": 2})
        engine.on_terminal_state("one", {"mode": "command", "version": 1})
        engine.add_subscriber("one", Socket())
        assert messages[-1] == {"type": "terminal_state", "mode": "auth", "version": 2}
        assert engine.handle_control("one", {"type": "terminal_state", "mode": "command", "version": 99}) is False
    finally:
        engine.unregister_session("one")
        engine.close()
