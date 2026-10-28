import asyncio
import hashlib
import json
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import uuid4

import httpx
from agno.db.base import SessionType
from agno.knowledge.content import Content
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from agno.session.agent import AgentSession
from anyio import CapacityLimiter, fail_after, to_process
from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from auditbox_agent.api_support import (
    append_public_timeline,
    content_dict,
    ensure_conversation,
    knowledge_result_dict,
    public_event,
    public_history,
    session_dict,
    session_summary_dict,
    set_public_history,
    sse,
    strip_private_reasoning,
)
from auditbox_agent.capabilities import (
    EXECUTION_CAPABILITY_ID,
    install_skill_zip,
    public_capability,
    slug,
)
from auditbox_agent.conversation_files import (
    ConversationFileError,
    public_conversation_file,
)
from auditbox_agent.knowledge_ingestion import ingest_knowledge_file
from auditbox_agent.runtime import AgentRuntime, MCPProbeError
from auditbox_agent.schemas import (
    CapabilityPatch,
    ChatRequest,
    ConversationCreate,
    ConversationRename,
    ConversationTitleGenerate,
    KnowledgePatch,
    McpServerCreate,
    RunApprovalRequest,
)
from auditbox_agent.storage import (
    FileStaging,
    StorageValidationError,
    checked_storage_path,
    validate_uploaded_file,
)
from auditbox_agent.terminal_state import (
    VERIFIED_TERMINAL_STATE_KEY,
    update_verified_terminal_state,
)


