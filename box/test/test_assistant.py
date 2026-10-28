import asyncio
import base64
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import auditbox.gateway.app as gateway_app
from auditbox.gateway.app import ActiveTerminalSession, AssistantAgentMessageRequest, ConnectRequest
from auditbox.db.models import Target
from auditbox.db.repositories import RecentConnectionRepository, TargetRepository
from auditbox.relay.input_frames import InputDecoder


class FakeTerminalWriter:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return False


def test_mcp_server_create_allows_automatic_tool_discovery() -> None:
    payload = gateway_app.AssistantMcpServerCreate(
        name="local-mcp",
        url="http://127.0.0.1:9000/mcp",
    )

    assert payload.tools == []


def test_assistant_message_uses_conversation_file_workspace() -> None:
    payload = AssistantAgentMessageRequest(message="请概括文件区中的附件")

    assert payload.model_dump() == {
        "message": "请概括文件区中的附件",
        "mode": "standard",
        "target": None,
    }


def test_assistant_conversation_can_bind_to_a_managed_device() -> None:
    payload = gateway_app.AssistantConversationCreate(device_id="device-core-1")

    assert payload.model_dump() == {
        "title": "新对话",
        "device_id": "device-core-1",
    }


def test_device_conversation_scope_requires_a_live_matching_terminal(monkeypatch) -> None:
    writer = FakeTerminalWriter()
    session = ActiveTerminalSession(
        "abcdef123456",
        ConnectRequest(
            protocol="ssh",
            device_type="server",
            device_profile="linux",
            host="192.168.10.1",
            port=22,
            device_id="device-core-1",
        ),
        writer,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        gateway_app,
        "_active_terminal_sessions",
        {session.session_id: session},
    )

    gateway_app._require_connected_device_scope(None)
    gateway_app._require_connected_device_scope("device-core-1")

    try:
        gateway_app._require_connected_device_scope("device-other")
    except gateway_app.HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("未连接设备不应允许读取设备会话")


def test_terminal_public_metadata_contains_stable_device_id() -> None:
    session = ActiveTerminalSession(
        "abcdef123456",
        ConnectRequest(
            protocol="ssh",
            device_type="server",
            device_profile="linux",
            host="192.168.10.1",
            port=22,
            device_id="device-core-1",
        ),
        FakeTerminalWriter(),  # type: ignore[arg-type]
    )

    assert session.public()["device_id"] == "device-core-1"


@pytest.mark.parametrize("protocol,address,port", [
    ("console", "/dev/ttyUSB0", 9600),
    ("console", "/dev/ttyUSB0", 115200),
    ("ssh", "192.0.2.1", 22),
    ("telnet", "192.0.2.1", 23),
])
def test_saved_connection_binds_terminal_and_assistant_scope(
    monkeypatch, protocol, address, port,
) -> None:
    events = []
    agent_calls = []

    async def verify(payload, username):
        events.append("verify")
        assert payload.protocol == protocol
        assert payload.address == address
        assert payload.port == port

    class Targets:
        def save_connected(self, target):
            assert events == ["verify"]
            events.append("save")
            return replace(target, id="device-core-1")

    class Recent:
        def remember(self, target, *, device_id, opened_at):
            assert target.id == device_id == "device-core-1"
            events.append("recent")

    async def agent(method, path, payload=None):
        agent_calls.append((method, path, payload))
        return {"data": []}

    monkeypatch.setattr(gateway_app, "require_authenticated", lambda _request: SimpleNamespace(username="test"))
    monkeypatch.setattr(gateway_app, "_prepare_and_verify_target", verify)
    monkeypatch.setattr(gateway_app, "TargetRepository", Targets)
    monkeypatch.setattr(gateway_app, "RecentConnectionRepository", Recent)
    monkeypatch.setattr(gateway_app, "_request_agent_json", agent)
    monkeypatch.setattr(gateway_app, "_pending_ws_sessions", {})
    monkeypatch.setattr(gateway_app, "_active_terminal_sessions", {})
    # No lifespan: repositories, serial/network verification and Agent are mocked.
    client = TestClient(gateway_app.app)
    response = client.post("/api/connections", json={
        "protocol": protocol, "address": address, "port": port,
        "device_type": "switch", "device_profile": "h3c_comware",
        "remark": "Console regression", "username": "test", "target_password": "mock-only",
    })
    assert response.status_code == 201
    assert events == ["verify", "save", "recent"]
    assert response.json()["device_id"] == response.json()["id"] == "device-core-1"
    connect_request = gateway_app._pop_pending_ws_session(response.json()["session_id"])
    assert connect_request.device_id == "device-core-1"
    assert connect_request.protocol == protocol
    assert connect_request.port == port
    assert client.get("/api/assistant/conversations?device_id=device-core-1").status_code == 409
    assert agent_calls == [], "Creating a ticket alone must not unlock device history"

    session = ActiveTerminalSession("abcdef123456", connect_request, FakeTerminalWriter())
    gateway_app._active_terminal_sessions[session.session_id] = session
    assert client.get("/api/assistant/conversations?device_id=device-core-1").status_code == 200
    assert client.post("/api/assistant/conversations", json={"device_id": "device-core-1"}).status_code == 201
    assert len(agent_calls) == 2
    assert "device_id=device-core-1" in agent_calls[0][1]
    assert agent_calls[1][2]["device_id"] == "device-core-1"
    assert client.get("/api/assistant/conversations?device_id=device-other").status_code == 409
    session.connected = False
    assert client.get("/api/assistant/conversations?device_id=device-core-1").status_code == 409
    assert len(agent_calls) == 2, "Disconnected or unrelated devices must not reach the Agent"


