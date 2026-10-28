from unittest.mock import patch

from openai.types.completion_usage import CompletionUsage, PromptTokensDetails

from auditbox_agent.config import Settings
from auditbox_agent.runtime import AgentRuntime, CacheAwareOpenAILike


def test_siliconflow_thinking_uses_top_level_parameter_without_budget() -> None:
    settings = Settings(
        database_url="sqlite:///test.db",
        knowledge_enabled=False,
        model_base_url="https://api.siliconflow.cn/v1",
        model_backend="auto",
        model_thinking_enabled=True,
        model_extra_body={},
    )

    assert settings.resolved_model_backend == "siliconflow"
    assert settings.resolved_model_extra_body == {"enable_thinking": True}
    assert "thinking_budget" not in settings.resolved_model_extra_body


def test_siliconflow_cache_metrics_preserve_provider_availability() -> None:
    model = CacheAwareOpenAILike(id="test", api_key="test")
    reported = model._get_metrics(
        CompletionUsage(
            prompt_tokens=100,
            completion_tokens=5,
            total_tokens=105,
            prompt_tokens_details=PromptTokensDetails(cached_tokens=75),
        )
    )
    unavailable = model._get_metrics(
        CompletionUsage(prompt_tokens=100, completion_tokens=5, total_tokens=105)
    )

    assert reported.cache_read_tokens == 75
    assert reported.provider_metrics == {"prompt_cache_metrics_reported": True}
    assert unavailable.provider_metrics == {"prompt_cache_metrics_reported": False}


def test_vllm_and_sglang_use_qwen_chat_template_thinking_parameter() -> None:
    for backend in ("vllm", "sglang", "openai"):
        settings = Settings(
            database_url="sqlite:///test.db",
            knowledge_enabled=False,
            model_base_url="http://127.0.0.1:8000/v1",
            model_backend=backend,
            model_thinking_enabled=True,
            model_extra_body={"custom": "preserved"},
        )

        assert settings.resolved_model_extra_body == {
            "custom": "preserved",
            "chat_template_kwargs": {"enable_thinking": True},
        }


def test_thinking_can_be_explicitly_disabled_for_compatible_backends() -> None:
    settings = Settings(
        database_url="sqlite:///test.db",
        knowledge_enabled=False,
        model_base_url="http://127.0.0.1:8000/v1",
        model_backend="sglang",
        model_thinking_enabled=False,
    )

    assert settings.resolved_model_extra_body == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


def test_modes_share_2b_and_only_differ_in_thinking(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        model_backend="openai",
        model_base_url="http://127.0.0.1:8091/v1",
        standard_model_base_url="http://127.0.0.1:8081/v1",
        fast_model_base_url="http://127.0.0.1:8082/v1",
        standard_model="qwen3.5-2b",
        fast_model="command-guard",
        model_thinking_enabled=True,
        model_extra_body={"chat_template_kwargs": {"custom": "preserved", "enable_thinking": True}},
        skill_directory=tmp_path / "skills",
        workspace_directory=tmp_path / "workspace",
        generated_file_directory=tmp_path / "generated",
    )
    with patch("auditbox_agent.runtime.seed_default_capabilities"):
        runtime = AgentRuntime(settings)

    try:
        standard = runtime.agents["standard"]
        enhanced = runtime.agents["enhanced"]
        for agent, thinking in ((standard, False), (enhanced, True)):
            assert agent.model.id == "qwen3.5-2b"
            assert agent.model.base_url == "http://127.0.0.1:8081/v1"
            assert agent.model.extra_body == {
                "chat_template_kwargs": {"custom": "preserved", "enable_thinking": thinking}
            }
        assert standard.instructions == enhanced.instructions
        assert standard.tools == enhanced.tools
        assert standard.tool_call_limit == enhanced.tool_call_limit
        assert runtime.agent_for_mode("fast") is enhanced
        assert settings.model_context_window_tokens_for_mode("enhanced") == 32768
        assert settings.model_context_window_tokens_for_mode("fast") == 32768
    finally:
        runtime.close()
