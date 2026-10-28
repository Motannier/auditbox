from pathlib import Path

import pytest
from agno.db.base import SessionType
from agno.models.response import ToolExecution
from agno.run.agent import RunOutput
from agno.run.base import RunStatus
from fastapi.testclient import TestClient

from auditbox_agent.config import McpServerSettings, Settings
from auditbox_agent.main import create_app
from auditbox_agent.runtime import AgentRuntime
from auditbox_agent.storage import (
    FileStaging,
    StorageValidationError,
    checked_storage_path,
    validate_uploaded_file,
)


class _Event:
    def to_dict(self) -> dict[str, str]:
        return {"event": "RunContent", "run_id": "run-approval", "content": "已处理审批"}


def test_cancel_run_only_accepts_active_local_run(
    client: TestClient, runtime: AgentRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def cancel(run_id: str) -> bool:
        calls.append(run_id)
        return True

    monkeypatch.setattr(runtime.agent, "acancel_run", cancel)
    assert client.post("/api/v1/runs/run-a/cancel").status_code == 404
    runtime.register_active_run("run-a")
    allowed = client.post("/api/v1/runs/run-a/cancel")
    assert allowed.status_code == 200
    assert calls == ["run-a"]


@pytest.mark.parametrize("mode,expected_mode", [
    ("standard", "standard"), ("enhanced", "enhanced"), ("fast", "enhanced")
])
def test_tool_run_continues_only_with_explicit_approval(
    client: TestClient, runtime: AgentRuntime, monkeypatch: pytest.MonkeyPatch,
    mode: str, expected_mode: str,
) -> None:
    conversation_id = client.post("/api/v1/conversations", json={"title": "审批测试"}).json()[
        "session_id"
    ]
    confirmed: list[bool | None] = []
    session = runtime.db.get_session(conversation_id, SessionType.AGENT)
    session.runs = [RunOutput(
        run_id="run-approval", agent_id=runtime.agent_id,
        session_id=conversation_id, status=RunStatus.paused,
        metadata={"display_message": "查看版本", "audit_actor": {"account": "old"}},
        tools=[ToolExecution(
            tool_call_id="tool-1", tool_name="send_terminal_command",
            tool_args={"command": "show version"}, requires_confirmation=True,
        )],
    )]
    runtime.db.upsert_session(session)

    def continue_run(**kwargs):
        assert kwargs["run_response"].run_id == "run-approval"
        assert kwargs["metadata"]["display_message"] == "查看版本"
        assert kwargs["metadata"]["audit_actor"] == {
            "account": "current",
            "login_id": "login-current",
        }
        confirmed.extend(tool.confirmed for tool in kwargs["run_response"].tools)

        async def events():
            yield _Event()

        return events()

    monkeypatch.setattr(runtime.agent, "acontinue_run", continue_run)

    selected_modes = []

    def select_agent(value):
        selected_modes.append(value)
        return runtime.agent

    monkeypatch.setattr(runtime, "agent_for_mode", select_agent)
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/runs/run-approval/continue",
        json={
            "mode": mode,
            "approved": True,
            "audit_actor": {"account": "current", "login_id": "login-current"},
            "tools": [
                {
                    "tool_call_id": "tool-1",
                    "tool_name": "send_terminal_command",
                    "tool_args": {"command": "show version"},
                    "requires_confirmation": True,
                }
            ],
        },
    )
    assert response.status_code == 200
    assert confirmed == [True]
    assert selected_modes == [expected_mode]
    assert "已处理审批" in response.text
    stored = client.get(f"/api/v1/conversations/{conversation_id}").json()
    snapshot = stored["metadata"]["public_run_snapshots"]["run-approval"]
    assert snapshot["content"] == "已处理审批"
    assert snapshot["timeline"] == [{"kind": "content", "content": "已处理审批"}]


def test_removed_task_report_and_remote_knowledge_routes_are_absent(client: TestClient) -> None:
    assert client.get("/api/v1/tasks").status_code == 404
    assert client.get("/api/v1/task-templates").status_code == 404
    assert client.get("/api/v1/artifacts/example").status_code == 404
    assert client.post("/api/v1/knowledge", json={"url": "http://127.0.0.1"}).status_code == 405


def test_agentos_scheduler_routes_are_blocked(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{tmp_path / 'agent.db'}",
        model="test-model",
        model_base_url="http://model.invalid/v1",
        model_api_key="test-key",
        knowledge_enabled=False,
        skill_directory=tmp_path / "skills",
    )
    with TestClient(create_app(settings)) as app_client:
        assert app_client.get("/schedules").status_code == 404
        assert app_client.post("/schedules/example/trigger").status_code == 404


def test_mcp_mount_is_allowlisted_and_confirmation_required(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        execution_enabled=True,
        database_url=f"sqlite:///{tmp_path / 'agent.db'}",
        model="test-model",
        model_base_url="http://model.invalid/v1",
        model_api_key="test-key",
        knowledge_enabled=False,
        skill_directory=tmp_path / "skills",
        mcp_servers=[
            McpServerSettings(
                name="operations",
                url="http://mcp.invalid/mcp",
                tools=["query_device", "send_terminal_command"],
            )
        ],
    )
    runtime = AgentRuntime(settings)
    try:
        toolkit = runtime.mcp_tools[0]
        assert toolkit.include_tools == ["query_device", "send_terminal_command"]
        assert toolkit.requires_confirmation_tools == [
            "query_device",
            "send_terminal_command",
        ]
    finally:
        runtime.close()


def test_upload_signature_validation_and_file_staging(tmp_path: Path) -> None:
    fake_pdf = tmp_path / "bad.pdf"
    fake_pdf.write_text("not a pdf", encoding="utf-8")
    with pytest.raises(StorageValidationError):
        validate_uploaded_file(fake_pdf, ".pdf")

    source = tmp_path / "manual.html"
    source.write_text("ok", encoding="utf-8")
    staging = FileStaging(tmp_path)
    staging.stage([source])
    assert not source.exists()
    staging.rollback()
    assert source.read_text(encoding="utf-8") == "ok"


def test_checked_storage_path_rejects_outside_file(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    inside = upload_root / "global" / "manual.txt"
    inside.parent.mkdir(parents=True)
    inside.write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    assert checked_storage_path(str(inside), upload_root) == inside
    assert checked_storage_path(str(outside), upload_root) is None