@pytest.mark.parametrize("protocol,address,port", [
    ("console", "/dev/ttyUSB0", 9600),
    ("ssh", "192.0.2.1", 22),
    ("telnet", "192.0.2.1", 23),
])
def test_failed_saved_connection_does_not_persist_device_or_issue_ticket(
    monkeypatch, protocol, address, port,
) -> None:
    async def fail_verification(_payload, _username):
        raise gateway_app.HTTPException(status_code=409, detail="Connection verification failed")

    def forbid_repository():
        raise AssertionError("Failed connection must not save a device or recent connection")

    monkeypatch.setattr(gateway_app, "require_authenticated", lambda _request: SimpleNamespace(username="test"))
    monkeypatch.setattr(gateway_app, "_prepare_and_verify_target", fail_verification)
    monkeypatch.setattr(gateway_app, "TargetRepository", forbid_repository)
    monkeypatch.setattr(gateway_app, "RecentConnectionRepository", forbid_repository)
    monkeypatch.setattr(gateway_app, "_pending_ws_sessions", {})
    response = TestClient(gateway_app.app).post("/api/connections", json={
        "protocol": protocol, "address": address, "port": port,
        "device_type": "switch", "device_profile": "h3c_comware",
    })
    assert response.status_code == 409
    assert gateway_app._pending_ws_sessions == {}


@pytest.fixture
def session_api(monkeypatch, tmp_path):
    targets = TargetRepository(tmp_path / "sessions.db")
    recent = RecentConnectionRepository(tmp_path / "sessions.db")
    verified = []
    agent_calls = []

    async def verify(payload, username):
        verified.append((payload, username))

    async def agent(method, path, payload=None):
        agent_calls.append((method, path, payload))
        return {"data": []}

    monkeypatch.setattr(gateway_app, "require_authenticated", lambda _request: SimpleNamespace(username="test"))
    monkeypatch.setattr(gateway_app, "_prepare_and_verify_target", verify)
    monkeypatch.setattr(gateway_app, "TargetRepository", lambda: targets)
    monkeypatch.setattr(gateway_app, "RecentConnectionRepository", lambda: recent)
    monkeypatch.setattr(gateway_app, "_request_agent_json", agent)
    monkeypatch.setattr(gateway_app, "_pending_ws_sessions", {})
    monkeypatch.setattr(gateway_app, "_active_terminal_sessions", {})
    return SimpleNamespace(client=TestClient(gateway_app.app), targets=targets,
                           recent=recent, verified=verified, agent_calls=agent_calls)


def saved_session_target(protocol="ssh", host="192.0.2.1", port=22):
    return Target(id="saved-device", protocol=protocol, host=host, port=port,
                  device_type="switch", device_profile="h3c_comware", remark="已保存设备",
                  notes="保存的备注", username="saved-user", status="saved")


