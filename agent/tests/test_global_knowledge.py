from pathlib import Path
from typing import Any

import pytest
from agno.knowledge.content import Content
from fastapi.testclient import TestClient

from auditbox_agent.runtime import AgentRuntime


class FakeKnowledge:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self.insert_calls: list[dict[str, Any]] = []

    async def asearch(self, **kwargs: Any) -> list[Any]:
        self.search_calls.append(kwargs)
        return []

    def insert(self, **kwargs: Any) -> None:
        self.insert_calls.append(kwargs)


def test_knowledge_list_has_no_user_scope(
    client: TestClient, runtime: AgentRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime.knowledge = FakeKnowledge()  # type: ignore[assignment]
    content = Content(id="global-1", name="公共规程", metadata={"display_name": "公共规程"})
    monkeypatch.setattr(runtime.repository, "list_knowledge", lambda page, limit: ([content], 1))
    response = client.get("/api/v1/knowledge")
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "global-1"


def test_direct_knowledge_search_has_no_user_filter(
    client: TestClient, runtime: AgentRuntime
) -> None:
    knowledge = FakeKnowledge()
    runtime.knowledge = knowledge  # type: ignore[assignment]
    response = client.post("/api/v1/knowledge/search?query=交换机")
    assert response.status_code == 200
    assert "filters" not in knowledge.search_calls[0]


def test_upload_uses_global_directory(
    client: TestClient,
    runtime: AgentRuntime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    knowledge = FakeKnowledge()
    runtime.knowledge = knowledge  # type: ignore[assignment]
    runtime.settings.knowledge_upload_directory = tmp_path / "uploads"

    async def run_ingestion(
        _function: Any,
        path: str,
        display_name: str,
        description: str | None,
        stored_metadata: dict[str, Any],
        **_kwargs: Any,
    ) -> None:
        knowledge.insert(
            path=path, name=display_name, description=description, metadata=stored_metadata
        )

    monkeypatch.setattr("auditbox_agent.api.to_process.run_sync", run_ingestion)

    def latest(display_name: str) -> Content:
        inserted = knowledge.insert_calls[-1]
        return Content(
            id="global-upload",
            name=display_name,
            path=inserted["path"],
            metadata=inserted["metadata"],
        )

    monkeypatch.setattr(runtime.repository, "latest_knowledge_by_name", latest)
    response = client.post(
        "/api/v1/knowledge/files",
        data={"name": "公共规程", "metadata": '{"category":"规程"}'},
        files={"file": ("manual.txt", "公共知识", "text/plain")},
    )
    assert response.status_code == 201
    metadata = knowledge.insert_calls[0]["metadata"]
    assert metadata["category"] == "规程"
    assert Path(knowledge.insert_calls[0]["path"]).parent.name == "global"


def test_duplicate_file_upload_is_rejected(
    client: TestClient,
    runtime: AgentRuntime,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime.knowledge = FakeKnowledge()  # type: ignore[assignment]
    runtime.settings.knowledge_upload_directory = tmp_path / "uploads"
    duplicate = Content(id="existing", name="已有规程", metadata={"display_name": "已有规程"})
    monkeypatch.setattr(runtime.repository, "knowledge_by_sha256", lambda _sha: duplicate)
    response = client.post(
        "/api/v1/knowledge/files",
        files={"file": ("manual.txt", "公共知识", "text/plain")},
    )
    assert response.status_code == 409


def test_chat_uses_fixed_local_user(
    client: TestClient, runtime: AgentRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    class Output:
        def to_dict(self) -> dict[str, str]:
            return {"content": "ok"}

    async def run(*args: Any, **kwargs: Any) -> Output:
        calls.append(kwargs)
        return Output()

    monkeypatch.setattr(runtime.agent, "arun", run)
    conversation = client.post("/api/v1/conversations", json={"title": "知识问答"}).json()
    response = client.post(
        f"/api/v1/conversations/{conversation['session_id']}/messages",
        json={"message": "查询规程", "stream": False},
    )
    assert response.status_code == 200
    assert calls[0]["user_id"] == "local-user"
