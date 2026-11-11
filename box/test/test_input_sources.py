import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from auditbox.audit import logger
from auditbox.gateway.app import ActiveTerminalSession, ConnectRequest, _websocket_to_tcp
from auditbox.prediction import engine as prediction
from auditbox.relay.command_gate import CommandGate
from auditbox.relay.input_frames import InputDecoder, encode_input


def test_frames_keep_sources_across_fragmentation_and_coalescing():
    wire = encode_input(b"display version", "guide") + encode_input(b"\r") + encode_input(b"pwd\n", "agent")
    decoder = InputDecoder(True)
    result = []
    for byte in wire:
        result.extend(decoder.feed(bytes([byte])))
    assert result == [(b"display version", "guide"), (b"\r", "human"), (b"pwd\n", "agent")]
    assert InputDecoder().feed(wire) == [(wire, "human")]


@pytest.mark.parametrize("wire", [b'{"source":"bad","data":"YQ=="}\n', b'{"source":"guide","data":"???"}\n', b'[]\n'])
def test_malformed_frames_never_become_shell_input(wire):
    with pytest.raises(ValueError):
        InputDecoder(True).feed(wire)


@pytest.fixture
def audited_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(logger, "AUDIT_DIR", str(tmp_path))
    monkeypatch.setattr(logger, "CommandAuditRepository", lambda: SimpleNamespace(create=lambda row: None))
    commands, alerts, output = [], [], bytearray()
    audit = logger.AuditLogger("origin", "test", "ssh", command_hook=lambda cmd, meta: commands.append((cmd, meta)),
                               security_hook=lambda cmd, alert, blocked: alerts.append(alert))
    gate = CommandGate(audit, output.extend, lambda _: None, input_framing=True)
    return gate, commands, alerts, output


@pytest.mark.parametrize("source", ["guide", "agent"])
def test_programmatic_source_survives_human_enter_and_confirmation(audited_gate, source):
    gate, commands, alerts, output = audited_gate
    gate.feed_transport(encode_input(b"sudo reboot", source))
    gate.feed_transport(encode_input(b"\r"))
    assert not commands
    assert not bytes(output).endswith(b"\r")
    assert gate.confirm(alerts[-1]["confirmation_id"])
    assert commands[-1][1]["input_source"] == source
    assert commands[-1][1]["authorized"] is True
    gate.feed_transport(encode_input(b"pwd\r"))
    assert commands[-1][1]["input_source"] == "human"


def test_cancel_block_and_manual_clear_keep_audit(audited_gate):
    gate, commands, alerts, output = audited_gate
    gate.feed_transport(encode_input(b"sudo reboot\n", "agent"))
    gate.cancel_confirmation(alerts[-1]["confirmation_id"])
    assert commands[-1][1]["status"] == "cancelled"
    assert commands[-1][1]["input_source"] == "agent"
    gate.feed_transport(encode_input(b"rm -rf /\n", "guide"))
    assert commands[-1][1]["status"] == "blocked"
    assert commands[-1][1]["input_source"] == "guide"
    gate.feed_transport(encode_input(b"ls", "guide") + encode_input(b"\x15pwd\n"))
    assert commands[-1][0] == "pwd"
    assert commands[-1][1]["input_source"] == "human"


@pytest.mark.parametrize("profile,command", [
    ("linux", "cd /var/log"), ("h3c_comware", "display version"),
    ("huawei_vrp", "display version"), ("cisco_ios", "show version"), ("zte_zxr10", "show version"),
])
def test_only_humans_call_recommender_but_all_commands_are_audited(tmp_path, monkeypatch, profile, command):
    monkeypatch.setattr(prediction, "AUDITBOX_HISTORY_REVIEW_ENABLED", False)
    engine = prediction.PredictionEngine(tmp_path / "predict.db", enable_markov=True)
    try:
        engine.register_session("one", "ssh", device_type="server" if profile == "linux" else "switch", device_profile=profile)
        calls, reviews, messages, learns = [], [], [], []
        monkeypatch.setattr(prediction, "recommend_next_commands", lambda *args, **kwargs: calls.append(args) or [])
        monkeypatch.setattr(engine._predictor, "learn", lambda *args: learns.append(args))
        reviewer = SimpleNamespace(submit=lambda *args, **kwargs: reviews.append(args), close=lambda: None, discard=lambda _: None)
        engine._reviewer = engine._rules_reviewer = reviewer
        engine.add_subscriber("one", SimpleNamespace(sendall=messages.append, close=lambda: None))
        for source in ("guide", "agent", "human"):
            engine.on_command("one", command, {"input_source": source})
            assert json.loads(messages[-1])["input_source"] == source
        assert len(calls) == len(learns) == 1
        assert len(reviews) == 3
        assert [row["input_source"] for row in engine._sessions["one"]["history"]] == ["guide", "agent", "human"]
    finally:
        engine.close()


def test_gateway_routes_guide_and_human_input_on_same_stream():
    async def run():
        data, events = bytearray(), []
        async def drain(): pass
        writer = SimpleNamespace(write=data.extend, drain=drain, is_closing=lambda: False)
        request = ConnectRequest(protocol="ssh", device_type="server", device_profile="linux", host="192.0.2.1", port=22)
        session = ActiveTerminalSession("origin", request, writer, input_framing=True)
        incoming = iter([
            {"type": "websocket.receive", "text": json.dumps({"type": "terminal_input", "source": "guide", "data": "pwd"})},
            {"type": "websocket.receive", "text": "\r"},
            {"type": "websocket.receive", "text": json.dumps({"type": "terminal_input", "source": "agent", "data": "bad"})},
            {"type": "websocket.disconnect"},
        ])
        async def receive(): return next(incoming)
        async def send_json(payload): events.append(payload)
        await _websocket_to_tcp(SimpleNamespace(receive=receive, send_json=send_json), writer, shared_session=session)
        assert InputDecoder(True).feed(bytes(data)) == [(b"pwd", "guide"), (b"\r", "human")]
        assert events[-1]["type"] == "input_error"
        legacy = ActiveTerminalSession("old", request, writer)
        before = bytes(data)
        with pytest.raises(HTTPException):
            await legacy.execute("pwd", 0.01)
        assert bytes(data) == before
    asyncio.run(run())


def test_agent_notifies_browser_before_writing_and_bulk_paste_is_chunked():
    async def run():
        data, order = bytearray(), []
        def write(chunk):
            order.append("write")
            data.extend(chunk)
        async def drain(): pass
        async def notify(payload):
            order.append(payload["input_source"])
        writer = SimpleNamespace(write=write, drain=drain, is_closing=lambda: False)
        request = ConnectRequest(protocol="ssh", device_type="server", device_profile="linux", host="192.0.2.1", port=22)
        session = ActiveTerminalSession("source", request, writer, input_framing=True, input_notifier=notify)
        await session.execute("pwd", 0)
        assert order == ["agent", "write"]
        assert InputDecoder(True).feed(bytes(data)) == [(b"pwd\n", "agent")]
        data.clear()
        await session.write_browser(b"a" * 200000)
        frames = InputDecoder(True).feed(bytes(data))
        assert len(frames) == 4
        assert b"".join(chunk for chunk, source in frames) == b"a" * 200000
        assert all(source == "human" for _, source in frames)
    asyncio.run(run())