@pytest.mark.parametrize("protocol,address,port", [
    ("console", "/dev/ttyUSB0", 9600),
    ("console", "/dev/ttyUSB0", 115200),
    ("ssh", "192.0.2.1", 22),
    ("telnet", "192.0.2.1", 23),
])
def test_quick_open_and_sidebar_bind_same_saved_device(session_api, protocol, address, port):
    api = session_api
    saved = api.targets.create(saved_session_target(protocol, address, port))
    for device_id in (None, saved.id):
        response = api.client.post("/api/sessions", json={
            "protocol": protocol, "address": address, "port": port, "device_id": device_id,
            "device_type": "server", "device_profile": "linux", "remark": "旧的临时连接",
            "username": "stale-user", "target_password": "mock-only",
        })
        assert response.status_code == 201
        body = response.json()
        assert body["id"] == body["device_id"] == saved.id
        assert (body["remark"], body["notes"], body["device_profile"]) == (
            saved.remark, saved.notes, saved.device_profile,
        )
        payload, username = api.verified[-1]
        assert payload.device_id == saved.id
        assert payload.device_profile == saved.device_profile
        assert username == saved.username
        assert payload.target_password == "mock-only"
        ticket = gateway_app._pop_pending_ws_session(body["session_id"])
        assert ticket.device_id == saved.id
        assert ticket.device_profile == saved.device_profile
        assert ticket.username == saved.username
        assert api.client.get(f"/api/assistant/conversations?device_id={saved.id}").status_code == 409
        session = ActiveTerminalSession(body["session_id"], ticket, FakeTerminalWriter())
        gateway_app._active_terminal_sessions[session.session_id] = session
        assert api.client.get(f"/api/assistant/conversations?device_id={saved.id}").status_code == 200
        assert api.client.post("/api/assistant/conversations", json={"device_id": saved.id}).status_code == 201
        assert api.agent_calls[-1][2]["device_id"] == saved.id
        session.connected = False
    assert api.agent_calls[0][1] == api.agent_calls[2][1], "Both entry points must query the same history"
    assert len(api.targets.list()) == 1
    assert api.targets.get(saved.id).remark == saved.remark
    assert api.targets.get(saved.id).last_connected_at
    assert [item.device_id for item in api.recent.list()] == [saved.id]


@pytest.mark.parametrize("protocol,address,port", [
    ("ssh", "192.0.2.2", 22), ("ssh", "192.0.2.1", 2222),
    ("telnet", "192.0.2.1", 22), ("console", "/dev/ttyUSB0", 115200),
])
def test_unmatched_endpoint_stays_temporary(session_api, protocol, address, port):
    api = session_api
    saved = (saved_session_target("console", "/dev/ttyUSB0", 9600)
             if protocol == "console" else saved_session_target())
    api.targets.create(saved)
    response = api.client.post("/api/sessions", json={
        "protocol": protocol, "address": address, "port": port,
        "device_type": "server", "device_profile": "linux", "remark": "临时连接",
    })
    assert response.status_code == 201
    assert response.json()["device_id"] is None
    assert response.json()["remark"] == "临时连接"
    assert gateway_app._pop_pending_ws_session(response.json()["session_id"]).device_id is None
    assert len(api.targets.list()) == 1
    assert api.targets.get(saved.id).last_connected_at is None


def test_explicit_saved_id_wins_over_stale_endpoint(session_api):
    api = session_api
    original = api.targets.create(saved_session_target())
    moved = api.targets.update(original.id, replace(original, host="192.0.2.2"))
    api.targets.create(replace(original, id="other-device"))
    payload = {"device_id": moved.id, "protocol": "ssh", "address": original.host, "port": 22,
               "device_type": "server", "device_profile": "linux"}
    response = api.client.post("/api/sessions", json=payload)
    assert response.status_code == 201
    assert response.json()["device_id"] == moved.id
    assert response.json()["address"] == moved.host
    assert api.verified[-1][0].address == moved.host
    # A deleted/stale explicit ID must fail, never silently select another device.
    payload["device_id"] = "deleted-device"
    assert api.client.post("/api/sessions", json=payload).status_code == 404
    assert len(api.verified) == 1


def test_failed_quick_open_does_not_issue_ticket_or_remember_connection(session_api, monkeypatch):
    api = session_api
    saved = api.targets.create(saved_session_target())

    async def fail_verification(payload, _username):
        assert payload.device_id == saved.id
        raise gateway_app.HTTPException(status_code=401, detail="模拟认证失败")

    monkeypatch.setattr(gateway_app, "_prepare_and_verify_target", fail_verification)
    response = api.client.post("/api/sessions", json={
        "protocol": "ssh", "address": saved.host, "port": saved.port,
        "device_type": "server", "device_profile": "linux",
    })
    assert response.status_code == 401
    assert gateway_app._pending_ws_sessions == {}
    assert api.recent.list() == []
    assert api.targets.get(saved.id) == saved


