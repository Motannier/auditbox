import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

import uvicorn
from agno.os import AgentOS
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from auditbox_agent.api import build_router
from auditbox_agent.config import Settings, get_settings
from auditbox_agent.runtime import AgentRuntime


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.knowledge_upload_directory.mkdir(parents=True, exist_ok=True)

    runtime = AgentRuntime(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        warmup_task = asyncio.create_task(
            runtime.warm_standard_model(), name="standard-model-startup-warmup"
        )
        try:
            yield
        finally:
            if not warmup_task.done():
                warmup_task.cancel()
            with suppress(asyncio.CancelledError):
                await warmup_task
            runtime.close()

    base_app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "智维宝盒内部 Harness Agent 服务。业务接口位于 /api/v1。"
            "服务不实现鉴权，只允许由本机 Gateway 访问，并使用固定本地用户。"
        ),
        lifespan=lifespan,
    )
    base_app.state.agent_runtime = runtime

    @base_app.middleware("http")
    async def block_disabled_native_routes(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # AgentOS's generic knowledge endpoints support remote ingestion and can
        # bypass the local upload policy. Only the constrained API is exposed.
        path = request.url.path
        if (
            path == "/knowledge"
            or path.startswith("/knowledge/")
            or path == "/schedules"
            or path.startswith("/schedules/")
        ):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return await call_next(request)

    base_app.include_router(build_router(runtime))
    agent_os = AgentOS(
        id="auditbox-agent-os",
        name=settings.app_name,
        description="智维宝盒本地 Harness Agent 运行时",
        agents=[runtime.agent],
        knowledge=[runtime.knowledge] if runtime.knowledge is not None else None,
        db=runtime.db,
        base_app=base_app,
        on_route_conflict="preserve_base_app",
        authorization=False,
        cors_allowed_origins=[],
        telemetry=settings.telemetry_enabled,
    )
    app = agent_os.get_app()
    app.state.agent_runtime = runtime
    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "auditbox_agent.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.environment == "development",
    )


if __name__ == "__main__":
    run()
