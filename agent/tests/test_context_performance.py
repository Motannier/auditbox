from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from agno.models.message import Message
from agno.models.metrics import ModelMetrics, RunMetrics
from agno.run import RunContext
from agno.run.agent import RunInput, RunOutput
from agno.run.base import RunStatus
from agno.session.agent import AgentSession
from pydantic import ValidationError

from auditbox_agent.config import Settings
from auditbox_agent.context_manager import (
    CHECKPOINT_MESSAGE_NAME,
    CHECKPOINT_METADATA_KEY,
    CHECKPOINT_SESSION_DATA_KEY,
    REQUEST_CONTEXT_MESSAGE_NAME,
    RUNTIME_CONTEXT_MESSAGE_NAME,
    _estimate_text_tokens,
    _token_encoding,
    build_prefix_cache_anchor,
)
from auditbox_agent.runtime import AgentRuntime


def test_context_window_defaults_are_bounded() -> None:
    settings = Settings()

    assert Settings.model_fields["standard_model"].default == "qwen3.5-2b"
    assert settings.max_tool_calls_from_history == 0
    assert settings.compress_tool_results is False
    assert settings.model_context_window_tokens == 32_768
    assert settings.fast_model_context_window_tokens == 4096
    assert settings.ollama_keep_alive == "30m"
    assert settings.standard_model_warmup_enabled is True
    assert settings.context_max_input_ratio == 0.90
    assert settings.context_compact_trigger_ratio == 0.85
    assert settings.context_compact_target_ratio == 0.35
    assert settings.context_max_input_tokens == 29_491
    assert settings.context_compact_trigger_tokens == 27_852
    assert settings.context_compact_target_tokens == 11_468
    assert settings.context_max_input_tokens_for_mode("enhanced") == 29_491
    assert settings.context_max_input_tokens_for_mode("fast") == 29_491
    assert settings.context_recent_runs_min == 4
    assert settings.prefix_cache_anchor_tokens == 0
    assert settings.context_time_precision == "minute"


def test_only_loopback_standard_model_endpoint_is_warmed() -> None:
    assert Settings(
        standard_model_base_url="http://127.0.0.1:8081/v1"
    ).standard_model_endpoint_is_local
    assert not Settings(
        standard_model_base_url="https://api.deepseek.com/v1"
    ).standard_model_endpoint_is_local