def test_agent_terminal_internal_api_accepts_only_local_gateway_address(monkeypatch) -> None:
    monkeypatch.setattr(gateway_app, "GATEWAY_HOST", "192.168.77.1")

    gateway_app._require_loopback_agent(
        SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    )
    gateway_app._require_loopback_agent(
        SimpleNamespace(client=SimpleNamespace(host="192.168.77.1"))
    )

    try:
        gateway_app._require_loopback_agent(
            SimpleNamespace(client=SimpleNamespace(host="192.168.77.28"))
        )
    except gateway_app.HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("管理端客户端不应直接调用 Agent 内部终端接口")


def test_assistant_status_proxies_agent_capabilities(monkeypatch) -> None:
    async def fake_request(*_args, **_kwargs):
        return {
            "status": "ok",
            "model_ready": True,
            "service": "智维宝盒智能助手",
            "capabilities": {"skills": 2, "mcp_servers": 1, "toolkits": 1},
        }

    monkeypatch.setattr(gateway_app, "require_authenticated", lambda _request: object())
    monkeypatch.setattr(gateway_app, "_request_agent_json", fake_request)
    response = asyncio.run(gateway_app.assistant_status(object()))

    assert response["ready"] is True
    assert response["capabilities"]["skills"] == 2
    assert response["capabilities"]["toolkits"] == 1


@pytest.mark.parametrize("enabled", [False, None, True, "unavailable"])
@pytest.mark.parametrize("operation", ["execute", "interrupt"])
def test_internal_agent_terminal_writes_obey_execution_switch(monkeypatch, enabled, operation):
    writes = []

    async def health(method, path, **kwargs):
        assert (method, path) == ("GET", "/api/v1/health")
        if enabled == "unavailable":
            raise gateway_app.HTTPException(status_code=503, detail="Agent 服务不可用")
        return {"capabilities": {"execution_enabled": enabled}}

    async def execute(*args):
        writes.append("execute")
        return {"status": "completed"}

    async def interrupt():
        writes.append("interrupt")
        return {"status": "interrupted"}

    monkeypatch.setattr(gateway_app, "_request_agent_json", health)
    monkeypatch.setattr(gateway_app, "_require_active_terminal",
                        lambda _: SimpleNamespace(execute=execute, interrupt=interrupt))
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    if operation == "execute":
        action = gateway_app.agent_terminal_execute(
            gateway_app.AgentTerminalCommandRequest(session_id="test", command="pwd"), request,
        )
    else:
        action = gateway_app.agent_terminal_interrupt(
            gateway_app.AgentTerminalInterruptRequest(session_id="test"), request,
        )
    if enabled is True:
        assert asyncio.run(action)["status"] in {"completed", "interrupted"}
        assert writes == [operation]
    else:
        with pytest.raises(gateway_app.HTTPException) as exc:
            asyncio.run(action)
        assert exc.value.status_code == (503 if enabled == "unavailable" else 409)
        assert writes == []


def test_assistant_capability_delete_proxies_agent(monkeypatch) -> None:
    calls = []

    async def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        return {"id": "mcp:user-one", "deleted": True}

    monkeypatch.setattr(gateway_app, "require_authenticated", lambda _request: object())
    monkeypatch.setattr(gateway_app, "_request_agent_json", fake_request)

    response = asyncio.run(
        gateway_app.assistant_capability_delete("mcp:user-one", object())
    )

    assert response == {"id": "mcp:user-one", "deleted": True}
    assert calls == [("DELETE", "/api/v1/capabilities/mcp:user-one", None)]


@pytest.mark.parametrize("suffix", ["", "/files/file-one"])
def test_assistant_delete_returns_no_content(monkeypatch, suffix) -> None:
    calls = []

    async def fake_request(method, path):
        calls.append((method, path))

    monkeypatch.setattr(gateway_app, "require_authenticated", lambda _request: object())
    monkeypatch.setattr(gateway_app, "_request_agent", fake_request)
    # No lifespan: deletion is mocked and must not open a real database/Agent.
    response = TestClient(gateway_app.app).delete(
        f"/api/assistant/conversations/conversation-one{suffix}"
    )

    assert response.status_code == 204
    assert response.content == b""
    assert calls == [("DELETE", f"/api/v1/conversations/conversation-one{suffix}")]


