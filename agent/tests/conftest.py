from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auditbox_agent.api import build_router
from auditbox_agent.config import Settings
from auditbox_agent.runtime import AgentRuntime


@pytest.fixture
def runtime(tmp_path) -> Iterator[AgentRuntime]:
    settings = Settings(
        environment="test",
        # Existing harness regressions exercise the explicitly enabled path;
        # test_question_answering covers the production default (disabled).
        execution_enabled=True,
        database_url=f"sqlite:///{tmp_path / 'agent.db'}",
        model="test-model",
        model_base_url="http://model.invalid/v1",
        model_api_key="test-key",
        model_backend="siliconflow",
        knowledge_enabled=False,
        knowledge_upload_directory=tmp_path / "uploads",
        knowledge_chroma_directory=tmp_path / "chromadb",
        conversation_file_directory=tmp_path / "conversations",
        skill_directory=tmp_path / "skills",
        workspace_directory=tmp_path / "workspace",
        generated_file_directory=tmp_path / "generated",
        telemetry_enabled=False,
    )
    instance = AgentRuntime(settings)
    yield instance
    instance.close()


@pytest.fixture
def client(runtime: AgentRuntime) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(build_router(runtime))
    with TestClient(app) as test_client:
        yield test_client