@pytest.mark.asyncio
async def test_standard_model_warmup_uses_hidden_system_session(
    runtime: AgentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def warm(*_args: Any, **kwargs: Any) -> object:
        calls.append(kwargs)
        return object()

    runtime.settings.standard_model_base_url = "http://127.0.0.1:8081/v1"
    monkeypatch.setattr(runtime.agents["standard"], "arun", warm)

    assert await runtime.warm_standard_model() is True
    assert calls[0]["user_id"] == "system-warmup"
    assert calls[0]["session_id"] == "auditbox-standard-startup-warmup"
    assert calls[0]["metadata"] == {"source": "startup-warmup", "hidden": True}


def test_prefix_cache_anchor_is_deterministic_and_reaches_target() -> None:
    first = build_prefix_cache_anchor(1536)
    second = build_prefix_cache_anchor(1536)

    assert first == second
    assert len(_token_encoding().encode(first)) >= 1536
    assert "不得引用" in first


def test_token_estimator_is_offline_deterministic_and_conservative() -> None:
    assert _estimate_text_tokens("") == 0
    assert _estimate_text_tokens("abcdef") == 2
    assert _estimate_text_tokens("中文测试") == 4
    assert _estimate_text_tokens("ls -la") >= 3


def test_runtime_omits_unproven_cache_padding_by_default(
    runtime: AgentRuntime,
) -> None:
    instructions = runtime._agent_instructions()

    assert "<prefix_cache_anchor>" not in "\n".join(instructions)


def test_runtime_can_opt_in_to_cache_anchor(runtime: AgentRuntime) -> None:
    runtime.settings.prefix_cache_anchor_tokens = 64

    assert runtime._agent_instructions()[-1] == build_prefix_cache_anchor(64)


def test_history_tool_call_limit_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        Settings(max_tool_calls_from_history=-1)


def test_runtime_maps_bounded_history_policy_to_agno(runtime: AgentRuntime) -> None:
    assert runtime.agent.add_history_to_context is False
    # Agno retains its inert default, but it is never consulted because its
    # automatic history injection is disabled.
    assert runtime.agent.num_history_runs == 3
    assert runtime.agent.num_history_messages is None
    assert runtime.agent.max_tool_calls_from_history == 0
    assert runtime.agent.compress_tool_results is False
    assert runtime.agent.store_history_messages is False
    assert runtime.agent.add_datetime_to_context is False
    assert runtime.agent.pre_hooks == [runtime.context_manager.prepare_context]


def test_context_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        Settings(
            context_compact_target_ratio=0.5,
            context_compact_trigger_ratio=0.4,
            context_max_input_ratio=0.8,
        )


def test_context_budgets_scale_with_model_window() -> None:
    settings = Settings(model_context_window_tokens=32_768)

    assert settings.context_max_input_tokens == 29_491
    assert settings.context_compact_trigger_tokens == 27_852
    assert settings.context_compact_target_tokens == 11_468


def test_ollama_modes_share_context_but_toggle_thinking(tmp_path) -> None:
    runtime = AgentRuntime(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'ollama-agent.db'}",
            model_backend="ollama",
            skill_directory=tmp_path / "skills",
            knowledge_upload_directory=tmp_path / "uploads",
            knowledge_chroma_directory=tmp_path / "chromadb",
        )
    )
    try:
        standard_model = cast(Any, runtime.agents["standard"].model)
        enhanced_model = cast(Any, runtime.agents["enhanced"].model)
        assert standard_model.options["num_ctx"] == 32_768
        assert enhanced_model.options["num_ctx"] == 32_768
        assert enhanced_model.id == standard_model.id
        assert standard_model.request_params == {"think": False}
        assert enhanced_model.request_params == {"think": True}
        assert standard_model.keep_alive == "30m"
    finally:
        runtime.close()


def _completed_run(run_id: str, question: str, answer: str) -> RunOutput:
    return RunOutput(
        run_id=run_id,
        agent_id=AgentRuntime.agent_id,
        status=RunStatus.completed,
        messages=[
            Message(role="user", content=question),
            Message(role="assistant", content=answer),
        ],
    )


@pytest.mark.asyncio
async def test_runtime_time_is_final_and_persisted_for_prefix_reuse(
    runtime: AgentRuntime,
) -> None:
    session = AgentSession(
        session_id="context-session",
        agent_id=runtime.agent_id,
        user_id="operator",
        runs=[_completed_run("run-1", "问题1", "回答1")],
    )
    run_input = RunInput(input_content="问题2")
    run_context = RunContext(
        run_id="run-2",
        session_id=session.session_id,
        user_id="operator",
    )

    await runtime.context_manager.prepare_context(
        run_input=run_input,
        session=session,
        run_context=run_context,
        agent=runtime.agent,
    )

    messages = cast(list[Message], run_input.input_content)
    assert [message.content for message in messages[:2]] == ["问题1", "回答1"]
    assert messages[-2].content == "问题2"
    assert messages[-1].name == RUNTIME_CONTEXT_MESSAGE_NAME
    assert messages[-1].add_to_agent_memory is True
    assert "运行时间" in messages[-1].get_content_string()
    assert runtime.settings.default_timezone in messages[-1].get_content_string()
    assert run_context.metadata["context"]["strategy"] == "append_only_checkpoint"