def test_assistant_saved_mcp_probe_proxies_agent(monkeypatch) -> None:
    calls = []

    async def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        return {"id": "mcp:user-one", "runtime_status": "available", "discovered_tools": ["ping"]}

    monkeypatch.setattr(gateway_app, "require_authenticated", lambda _request: object())
    monkeypatch.setattr(gateway_app, "_request_agent_json", fake_request)

    response = asyncio.run(gateway_app.assistant_saved_mcp_probe("mcp:user-one", object()))

    assert response["discovered_tools"] == ["ping"]
    assert calls == [("POST", "/api/v1/capabilities/mcp:user-one/probe", {})]


def test_terminal_confirmation_control_requires_exact_message() -> None:
    assert gateway_app._terminal_control_message(
        '{"type":"command_confirmation","confirmation_id":"token-one"}'
    ) == {
        "type": "command_confirmation",
        "confirmation_id": "token-one",
    }
    assert gateway_app._terminal_control_message("show version") is None
    assert gateway_app._terminal_control_message(
        '{"type":"command_confirmation","confirmation_id":""}'
    ) is None
    assert gateway_app._terminal_control_message(
        '{"type":"guide_audit_event","event":"guide_batch_started","details":{"batch_id":"one"}}'
    ) == {
        "type": "guide_audit_event",
        "event": "guide_batch_started",
        "details": {"batch_id": "one"},
    }


def test_agent_terminal_command_uses_existing_session_and_returns_output() -> None:
    async def scenario() -> None:
        writer = FakeTerminalWriter()
        session = ActiveTerminalSession(
            "abcdef123456",
            ConnectRequest(
                protocol="ssh",
                device_type="server",
                device_profile="linux",
                host="192.168.10.1",
                port=22,
            ),
            writer,  # type: ignore[arg-type]
            input_framing=True,
        )
        execution = asyncio.create_task(session.execute("show version", 2.0))
        await asyncio.sleep(0)
        assert InputDecoder(True).feed(bytes(writer.data)) == [(b"show version\n", "agent")]
        await session.record_review({
            "type": "operation_review",
            "history": [{"seq": 1, "command": "show version", "status": "executed"}],
        })
        await session.record_output(b"show version\r\nVersion 1.0\r\nswitch# ")
        result = await execution
        assert result["status"] == "completed"
        assert result["connection_status"] == "connected"
        assert "Version 1.0" in result["output"]
        transcript = session.transcript_after(0, 4_000)
        assert "[Agent命令/executed] show version" in transcript["content"]
        assert "Version 1.0" in transcript["content"]

    asyncio.run(scenario())


def test_destructive_command_does_not_close_shared_terminal_session() -> None:
    async def scenario() -> None:
        writer = FakeTerminalWriter()
        session = ActiveTerminalSession(
            "abcdef123456",
            ConnectRequest(
                protocol="ssh",
                device_type="server",
                device_profile="linux",
                host="192.168.10.1",
                port=22,
            ),
            writer,  # type: ignore[arg-type]
            input_framing=True,
        )
        first = asyncio.create_task(session.execute("rm -rf /tmp/example", 2.0))
        await asyncio.sleep(0)
        await session.record_output(b"root@host:# ")
        first_result = await first

        second = asyncio.create_task(session.execute("pwd", 2.0))
        await asyncio.sleep(0)
        await session.record_output(b"/root\r\nroot@host:# ")
        second_result = await second

        assert first_result["connection_status"] == "connected"
        assert second_result["connection_status"] == "connected"
        assert session.connected is True
        assert InputDecoder(True).feed(bytes(writer.data)) == [
            (b"rm -rf /tmp/example\n", "agent"), (b"pwd\n", "agent"),
        ]

    asyncio.run(scenario())


def test_terminal_transcript_exposes_commands_and_redacts_sensitive_input(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(gateway_app, "AUDIT_DIR", str(tmp_path))
    records = [
        {"event": "command", "command": "ip addr"},
        {
            "event": "terminal_output",
            "data_b64": base64.b64encode(b"eth0: UP\r\nroot@box:# ").decode(),
        },
        {"event": "terminal_input", "redacted": True, "data": "[敏感输入已隐藏]"},
    ]
    path = tmp_path / "abcdef123456.jsonl"
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")

    result = gateway_app._read_terminal_transcript("abcdef123456", 0, 4_000)

    assert "[命令] ip addr" in result["content"]
    assert "eth0: UP" in result["content"]
    assert "敏感输入已隐藏" in result["content"]
    assert result["cursor"] == 3
