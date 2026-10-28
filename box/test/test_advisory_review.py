"""Risk reviews cannot freeze relays, restore old locks, or grant execution."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from auditbox.db.models import TargetLock
from auditbox.db.repositories import TargetLockRepository
from auditbox.gateway import app as gateway
from auditbox.prediction import engine as engine_module
from auditbox.prediction.engine import PredictionEngine
from auditbox.relay.command_gate import CommandGate
from auditbox.security.alert import check
from auditbox.security.review_result import advisory_review
from test_command_gate import FakeAudit
from test_prediction_engine import CaptureSocket
from test_switch_history_review import CaptureReviewer


PROFILES = ["linux", "h3c_comware", "huawei_vrp", "cisco_ios", "zte_zxr10"]


def critical_review(**overrides):
    return {
        "type": "operation_review", "status": "locked", "risk": "critical",
        "reason": "可能破坏系统或配置", "intent": "危险操作", "evidence": [1],
        "confidence": 0.99, "reviewed_through_seq": 1, "lock": True, "locked": True,
        "lock_policy": {"allow_lock": True, "code": "verified_critical"},
        **overrides,
    }


@pytest.mark.parametrize("risk", ["low", "medium", "high", "critical", "unknown"])
def test_legacy_lock_flags_cannot_change_grade_or_evidence(risk):
    original = critical_review(risk=risk)
    result = advisory_review(original)
    assert result["status"] == "ready"
    assert not result["lock"] and not result["locked"] and not result["lock_policy"]["allow_lock"]
    for field in ("risk", "reason", "intent", "evidence", "confidence"):
        assert result[field] == original[field]
    assert original["lock"] and original["locked"] and original["lock_policy"]["allow_lock"]


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("protocol", ["ssh", "telnet", "console"])
def test_critical_reviews_keep_terminal_confirmation_and_reconnect_working(profile, protocol, tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "AUDITBOX_HISTORY_REVIEW_ENABLED", False)
    database = tmp_path / "legacy.db"
    repository = TargetLockRepository(database)
    old_lock = TargetLock("same-device", "旧锁定原因", "旧风险", '["old command"]', "old", "2026-01-01T00:00:00Z")
    repository.save(old_lock)
    engine = PredictionEngine(database)
    engine._reviewer, engine._rules_reviewer = CaptureReviewer(), CaptureReviewer()
    one, two = CaptureSocket(), CaptureSocket()
    target, client, events = bytearray(), bytearray(), []
    category = "server" if profile == "linux" else "switch"
    safe = "ls" if profile == "linux" else "display version" if profile in {"h3c_comware", "huawei_vrp"} else "show version"
    confirm = "sudo reboot" if profile == "linux" else "shutdown"
    block = "rm -rf /" if profile == "linux" else "write erase"

    class Audit(FakeAudit):
        def log_command(self, command):
            super().log_command(command)
            engine.on_command("one", command)

        def log_authorized_command(self, command, authorization):
            super().log_command(command)
            engine.on_command("one", command, {"authorized": True, "authorization": authorization})

        def log_blocked_command(self, command, alert):
            super().log_blocked_command(command, alert)
            engine.on_command("one", command, {"status": "blocked"})
            # A cached relay can still send the old extra flag. It is ignored.
            engine.on_security_alert("one", command, {**alert, "lock_terminal": True}, blocked=True)

    audit = Audit()
    gate = CommandGate(audit, target.extend, client.extend, device_profile=profile)
    try:
        for session, socket in (("one", one), ("two", two)):
            engine.register_session(session, protocol, "same-device", category, profile)
            engine.add_subscriber(session, socket)
            assert json.loads(socket.messages[-1])["status"] == "idle"
        engine.register_audit_handler("one", lambda *event: events.append(event))
        engine.register_confirmation_handler("one", gate.confirm, gate.cancel_confirmation)
        gate.feed((safe + "\r").encode())
        gate.feed((confirm + "\r").encode())
        token = audit.confirmations[-1][2]
        before_review, before_other = bytes(target), len(two.messages)
        engine._on_review_result("one", {**critical_review(), "review_token": engine._sessions["one"]["review"]["review_token"]})
        result = json.loads(one.messages[-1])
        assert result["risk"] == "critical" and not result["locked"] and not result["lock"]
        assert result["reason"] == "可能破坏系统或配置"
        assert bytes(target) == before_review  # No interrupt, clear-line, or execution.
        assert len(two.messages) == before_other
        assert gate._pending_confirmation[0] == token
        assert events[-1][0] in {"security_model_review", "security_rule_review"}
        assert events[-1][1]["risk"] == "critical"
        assert not engine.handle_control("one", {"type": "session_unlock_debug"})
        assert not engine.handle_control("two", {"type": "command_confirmation", "confirmation_id": token})
        assert not gate.confirm("wrong-token")
        gate.feed(b"unexpected-input\r")
        assert bytes(target) == before_review  # Pending confirmation still gates input.
        assert engine.handle_control("one", {"type": "command_confirmation", "confirmation_id": token})
        assert not gate.confirm(token)  # Never reusable.
        assert bytes(target).endswith((confirm + "\r").encode())
        assert engine._sessions["one"]["history"][-1]["authorized"]

        block_start = len(target)
        gate.feed((block + "\r").encode())
        assert check(block, device_profile=profile)["action"] == "block"
        assert not check(block, device_profile=profile)["lock_terminal"]
        assert audit.blocked[-1][0] == block
        assert b"\r" not in target[block_start:] and b"\n" not in target[block_start:]
        gate.feed((safe + "\r").encode())
        assert bytes(target).endswith((safe + "\r").encode())
        assert audit.commands[-1] == safe
        assert not any(json.loads(item).get("locked") for item in one.messages)
        assert b"\x03" not in target

        gate.feed((confirm + "\r").encode())
        next_token = audit.confirmations[-1][2]
        assert next_token != token
        engine._on_review_result("one", {**critical_review(reviewed_through_seq=4), "review_token": engine._sessions["one"]["review"]["review_token"]})
        assert gate.cancel_confirmation(next_token)
        assert not gate.confirm(next_token)
        assert audit.confirmation_results[-1] == (confirm, False)
        reviewer = engine._reviewer if profile in {"linux", "h3c_comware"} else engine._rules_reviewer
        assert len(reviewer.submissions) >= 4  # Review/recommendation flow continues.
        assert repository.list() == [old_lock]  # Neither deleted nor replaced.
        engine.unregister_session("one")
        engine.register_session("one", protocol, "same-device", category, profile)
        engine.add_subscriber("one", CaptureSocket())
        assert engine._sessions["one"]["review"]["status"] == "idle"
    finally:
        engine.close()
    restarted = PredictionEngine(database)
    try:
        restarted.register_session("new", protocol, "same-device", category, profile)
        assert restarted._sessions["new"]["review"]["status"] == "idle"
        assert repository.list() == [old_lock]
    finally:
        restarted.close()


class Writer:
    def __init__(self):
        self.data = bytearray()
    def write(self, data):
        self.data.extend(data)
    async def drain(self):
        pass


@pytest.mark.parametrize("with_control", [False, True])
def test_retired_unlock_control_is_consumed_not_forwarded_to_shell_or_old_backend(with_control):
    messages = iter([
        {"type": "websocket.receive", "text": '{"type":"session_unlock_debug"}'},
        {"type": "websocket.receive", "text": '{"type":"command_confirmation","confirmation_id":"once"}'},
        {"type": "websocket.receive", "text": "show version\r"},
        {"type": "websocket.disconnect"},
    ])
    class WebSocket:
        async def receive(self):
            return next(messages)
        async def send_json(self, payload):
            assert payload["type"] == "confirmation_error"
    target, control = Writer(), Writer() if with_control else None
    asyncio.run(gateway._websocket_to_tcp(WebSocket(), target, control))
    assert bytes(target.data) == b"show version\r"
    if control:
        assert json.loads(control.data) == {"type": "command_confirmation", "confirmation_id": "once"}


def test_gateway_normalizes_legacy_reviews_before_history_and_browser_delivery(monkeypatch):
    delivered, recorded, recent = [], [], []
    async def capture_review(payload):
        recorded.append(payload)
    async def send_json(payload):
        delivered.append(payload)
    monkeypatch.setattr(gateway, "_record_recent_operation", lambda request, result: recent.append(result))
    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data((json.dumps(critical_review()) + "\n").encode())
        reader.feed_eof()
        await gateway._prediction_to_websocket(reader, SimpleNamespace(send_json=send_json), None,
                                               SimpleNamespace(record_review=capture_review))
    asyncio.run(run())
    for output in (delivered, recorded, recent):
        assert len(output) == 1 and output[0]["risk"] == "critical"
        assert output[0]["status"] == "ready" and not output[0]["locked"] and not output[0]["lock"]


@pytest.mark.parametrize("risk,label", [("critical", "发现严重风险"), ("unknown", "风险待核验")])
def test_recent_activity_never_claims_a_box_was_locked(risk, label, monkeypatch):
    records = []
    monkeypatch.setattr(gateway, "RecentConnectionRepository", lambda: SimpleNamespace(record_operation=lambda **data: records.append(data)))
    request = SimpleNamespace(protocol="ssh", host="192.0.2.1", port=22)
    gateway._record_recent_operation(request, critical_review(risk=risk, history=[{"seq": 1}]))
    assert records[0]["result"] == label and records[0]["risk"] is True