@pytest.mark.asyncio
async def test_previous_model_messages_become_next_turn_prefix(runtime: AgentRuntime) -> None:
    first_session = AgentSession(
        session_id="prefix-session",
        agent_id=runtime.agent_id,
        user_id="operator",
        runs=[_completed_run("run-1", "问题1", "回答1")],
    )
    first_input = RunInput(input_content="问题2")
    await runtime.context_manager.prepare_context(
        run_input=first_input,
        session=first_session,
        run_context=RunContext(
            run_id="run-2",
            session_id=first_session.session_id,
            user_id="operator",
        ),
        agent=runtime.agent,
    )
    first_messages = cast(list[Message], first_input.input_content)

    persisted = [
        message.model_copy(deep=True) for message in first_messages if message.add_to_agent_memory
    ]
    persisted.append(Message(role="assistant", content="回答2"))
    second_session = AgentSession(
        session_id=first_session.session_id,
        agent_id=runtime.agent_id,
        user_id="operator",
        runs=[
            _completed_run("run-1", "问题1", "回答1"),
            RunOutput(
                run_id="run-2",
                agent_id=runtime.agent_id,
                status=RunStatus.completed,
                messages=persisted,
            ),
        ],
    )
    second_input = RunInput(input_content="问题3")
    await runtime.context_manager.prepare_context(
        run_input=second_input,
        session=second_session,
        run_context=RunContext(
            run_id="run-3",
            session_id=second_session.session_id,
            user_id="operator",
        ),
        agent=runtime.agent,
    )
    second_messages = cast(list[Message], second_input.input_content)

    first_shape = [(message.role, message.name, message.content) for message in first_messages]
    second_prefix = [
        (message.role, message.name, message.content)
        for message in second_messages[: len(first_messages)]
    ]
    assert second_prefix == first_shape


