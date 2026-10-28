"""Presentation and SSE helpers shared by the business API routers."""

import json
import time
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any, cast

from agno.db.base import SessionType
from agno.knowledge.content import Content
from agno.session.agent import AgentSession
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from starlette.concurrency import run_in_threadpool

from auditbox_agent.runtime import AgentRuntime

INTERNAL_CONTEXT_MESSAGE_NAMES = {
    "_runtime_context",
    "_conversation_checkpoint",
    "_request_context",
    "_previous_run_state",
    "_historical_tool_result",
}

TOOL_DISPLAY_NAMES = {
    "search_knowledge_base": "知识库检索",
    "get_skill_instructions": "业务技能加载",
    "get_skill": "业务技能加载",
    "terminal_input": "终端命令输入",
    "send_terminal_command": "终端命令输入",
    "list_installed_skills": "已安装 Skill 查询",
    "create_skill": "创建并挂载 Skill",
    "register_workspace_skill": "注册工作区 Skill",
    "set_skill_enabled": "Skill 启停设置",
}

PUBLIC_TIMELINES_KEY = "_public_timelines"
PUBLIC_RUN_SNAPSHOTS_KEY = "_public_run_snapshots"


def tool_display_name(tool_name: str | None) -> str:
    """Return a stable user-facing Chinese label for a technical tool name."""
    normalized = str(tool_name or "").strip()
    if not normalized:
        return "业务工具"
    direct = TOOL_DISPLAY_NAMES.get(normalized)
    if direct:
        return direct
    # MCPTools prefixes names with the configured server name. Match known
    # generic tools from the end so changing the server alias does not affect UI.
    for catalog_name, display_name in sorted(
        TOOL_DISPLAY_NAMES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if normalized.endswith(f"_{catalog_name}"):
            return display_name
    return normalized.replace("_", " ")


def event_tool_name(event: dict[str, Any]) -> str | None:
    tool = event.get("tool") or event.get("tool_call") or event
    if not isinstance(tool, dict):
        return None
    value = tool.get("tool_name") or tool.get("name")
    if value is None and isinstance(tool.get("function"), dict):
        value = tool["function"].get("name")
    return str(value) if value else None


def _session_run_ids(session: AgentSession) -> set[str]:
    result: set[str] = set()
    for run in session.runs or []:
        value = run.get("run_id") if isinstance(run, dict) else getattr(run, "run_id", None)
        if value:
            result.add(str(value))
    return result


def public_history(session: AgentSession) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return UI history owned by this session, migrating safe legacy rows.

    Agno may merge ``metadata`` while reusing an Agent instance.  Public stream
    snapshots are conversation state, so keep their canonical copy in
    ``session_data`` and only expose a compatibility view in the API response.
    """
    session_data = dict(session.session_data or {})
    timelines = dict(session_data.get(PUBLIC_TIMELINES_KEY) or {})
    snapshots = dict(session_data.get(PUBLIC_RUN_SNAPSHOTS_KEY) or {})
    if timelines or snapshots:
        return timelines, snapshots

    metadata = dict(session.metadata or {})
    legacy_timelines = dict(metadata.get("public_timelines") or {})
    legacy_snapshots = dict(metadata.get("public_run_snapshots") or {})
    owned_run_ids = _session_run_ids(session)
    session_id = str(session.session_id or "")
    created_at = float(session.created_at or 0)
    updated_at = float(session.updated_at or time.time())
    for run_id, value in legacy_snapshots.items():
        snapshot = dict(value or {})
        owner = str(snapshot.get("conversation_id") or "")
        snapshot_created = float(snapshot.get("created_at") or 0)
        is_owned = owner == session_id or str(run_id) in owned_run_ids
        # Preserve interrupted legacy runs which Agno never committed to runs,
        # but reject snapshots older/newer than the conversation itself.
        if not owner and created_at <= snapshot_created <= updated_at + 2:
            is_owned = True
        if not is_owned:
            continue
        snapshots[str(run_id)] = snapshot
        if run_id in legacy_timelines:
            timelines[str(run_id)] = list(legacy_timelines[run_id] or [])
    return timelines, snapshots


def set_public_history(
    session: AgentSession,
    timelines: Mapping[str, Any],
    snapshots: Mapping[str, Any],
) -> None:
    session_data = dict(session.session_data or {})
    session_data[PUBLIC_TIMELINES_KEY] = dict(timelines)
    session_data[PUBLIC_RUN_SNAPSHOTS_KEY] = dict(snapshots)
    session.session_data = session_data
    # Remove the unsafe legacy canonical copy. session_dict() adds a scoped,
    # read-only compatibility view for the existing frontend.
    metadata = dict(session.metadata or {})
    metadata.pop("public_timelines", None)
    metadata.pop("public_run_snapshots", None)
    session.metadata = metadata


def session_dict(session: AgentSession) -> dict[str, Any]:
    data = session.to_dict()
    session_data = dict(session.session_data or {})
    data["title"] = session_data.get("session_name", "新对话")
    data["device_id"] = session_data.get("device_id")
    timelines, snapshots = public_history(session)
    metadata = dict(data.get("metadata") or {})
    metadata["public_timelines"] = timelines
    metadata["public_run_snapshots"] = snapshots
    data["metadata"] = metadata
    serialized_session_data = dict(data.get("session_data") or {})
    serialized_session_data.pop(PUBLIC_TIMELINES_KEY, None)
    serialized_session_data.pop(PUBLIC_RUN_SNAPSHOTS_KEY, None)
    data["session_data"] = serialized_session_data
    return cast(dict[str, Any], strip_private_reasoning(jsonable_encoder(data)))


def session_summary_dict(session: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize the intentionally small DTO used by the conversation list.

    In particular, never add ``runs`` here: that column contains the complete
    message and tool history and can grow to several megabytes per session.
    """
    session_data: Any = session.get("session_data")
    # Agno's SQLite JSON representation can be encoded one level deeper.
    # Decode only the small title object locally.
    for _ in range(2):
        if not isinstance(session_data, str):
            break
        try:
            session_data = json.loads(session_data)
        except json.JSONDecodeError:
            session_data = {}
            break
    if not isinstance(session_data, dict):
        session_data = {}
    return {
        "session_id": session.get("session_id"),
        "title": session_data.get("session_name", "新对话"),
        "device_id": session_data.get("device_id"),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
    }


def strip_private_reasoning(value: Any) -> Any:
    """Recursively remove raw hidden chain-of-thought from stored responses."""
    private_fields = {
        "qa_file_excerpts",
        "qa_knowledge",
        "reasoning_content",
        "reasoning_steps",
        "reasoning_messages",
        "_context_checkpoint",
        "documents",
    }
    if isinstance(value, dict):
        return {
            key: strip_private_reasoning(item)
            for key, item in value.items()
            if key not in private_fields
        }
    if isinstance(value, list):
        return [
            strip_private_reasoning(item)
            for item in value
            if not (isinstance(item, dict) and item.get("name") in INTERNAL_CONTEXT_MESSAGE_NAMES)
        ]
    return value


def content_dict(content: Content) -> dict[str, Any]:
    data = cast(dict[str, Any], jsonable_encoder(asdict(content)))
    metadata = data.get("metadata") or {}
    if metadata.get("display_name"):
        data["name"] = metadata["display_name"]
    return data


def knowledge_result_dict(document: Any) -> dict[str, Any]:
    """Serialize an Agno document without deep-copying its runtime embedder."""
    if hasattr(document, "to_dict"):
        data = document.to_dict()
    else:
        data = {
            key: getattr(document, key, None)
            for key in ("id", "name", "content", "meta_data", "reranking_score")
        }
    if getattr(document, "id", None) is not None:
        data["id"] = document.id
    if getattr(document, "reranking_score", None) is not None:
        data["reranking_score"] = document.reranking_score
    return cast(dict[str, Any], jsonable_encoder(data))


def sse(event: dict[str, Any]) -> str:
    event_name = str(event.get("event", "message"))
    return f"event: {event_name}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def public_event(event: dict[str, Any]) -> dict[str, Any]:
    """Keep execution milestones while withholding private model reasoning."""
    safe = cast(dict[str, Any], strip_private_reasoning(dict(event)))
    for key in ("reasoning_content", "reasoning_steps", "reasoning_messages"):
        safe.pop(key, None)
    if safe.get("event") == "ReasoningContentDelta":
        safe.pop("content", None)
    if safe.get("event") in {"PreHookStarted", "PreHookCompleted"}:
        safe.pop("run_input", None)
    raw_tool_name = event_tool_name(safe)
    if raw_tool_name:
        safe["tool_display_name"] = tool_display_name(raw_tool_name)
    return safe


def append_public_timeline(timeline: list[dict[str, Any]], event: dict[str, Any]) -> None:
    """Persist a compact, safe timeline in streamed event order."""
    event_name = str(event.get("event") or "")
    if event_name == "RunContent" and isinstance(event.get("content"), str):
        content = event["content"]
        if not content:
            return
        if timeline and timeline[-1].get("kind") == "content":
            timeline[-1]["content"] += content
        else:
            timeline.append({"kind": "content", "content": content})
        return
    allowed = {
        "RunStarted",
        "ModelRequestStarted",
        "ModelRequestCompleted",
        "ReasoningStarted",
        "ReasoningCompleted",
        "ToolCallStarted",
        "ToolCallCompleted",
        "ToolCallError",
        "RunContentCompleted",
        "RunCompleted",
        "RunError",
        "CapabilityWarning",
    }
    if event_name not in allowed:
        return
    tool = event.get("tool") or event.get("tool_call") or {}
    entry = {
        "kind": "event",
        "event": event_name,
        "created_at": event.get("created_at"),
        "tool_name": (
            tool.get("tool_name") or tool.get("name") if isinstance(tool, dict) else None
        ),
        "tool_call_id": (tool.get("tool_call_id") if isinstance(tool, dict) else None),
    }
    if event.get("message") is not None:
        entry["message"] = event["message"]
    if event.get("name") is not None:
        entry["name"] = event["name"]
    raw_tool_name = event_tool_name(event)
    if raw_tool_name:
        entry["tool_display_name"] = event.get("tool_display_name") or tool_display_name(
            raw_tool_name
        )
    timeline.append(entry)


async def ensure_conversation(runtime: AgentRuntime, conversation_id: str) -> AgentSession:
    session = await run_in_threadpool(
        runtime.db.get_session,
        conversation_id,
        SessionType.AGENT,
        runtime.settings.local_user_id,
    )
    if not isinstance(session, AgentSession):
        raise HTTPException(status_code=404, detail="对话不存在")
    return session
