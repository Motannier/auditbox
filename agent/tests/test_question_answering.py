"""Exercise Q&A through real Agno/SQLite/SSE, with simulated model responses."""

import json
from pathlib import Path

import httpx
import pytest
from agno.db.base import SessionType
from agno.models.message import Message
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from auditbox_agent.api import build_router
from auditbox_agent.capabilities import EXECUTION_CAPABILITY_ID
from auditbox_agent.config import Settings
from auditbox_agent.runtime import AgentRuntime


@pytest.fixture
def qa_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("AUDITBOX_AGENT_EXECUTION_ENABLED", raising=False)
    settings = Settings(
        _env_file=None, environment="test", database_url=f"sqlite:///{tmp_path / 'qa.db'}",
        model_backend="openai", model_base_url="http://model.invalid/v1", model_api_key="test",
        skill_directory=tmp_path / "skills",
        default_skill_directory=Path(__file__).parents[1] / "defaults/skills",
        workspace_directory=tmp_path / "workspace",
        generated_file_directory=tmp_path / "generated",
        conversation_file_directory=tmp_path / "conversations",
    )
    assert settings.execution_enabled is False
    runtime = AgentRuntime(settings)
    yield runtime
    runtime.close()


@pytest.fixture
def qa_client(qa_runtime):
    app = FastAPI()
    app.include_router(build_router(qa_runtime))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def model_requests(qa_runtime):
    requests = []

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        answer = "建议手动检查接口配置；请提供设备输出供分析。"
        base = {"id": "qa-mock", "created": 1, "model": "qa-mock"}
        if not body.get("stream"):
            return httpx.Response(200, json={
                **base, "object": "chat.completion", "choices": [{
                    "index": 0, "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }],
            })
        chunks = [
            {**base, "object": "chat.completion.chunk", "choices": [{
                "index": 0, "delta": {"role": "assistant", "content": answer},
                "finish_reason": None,
            }]},
            {**base, "object": "chat.completion.chunk", "choices": [{
                "index": 0, "delta": {}, "finish_reason": "stop",
            }]},
        ]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=(
            "".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
                    for chunk in chunks) + "data: [DONE]\n\n"
        ))

    for agent in qa_runtime.agents.values():
        agent.model.async_client = AsyncOpenAI(
            api_key="test", base_url="http://model.invalid/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )
    return requests


def create_conversation(client):
    return client.post("/api/v1/conversations", json={}).json()["session_id"]


def test_default_qa_has_no_model_tools_or_skill_routing(qa_runtime, qa_client):
    for agent in qa_runtime.agents.values():
        assert agent.tools == [] and agent.skills is None and agent.search_knowledge is False
        prompt = "\n".join(agent.instructions)
        assert "当前为模型问答模式" in prompt and "<capability_catalog>" not in prompt
    health = qa_client.get("/api/v1/health").json()
    assert health["tool_approval"] == "disabled"
    assert health["capabilities"]["execution_enabled"] is False
    assert health["capabilities"]["interaction_mode"] == "qa"
    assert health["capabilities"]["skills"] == 0
    assert qa_runtime.repository.get_capability(EXECUTION_CAPABILITY_ID)["enabled"] is False
    assert "未解疑问与缺少资料" in "\n".join(qa_runtime.compactor_agent.instructions)
    assert qa_client.delete(f"/api/v1/capabilities/{EXECUTION_CAPABILITY_ID}").status_code == 403


@pytest.mark.parametrize("mode,profile", [("standard", "linux"), ("enhanced", "h3c_comware")])
@pytest.mark.parametrize("stream", [False, True])
def test_command_question_has_no_tool_calls_and_keeps_device_type(
    qa_client, model_requests, mode, profile, stream,
):
    conversation = create_conversation(qa_client)
    response = qa_client.post(f"/api/v1/conversations/{conversation}/messages", json={
        "message": "请执行查询接口命令并说明原因", "mode": mode, "stream": stream,
        "target": {"address": "192.0.2.1", "protocol": "ssh", "device_type": "switch",
                   "device_profile": profile, "session_id": "mock-terminal"},
    })
    assert response.status_code == 200 and "建议手动检查" in response.text, response.text
    assert "ToolCallStarted" not in response.text and "RunPaused" not in response.text
    body = model_requests[-1]
    assert not body.get("tools")
    assert not any(m.get("tool_calls") or m["role"] == "tool" for m in body["messages"])
    prompt = json.dumps(body["messages"], ensure_ascii=False)
    assert profile in prompt and "Agent 执行已关闭" in prompt
    assert body["chat_template_kwargs"]["enable_thinking"] is (mode == "enhanced")


def test_switch_gates_tools_and_persists_across_restart(qa_runtime, qa_client):
    assert qa_client.patch("/api/v1/capabilities/tool:file-generation",
                           json={"enabled": True}).status_code == 200
    qa_runtime.repository.upsert_capability(
        "mcp:offline", "mcp", "offline", True,
        {"url": "http://offline.invalid/mcp", "tools": ["run_command"]}, "user", 1,
    )
    qa_runtime.reload_capabilities()
    assert qa_runtime.local_tools == qa_runtime.mcp_tools == []
    assert all(not row["active"] for row in qa_runtime.capability_details())
    assert qa_client.patch(f"/api/v1/capabilities/{EXECUTION_CAPABILITY_ID}",
                           json={"enabled": True}).status_code == 200
    assert qa_runtime.settings.execution_enabled and qa_runtime.mcp_tools and qa_runtime.local_tools
    assert qa_runtime.agent.skills is not None
    assert "未完成步骤与阻塞" in "\n".join(qa_runtime.compactor_agent.instructions)
    tools = qa_runtime.terminal_workspace_tools
    assert tools in qa_runtime.agent.tools
    assert tools.functions["send_terminal_command"].requires_confirmation
    assert tools.functions["interrupt_terminal"].requires_confirmation
    assert qa_client.patch(f"/api/v1/capabilities/{EXECUTION_CAPABILITY_ID}",
                           json={"enabled": False}).status_code == 200
    recreated = AgentRuntime(qa_runtime.settings.model_copy(update={"execution_enabled": True}))
    try:
        assert recreated.settings.execution_enabled is False and recreated.agent.tools == []
        assert recreated.repository.get_capability("tool:file-generation")["enabled"] is True
    finally:
        recreated.close()


def test_active_run_keeps_switch_and_registry_consistent(qa_runtime, qa_client):
    qa_runtime.register_request_run("active", "standard")
    try:
        response = qa_client.patch(f"/api/v1/capabilities/{EXECUTION_CAPABILITY_ID}",
                                   json={"enabled": True})
        assert response.status_code == 409
        assert qa_runtime.settings.execution_enabled is False
        assert qa_runtime.repository.get_capability(EXECUTION_CAPABILITY_ID)["enabled"] is False
    finally:
        qa_runtime.unregister_active_run("active")


def test_disabled_execution_blocks_old_approval_and_terminal_calls(
    qa_runtime, qa_client, monkeypatch,
):
    conversation = create_conversation(qa_client)
    session = qa_runtime.db.get_session(conversation, SessionType.AGENT)
    session.runs = [RunOutput(
        run_id="old-pause", status=RunStatus.paused, session_id=conversation,
        tools=[ToolExecution(tool_name="send_terminal_command", tool_call_id="old-call",
                             tool_args={"command": "pwd"}, requires_confirmation=True)],
    )]
    qa_runtime.db.upsert_session(session)
    response = qa_client.post(
        f"/api/v1/conversations/{conversation}/runs/old-pause/continue",
        json={"approved": True, "tools": [{"tool_call_id": "old-call"}]},
    )
    assert response.status_code == 409

    def unexpected_http(*args, **kwargs):
        raise AssertionError("Disabled terminal tools must not reach the Gateway")

    monkeypatch.setattr(httpx, "Client", unexpected_http)
    tools = qa_runtime.terminal_workspace_tools
    assert json.loads(tools.send_terminal_command("pwd", "target"))["status"] == "error"
    assert json.loads(tools.interrupt_terminal("target"))["status"] == "error"


def test_agent_history_is_inert_evidence_in_qa(qa_runtime, qa_client, model_requests):
    conversation = create_conversation(qa_client)
    session = qa_runtime.db.get_session(conversation, SessionType.AGENT)
    session.runs = [RunOutput(run_id="old", status=RunStatus.completed, messages=[
        Message(role="user", content="查看接口"),
        Message(role="assistant", tool_calls=[{
            "id": "old-call", "type": "function",
            "function": {"name": "send_terminal_command", "arguments": "{}"},
        }]),
        Message(role="tool", tool_call_id="old-call", content="OLD_EVIDENCE: 接口 down"),
        Message(role="assistant", content="之前接口未连接"),
    ], agent_id=qa_runtime.agent_id, session_id=conversation)]
    qa_runtime.db.upsert_session(session)
    response = qa_client.post(f"/api/v1/conversations/{conversation}/messages",
                              json={"message": "为什么接口不通？"})
    assert "RunError" not in response.text
    messages = model_requests[-1]["messages"]
    assert "OLD_EVIDENCE" in json.dumps(messages, ensure_ascii=False)
    assert all(m["role"] != "tool" and not m.get("tool_calls") for m in messages)


def test_attachment_context_is_bounded_scoped_and_private(qa_runtime, qa_client, model_requests):
    conversation, other = create_conversation(qa_client), create_conversation(qa_client)
    long_text = "无关内容。" * 2000 + "\n\n端口关闭原因 ATTACHMENT_EVIDENCE：配置为 shutdown。"
    for session_id, text in [(conversation, long_text), (other, "OTHER_PRIVATE_EVIDENCE")]:
        response = qa_client.post(f"/api/v1/conversations/{session_id}/files",
                                  files={"file": ("manual.txt", text.encode(), "text/plain")})
        assert response.status_code == 201, response.text
    qa_client.post(
        f"/api/v1/conversations/{conversation}/messages", json={"message": "SSH 是什么？"}
    )
    assert "ATTACHMENT_EVIDENCE" not in json.dumps(model_requests[-1])
    response = qa_client.post(f"/api/v1/conversations/{conversation}/messages",
                              json={"message": "附件 manual.txt 中端口关闭原因是什么？"})
    assert "RunError" not in response.text
    prompt = json.dumps(model_requests[-1]["messages"], ensure_ascii=False)
    assert "ATTACHMENT_EVIDENCE" in prompt and "OTHER_PRIVATE_EVIDENCE" not in prompt
    assert "可能不完整" in prompt
    excerpts = qa_runtime.conversation_files.question_excerpts(conversation, "附件端口关闭")
    assert sum(len(item.get("content", "")) for item in excerpts) <= 6000
    public = qa_client.get(f"/api/v1/conversations/{conversation}").text
    assert "qa_file_excerpts" not in public and "ATTACHMENT_EVIDENCE" not in public


def test_optional_knowledge_retrieval_uses_no_model_tools(
    qa_runtime, qa_client, model_requests, monkeypatch,
):
    qa_runtime.knowledge = object()

    async def knowledge(query):
        return [{"name": "运维资料", "content": "KNOWLEDGE_EVIDENCE：使用专用管理 VLAN。"}]

    monkeypatch.setattr(qa_runtime, "_retrieve_knowledge", knowledge)
    conversation = create_conversation(qa_client)
    response = qa_client.post(f"/api/v1/conversations/{conversation}/messages",
                              json={"message": "管理网应该如何设置？"})
    assert "RunError" not in response.text
    assert "KNOWLEDGE_EVIDENCE" in json.dumps(model_requests[-1]["messages"])
    assert not model_requests[-1].get("tools")


@pytest.mark.parametrize("stream", [False, True])
def test_qa_budget_rejects_large_input_before_model(qa_runtime, qa_client, model_requests, stream):
    qa_runtime.settings.model_context_window_tokens = 1000
    conversation = create_conversation(qa_client)
    response = qa_client.post(f"/api/v1/conversations/{conversation}/messages",
                              json={"message": "非常长的问题" * 1000, "stream": stream})
    assert "超过可用输入预算" in response.text
    if stream:
        assert "RunError" in response.text
    else:
        assert response.json()["status"] == RunStatus.error.value
    assert model_requests == []
