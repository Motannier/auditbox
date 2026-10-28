import time
from typing import Any, cast

from agno.models.message import Message
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from agno.session.agent import AgentSession
from fastapi.testclient import TestClient

from auditbox_agent.api_support import public_event, session_dict
from auditbox_agent.runtime import AgentRuntime


def test_single_user_conversation_lifecycle(client: TestClient) -> None:
    created = client.post("/api/v1/conversations", json={"title": "网络异常分析"})
    assert created.status_code == 201
    conversation_id = created.json()["session_id"]

    listed = client.get("/api/v1/conversations")
    assert [item["session_id"] for item in listed.json()["data"]] == [conversation_id]
    assert "runs" not in listed.json()["data"][0]

    renamed = client.patch(
        f"/api/v1/conversations/{conversation_id}", json={"title": "交换机接口分析"}
    )
    assert renamed.json()["title"] == "交换机接口分析"
    assert client.get(f"/api/v1/conversations/{conversation_id}").status_code == 200
    assert client.delete(f"/api/v1/conversations/{conversation_id}").status_code == 204
    assert client.get(f"/api/v1/conversations/{conversation_id}").status_code == 404


def test_descending_history_pagination_keeps_clock_rollback_sessions_accessible(client, runtime):
    for i in range(55):
        runtime.db.upsert_session(AgentSession(
            session_id=f"history-{i:03d}", agent_id=runtime.agent_id,
            user_id=runtime.settings.local_user_id,
            session_data={"session_name": f"历史{i}", "device_id": "saved-device"},
            created_at=1789430000 + i, updated_at=1789430000 + i,
        ))
    runtime.db.upsert_session(AgentSession(
        session_id="clock-rollback-session", agent_id=runtime.agent_id,
        user_id=runtime.settings.local_user_id,
        session_data={"session_name": "校时前的会话", "device_id": "saved-device"},
        runs=[RunOutput(run_id="saved-reply", agent_id=runtime.agent_id, content="已保存的回复")],
        created_at=1780670000, updated_at=1780670000,
    ))
    params = {"device_id": "saved-device", "limit": 50}
    first = client.get("/api/v1/conversations", params=params).json()
    second = client.get("/api/v1/conversations", params={**params, "page": 2}).json()
    assert first["has_more"] is True
    assert second["has_more"] is False
    assert first["order"] == "updated_at_desc"
    ids = [row["session_id"] for row in first["data"] + second["data"]]
    assert len(ids) == len(set(ids)) == 56
    assert ids[0] == "history-054"
    assert ids[-1] == "clock-rollback-session"
    detail = client.get("/api/v1/conversations/clock-rollback-session").json()
    assert detail["runs"][0]["content"] == "已保存的回复"
    assert client.get("/api/v1/conversations").json()["data"] == []


def test_history_sort_never_precedes_creation_and_ties_are_stable(client, runtime):
    from sqlalchemy import update
    for sid in ("tie-b", "tie-a"):
        runtime.db.upsert_session(AgentSession(
            session_id=sid, agent_id=runtime.agent_id, user_id=runtime.settings.local_user_id,
            session_data={"session_name": sid}, created_at=1789430000,
        ))
    with runtime.repository.engine.begin() as conn:
        conn.execute(update(runtime.repository.sessions).values(updated_at=1780670000))
    first = client.get("/api/v1/conversations", params={"limit": 1}).json()
    second = client.get("/api/v1/conversations", params={"limit": 1, "page": 2}).json()
    assert first["data"][0]["session_id"] == "tie-b"
    assert second["data"][0]["session_id"] == "tie-a"


def test_conversation_api_does_not_require_or_expose_user_id(client: TestClient) -> None:
    created = client.post("/api/v1/conversations", json={"title": "本地会话"})
    assert created.status_code == 201
    assert created.json()["user_id"] == "local-user"
    # Extra legacy fields are ignored, never used as an ownership selector.
    other = client.post(
        "/api/v1/conversations", json={"title": "仍是本地会话", "user_id": "forged"}
    )
    assert other.status_code == 201
    assert other.json()["user_id"] == "local-user"


