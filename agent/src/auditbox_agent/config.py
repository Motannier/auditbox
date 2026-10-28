from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpServerSettings(BaseModel):
    """One MCP server exposed to the harness agent."""

    name: str
    url: str
    transport: Literal["streamable-http", "sse"] = "streamable-http"
    headers: dict[str, str] = Field(default_factory=dict)
    # Empty means all server tools. A non-empty value is an optional allowlist;
    # every mounted tool still pauses for user confirmation before execution.
    tools: list[str] = Field(default_factory=list)
    enabled: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AUDITBOX_AGENT_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "AuditBox Agent"
    environment: Literal["development", "test", "production"] = "development"
    # This service has no authentication layer and must remain loopback-only.
    host: str = "127.0.0.1"
    port: int = 18765
    local_user_id: str = "local-user"
    gateway_internal_url: str = "http://127.0.0.1:18080"
    terminal_tool_timeout_seconds: float = 20.0

    database_url: str = "sqlite:///data/agent.sqlite3"
    standard_model: str = "minicpm5-2b"
    # Legacy settings are accepted for upgrades, but no longer route chat traffic.
    fast_model: str = "qwen3.5:0.8b"
    model_base_url: str | None = "http://127.0.0.1:18081/v1"
    # Optional per-mode endpoints allow two independent llama.cpp processes.
    # When unset, both modes continue to use model_base_url.
    standard_model_base_url: str | None = None
    fast_model_base_url: str | None = None
    model_api_key: SecretStr | None = None
    model_backend: Literal[
        "auto", "ollama", "siliconflow", "deepseek", "vllm", "sglang", "openai"
    ] = "openai"
    model_thinking_enabled: bool = False
    model_extra_body: dict[str, object] = Field(default_factory=dict)
    model_retries: int = 2
    # Initial value of the persisted execution switch. Existing installations
    # enter model Q&A on upgrade; only an explicit switch enables Agent tools.
    execution_enabled: bool = False
    tool_call_limit: int = 12
    max_tool_calls_from_history: int = 0
    compress_tool_results: bool = False

    # Standard mode runs the local 2B model with a 32K window. Keep the 0.8B
    # fast mode bounded separately so low-memory deployments do not allocate
    # a second 32K KV cache when users switch modes.
    model_context_window_tokens: int = 32_768
    fast_model_context_window_tokens: int = 4096
    # Prefix/KV reuse only survives while the Ollama runner remains loaded.
    # Thirty minutes covers a normal local troubleshooting session without
    # pinning both mode models in memory forever.
    ollama_keep_alive: str | float | None = "30m"
    # Warm only a loopback standard-model endpoint. This primes the local
    # llama.cpp/Ollama prefix cache without creating a user-visible message.
    standard_model_warmup_enabled: bool = True
    standard_model_warmup_timeout_seconds: float = 120.0
    # Compaction must precede rejection. The requested 90%/85% pair is
    # therefore represented as 85% compaction trigger and 90% hard ceiling.
    context_max_input_ratio: float = 0.90
    context_compact_trigger_ratio: float = 0.85
    context_compact_target_ratio: float = 0.35
    context_recent_runs_min: int = 4
    context_token_estimate_multiplier: float = 1.15
    context_time_precision: Literal["minute", "second"] = "minute"
    # Optional compatibility knob for providers that prove a minimum cache
    # prefix size. The previous 256-token inert padding cost tokens on every
    # request without providing a measurable benefit, so it is opt-in.
    prefix_cache_anchor_tokens: int = 0

    # Preserve the knowledge feature but keep it off until embeddings exist.
    knowledge_enabled: bool = False
    knowledge_chroma_directory: Path = Path("data/chromadb")
    knowledge_collection: str = "auditbox-knowledge"
    embedding_model: str = ""
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_dimensions: int = 1024
    knowledge_max_results: int = 5
    knowledge_candidate_results: int = 20
    knowledge_similarity_threshold: float = 0.5
    knowledge_vector_score_weight: float = 0.8
    knowledge_chunk_size: int = 512
    knowledge_chunk_overlap: int = 50
    knowledge_upload_directory: Path = Path("data/uploads")
    knowledge_upload_max_bytes: int = 25 * 1024 * 1024
    knowledge_upload_max_files: int = 100
    knowledge_upload_quota_bytes: int = 500 * 1024 * 1024
    knowledge_ingestion_timeout_seconds: int = 180
    knowledge_ingestion_concurrency: int = 1
    # Office formats stay optional instead of burdening the box by default.
    knowledge_upload_allowed_extensions: list[str] = Field(
        default_factory=lambda: [".txt", ".md", ".html", ".htm", ".pdf"]
    )

    mcp_servers: list[McpServerSettings] = Field(default_factory=list)
    skill_directory: Path = Path("data/skills")
    default_skill_directory: Path = Path("defaults/skills")
    workspace_directory: Path = Path("data/workspace")
    conversation_file_directory: Path = Path("data/conversations")
    conversation_file_max_bytes: int = 5 * 1024 * 1024
    conversation_file_max_files: int = 10
    conversation_file_max_characters: int = 1_000_000
    conversation_file_read_characters: int = 4_000
    qa_file_context_characters: int = Field(default=6_000, ge=256, le=20_000)
    generated_file_directory: Path = Path("data/generated")
    sql_tool_database_url: str = "sqlite:///data/tool-workspace.sqlite3"
    default_timezone: str = "Asia/Shanghai"
    telemetry_enabled: bool = False
    debug: bool = False

    @field_validator("database_url")
    @classmethod
    def sqlite_only(cls, value: str) -> str:
        if not value.startswith("sqlite"):
            raise ValueError("智维宝盒 Agent 仅支持 SQLite")
        return value

    @field_validator("knowledge_similarity_threshold", "knowledge_vector_score_weight")
    @classmethod
    def validate_probability(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("RAG 分数参数必须位于 0 到 1 之间")
        return value

    @field_validator("knowledge_chunk_size")
    @classmethod
    def validate_chunk_size(cls, value: int) -> int:
        if value < 64:
            raise ValueError("知识分块大小不能小于 64 字符")
        return value

    @field_validator("knowledge_chunk_overlap")
    @classmethod
    def validate_chunk_overlap(cls, value: int) -> int:
        if value < 0:
            raise ValueError("知识分块重叠不能小于 0")
        return value

    @field_validator("max_tool_calls_from_history")
    @classmethod
    def validate_history_tool_calls(cls, value: int) -> int:
        if value < 0:
            raise ValueError("历史工具调用数不能小于 0")
        return value

    @field_validator(
        "model_context_window_tokens",
        "fast_model_context_window_tokens",
        "context_recent_runs_min",
    )
    @classmethod
    def validate_positive_context_limits(cls, value: int) -> int:
        if value < 1:
            raise ValueError("上下文策略参数必须大于 0")
        return value

    @field_validator("prefix_cache_anchor_tokens")
    @classmethod
    def validate_optional_cache_anchor(cls, value: int) -> int:
        if value < 0:
            raise ValueError("缓存锚点 token 数不能小于 0")
        return value

    @field_validator("context_token_estimate_multiplier")
    @classmethod
    def validate_context_token_multiplier(cls, value: float) -> float:
        if value < 1:
            raise ValueError("token 估算安全系数不能小于 1")
        return value

    @field_validator(
        "context_max_input_ratio",
        "context_compact_trigger_ratio",
        "context_compact_target_ratio",
    )
    @classmethod
    def validate_context_ratio(cls, value: float) -> float:
        if not 0 < value < 1:
            raise ValueError("上下文比例必须位于 0 和 1 之间")
        return value

    def model_post_init(self, __context: object) -> None:
        if self.knowledge_chunk_overlap >= self.knowledge_chunk_size:
            raise ValueError("知识分块重叠必须小于分块大小")
        if not (
            0
            < self.context_compact_target_ratio
            < self.context_compact_trigger_ratio
            < self.context_max_input_ratio
            < 1
        ):
            raise ValueError("上下文比例必须满足 0 < target < trigger < max_input < 1")
        if self.knowledge_enabled and not self.embedding_model.strip():
            raise ValueError("启用知识库前必须配置 AUDITBOX_AGENT_EMBEDDING_MODEL")

    @property
    def context_max_input_tokens(self) -> int:
        return int(self.model_context_window_tokens * self.context_max_input_ratio)

    @property
    def context_compact_trigger_tokens(self) -> int:
        return int(self.model_context_window_tokens * self.context_compact_trigger_ratio)

    @property
    def context_compact_target_tokens(self) -> int:
        return int(self.model_context_window_tokens * self.context_compact_target_ratio)

    def model_context_window_tokens_for_mode(self, mode: str) -> int:
        return self.model_context_window_tokens

    def model_base_url_for_mode(self, mode: str) -> str | None:
        return self.standard_model_base_url or self.model_base_url

    @property
    def standard_model_endpoint_is_local(self) -> bool:
        from urllib.parse import urlparse

        hostname = urlparse(self.model_base_url_for_mode("standard") or "").hostname
        return hostname in {"127.0.0.1", "localhost", "::1"}

    def context_max_input_tokens_for_mode(self, mode: str) -> int:
        return int(self.model_context_window_tokens_for_mode(mode) * self.context_max_input_ratio)

    def context_compact_trigger_tokens_for_mode(self, mode: str) -> int:
        return int(
            self.model_context_window_tokens_for_mode(mode) * self.context_compact_trigger_ratio
        )

    def context_compact_target_tokens_for_mode(self, mode: str) -> int:
        return int(
            self.model_context_window_tokens_for_mode(mode) * self.context_compact_target_ratio
        )

    @property
    def resolved_model_backend(self) -> str:
        if self.model_backend != "auto":
            return self.model_backend
        if self.model_base_url and "siliconflow" in self.model_base_url.lower():
            return "siliconflow"
        if self.model_base_url and "deepseek" in self.model_base_url.lower():
            return "deepseek"
        if self.model_base_url and "11434" in self.model_base_url:
            return "ollama"
        return "openai"

    @property
    def resolved_model_extra_body(self) -> dict[str, object]:
        return self.model_extra_body_for(self.model_thinking_enabled)

    def model_extra_body_for(self, thinking_enabled: bool) -> dict[str, object]:
        body = dict(self.model_extra_body)
        if self.resolved_model_backend == "siliconflow":
            body["enable_thinking"] = thinking_enabled
        elif self.resolved_model_backend in {"vllm", "sglang", "openai"}:
            configured = body.get("chat_template_kwargs")
            template_kwargs = dict(configured) if isinstance(configured, dict) else {}
            template_kwargs["enable_thinking"] = thinking_enabled
            body["chat_template_kwargs"] = template_kwargs
        return body


@lru_cache
def get_settings() -> Settings:
    return Settings()
