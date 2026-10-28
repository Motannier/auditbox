from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field

# Normalize old clients and saved approvals before validating the public modes.
AssistantMode = Annotated[
    Literal["standard", "enhanced"],
    BeforeValidator(lambda value: "enhanced" if value == "fast" else value),
]


class ConversationCreate(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=255)
    device_id: str | None = Field(default=None, min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class ConversationTitleGenerate(BaseModel):
    first_message: str = Field(min_length=1, max_length=100_000)


class TargetContext(BaseModel):
    session_id: str = Field(default="", max_length=255)
    device_id: str | None = Field(default=None, min_length=1, max_length=128)
    protocol: str = Field(default="", max_length=32)
    address: str = Field(default="", max_length=255)
    port: int | None = Field(default=None, ge=1, le=4_000_000)
    remark: str = Field(default="", max_length=255)
    device_type: str = Field(default="", max_length=128)
    device_profile: str = Field(default="", max_length=128)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    mode: AssistantMode = "standard"
    target: TargetContext | None = None
    stream: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunApprovalRequest(BaseModel):
    mode: AssistantMode = "standard"
    tools: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    approved: bool
    audit_actor: dict[str, Any] = Field(default_factory=dict)


class CapabilityPatch(BaseModel):
    enabled: bool


class McpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2_000)
    transport: Literal["streamable-http", "sse"] = "streamable-http"
    headers: dict[str, str] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True


class KnowledgePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2_000)
    metadata: dict[str, Any] | None = None