def test_public_and_device_conversations_are_listed_separately(client: TestClient) -> None:
    public = client.post("/api/v1/conversations", json={"title": "公共会话"}).json()
    device = client.post(
        "/api/v1/conversations",
        json={"title": "核心交换机", "device_id": "device-core-1"},
    ).json()

    public_rows = client.get("/api/v1/conversations").json()["data"]
    device_rows = client.get(
        "/api/v1/conversations", params={"device_id": "device-core-1"}
    ).json()["data"]

    assert [row["session_id"] for row in public_rows] == [public["session_id"]]
    assert [row["session_id"] for row in device_rows] == [device["session_id"]]
    assert device_rows[0]["device_id"] == "device-core-1"


def test_message_rejects_a_different_device_scope(client: TestClient) -> None:
    conversation = client.post(
        "/api/v1/conversations",
        json={"title": "设备会话", "device_id": "device-one"},
    ).json()

    response = client.post(
        f"/api/v1/conversations/{conversation['session_id']}/messages",
        json={
            "message": "检查接口状态",
            "target": {
                "device_id": "device-two",
                "session_id": "abcdef123456",
                "address": "192.0.2.10",
            },
        },
    )

    assert response.status_code == 409


def test_partial_stream_is_persisted_when_model_fails(
    client: TestClient,
    runtime: AgentRuntime,
    monkeypatch: Any,
) -> None:
    class Event:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def to_dict(self) -> dict[str, Any]:
            return self.payload

    async def failed_stream(*_args: Any, **kwargs: Any) -> Any:
        run_id = kwargs["run_id"]
        yield Event({"event": "RunStarted", "run_id": run_id})
        yield Event(
            {
                "event": "RunContent",
                "run_id": run_id,
                "content": "已经生成的一半回答",
            }
        )
        raise RuntimeError("model stopped")

    monkeypatch.setattr(runtime.agents["standard"], "arun", failed_stream)
    conversation = client.post("/api/v1/conversations", json={"title": "异常回复"}).json()

    response = client.post(
        f"/api/v1/conversations/{conversation['session_id']}/messages",
        json={"message": "请执行检查"},
    )

    assert response.status_code == 200
    stored = client.get(f"/api/v1/conversations/{conversation['session_id']}").json()
    snapshots = stored["metadata"]["public_run_snapshots"]
    assert len(snapshots) == 1
    snapshot = next(iter(snapshots.values()))
    assert snapshot["input"] == "请执行检查"
    assert snapshot["content"] == "已经生成的一半回答"
    assert snapshot["status"] == "error"


def test_user_message_is_persisted_when_model_fails_before_first_event(
    client: TestClient,
    runtime: AgentRuntime,
    monkeypatch: Any,
) -> None:
    async def failed_before_event(*_args: Any, **_kwargs: Any) -> Any:
        if False:
            yield None
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(runtime.agents["standard"], "arun", failed_before_event)
    conversation = client.post("/api/v1/conversations", json={"title": "立即异常"}).json()

    response = client.post(
        f"/api/v1/conversations/{conversation['session_id']}/messages",
        json={"message": "这条用户消息必须保留"},
    )

    assert response.status_code == 200
    assert "RunAccepted" in response.text
    stored = client.get(f"/api/v1/conversations/{conversation['session_id']}").json()
    snapshot = next(iter(stored["metadata"]["public_run_snapshots"].values()))
    assert snapshot["input"] == "这条用户消息必须保留"
    assert snapshot["content"] == ""
    assert snapshot["status"] == "error"


def test_conversation_list_repository_does_not_load_history(runtime: AgentRuntime) -> None:
    now = int(time.time())
    runtime.db.upsert_session(
        AgentSession(
            session_id="large-history-session",
            agent_id=runtime.agent_id,
            user_id=runtime.settings.local_user_id,
            session_data={"session_name": "大会话"},
            metadata={"source": "api"},
            runs=[],
            created_at=now,
            updated_at=now,
        )
    )
    summaries = runtime.repository.list_conversations(
        runtime.settings.local_user_id, runtime.agent_id, None, 1, 20
    )
    assert set(summaries[0]) == {
        "session_id",
        "session_data",
        "created_at",
        "updated_at",
    }


