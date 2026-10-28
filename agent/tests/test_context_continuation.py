"""Regression coverage using real Agno/SQLite/SSE, with no model or device I/O."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from agno.agent import Agent
from agno.db.base import SessionType
from agno.models.message import Message
from agno.run import RunContext
from agno.run.agent import RunInput, RunOutput
from agno.run.base import RunStatus
from agno.session.agent import AgentSession
from agno.tools.function import Function
from openai import AsyncOpenAI

from auditbox_agent.api_support import session_dict
from auditbox_agent.context_manager import (
    CHECKPOINT_SESSION_DATA_KEY,
    REQUEST_CONTEXT_MESSAGE_NAME,
    RUN_STATE_MESSAGE_NAME,
    ContextCheckpoint,
    ConversationContextManager,
)
from auditbox_agent.runtime import CacheAwareOpenAILike


def sse_events(response):
    assert response.status_code == 200, response.text
    events = [
        json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
    ]
    assert not [event for event in events if event.get("event") == "RunError"], response.text
    return events


@pytest.fixture
def model_loop(runtime, client):
    """Seed a partly completed goal and simulate two further approved steps."""
    requests = []
    executed = []
    target = {"address": "192.0.2.10", "session_id": "mock-terminal", "protocol": "ssh"}
    session_id = client.post("/api/v1/conversations", json={"title": "多步任务"}).json()[
        "session_id"
    ]
    session = runtime.db.get_session(session_id, SessionType.AGENT)
    session.session_data[CHECKPOINT_SESSION_DATA_KEY] = ContextCheckpoint(
        summary="保持管理口不变 CONSTRAINT",
        covered_run_ids=["old-run"],
        source_token_count=100,
        created_at="2026-09-18T10:00:00+08:00",
    ).model_dump()
    session.runs = [
        RunOutput(
            run_id="goal-run",
            agent_id=runtime.agent_id,
            session_id=session_id,
            status=RunStatus.completed,
            messages=[
                runtime.context_manager._request_context({"target": target}),
                Message(
                    role="user", content="ORIGINAL_GOAL 先查版本，再查内存，最后查磁盘并一起总结。"
                ),
                Message(
                    role="assistant",
                    tool_calls=[
                        {
                            "id": "version-call",
                            "type": "function",
                            "function": {"name": "query", "arguments": '{"step":"version"}'},
                        }
                    ],
                ),
                Message(role="tool", tool_call_id="version-call", content="VERSION_EVIDENCE=1.0"),
                Message(role="assistant", content="版本已查，内存和磁盘尚未检查。"),
            ],
        )
    ]
    runtime.db.upsert_session(session)

    async def model_http(request):
        body = json.loads(request.content)
        requests.append(body)
        messages = json.dumps(body["messages"], ensure_ascii=False)
        # Deliberately fail if the resumed model lacks its task/evidence. This
        # exercises context transport, not real-model instruction compliance.
        for marker in ("ORIGINAL_GOAL", "CONSTRAINT", "192.0.2.10", "VERSION_EVIDENCE"):
            assert marker in messages, f"context lost: {marker}"
        outputs = "\n".join(
            str(message.get("content") or "")
            for message in body["messages"]
            if message["role"] == "tool"
        )
        if "reject" in outputs.lower() or "denied" in outputs.lower():
            delta = {"role": "assistant", "content": "操作已拒绝；内存和磁盘检查未完成。"}
            finish = "stop"
        elif "DISK_EVIDENCE" in outputs:
            delta = {"role": "assistant", "content": "版本1.0，内存42%，磁盘30%，三步检查完成。"}
            finish = "stop"
        else:
            step = "disk" if "MEMORY_EVIDENCE" in outputs else "memory"
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"call-{step}",
                        "type": "function",
                        "function": {"name": "query", "arguments": json.dumps({"step": step})},
                    }
                ],
            }
            finish = "tool_calls"
        chunks = [
            {
                "id": "mock",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "mock",
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": "mock",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "mock",
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            },
        ]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                "".join(
                    "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks
                )
                + "data: [DONE]\n\n"
            ),
        )

    def query(step: str) -> str:
        """Read fixture data only; never connect to or mutate a device."""
        executed.append(step)
        return {"memory": "MEMORY_EVIDENCE=42%", "disk": "DISK_EVIDENCE=30%"}[step]

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(model_http))

    def restart():
        # New manager/Agent instances with only SQLite state surviving.
        runtime.context_manager = ConversationContextManager(
            runtime.settings, runtime.compactor_agent
        )
        tool = Function.from_callable(query)
        tool.requires_confirmation = True
        agent = Agent(
            id=runtime.agent_id,
            db=runtime.db,
            model=CacheAwareOpenAILike(
                id="mock",
                async_client=AsyncOpenAI(
                    api_key="mock",
                    base_url="http://mock.invalid/v1",
                    http_client=http_client,
                ),
            ),
            tools=[tool],
            instructions=runtime._agent_instructions(),
            pre_hooks=[runtime.context_manager.prepare_context],
            post_hooks=[runtime.context_manager.annotate_cache_metrics],
            add_history_to_context=False,
            store_history_messages=False,
            store_tool_messages=True,
            tool_call_limit=12,
            telemetry=False,
        )
        runtime.agent = agent
        runtime.agents = {"standard": agent, "enhanced": agent}

    restart()

    def start():
        events = sse_events(
            client.post(
                f"/api/v1/conversations/{session_id}/messages",
                json={
                    "message": "继续",
                    "target": target,
                },
            )
        )
        return next(event for event in events if event["event"] == "RunPaused")

    def approve(pause, approved=True):
        return client.post(
            f"/api/v1/conversations/{session_id}/runs/{pause['run_id']}/continue",
            json={
                "approved": approved,
                "tools": [tool for tool in pause["tools"] if tool.get("requires_confirmation")],
            },
        )

    yield SimpleNamespace(
        requests=requests,
        executed=executed,
        session_id=session_id,
        start=start,
        approve=approve,
        restart=restart,
        target=target,
    )
    asyncio.run(http_client.aclose())


def test_multistep_approval_restart_preserves_task_and_exact_prefix(model_loop, runtime, client):
    case = model_loop
    first_pause = case.start()
    assert case.executed == []  # Approval still required before any operation.
    case.restart()
    events = sse_events(case.approve(first_pause))
    second_pause = next(event for event in events if event["event"] == "RunPaused")
    assert case.executed == ["memory"]
    case.restart()
    events = sse_events(case.approve(second_pause))
    assert any(event["event"] == "RunCompleted" for event in events)
    assert "三步检查完成" in str(events)
    assert case.executed == ["memory", "disk"]
    assert len(case.requests) == 3  # No extra planner/validator model requests.
    for before, after in zip(case.requests, case.requests[1:], strict=False):
        assert after["messages"][: len(before["messages"])] == before["messages"]
        assert after["tools"] == before["tools"]

    # A completed run stores its own messages only, not a duplicate full history.
    stored = runtime.db.get_session(case.session_id, SessionType.AGENT)
    finished = next(run for run in stored.runs if run.run_id == first_pause["run_id"])
    assert finished.input.input_content == "继续"
    assert not any(message.from_history for message in finished.messages)
    assert "ORIGINAL_GOAL" not in json.dumps(
        [m.to_dict() for m in finished.messages], ensure_ascii=False
    )
    assert {tool.tool_name for tool in finished.tools} == {"query"}
    assert len(finished.tools) == 2
    assert all(tool.result for tool in finished.tools)
    public = client.get(f"/api/v1/conversations/{case.session_id}").json()
    assert "_conversation_checkpoint" not in json.dumps(public)
    assert "三步检查完成" in json.dumps(public, ensure_ascii=False)

    # Next ordinary turn also extends the previous prompt instead of stripping
    # the tools and rewriting the history prefix.
    response = client.post(
        f"/api/v1/conversations/{case.session_id}/messages",
        json={
            "message": "汇总已经查到的结果，不要重新执行",
            "target": case.target,
        },
    )
    sse_events(response)
    assert (
        case.requests[-1]["messages"][: len(case.requests[-2]["messages"])]
        == case.requests[-2]["messages"]
    )
    assert case.executed == ["memory", "disk"]


def test_rejected_approval_does_not_execute_or_lose_context(model_loop):
    case = model_loop
    pause = case.start()
    case.restart()
    events = sse_events(case.approve(pause, approved=False))
    assert case.executed == []
    assert "未完成" in str(events)
    assert any(event["event"] == "RunCompleted" for event in events)
    assert len(case.requests) == 2
    assert case.approve(pause).status_code == 409  # Stale approval cannot rerun.


def test_approval_rejects_foreign_or_changed_operations(model_loop, client):
    case = model_loop
    pause = case.start()
    tools = deepcopy(pause["tools"])
    tools[0]["tool_args"] = {"step": "disk"}
    response = client.post(
        f"/api/v1/conversations/{case.session_id}/runs/{pause['run_id']}/continue",
        json={"approved": True, "tools": tools},
    )
    assert response.status_code == 422
    other = client.post("/api/v1/conversations", json={"title": "无关任务"}).json()["session_id"]
    response = client.post(
        f"/api/v1/conversations/{other}/runs/{pause['run_id']}/continue",
        json={
            "approved": True,
            "tools": pause["tools"],
        },
    )
    assert response.status_code == 404
    assert case.executed == []
    assert len(case.requests) == 1


@pytest.mark.asyncio
async def test_compaction_failure_never_silently_drops_history(runtime, monkeypatch):
    manager = runtime.context_manager
    runtime.settings.model_context_window_tokens = 4096
    runtime.settings.context_recent_runs_min = 1
    groups = [(str(i), [Message(role="user", content=f"约束{i}" + "甲" * 1200)]) for i in range(3)]
    session = AgentSession(session_id="failed-compaction")

    async def fail(*args, **kwargs):
        raise RuntimeError("summarizer unavailable")

    monkeypatch.setattr(manager, "_summarize", fail)
    checkpoint, retained, changed = await manager._compact_if_needed(
        runtime.agent, session, None, groups, Message(role="user", content="继续")
    )
    assert retained == groups and checkpoint is None and changed is False
    assert not session.session_data
    monkeypatch.setattr(manager, "_fixed_context_tokens", lambda *args: 0)
    session.runs = [
        RunOutput(run_id=run_id, status=RunStatus.completed, messages=messages)
        for run_id, messages in groups
    ]
    with pytest.raises(ValueError, match="输入预算"):
        await manager.prepare_context(
            RunInput(input_content="继续"),
            session,
            RunContext(run_id="new", session_id=session.session_id),
            runtime.agent,
        )
    assert len(session.runs) == 3


def test_partial_runs_keep_goals_and_real_evidence_but_not_unresolved_calls(runtime):
    for status in (RunStatus.error, RunStatus.cancelled, RunStatus.paused):
        run = RunOutput(
            run_id="partial",
            status=status,
            messages=[
                Message(role="user", content="完成检查和报告，不改管理口"),
                Message(
                    role="assistant",
                    tool_calls=[
                        {"id": "finished", "function": {"name": "query", "arguments": "{}"}},
                        {"id": "pending", "function": {"name": "query", "arguments": "{}"}},
                    ],
                ),
                Message(role="tool", tool_call_id="finished", content="故障接口 Gi1/0/24"),
                Message(role="tool", tool_call_id="orphan", content="不完整旧协议"),
            ],
        )
        session = AgentSession(session_id="partial-session", runs=[run])
        groups = runtime.context_manager._history_groups(session, None)
        messages = groups[0][1]
        assert messages[0].content == "完成检查和报告，不改管理口"
        assert [call["id"] for call in messages[1].tool_calls] == ["finished"]
        assert messages[2].content == "故障接口 Gi1/0/24"
        assert messages[-1].name == RUN_STATE_MESSAGE_NAME
        assert status.value in messages[-1].content
        assert "orphan" not in str([message.to_dict() for message in messages])
        assert RUN_STATE_MESSAGE_NAME not in str(
            session_dict(AgentSession(session_id="public", runs=[RunOutput(messages=messages)]))
        )


def test_dynamic_context_dedup_uses_latest_snapshot_not_any_previous_match(runtime):
    manager = runtime.context_manager
    context_a = manager._request_context({"target": {"address": "192.0.2.1"}})
    context_b = manager._request_context({"target": {"address": "192.0.2.2"}})
    history = [("a", [context_a]), ("b", [context_b])]
    assert manager._deduplicate_request_context(context_b, history) is None
    assert manager._deduplicate_request_context(context_a, history) is context_a


@pytest.mark.asyncio
async def test_removing_target_and_files_appends_empty_authoritative_state(runtime):
    old = runtime.context_manager._request_context(
        {
            "target": {"address": "192.0.2.1"},
            "conversation_files": [{"id": "file-1"}],
        }
    )
    session = AgentSession(
        session_id="cleared",
        runs=[
            RunOutput(
                run_id="old",
                status=RunStatus.completed,
                messages=[
                    old,
                    Message(role="user", content="查看附件"),
                    Message(role="assistant", content="已读"),
                ],
            )
        ],
    )
    new_input = RunInput(input_content="继续")
    await runtime.context_manager.prepare_context(
        new_input,
        session,
        RunContext(
            run_id="new",
            session_id=session.session_id,
            metadata={"target": None, "conversation_files": []},
        ),
        runtime.agent,
    )
    messages = new_input.input_content
    assert messages[0].content == old.content
    latest = [m for m in messages if m.name == REQUEST_CONTEXT_MESSAGE_NAME][-1].content
    assert "当前没有活动终端" in latest and "当前文件区为空" in latest


def test_tool_arguments_count_towards_input_budget(runtime):
    short = Message(
        role="assistant",
        tool_calls=[
            {
                "id": "call",
                "function": {"name": "query", "arguments": "{}"},
            }
        ],
    )
    long = short.model_copy(deep=True)
    long.tool_calls[0]["function"]["arguments"] = "甲" * 1000
    manager = runtime.context_manager
    assert (
        manager._count_tokens(runtime.agent, [long])
        > manager._count_tokens(runtime.agent, [short]) + 900
    )


def test_local_missing_cache_counter_is_not_reported_as_zero(runtime):
    from agno.models.metrics import RunMetrics

    runtime.settings.model_backend = "openai"
    output = RunOutput(metrics=RunMetrics(input_tokens=2000))
    runtime.context_manager.annotate_cache_metrics(output)
    assert output.metadata["prompt_cache"]["measurement"] == "unavailable"
    assert output.metadata["prompt_cache"]["hit_ratio"] is None


def test_resume_budget_guard_precedes_approved_tool_execution(model_loop, runtime):
    case = model_loop
    pause = case.start()
    runtime.settings.model_context_window_tokens = 128
    response = case.approve(pause)
    assert response.status_code == 409
    assert "尚未执行" in response.text
    assert case.executed == [] and len(case.requests) == 1


def test_cancelled_run_cannot_be_approved(model_loop, runtime):
    case = model_loop
    pause = case.start()
    session = runtime.db.get_session(case.session_id, SessionType.AGENT)
    run = next(run for run in session.runs if run.run_id == pause["run_id"])
    run.status = RunStatus.cancelled
    runtime.db.upsert_session(session)
    assert case.approve(pause).status_code == 409
    assert case.executed == []


def test_first_model_error_preserves_input_as_unfinished_task(runtime):
    session = AgentSession(
        session_id="early-error",
        runs=[
            RunOutput(
                run_id="early",
                status=RunStatus.error,
                input=RunInput(input_content="检查交换机所有接口并总结"),
            )
        ],
    )
    groups = runtime.context_manager._history_groups(session, None)
    assert groups[0][1][0].content == "检查交换机所有接口并总结"
    assert groups[0][1][-1].name == RUN_STATE_MESSAGE_NAME


@pytest.mark.parametrize("status", [RunStatus.error, RunStatus.cancelled, RunStatus.paused])
def test_persisted_string_run_status_keeps_unfinished_task(runtime, status):
    original = RunOutput(
        run_id="persisted", agent_id=runtime.agent_id, status=status,
        input=RunInput(input_content="检查交换机所有接口并总结"),
        messages=[Message(role="user", content="检查交换机所有接口并总结")],
    )
    row = original.to_dict()
    row["status"] = status.value
    restored = RunOutput.from_dict(row)
    session = AgentSession(session_id="from-database", runs=[restored])
    groups = runtime.context_manager._history_groups(session, None)
    assert groups[0][1][0].content == "检查交换机所有接口并总结"
    assert groups[0][1][-1].name == RUN_STATE_MESSAGE_NAME
    assert status.value in groups[0][1][-1].content


@pytest.mark.asyncio
async def test_oversized_summary_is_not_committed(runtime, monkeypatch):
    runtime.settings.model_context_window_tokens = 4096
    runtime.settings.context_recent_runs_min = 1
    manager = runtime.context_manager
    groups = [(str(i), [Message(role="user", content="甲" * 1200)]) for i in range(3)]
    session = AgentSession(session_id="oversized-summary")

    async def oversized(*args, **kwargs):
        return "乙" * 5000

    async def unexpected_save(*args, **kwargs):
        pytest.fail("An oversized checkpoint must not be persisted")

    monkeypatch.setattr(manager, "_summarize", oversized)
    monkeypatch.setattr(runtime.agent, "asave_session", unexpected_save)
    checkpoint, remaining, changed = await manager._compact_if_needed(
        runtime.agent,
        session,
        None,
        groups,
        Message(role="user", content="继续"),
    )
    assert checkpoint is None and remaining == groups and changed is False
    assert not session.session_data


def test_legacy_pause_uses_only_preceding_runs(runtime):
    before = RunOutput(
        run_id="before",
        status=RunStatus.completed,
        messages=[
            Message(role="user", content="原任务"),
            Message(role="assistant", content="步骤一已完成"),
        ],
    )
    paused = RunOutput(
        run_id="paused",
        session_id="legacy",
        status=RunStatus.paused,
        input=RunInput(input_content="继续"),
        messages=[Message(role="user", content="继续")],
    )
    after = RunOutput(
        run_id="after",
        status=RunStatus.completed,
        messages=[Message(role="user", content="另一个新任务")],
    )
    checkpoint = ContextCheckpoint(
        summary="不应混入后来的摘要",
        covered_run_ids=["after"],
        source_token_count=10,
        created_at="2026-09-18",
    )
    session = AgentSession(
        session_id="legacy",
        runs=[before, paused, after],
        session_data={
            CHECKPOINT_SESSION_DATA_KEY: checkpoint.model_dump(),
        },
    )
    restored = runtime.context_manager.restore_continuation_context(paused, session, runtime.agent)
    assert [m.content for m in restored.messages] == ["原任务", "步骤一已完成", "继续"]
    assert len(paused.messages) == 1  # No mutation of persisted input.


@pytest.mark.asyncio
async def test_compaction_cannot_cover_an_unfinished_approval(runtime, monkeypatch):
    runtime.settings.model_context_window_tokens = 4096
    runtime.settings.context_recent_runs_min = 1
    groups = [(str(i), [Message(role="user", content="甲" * 1200)]) for i in range(3)]
    session = AgentSession(
        session_id="pending",
        runs=[
            RunOutput(run_id="0", status=RunStatus.paused, messages=groups[0][1]),
            RunOutput(run_id="1", status=RunStatus.completed, messages=groups[1][1]),
            RunOutput(run_id="2", status=RunStatus.completed, messages=groups[2][1]),
        ],
    )

    async def unexpected_summary(*args, **kwargs):
        pytest.fail("Pending approvals must remain outside checkpoint coverage")

    monkeypatch.setattr(runtime.context_manager, "_summarize", unexpected_summary)
    checkpoint, remaining, changed = await runtime.context_manager._compact_if_needed(
        runtime.agent,
        session,
        None,
        groups,
        Message(role="user", content="继续"),
    )
    assert checkpoint is None and remaining == groups and changed is False