def build_router(runtime: AgentRuntime) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    repository = runtime.repository
    user_id = runtime.settings.local_user_id
    ingestion_limiter = CapacityLimiter(runtime.settings.knowledge_ingestion_concurrency)

    @router.get("/health", tags=["system"])
    async def health() -> dict[str, Any]:
        model_ready = runtime.settings.environment == "test"
        backend = runtime.settings.resolved_model_backend
        if not model_ready and backend == "ollama":
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    response = await client.get(
                        f"{runtime.settings.model_base_url_for_mode('standard')}/api/tags"
                    )
                    response.raise_for_status()
                    installed = {
                        str(item.get("name")) for item in response.json().get("models", [])
                    }
                    model_ready = {
                        runtime.settings.standard_model,
                    }.issubset(installed)
            except (httpx.HTTPError, ValueError, AttributeError):
                model_ready = False
        elif not model_ready and backend in {"siliconflow", "deepseek"}:
            model_ready = bool(
                runtime.settings.model_base_url
                and runtime.settings.model_api_key
                and runtime.settings.standard_model.strip()
            )
        elif not model_ready:
            model_ready = bool(
                runtime.settings.standard_model.strip()
            )
        return {
            "status": "ok",
            "model_ready": model_ready,
            "service": runtime.settings.app_name,
            "agent_id": runtime.agent_id,
            "knowledge_enabled": runtime.knowledge is not None,
            "auth_enabled": False,
            "single_user": True,
            "tool_approval": "required-for-enabled-tools"
            if runtime.settings.execution_enabled else "disabled",
            "capabilities": runtime.capabilities(),
        }

    @router.get("/capabilities", tags=["capabilities"])
    async def list_capabilities() -> dict[str, Any]:
        return {"data": runtime.capability_details(), "summary": runtime.capabilities()}

    @router.patch("/capabilities/{capability_id:path}", tags=["capabilities"])
    async def patch_capability(capability_id: str, body: CapabilityPatch) -> dict[str, Any]:
        existing = await run_in_threadpool(repository.get_capability, capability_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="能力不存在")
        if existing["kind"] not in {"tool", "skill", "mcp"} and (
            capability_id != EXECUTION_CAPABILITY_ID
        ):
            raise HTTPException(status_code=422, detail="不支持修改此能力")
        try:
            return await run_in_threadpool(
                runtime.set_capability_enabled, capability_id, body.enabled
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"能力切换失败：{exc}") from exc

    @router.delete("/capabilities/{capability_id:path}", tags=["capabilities"])
    async def delete_capability(capability_id: str) -> dict[str, Any]:
        existing = await run_in_threadpool(repository.get_capability, capability_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="能力不存在")
        is_user_skill = (
            existing["kind"] == "skill"
            and existing.get("source") in {"user-upload", "agent-created"}
            and not bool(existing["config"].get("bundled"))
        )
        is_user_mcp = existing["kind"] == "mcp" and existing.get("source") == "user"
        if not (is_user_skill or is_user_mcp):
            raise HTTPException(status_code=403, detail="系统内置或环境预置能力不能删除")

        skill_path: Path | None = None
        staged_skill_path: Path | None = None
        if is_user_skill:
            skill_root = runtime.settings.skill_directory.resolve()
            folder = str(existing["config"].get("folder") or "")
            skill_path = (skill_root / folder).resolve()
            if not folder or skill_path.parent != skill_root:
                raise HTTPException(status_code=422, detail="Skill 存储路径无效")
            if skill_path.exists():
                trash_root = skill_root / ".trash"
                trash_root.mkdir(parents=True, exist_ok=True)
                staged_skill_path = trash_root / f"{skill_path.name}-{uuid4().hex}"
                skill_path.rename(staged_skill_path)

        deleted = await run_in_threadpool(repository.delete_capability, capability_id)
        if not deleted:
            if staged_skill_path is not None and skill_path is not None:
                staged_skill_path.rename(skill_path)
            raise HTTPException(status_code=404, detail="能力不存在")
        try:
            runtime.reload_capabilities()
        except Exception as exc:
            await run_in_threadpool(
                repository.upsert_capability,
                existing["id"],
                existing["kind"],
                existing["name"],
                existing["enabled"],
                existing["config"],
                existing.get("source"),
                int(time.time()),
            )
            if staged_skill_path is not None and skill_path is not None:
                staged_skill_path.rename(skill_path)
            runtime.reload_capabilities()
            raise HTTPException(status_code=409, detail=f"能力删除失败：{exc}") from exc
        if staged_skill_path is not None:
            await run_in_threadpool(shutil.rmtree, staged_skill_path)
        return {"id": capability_id, "deleted": True}

    @router.post("/capabilities/mcp", status_code=201, tags=["capabilities"])
    async def create_mcp_server(body: McpServerCreate) -> dict[str, Any]:
        capability_id = f"mcp:{slug(body.name)}"
        if await run_in_threadpool(repository.get_capability, capability_id):
            raise HTTPException(status_code=409, detail="同名 MCP 已存在")
        try:
            discovered_tools = await runtime.probe_mcp(
                name=body.name,
                url=body.url,
                transport=body.transport,
                headers=body.headers,
            )
        except MCPProbeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        missing_tools = sorted(set(body.tools) - set(discovered_tools))
        if missing_tools:
            raise HTTPException(
                status_code=422,
                detail=f"工具白名单包含服务未提供的工具：{', '.join(missing_tools)}",
            )
        created = await run_in_threadpool(
            repository.upsert_capability,
            capability_id,
            "mcp",
            body.name,
            body.enabled,
            {
                "url": body.url,
                "transport": body.transport,
                "headers": body.headers,
                "tools": body.tools,
                "discovered_tools": discovered_tools,
            },
            "user",
            int(time.time()),
        )
        try:
            runtime.reload_capabilities()
        except Exception as exc:
            await run_in_threadpool(repository.delete_capability, capability_id)
            runtime.reload_capabilities()
            raise HTTPException(status_code=422, detail=f"MCP 配置无效：{exc}") from exc
        return public_capability(created)

    @router.post("/capabilities/mcp/probe", tags=["capabilities"])
    async def probe_mcp_server(body: McpServerCreate) -> dict[str, Any]:
        try:
            tools = await runtime.probe_mcp(
                name=body.name,
                url=body.url,
                transport=body.transport,
                headers=body.headers,
            )
        except MCPProbeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"status": "available", "tools": tools}

    @router.post("/capabilities/{capability_id:path}/probe", tags=["capabilities"])
    async def probe_saved_mcp_server(capability_id: str) -> dict[str, Any]:
        existing = await run_in_threadpool(repository.get_capability, capability_id)
        if existing is None or existing["kind"] != "mcp":
            raise HTTPException(status_code=404, detail="MCP 连接不存在")
        config = dict(existing["config"])
        try:
            tools = await runtime.probe_mcp(
                name=existing["name"],
                url=str(config["url"]),
                transport=str(config.get("transport") or "streamable-http"),
                headers=dict(config.get("headers") or {}),
            )
        except MCPProbeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        config["discovered_tools"] = tools
        updated = await run_in_threadpool(
            repository.upsert_capability,
            existing["id"],
            existing["kind"],
            existing["name"],
            existing["enabled"],
            config,
            existing.get("source"),
            int(time.time()),
        )
        return {
            **public_capability(updated),
            "runtime_status": "available",
            "runtime_error": "",
            "discovered_tools": tools,
        }

    @router.post("/capabilities/skills", status_code=201, tags=["capabilities"])
    async def upload_skill(
        file: Annotated[UploadFile, File()],
        name: str | None = Form(default=None),
        enabled: bool = Form(default=True),
    ) -> dict[str, Any]:
        if Path(file.filename or "").suffix.casefold() not in {".zip", ".skill"}:
            raise HTTPException(status_code=415, detail="Skill 必须是 .zip 或 .skill 文件")
        upload_root = runtime.settings.skill_directory / ".uploads"
        upload_root.mkdir(parents=True, exist_ok=True)
        temporary = upload_root / f"{uuid4().hex}.zip"
        size = 0
        try:
            with temporary.open("xb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 10 * 1024 * 1024:
                        raise HTTPException(status_code=413, detail="Skill 压缩包不能超过 10MB")
                    output.write(chunk)
            folder, display_name = await run_in_threadpool(
                install_skill_zip, temporary, runtime.settings.skill_directory, name
            )
            capability_id = f"skill:{folder}"
            if await run_in_threadpool(repository.get_capability, capability_id):
                shutil.rmtree(runtime.settings.skill_directory / folder, ignore_errors=True)
                raise HTTPException(status_code=409, detail="同名 Skill 已存在")
            created = await run_in_threadpool(
                repository.upsert_capability,
                capability_id,
                "skill",
                display_name,
                enabled,
                {"folder": folder, "bundled": False},
                "user-upload",
                int(time.time()),
            )
            runtime.reload_capabilities()
            return public_capability(created)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            temporary.unlink(missing_ok=True)

    @router.post("/conversations", status_code=201, tags=["conversations"])
    async def create_conversation(body: ConversationCreate) -> dict[str, Any]:
        now = int(time.time())
        session = AgentSession(
            session_id=str(uuid4()),
            agent_id=runtime.agent_id,
            user_id=user_id,
            session_data={"session_name": body.title, "device_id": body.device_id},
            metadata={**body.metadata, "source": "api"},
            created_at=now,
            updated_at=now,
        )
        stored = await run_in_threadpool(runtime.db.upsert_session, session)
        if not isinstance(stored, AgentSession):
            raise HTTPException(status_code=500, detail="创建对话失败")
        return session_dict(stored)

    @router.get("/conversations", tags=["conversations"])
    async def list_conversations(
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=20, ge=1, le=100),
        title: str | None = None,
        device_id: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> dict[str, Any]:
        sessions = await run_in_threadpool(
            repository.list_conversations,
            user_id,
            runtime.agent_id,
            title,
            page,
            limit,
            device_id,
            True,
        )
        return {
            "data": [session_summary_dict(item) for item in sessions[:limit]],
            "page": page,
            "limit": limit,
            "has_more": len(sessions) > limit,
            "order": "updated_at_desc",
        }

    @router.get("/conversations/{conversation_id}", tags=["conversations"])
    async def get_conversation(conversation_id: str) -> dict[str, Any]:
        return session_dict(await ensure_conversation(runtime, conversation_id))

    @router.get("/conversations/{conversation_id}/files", tags=["conversation-files"])
    async def list_conversation_files(conversation_id: str) -> dict[str, Any]:
        await ensure_conversation(runtime, conversation_id)
        rows = await run_in_threadpool(repository.list_conversation_files, conversation_id)
        return {"data": [public_conversation_file(row) for row in rows]}

    @router.post(
        "/conversations/{conversation_id}/files",
        status_code=201,
        tags=["conversation-files"],
    )
    async def upload_conversation_file(
        conversation_id: str,
        file: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        await ensure_conversation(runtime, conversation_id)
        upload_root = runtime.settings.conversation_file_directory / ".uploads"
        upload_root.mkdir(parents=True, exist_ok=True)
        temporary = upload_root / f"{uuid4().hex}.upload"
        size = 0
        try:
            with temporary.open("xb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > runtime.settings.conversation_file_max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                "单个文件不能超过 "
                                f"{runtime.settings.conversation_file_max_bytes // 1024 // 1024}MB"
                            ),
                        )
                    output.write(chunk)
            if not size:
                raise HTTPException(status_code=422, detail="文件内容为空")
            row = await run_in_threadpool(
                runtime.conversation_files.ingest_staged_file,
                conversation_id,
                temporary,
                file.filename or "未命名文档",
                file.content_type,
            )
            return public_conversation_file(row)
        except ConversationFileError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            temporary.unlink(missing_ok=True)
            await file.close()

    @router.delete(
        "/conversations/{conversation_id}/files/{file_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["conversation-files"],
    )
    async def delete_conversation_file(
        conversation_id: str,
        file_id: str,
    ) -> Response:
        await ensure_conversation(runtime, conversation_id)
        deleted = await run_in_threadpool(
            runtime.conversation_files.delete_file, conversation_id, file_id
        )
        if deleted is None:
            raise HTTPException(status_code=404, detail="文件不存在")
        return Response(status_code=204)

    @router.patch("/conversations/{conversation_id}", tags=["conversations"])
    async def rename_conversation(conversation_id: str, body: ConversationRename) -> dict[str, Any]:
        await ensure_conversation(runtime, conversation_id)
        session = await run_in_threadpool(
            runtime.db.rename_session,
            conversation_id,
            SessionType.AGENT,
            body.title,
            user_id,
        )
        if not isinstance(session, AgentSession):
            raise HTTPException(status_code=500, detail="更新对话失败")
        return session_dict(session)

    @router.post("/conversations/{conversation_id}/title", tags=["conversations"])
    async def generate_conversation_title(
        conversation_id: str, body: ConversationTitleGenerate
    ) -> dict[str, Any]:
        session = await ensure_conversation(runtime, conversation_id)
        current_title = (session.session_data or {}).get("session_name", "新对话")
        if current_title != "新对话":
            return {"conversation_id": conversation_id, "title": current_title}
        try:
            title = await runtime.generate_conversation_title(body.first_message)
        except Exception:
            title = body.first_message.replace("\n", " ").strip()[:24] or "新对话"
        updated = await run_in_threadpool(
            runtime.db.rename_session,
            conversation_id,
            SessionType.AGENT,
            title,
            user_id,
        )
        if not isinstance(updated, AgentSession):
            raise HTTPException(status_code=500, detail="自动生成对话标题失败")
        return {"conversation_id": conversation_id, "title": title}

    @router.delete(
        "/conversations/{conversation_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["conversations"],
    )
    async def delete_conversation(conversation_id: str) -> Response:
        await ensure_conversation(runtime, conversation_id)
        deleted = await run_in_threadpool(runtime.db.delete_session, conversation_id, user_id)
        if not deleted:
            raise HTTPException(status_code=500, detail="删除对话失败")
        await run_in_threadpool(runtime.conversation_files.delete_conversation, conversation_id)
        return Response(status_code=204)

    @router.post("/conversations/{conversation_id}/messages", tags=["conversations"])
    async def send_message(conversation_id: str, body: ChatRequest) -> Response:
        session = await ensure_conversation(runtime, conversation_id)
        conversation_device_id = (session.session_data or {}).get("device_id") or None
        request_device_id = body.target.device_id if body.target else None
        if conversation_device_id != request_device_id:
            raise HTTPException(
                status_code=409,
                detail="当前会话不属于正在连接的设备，请切换对应设备会话或新建会话",
            )
        model_input = body.message
        conversation_files = await run_in_threadpool(
            repository.list_conversation_files, conversation_id
        )
        run_metadata = {
            **body.metadata,
            "source": "interactive",
            "mode": body.mode,
            "interaction_mode": "agent" if runtime.settings.execution_enabled else "qa",
            "display_message": body.message,
            "conversation_files": [
                public_conversation_file(row) for row in conversation_files
            ],
            "target": body.target.model_dump() if body.target else None,
            VERIFIED_TERMINAL_STATE_KEY: (session.session_data or {}).get(
                VERIFIED_TERMINAL_STATE_KEY
            ),
        }
        async def prepare_question_context() -> None:
            # Called after reserving the Agent so a switch cannot race grounding
            # or model selection. Never trust client-supplied evidence.
            run_metadata["interaction_mode"] = (
                "agent" if runtime.settings.execution_enabled else "qa"
            )
            run_metadata["qa_file_excerpts"] = []
            run_metadata["qa_knowledge"] = []
            if runtime.settings.execution_enabled:
                return
            run_metadata["qa_file_excerpts"] = await run_in_threadpool(
                runtime.conversation_files.question_excerpts, conversation_id, body.message
            )
            if runtime.knowledge is not None:
                documents = await runtime._retrieve_knowledge(body.message)
                remaining = min(
                    runtime.settings.qa_file_context_characters,
                    runtime.settings.context_max_input_tokens // 4,
                )
                for document in documents:
                    content = str(document.get("content") or "")[:remaining]
                    if not content:
                        continue
                    run_metadata["qa_knowledge"].append({
                        "name": str(document.get("name") or "知识库资料"), "content": content,
                    })
                    remaining -= len(content)
                    if remaining <= 0:
                        break
        conversation_title = (session.session_data or {}).get("session_name", "新对话")

        async def preserve_title() -> None:
            await run_in_threadpool(
                runtime.db.rename_session,
                conversation_id,
                SessionType.AGENT,
                conversation_title,
                user_id,
            )

        if not body.stream:
            run_id = str(uuid4())
            selected_agent = runtime.register_request_run(run_id, body.mode)
            try:
                await prepare_question_context()
                output = await selected_agent.arun(
                    model_input,
                    stream=False,
                    user_id=user_id,
                    session_id=conversation_id,
                    metadata=run_metadata,
                    run_id=run_id,
                )
            finally:
                runtime.unregister_active_run(run_id)
                await preserve_title()
            return JSONResponse(
                cast(dict[str, Any], strip_private_reasoning(jsonable_encoder(output.to_dict())))
            )

        async def events() -> AsyncIterator[str]:
            timeline: list[dict[str, Any]] = []
            # Allocate the public run identity before invoking the model. This
            # lets us durably record the user's message even if the model dies
            # before producing its first event.
            run_id = str(uuid4())
            warned_mcp_ids: set[str] = set()
            run_status = "running"
            last_persisted_at = 0.0
            terminal_state = dict(
                (session.session_data or {}).get(VERIFIED_TERMINAL_STATE_KEY) or {}
            )

            async def persist_public_run(status_value: str) -> None:
                if not run_id:
                    return
                latest = await run_in_threadpool(
                    runtime.db.get_session,
                    conversation_id,
                    SessionType.AGENT,
                    user_id,
                )
                if not isinstance(latest, AgentSession):
                    return
                timelines, snapshots = public_history(latest)
                timeline_copy = [dict(item) for item in timeline]
                timelines[run_id] = timeline_copy
                previous_snapshot = snapshots.get(run_id) or {}
                snapshots[run_id] = {
                    "run_id": run_id,
                    "conversation_id": conversation_id,
                    "input": body.message,
                    "content": "".join(
                        str(item.get("content") or "")
                        for item in timeline_copy
                        if item.get("kind") == "content"
                    ),
                    "timeline": timeline_copy,
                    "status": status_value,
                    "created_at": previous_snapshot.get("created_at") or time.time(),
                    "updated_at": int(time.time()),
                }
                if len(timelines) > 50:
                    timelines = dict(list(timelines.items())[-50:])
                if len(snapshots) > 50:
                    snapshots = dict(list(snapshots.items())[-50:])
                set_public_history(latest, timelines, snapshots)
                latest_session_data = dict(latest.session_data or {})
                if terminal_state:
                    latest_session_data[VERIFIED_TERMINAL_STATE_KEY] = terminal_state
                    latest.session_data = latest_session_data
                await run_in_threadpool(runtime.db.upsert_session, latest)

            try:
                selected_agent = runtime.register_request_run(run_id, body.mode)
                await prepare_question_context()
                await persist_public_run(run_status)
                yield sse({"event": "RunAccepted", "run_id": run_id})
                stream = selected_agent.arun(
                    model_input,
                    stream=True,
                    stream_events=True,
                    run_id=run_id,
                    user_id=user_id,
                    session_id=conversation_id,
                    metadata=run_metadata,
                )
                async for event in stream:
                    for warning in runtime.mcp_warnings():
                        if warning["id"] in warned_mcp_ids:
                            continue
                        warned_mcp_ids.add(warning["id"])
                        warning_event = {
                            "event": "CapabilityWarning",
                            "capability": "mcp",
                            **warning,
                        }
                        append_public_timeline(timeline, warning_event)
                        yield sse(warning_event)
                    safe_event = public_event(
                        cast(dict[str, Any], jsonable_encoder(event.to_dict()))
                    )
                    terminal_state = update_verified_terminal_state(terminal_state, safe_event)
                    append_public_timeline(timeline, safe_event)
                    event_name = str(safe_event.get("event") or "")
                    if event_name == "RunCompleted":
                        run_status = "completed"
                    elif event_name == "RunPaused":
                        run_status = "paused"
                    elif event_name == "RunError":
                        run_status = "error"
                    now = time.monotonic()
                    if (
                        last_persisted_at == 0.0
                        or (
                            event_name == "RunContent"
                            and now - last_persisted_at >= 0.5
                        )
                    ):
                        await persist_public_run(run_status)
                        last_persisted_at = now
                    yield sse(safe_event)
            except asyncio.CancelledError:
                run_status = "interrupted"
                raise
            except Exception as exc:
                run_status = "error"
                error_event = {"event": "RunError", "error": str(exc)}
                append_public_timeline(timeline, error_event)
                yield sse(error_event)
            finally:
                runtime.unregister_active_run(run_id)
                await asyncio.shield(preserve_title())
                await asyncio.shield(persist_public_run(run_status))

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/runs/{run_id}/cancel", tags=["conversations"])
    async def cancel_run(run_id: str) -> dict[str, Any]:
        cancelled = await runtime.cancel_run(run_id)
        if cancelled is None:
            raise HTTPException(status_code=404, detail="运行不存在或已结束")
        return {"run_id": run_id, "cancelled": cancelled}

    @router.post(
        "/conversations/{conversation_id}/runs/{run_id}/continue",
        tags=["conversations"],
    )
    async def continue_run(
        conversation_id: str, run_id: str, body: RunApprovalRequest
    ) -> StreamingResponse:
        if not runtime.settings.execution_enabled:
            raise HTTPException(
                status_code=409, detail="Agent 执行已关闭，不能继续待审批操作；请重新提问"
            )
        approval_session = await ensure_conversation(runtime, conversation_id)
        selected_agent = runtime.agent_for_mode(body.mode)
        saved_run = next(
            (run for run in approval_session.runs or []
             if isinstance(run, RunOutput) and run.run_id == run_id), None
        )
        if saved_run is None:
            raise HTTPException(status_code=404, detail="当前会话中不存在此运行")
        if saved_run.status != RunStatus.paused:
            raise HTTPException(status_code=409, detail="运行不在待审批状态，请刷新会话")
        try:
            restored_run = runtime.context_manager.restore_continuation_context(
                saved_run, approval_session, selected_agent
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        pending = {
            tool.tool_call_id: tool for tool in restored_run.tools or []
            if tool.requires_confirmation and tool.confirmed is None
        }
        updated_tools = []
        for value in body.tools:
            submitted = ToolExecution.from_dict(value)
            tool = pending.pop(submitted.tool_call_id, None)
            if tool is None or (
                tool.tool_name != submitted.tool_name or tool.tool_args != submitted.tool_args
            ):
                raise HTTPException(status_code=422, detail="审批工具与保存的待执行操作不一致")
            # Only accept the decision from the client, never its executable
            # arguments/results. Preserve all previous completed tool records.
            tool.confirmed = body.approved
            updated_tools.append(tool)
        if not updated_tools or pending:
            raise HTTPException(status_code=422, detail="请确认本次全部待审批操作")
        restored_run.metadata = {**(restored_run.metadata or {}), "audit_actor": body.audit_actor}
        for tool in updated_tools:
            await run_in_threadpool(
                runtime.terminal_workspace_tools._request,
                "POST", "/api/internal/agent/audit",
                payload={"action": "agent_approved" if body.approved else "agent_rejected",
                         "context": body.audit_actor,
                         "details": {"conversation_id": conversation_id, "run_id": run_id,
                                     "tool_call_id": tool.tool_call_id, "tool_name": tool.tool_name,
                                     "command": (tool.tool_args or {}).get("command", "")}},
            )
        tool_map = {tool.tool_call_id: tool for tool in restored_run.tools or []}
        for requirement in restored_run.requirements or []:
            if requirement.tool_execution:
                requirement.tool_execution = tool_map.get(
                    requirement.tool_execution.tool_call_id, requirement.tool_execution
                )

        async def events() -> AsyncIterator[str]:
            session = await ensure_conversation(runtime, conversation_id)
            timelines, snapshots = public_history(session)
            timeline = [dict(item) for item in timelines.get(run_id) or []]
            previous_snapshot = dict(snapshots.get(run_id) or {})
            run_status = "running"
            last_persisted_at = 0.0
            terminal_state = dict(
                (session.session_data or {}).get(VERIFIED_TERMINAL_STATE_KEY) or {}
            )

            async def persist_continuation(status_value: str) -> None:
                latest = await run_in_threadpool(
                    runtime.db.get_session,
                    conversation_id,
                    SessionType.AGENT,
                    user_id,
                )
                if not isinstance(latest, AgentSession):
                    return
                latest_timelines, latest_snapshots = public_history(latest)
                timeline_copy = [dict(item) for item in timeline]
                latest_timelines[run_id] = timeline_copy
                existing_snapshot = dict(latest_snapshots.get(run_id) or previous_snapshot)
                latest_snapshots[run_id] = {
                    **existing_snapshot,
                    "run_id": run_id,
                    "conversation_id": conversation_id,
                    "content": "".join(
                        str(item.get("content") or "")
                        for item in timeline_copy
                        if item.get("kind") == "content"
                    ),
                    "timeline": timeline_copy,
                    "status": status_value,
                    "updated_at": int(time.time()),
                }
                set_public_history(latest, latest_timelines, latest_snapshots)
                latest_session_data = dict(latest.session_data or {})
                if terminal_state:
                    latest_session_data[VERIFIED_TERMINAL_STATE_KEY] = terminal_state
                    latest.session_data = latest_session_data
                await run_in_threadpool(runtime.db.upsert_session, latest)

            runtime.register_active_run(run_id, selected_agent)
            try:
                if not runtime.settings.execution_enabled:
                    raise ValueError("Agent 执行已关闭，不能继续待审批操作；请重新提问")
                stream = selected_agent.acontinue_run(
                    run_response=restored_run,
                    stream=True,
                    stream_events=True,
                    user_id=user_id,
                    session_id=conversation_id,
                    metadata=restored_run.metadata,
                )
                async for event in stream:
                    safe_event = public_event(
                        cast(dict[str, Any], jsonable_encoder(event.to_dict()))
                    )
                    terminal_state = update_verified_terminal_state(terminal_state, safe_event)
                    append_public_timeline(timeline, safe_event)
                    event_name = str(safe_event.get("event") or "")
                    if event_name == "RunCompleted":
                        run_status = "completed"
                    elif event_name == "RunPaused":
                        run_status = "paused"
                    elif event_name == "RunError":
                        run_status = "error"
                    now = time.monotonic()
                    if last_persisted_at == 0.0 or (
                        event_name == "RunContent" and now - last_persisted_at >= 0.5
                    ):
                        await persist_continuation(run_status)
                        last_persisted_at = now
                    yield sse(safe_event)
            except asyncio.CancelledError:
                run_status = "interrupted"
                raise
            except Exception as exc:
                run_status = "error"
                error_event = {"event": "RunError", "run_id": run_id, "error": str(exc)}
                append_public_timeline(timeline, error_event)
                yield sse(error_event)
            finally:
                runtime.unregister_active_run(run_id)
                if timeline:
                    await asyncio.shield(persist_continuation(run_status))

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def require_knowledge() -> Any:
        if runtime.knowledge is None:
            raise HTTPException(
                status_code=503,
                detail="知识库代码已保留，但当前未配置 Embedding 服务，因此未启用",
            )
        return runtime.knowledge

    @router.get("/knowledge/config", tags=["knowledge"])
    async def knowledge_config() -> dict[str, Any]:
        settings = runtime.settings
        return {
            "enabled": runtime.knowledge is not None,
            "disabled_reason": (
                None if runtime.knowledge is not None else "embedding_service_not_configured"
            ),
            "storage": "chromadb",
            "metadata_storage": "sqlite",
            "embedding_model": settings.embedding_model or None,
            "embedding_dimensions": settings.embedding_dimensions,
            "search_type": "hybrid",
            "candidate_results": settings.knowledge_candidate_results,
            "max_results": settings.knowledge_max_results,
            "chunk_size": settings.knowledge_chunk_size,
            "chunk_overlap": settings.knowledge_chunk_overlap,
            "upload_max_bytes": settings.knowledge_upload_max_bytes,
            "upload_max_files": settings.knowledge_upload_max_files,
            "upload_quota_bytes": settings.knowledge_upload_quota_bytes,
            "upload_allowed_extensions": settings.knowledge_upload_allowed_extensions,
        }

    @router.get("/knowledge", tags=["knowledge"])
    async def list_knowledge(
        page: int = Query(default=1, ge=1), limit: int = Query(default=20, ge=1, le=100)
    ) -> dict[str, Any]:
        require_knowledge()
        contents, total = await run_in_threadpool(repository.list_knowledge, page, limit)
        return {
            "data": [content_dict(content) for content in contents],
            "page": page,
            "limit": limit,
            "total": total,
        }

    @router.post("/knowledge/files", status_code=201, tags=["knowledge"])
    async def upload_knowledge_file(
        file: Annotated[UploadFile, File()],
        name: str | None = Form(default=None),
        description: str | None = Form(default=None),
        metadata: str = Form(default="{}"),
    ) -> dict[str, Any]:
        require_knowledge()
        try:
            extra_metadata = json.loads(metadata)
            if not isinstance(extra_metadata, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="metadata 必须是 JSON 对象") from exc
        display_name = name or file.filename or "未命名文件"
        upload_root = runtime.settings.knowledge_upload_directory
        upload_dir = upload_root / "global"
        upload_dir.mkdir(parents=True, exist_ok=True)
        existing = [
            path
            for path in upload_root.rglob("*")
            if path.is_file() and ".trash" not in path.parts and not path.name.endswith(".part")
        ]
        existing_bytes = sum(path.stat().st_size for path in existing)
        if len(existing) >= runtime.settings.knowledge_upload_max_files:
            raise HTTPException(status_code=413, detail="已达到知识文件数量上限")
        suffix = Path(file.filename or "").suffix.casefold()
        allowed = {
            value.casefold() for value in runtime.settings.knowledge_upload_allowed_extensions
        }
        if suffix not in allowed:
            raise HTTPException(
                status_code=415,
                detail=f"不支持的知识文件类型：{suffix or '无扩展名'}",
            )
        target = upload_dir / f"{uuid4().hex}{suffix}"
        temporary = target.with_suffix(f"{target.suffix}.part")
        size = 0
        digest = hashlib.sha256()
        try:
            with temporary.open("xb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > runtime.settings.knowledge_upload_max_bytes:
                        raise HTTPException(status_code=413, detail="知识文件超过单文件大小上限")
                    if existing_bytes + size > runtime.settings.knowledge_upload_quota_bytes:
                        raise HTTPException(status_code=413, detail="已超过知识文件存储配额")
                    digest.update(chunk)
                    output.write(chunk)
            await run_in_threadpool(validate_uploaded_file, temporary, suffix)
            sha256 = digest.hexdigest()
            duplicate = await run_in_threadpool(repository.knowledge_by_sha256, sha256)
            if duplicate is not None:
                duplicate_name = (duplicate.metadata or {}).get("display_name") or duplicate.name
                raise HTTPException(status_code=409, detail=f"相同文件已存在：{duplicate_name}")
            temporary.replace(target)
            stored_metadata = {
                **{
                    key: value
                    for key, value in extra_metadata.items()
                    if key
                    not in {
                        "stored_path",
                        "source",
                        "original_filename",
                        "size_bytes",
                        "sha256",
                        "chunk_policy",
                    }
                },
                "display_name": display_name,
                "source": "file",
                "original_filename": file.filename,
                "stored_path": str(target.resolve()),
                "size_bytes": size,
                "sha256": sha256,
                "chunk_policy": {
                    "strategy": "structure_aware_character_window",
                    "size": runtime.settings.knowledge_chunk_size,
                    "overlap": runtime.settings.knowledge_chunk_overlap,
                },
            }
            with fail_after(runtime.settings.knowledge_ingestion_timeout_seconds):
                await to_process.run_sync(
                    ingest_knowledge_file,
                    str(target.resolve()),
                    display_name,
                    description,
                    stored_metadata,
                    cancellable=True,
                    limiter=ingestion_limiter,
                )
        except StorageValidationError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except TimeoutError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=504, detail="知识文件解析或索引超时") from exc
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
        inserted = await run_in_threadpool(repository.latest_knowledge_by_name, display_name)
        if inserted is None:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="知识文件写入后未找到")
        return content_dict(inserted)

    async def knowledge_content(content_id: str) -> Content:
        knowledge = require_knowledge()
        content = await knowledge.aget_content_by_id(content_id)
        if content is None:
            raise HTTPException(status_code=404, detail="知识内容不存在")
        return cast(Content, content)

    @router.get("/knowledge/{content_id}", tags=["knowledge"])
    async def get_knowledge(content_id: str) -> dict[str, Any]:
        return content_dict(await knowledge_content(content_id))

    @router.patch("/knowledge/{content_id}", tags=["knowledge"])
    async def patch_knowledge(content_id: str, body: KnowledgePatch) -> dict[str, Any]:
        knowledge = require_knowledge()
        content = await knowledge_content(content_id)
        if body.name is not None:
            content.name = body.name
            content.metadata = {**(content.metadata or {}), "display_name": body.name}
        if body.description is not None:
            content.description = body.description
        if body.metadata is not None:
            content.metadata = {
                **(content.metadata or {}),
                **{
                    key: value
                    for key, value in body.metadata.items()
                    if key
                    not in {
                        "stored_path",
                        "source",
                        "original_filename",
                        "size_bytes",
                        "sha256",
                        "chunk_policy",
                    }
                },
            }
        await knowledge.apatch_content(content)
        return content_dict(content)

    @router.delete(
        "/knowledge/{content_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["knowledge"]
    )
    async def delete_knowledge(content_id: str) -> Response:
        knowledge = require_knowledge()
        content = await knowledge_content(content_id)
        source = checked_storage_path(
            (content.metadata or {}).get("stored_path"), runtime.settings.knowledge_upload_directory
        )
        staging = FileStaging(runtime.settings.knowledge_upload_directory)
        if source is not None:
            await run_in_threadpool(staging.stage, [source])
        try:
            await knowledge.aremove_content_by_id(content_id)
        except Exception:
            await run_in_threadpool(staging.rollback)
            raise
        await run_in_threadpool(staging.commit)
        return Response(status_code=204)

    @router.post("/knowledge/search", tags=["knowledge"])
    async def search_knowledge(
        query: str = Query(min_length=1), limit: int = Query(8, ge=1, le=50)
    ) -> dict[str, Any]:
        knowledge = require_knowledge()
        results = await knowledge.asearch(query=query, max_results=limit)
        return {"data": [knowledge_result_dict(document) for document in results]}

    return router