def test_session_dict_filters_legacy_snapshots_owned_by_other_conversations() -> None:
    now = int(time.time())
    own_run = RunOutput(run_id="own-run", content="本会话回复")
    session = AgentSession(
        session_id="conversation-a",
        agent_id="agent",
        user_id="local-user",
        session_data={"session_name": "会话 A"},
        runs=[own_run],
        metadata={
            "public_run_snapshots": {
                "own-run": {
                    "run_id": "own-run",
                    "input": "本会话消息",
                    "created_at": now,
                },
                "foreign-run": {
                    "run_id": "foreign-run",
                    "conversation_id": "conversation-b",
                    "input": "其他会话消息",
                    "created_at": now,
                },
            },
            "public_timelines": {
                "own-run": [{"kind": "content", "content": "本会话回复"}],
                "foreign-run": [{"kind": "content", "content": "其他会话回复"}],
            },
        },
        created_at=now - 1,
        updated_at=now + 1,
    )

    serialized = session_dict(session)

    assert set(serialized["metadata"]["public_run_snapshots"]) == {"own-run"}
    assert set(serialized["metadata"]["public_timelines"]) == {"own-run"}


def test_health_reports_local_single_user(client: TestClient) -> None:
    data = client.get("/api/v1/health").json()
    assert data["model_ready"] is True
    assert data["auth_enabled"] is False
    assert data["single_user"] is True
    assert data["tool_approval"] == "required-for-enabled-tools"


def test_each_mode_always_uses_its_own_agent(
    runtime: AgentRuntime,
) -> None:
    assert runtime.agent_for_request(
        "enhanced",
        "请直接在终端执行 pwd && whoami",
        has_target=True,
    ) is runtime.agents["enhanced"]
    assert runtime.agent_for_request(
        "standard", "请执行 pwd", has_target=True
    ) is runtime.agents["standard"]


def test_legacy_fast_requests_use_enhanced_regardless_of_target(
    runtime: AgentRuntime,
) -> None:
    assert runtime.agent_for_request(
        "fast", "Nginx 中怎么添加反向代理？", has_target=True
    ) is runtime.agents["enhanced"]
    assert runtime.agent_for_request(
        "fast", "请执行 pwd", has_target=False
    ) is runtime.agents["enhanced"]
    assert runtime.agent_for_request(
        "fast", "请读取文件区里的附件", has_target=False
    ) is runtime.agents["enhanced"]
    assert runtime.agent_for_request(
        "fast", "请调用 MCP 工具查询", has_target=False
    ) is runtime.agents["enhanced"]


def test_title_generation_endpoint_uses_runtime_title_policy(
    client: TestClient, runtime: AgentRuntime
) -> None:
    created = client.post("/api/v1/conversations", json={"title": "新对话"}).json()

    async def fake_title(_: str) -> str:
        return "交换机告警分析"

    runtime.generate_conversation_title = cast(Any, fake_title)
    generated = client.post(
        f"/api/v1/conversations/{created['session_id']}/title",
        json={"first_message": "帮我分析今日告警"},
    )
    assert generated.json()["title"] == "交换机告警分析"


def test_knowledge_config_reports_disabled_embedding_state(client: TestClient) -> None:
    config = client.get("/api/v1/knowledge/config").json()
    assert config["enabled"] is False
    assert config["disabled_reason"] == "embedding_service_not_configured"
    assert config["storage"] == "chromadb"
    assert config["metadata_storage"] == "sqlite"
    assert config["upload_allowed_extensions"] == [".txt", ".md", ".html", ".htm", ".pdf"]
    assert "api_key" not in config


def test_internal_runtime_context_is_not_exposed_to_frontend() -> None:
    session = AgentSession(
        session_id="hidden-context",
        session_data={"session_name": "隐藏上下文", "_context_checkpoint": {"summary": "秘密"}},
        metadata={"_context_checkpoint": {"summary": "内部摘要"}},
        runs=[
            RunOutput(
                run_id="run-2",
                status=RunStatus.completed,
                messages=[
                    Message(role="user", name="_runtime_context", content="当前时间：2026-08-31"),
                    Message(role="user", content="查询接口"),
                    Message(role="assistant", content="暂无异常"),
                ],
            )
        ],
    )
    data = session_dict(session)
    assert "_context_checkpoint" not in str(data)
    assert "_runtime_context" not in str(data)
    assert "查询接口" in str(data)
    event = public_event({"event": "PreHookCompleted", "run_input": {"secret": True}})
    assert "run_input" not in event