@pytest.mark.asyncio
async def test_dynamic_time_suffix_preserves_repeated_request_prefix(
    runtime: AgentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = iter(["时间-A", "时间-B"])
    monkeypatch.setattr(runtime.context_manager, "_runtime_context", lambda: next(values))

    inputs: list[list[Message]] = []
    for run_id in ("repeat-1", "repeat-2"):
        run_input = RunInput(input_content="检查网络")
        await runtime.context_manager.prepare_context(
            run_input=run_input,
            session=AgentSession(
                session_id=run_id,
                agent_id=runtime.agent_id,
                user_id="operator",
                runs=[],
            ),
            run_context=RunContext(
                run_id=run_id,
                session_id=run_id,
                user_id="operator",
            ),
            agent=runtime.agent,
        )
        inputs.append(cast(list[Message], run_input.input_content))

    first, second = inputs
    assert first[-2].content == second[-2].content == "检查网络"
    assert first[-1].name == second[-1].name == RUNTIME_CONTEXT_MESSAGE_NAME
    assert first[-1].content != second[-1].content
    # The repeated user request is reusable; only the final dynamic suffix
    # differs. With the old ordering the common message prefix was empty.
    assert [message.content for message in first[:-1]] == [
        message.content for message in second[:-1]
    ]


def test_runtime_context_uses_configured_timezone(runtime: AgentRuntime) -> None:
    context = runtime.context_manager._runtime_context()
    current_date = datetime.now(ZoneInfo(runtime.settings.default_timezone)).strftime("%Y-%m-%d")
    assert current_date in context


def test_empty_file_manifest_is_not_injected_into_unrelated_requests(
    runtime: AgentRuntime,
) -> None:
    message = runtime.context_manager._request_context({"conversation_files": []})
    assert message is None


@pytest.mark.asyncio
async def test_conversation_file_manifest_is_injected_as_private_request_context(
    runtime: AgentRuntime,
) -> None:
    session = AgentSession(
        session_id="file-session",
        agent_id=runtime.agent_id,
        user_id="operator",
        runs=[],
    )
    run_input = RunInput(input_content="请概括附件")
    run_context = RunContext(
        run_id="file-run",
        session_id=session.session_id,
        user_id="operator",
        metadata={
            "conversation_files": [
                {
                    "id": "file-42",
                    "name": "guide.md",
                    "character_count": 128,
                    "outline": ["网络排障", "路由检查"],
                }
            ],
            "display_message": "请概括附件",
        },
    )

    await runtime.context_manager.prepare_context(
        run_input=run_input,
        session=session,
        run_context=run_context,
        agent=runtime.agent,
    )

    messages = cast(list[Message], run_input.input_content)
    file_manifest_message = next(
        message for message in messages if message.name == REQUEST_CONTEXT_MESSAGE_NAME
    )
    assert "guide.md" in file_manifest_message.get_content_string()
    assert "file-42" in file_manifest_message.get_content_string()
    assert "网络排障" in file_manifest_message.get_content_string()
    assert "BOX-42" not in file_manifest_message.get_content_string()
    assert file_manifest_message.temporary is False
    assert file_manifest_message.add_to_agent_memory is True
    assert messages[-2].content == "请概括附件"
    assert messages[-1].name == RUNTIME_CONTEXT_MESSAGE_NAME


@pytest.mark.asyncio
async def test_unchanged_file_manifest_is_not_repeated_and_keeps_prompt_prefix(
    runtime: AgentRuntime,
) -> None:
    files = [{"id": "file-42", "name": "guide.md", "character_count": 6}]
    file_manifest = runtime.context_manager._request_context({"conversation_files": files})
    assert file_manifest is not None
    first_run = RunOutput(
        run_id="run-1",
        agent_id=runtime.agent_id,
        status=RunStatus.completed,
        messages=[
            file_manifest,
            Message(role="user", name=RUNTIME_CONTEXT_MESSAGE_NAME, content="时间1"),
            Message(role="user", content="问题1"),
            Message(role="assistant", content="回答1"),
        ],
    )
    session = AgentSession(
        session_id="file-prefix-session",
        agent_id=runtime.agent_id,
        user_id="operator",
        runs=[first_run],
    )
    run_input = RunInput(input_content="问题2")
    await runtime.context_manager.prepare_context(
        run_input=run_input,
        session=session,
        run_context=RunContext(
            run_id="run-2",
            session_id=session.session_id,
            user_id="operator",
            metadata={
                "mode": "standard",
                "conversation_files": files,
            },
        ),
        agent=runtime.agent,
    )

    messages = cast(list[Message], run_input.input_content)
    assert sum(message.name == REQUEST_CONTEXT_MESSAGE_NAME for message in messages) == 1
    assert [(message.role, message.name, message.content) for message in messages[:4]] == [
        (message.role, message.name, message.content) for message in first_run.messages
    ]


@pytest.mark.asyncio
async def test_file_manifest_is_restored_if_compaction_removes_its_original_run(
    runtime: AgentRuntime,
) -> None:
    runtime.settings.model_context_window_tokens = 600
    runtime.settings.context_max_input_ratio = 0.9
    runtime.settings.context_compact_trigger_ratio = 0.2
    runtime.settings.context_compact_target_ratio = 0.1
    runtime.settings.context_recent_runs_min = 1
    runtime.settings.context_token_estimate_multiplier = 1
    runtime.context_manager._count_tokens = cast(
        Any,
        lambda _agent, messages: sum(len(message.get_content_string()) for message in messages),
    )
    runtime.context_manager._fixed_context_tokens = cast(Any, lambda *_args: 0)

    async def fake_compactor(*_args: Any, **_kwargs: Any) -> RunOutput:
        return RunOutput(content="旧会话摘要")

    async def fake_save(_session: AgentSession) -> None:
        return None

    cast(Any, runtime.compactor_agent).arun = fake_compactor
    cast(Any, runtime.agent).asave_session = fake_save
    files = [{"id": "file-42", "name": "guide.md", "character_count": 6}]
    file_manifest = runtime.context_manager._request_context({"conversation_files": files})
    assert file_manifest is not None
    session = AgentSession(
        session_id="file-compact-session",
        agent_id=runtime.agent_id,
        user_id="operator",
        runs=[
            RunOutput(
                run_id="run-1",
                agent_id=runtime.agent_id,
                status=RunStatus.completed,
                messages=[
                    file_manifest,
                    Message(role="user", content="A" * 80),
                    Message(role="assistant", content="B" * 80),
                ],
            ),
            _completed_run("run-2", "C" * 80, "D" * 80),
        ],
    )
    run_input = RunInput(input_content="继续分析附件")
    await runtime.context_manager.prepare_context(
        run_input=run_input,
        session=session,
        run_context=RunContext(
            run_id="run-3",
            session_id=session.session_id,
            user_id="operator",
            metadata={"mode": "standard", "conversation_files": files},
        ),
        agent=runtime.agent,
    )

    messages = cast(list[Message], run_input.input_content)
    assert any(
        message.name == REQUEST_CONTEXT_MESSAGE_NAME and "file-42" in str(message.content)
        for message in messages
    )


@pytest.mark.asyncio
async def test_threshold_compaction_creates_stable_checkpoint(
    runtime: AgentRuntime,
) -> None:
    runtime.settings.model_context_window_tokens = 400
    runtime.settings.context_max_input_ratio = 0.9
    runtime.settings.context_compact_trigger_ratio = 0.2
    runtime.settings.context_compact_target_ratio = 0.1
    runtime.settings.context_recent_runs_min = 1
    runtime.settings.context_token_estimate_multiplier = 1

    runtime.context_manager._count_tokens = cast(
        Any,
        lambda _agent, messages: sum(len(message.get_content_string()) for message in messages),
    )
    runtime.context_manager._fixed_context_tokens = cast(Any, lambda *_args: 0)
    compactor_calls: list[str] = []

    async def fake_compactor(prompt: str, **_: Any) -> RunOutput:
        compactor_calls.append(prompt)
        return RunOutput(content="用户正在排查交换机告警；已确认接口 A 异常。")

    saved: list[AgentSession] = []

    async def fake_save(session: AgentSession) -> None:
        saved.append(session)

    cast(Any, runtime.compactor_agent).arun = fake_compactor
    cast(Any, runtime.agent).asave_session = fake_save
    session = AgentSession(
        session_id="compact-session",
        agent_id=runtime.agent_id,
        user_id="operator",
        runs=[
            _completed_run("run-1", "A" * 30, "B" * 30),
            _completed_run("run-2", "C" * 30, "D" * 30),
            _completed_run("run-3", "E" * 30, "F" * 30),
        ],
    )
    run_input = RunInput(input_content="继续")
    run_context = RunContext(
        run_id="run-4",
        session_id=session.session_id,
        user_id="operator",
    )

    await runtime.context_manager.prepare_context(
        run_input=run_input,
        session=session,
        run_context=run_context,
        agent=runtime.agent,
    )

    checkpoint = session.session_data[CHECKPOINT_SESSION_DATA_KEY]
    assert checkpoint["version"] == 1
    assert checkpoint["covered_run_ids"] == ["run-1", "run-2"]
    assert len(compactor_calls) == 1
    assert saved == [session]
    messages = cast(list[Message], run_input.input_content)
    assert messages[0].name == CHECKPOINT_MESSAGE_NAME
    assert messages[0].add_to_agent_memory is False
    assert [message.content for message in messages[1:3]] == ["E" * 30, "F" * 30]
    assert run_context.metadata["context"]["compacted"] is True
    assert CHECKPOINT_METADATA_KEY not in (session.metadata or {})


@pytest.mark.asyncio
async def test_legacy_checkpoint_is_migrated_without_cross_session_leakage(
    runtime: AgentRuntime,
) -> None:
    legacy = {
        "version": 1,
        "covered_run_ids": ["run-1"],
        "summary": "会话甲的内部摘要",
        "source_token_count": 20,
        "created_at": "2026-08-31T12:00:00+08:00",
    }
    session = AgentSession(
        session_id="legacy-session",
        agent_id=runtime.agent_id,
        user_id="operator",
        metadata={CHECKPOINT_METADATA_KEY: legacy},
        runs=[_completed_run("run-1", "问题1", "回答1")],
    )
    runtime.agent.metadata = session.metadata

    await runtime.context_manager.prepare_context(
        run_input=RunInput(input_content="继续"),
        session=session,
        run_context=RunContext(
            run_id="run-2",
            session_id=session.session_id,
            user_id="operator",
        ),
        agent=runtime.agent,
    )

    assert session.session_data[CHECKPOINT_SESSION_DATA_KEY]["summary"] == "会话甲的内部摘要"
    assert CHECKPOINT_METADATA_KEY not in (session.metadata or {})
    assert CHECKPOINT_METADATA_KEY not in (runtime.agent.metadata or {})

    foreign_session = AgentSession(
        session_id="foreign-session",
        agent_id=runtime.agent_id,
        user_id="operator",
        metadata={CHECKPOINT_METADATA_KEY: legacy},
        runs=[_completed_run("other-run", "问题甲", "回答甲")],
    )
    runtime.agent.metadata = foreign_session.metadata
    foreign_input = RunInput(input_content="继续")
    await runtime.context_manager.prepare_context(
        run_input=foreign_input,
        session=foreign_session,
        run_context=RunContext(
            run_id="other-run-2",
            session_id=foreign_session.session_id,
            user_id="operator",
        ),
        agent=runtime.agent,
    )

    assert CHECKPOINT_SESSION_DATA_KEY not in (foreign_session.session_data or {})
    assert CHECKPOINT_METADATA_KEY not in (foreign_session.metadata or {})
    assert cast(list[Message], foreign_input.input_content)[0].content == "问题甲"


def test_historical_tool_protocol_and_evidence_are_replayed(runtime: AgentRuntime) -> None:
    run = _completed_run("tool-run", "查询告警", "已查到 2 条告警")
    assert run.messages is not None
    run.messages[1].tool_calls = [{"id": "call-1", "function": {"name": "query"}}]
    run.messages.insert(
        2,
        Message(role="tool", tool_call_id="call-1", content='{"large":"payload"}'),
    )
    groups = runtime.context_manager._history_groups(
        AgentSession(session_id="tool-session", runs=[run]),
        None,
    )

    assert len(groups) == 1
    assert [message.role for message in groups[0][1]] == ["user", "assistant", "tool"]
    assert groups[0][1][1].tool_calls == run.messages[1].tool_calls
    assert groups[0][1][2].content == '{"large":"payload"}'
    assert all(message.from_history and not message.add_to_agent_memory for message in groups[0][1])


def test_request_snapshots_stay_in_place_to_preserve_prefix(runtime: AgentRuntime) -> None:
    first = _completed_run("context-1", "问题一", "回答一")
    second = _completed_run("context-2", "问题二", "回答二")
    assert first.messages is not None
    assert second.messages is not None
    first.messages.insert(
        1,
        Message(role="user", name=REQUEST_CONTEXT_MESSAGE_NAME, content="旧快照"),
    )
    second.messages.insert(
        1,
        Message(role="user", name=REQUEST_CONTEXT_MESSAGE_NAME, content="新快照"),
    )

    groups = runtime.context_manager._history_groups(
        AgentSession(session_id="context-session", runs=[first, second]),
        None,
    )
    context_messages = [
        message.content
        for _, messages in groups
        for message in messages
        if message.name == REQUEST_CONTEXT_MESSAGE_NAME
    ]

    assert context_messages == ["旧快照", "新快照"]


def test_checkpoint_summary_removes_ephemeral_current_time(runtime: AgentRuntime) -> None:
    summary = runtime.context_manager._sanitize_summary(
        "- 测试代号：ALPHA-731\n- 当前日期和时间：2026-08-31 17:57:36\n- 未解决事项：检查阀冷系统"
    )

    assert "ALPHA-731" in summary
    assert "2026-08-31" not in summary
    assert "阀冷系统" in summary


@pytest.mark.asyncio
async def test_compactor_never_receives_runtime_timestamps(
    runtime: AgentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    async def summarize(prompt: str, **_kwargs: Any) -> Any:
        prompts.append(prompt)
        return type("Summary", (), {"content": "保留运维结论"})()

    monkeypatch.setattr(runtime.context_manager.compactor_agent, "arun", summarize)

    result = await runtime.context_manager._summarize(
        None,
        [
            Message(role="user", content="检查路由"),
            Message(
                role="user",
                name=RUNTIME_CONTEXT_MESSAGE_NAME,
                content="运行时间：2026-09-10 11:05 Asia/Shanghai",
            ),
            Message(role="assistant", content="路由正常"),
        ],
    )

    assert result == "保留运维结论"
    assert "检查路由" in prompts[0]
    assert "路由正常" in prompts[0]
    assert "2026-09-10" not in prompts[0]


def test_cache_metrics_are_annotated(runtime: AgentRuntime) -> None:
    runtime.settings.model_backend = "openai"
    output = RunOutput(
        metadata={"source": "interactive"},
        metrics=RunMetrics(),
        input=RunInput(
            input_content=[
                Message(
                    role="user",
                    name=RUNTIME_CONTEXT_MESSAGE_NAME,
                    content="当前时间",
                ),
                Message(role="user", content="查询告警"),
            ]
        ),
    )
    output.metrics.input_tokens = 10_000
    output.metrics.cache_read_tokens = 7_500
    output.metrics.details = {
        "model": [ModelMetrics(provider_metrics={"prompt_cache_metrics_reported": 1})]
    }

    hook = runtime.agent.post_hooks[0]
    hook(run_output=output, metadata={"context": {"checkpoint_version": 0}})

    assert output.metadata["prompt_cache"] == {
        "backend": "openai",
        "measurement": "provider_reported",
        "input_tokens": 10_000,
        "cache_read_tokens": 7_500,
        "hit_ratio": 0.75,
        "run_type": "main_agent",
    }
    assert output.metadata["context"] == {"checkpoint_version": 0}
    assert output.input.input_content == "查询告警"


def test_ollama_cache_metrics_do_not_report_false_zero_hit_ratio(
    runtime: AgentRuntime,
) -> None:
    runtime.settings.model_backend = "ollama"
    output = RunOutput(
        metrics=RunMetrics(input_tokens=2000),
        input=RunInput(input_content="查询告警"),
    )

    runtime.context_manager.annotate_cache_metrics(run_output=output)

    assert output.metadata["prompt_cache"] == {
        "backend": "ollama",
        "measurement": "timing_only",
        "input_tokens": 2000,
        "cache_read_tokens": None,
        "hit_ratio": None,
        "prompt_eval_duration_ns": None,
        "run_type": "main_agent",
    }


def test_siliconflow_missing_cache_metrics_are_reported_as_unavailable(
    runtime: AgentRuntime,
) -> None:
    runtime.settings.model_backend = "siliconflow"
    output = RunOutput(
        metrics=RunMetrics(input_tokens=4275),
        input=RunInput(input_content="查询告警"),
    )

    runtime.context_manager.annotate_cache_metrics(run_output=output)

    assert output.metadata["prompt_cache"] == {
        "backend": "siliconflow",
        "measurement": "unavailable",
        "input_tokens": 4275,
        "cache_read_tokens": None,
        "hit_ratio": None,
        "run_type": "main_agent",
    }


def test_deepseek_multistage_cache_metrics_remain_provider_reported(
    runtime: AgentRuntime,
) -> None:
    runtime.settings.model_backend = "deepseek"
    output = RunOutput(
        metrics=RunMetrics(
            input_tokens=19_517,
            cache_read_tokens=8192,
            details={
                "model": [
                    ModelMetrics(
                        provider_metrics={"prompt_cache_metrics_reported": 2}
                    )
                ]
            },
        ),
        input=RunInput(input_content="查询 MCP"),
    )

    runtime.context_manager.annotate_cache_metrics(run_output=output)

    assert output.metadata["prompt_cache"] == {
        "backend": "deepseek",
        "measurement": "provider_reported",
        "input_tokens": 19_517,
        "cache_read_tokens": 8192,
        "hit_ratio": 0.4197,
        "run_type": "main_agent",
    }
