import asyncio
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from agno.tools.mcp import MCPTools
from fastapi.testclient import TestClient

from auditbox_agent.runtime import (
    AgentRuntime,
    ApprovalRequiredMCPTools,
    MCPProbeError,
    SkillManagementTools,
    TerminalWorkspaceTools,
    mcp_tool_prefix,
)


def test_default_tools_are_installed_hidden_and_default_skills_enabled(
    client: TestClient,
) -> None:
    result = client.get("/api/v1/capabilities")
    assert result.status_code == 200
    rows = result.json()["data"]
    tools = [row for row in rows if row["kind"] == "tool"]
    skills = [row for row in rows if row["kind"] == "skill"]

    assert len(tools) == 4
    assert all(row["enabled"] is False for row in tools)
    assert {row["id"] for row in skills} == {
        "skill:skill-creator",
        "skill:skill-vetter",
    }
    assert all(row["enabled"] is True for row in skills)


def test_enabling_toolkit_rebuilds_agent_and_requires_confirmation(
    client: TestClient, runtime: AgentRuntime
) -> None:
    result = client.patch("/api/v1/capabilities/tool:file-generation", json={"enabled": True})
    assert result.status_code == 200
    assert result.json()["enabled"] is True
    toolkit = next(tool for tool in runtime.local_tools if tool.name == "file_generation")
    assert toolkit.functions
    assert all(function.requires_confirmation for function in toolkit.functions.values())

    disabled = client.patch(
        "/api/v1/capabilities/tool:file-generation", json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert all(tool.name != "file_generation" for tool in runtime.local_tools)


def test_terminal_workspace_write_tools_require_confirmation(runtime: AgentRuntime) -> None:
    toolkit = TerminalWorkspaceTools(runtime.settings)

    assert set(toolkit.functions) == {
        "list_terminal_sessions",
        "read_terminal",
        "send_terminal_command",
        "interrupt_terminal",
    }
    assert toolkit.functions["list_terminal_sessions"].requires_confirmation is False
    assert toolkit.functions["read_terminal"].requires_confirmation is False
    assert toolkit.functions["send_terminal_command"].requires_confirmation is True
    assert toolkit.functions["interrupt_terminal"].requires_confirmation is True


def test_skill_management_creates_and_mounts_skill_after_active_run(
    runtime: AgentRuntime,
) -> None:
    toolkit = runtime.skill_management_tools
    assert isinstance(toolkit, SkillManagementTools)
    assert toolkit.functions["list_installed_skills"].requires_confirmation is False
    assert toolkit.functions["create_skill"].requires_confirmation is True
    previous_agent = runtime.agent
    runtime.register_active_run("skill-creation", previous_agent)

    result = json.loads(
        toolkit.create_skill(
            "网络巡检",
            "检查 Linux 主机的基础网络状态。",
            "依次检查地址、路由和 DNS，并总结异常。",
        )
    )

    assert result["status"] == "installed"
    assert result["enabled"] is True
    assert runtime.agent is previous_agent
    assert runtime.repository.get_capability(result["skill_id"]) is not None
    runtime.unregister_active_run("skill-creation")
    assert runtime.agent is not previous_agent
    assert "网络巡检" in runtime.capability_catalog()
    installed = runtime.repository.get_capability(result["skill_id"])
    assert installed is not None
    folder = installed["config"]["folder"]
    assert (runtime.settings.skill_directory / folder / "SKILL.md").is_file()
    assert runtime.agent.skills is not None
    assert folder in runtime.agent.skills.get_skill_names()


def test_skill_management_registers_workspace_skill(runtime: AgentRuntime) -> None:
    source = runtime.settings.workspace_directory / "disk-helper"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: disk-helper\ndescription: Diagnose disk usage.\n---\n\nCheck df and du.\n",
        encoding="utf-8",
    )

    result = json.loads(runtime.skill_management_tools.register_workspace_skill("disk-helper"))

    assert result["status"] == "installed"
    assert runtime.repository.get_capability("skill:disk-helper") is not None
    assert (runtime.settings.skill_directory / "disk-helper" / "SKILL.md").is_file()


def test_enabled_capabilities_are_exposed_directly_in_system_context(
    runtime: AgentRuntime,
) -> None:
    catalog = runtime.capability_catalog()
    agent_tool_names = {
        name
        for toolkit in runtime.agent.tools or []
        if hasattr(toolkit, "functions")
        for name in toolkit.functions
    }

    assert "<capability_catalog>" in catalog
    assert "Skill Creator" in catalog
    assert "Skill Vetter" in catalog
    assert '"load_with":"get_skill_instructions"' in catalog
    assert "list_agent_capabilities" not in catalog
    assert "list_agent_capabilities" not in agent_tool_names


def test_standard_prompt_routes_skill_and_terminal_actions_explicitly(
    runtime: AgentRuntime,
) -> None:
    prompt = "\n".join(runtime._agent_instructions("standard"))

    assert "显式点名 Skill > 显式点名 MCP" in prompt
    assert "点名 Skill 必须先调用 get_skill_instructions" in prompt
    assert "没有 ToolCall 即为失败" in prompt
    assert "禁止只输出函数名或‘我将调用’" in prompt
    assert "审批后自动执行并检查 status/output" in prompt
    assert "可合并历史中明确标注为已执行的事实" in prompt
    assert "用户限定命令范围时不得扩大" in prompt
    assert "必须原样调用该命令" in prompt
    assert "不得把 Skill/MCP 任务改写为 echo" in prompt
    assert "工具结束后只给一份连贯答复" in prompt
    assert "不确定输出格式含义时原样引用" in prompt


def test_user_can_import_and_delete_skill_package(
    client: TestClient, runtime: AgentRuntime
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr(
            "network-helper/SKILL.md",
            "---\nname: network-helper\ndescription: Diagnose local network links.\n---\n",
        )
    response = client.post(
        "/api/v1/capabilities/skills",
        files={"file": ("network-helper.zip", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "skill"
    assert response.json()["enabled"] is True
    capability_id = response.json()["id"]
    folder = response.json()["config"]["folder"]
    assert (runtime.settings.skill_directory / folder).is_dir()

    deleted = client.delete(f"/api/v1/capabilities/{capability_id}")

    assert deleted.status_code == 200
    assert deleted.json() == {"id": capability_id, "deleted": True}
    assert runtime.repository.get_capability(capability_id) is None
    assert not (runtime.settings.skill_directory / folder).exists()


def test_user_can_delete_agent_created_skill(client: TestClient, runtime: AgentRuntime) -> None:
    created = json.loads(
        runtime.skill_management_tools.create_skill(
            "log-helper",
            "Summarize service logs.",
            "Read the relevant log excerpt and summarize errors.",
        )
    )

    deleted = client.delete(f"/api/v1/capabilities/{created['skill_id']}")

    assert deleted.status_code == 200
    assert runtime.repository.get_capability(created["skill_id"]) is None


def test_system_capabilities_cannot_be_deleted(client: TestClient) -> None:
    response = client.delete("/api/v1/capabilities/skill:skill-creator")

    assert response.status_code == 403
    assert "不能删除" in response.json()["detail"]


def test_user_can_import_mcp_with_allowlist_and_secret_redaction(
    client: TestClient,
    runtime: AgentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def probe(**_kwargs: object) -> list[str]:
        return ["query_device", "list_devices"]

    monkeypatch.setattr(runtime, "probe_mcp", probe)
    response = client.post(
        "/api/v1/capabilities/mcp",
        json={
            "name": "operations",
            "url": "http://127.0.0.1:8770/mcp",
            "tools": ["query_device"],
            "headers": {"Authorization": "Bearer local-test-secret"},
        },
    )
    assert response.status_code == 201
    assert response.json()["config"]["headers"] == {"Authorization": "***"}
    assert response.json()["config"]["discovered_tools"] == [
        "query_device",
        "list_devices",
    ]

    deleted = client.delete(f"/api/v1/capabilities/{response.json()['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


def test_user_can_import_mcp_without_an_explicit_tool_allowlist(
    client: TestClient,
    runtime: AgentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def probe(**_kwargs: object) -> list[str]:
        return ["inventory"]

    monkeypatch.setattr(runtime, "probe_mcp", probe)
    response = client.post(
        "/api/v1/capabilities/mcp",
        json={"name": "inventory", "url": "http://127.0.0.1:8771/mcp"},
    )
    assert response.status_code == 201
    toolkit = next(tool for tool in runtime.mcp_tools if tool.name == "inventory")
    assert toolkit.include_tools is None
    assert toolkit.tool_name_prefix == mcp_tool_prefix(response.json()["id"])


def test_mcp_capability_details_never_expose_runtime_namespace(
    runtime: AgentRuntime,
) -> None:
    capability_id = "mcp:connection-example"
    runtime.repository.upsert_capability(
        capability_id,
        "mcp",
        "服务器文件系统",
        True,
        {
            "url": "http://127.0.0.1:8771/mcp",
            "transport": "streamable-http",
            "discovered_tools": ["create_directory", "read_file"],
        },
        "user",
        1,
    )
    prefix = mcp_tool_prefix(capability_id)
    runtime.mcp_tools = [
        SimpleNamespace(
            capability_id=capability_id,
            connection_status="available",
            connection_error="",
            functions={
                f"{prefix}_create_directory": object(),
                f"{prefix}_read_file": object(),
            },
        )
    ]

    row = next(item for item in runtime.capability_details() if item["id"] == capability_id)

    assert row["discovered_tools"] == ["create_directory", "read_file"]
    assert prefix not in json.dumps(row, ensure_ascii=False)


def test_user_can_save_disabled_mcp_with_a_chinese_name(
    client: TestClient,
    runtime: AgentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def probe(**_kwargs: object) -> list[str]:
        return []

    monkeypatch.setattr(runtime, "probe_mcp", probe)
    response = client.post(
        "/api/v1/capabilities/mcp",
        json={
            "name": "设备管理工具",
            "url": "http://127.0.0.1:8772/mcp",
            "transport": "streamable-http",
            "enabled": False,
        },
    )

    assert response.status_code == 201
    assert response.json()["name"] == "设备管理工具"
    assert response.json()["enabled"] is False


def test_mcp_probe_failure_is_reported_without_saving(
    client: TestClient,
    runtime: AgentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def probe(**_kwargs: object) -> list[str]:
        raise MCPProbeError("MCP 不可达")

    monkeypatch.setattr(runtime, "probe_mcp", probe)
    response = client.post(
        "/api/v1/capabilities/mcp",
        json={"name": "offline", "url": "http://127.0.0.1:8779/mcp"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "MCP 不可达"
    assert runtime.repository.get_capability("mcp:offline") is None


def test_mcp_allowlist_must_match_discovered_tools(
    client: TestClient,
    runtime: AgentRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def probe(**_kwargs: object) -> list[str]:
        return ["list_devices"]

    monkeypatch.setattr(runtime, "probe_mcp", probe)
    response = client.post(
        "/api/v1/capabilities/mcp",
        json={
            "name": "inventory-restricted",
            "url": "http://127.0.0.1:8780/mcp",
            "tools": ["delete_device"],
        },
    )

    assert response.status_code == 422
    assert "delete_device" in response.json()["detail"]
    assert runtime.repository.get_capability("mcp:inventory-restricted") is None


@pytest.mark.asyncio
async def test_discovered_mcp_tools_always_require_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_build(toolkit: MCPTools) -> None:
        toolkit.functions["discovered_tool"] = SimpleNamespace(requires_confirmation=False)

    monkeypatch.setattr(MCPTools, "build_tools", fake_build)
    toolkit = ApprovalRequiredMCPTools(url="http://mcp.invalid/mcp")
    await toolkit.build_tools()
    assert toolkit.functions["discovered_tool"].requires_confirmation is True


@pytest.mark.asyncio
async def test_unavailable_mcp_is_skipped_without_blocking_agent_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_connect(_toolkit: MCPTools) -> None:
        raise OSError("network unavailable")

    monkeypatch.setattr(MCPTools, "_connect", fail_connect)
    toolkit = ApprovalRequiredMCPTools(
        capability_id="mcp:offline",
        name="offline",
        url="http://offline.invalid/mcp",
        refresh_connection=False,
    )

    await asyncio.wait_for(toolkit.connect(), timeout=1)

    assert toolkit.initialized is False
    assert toolkit.connection_status == "unavailable"
    assert toolkit.connection_error
    assert toolkit.functions == {}
    assert toolkit.refresh_connection is False


@pytest.mark.asyncio
async def test_mcp_transport_cancel_scope_failure_does_not_cancel_agent_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_connect(_toolkit: MCPTools) -> None:
        raise asyncio.CancelledError("Attempted to exit cancel scope in a different task")

    monkeypatch.setattr(MCPTools, "_connect", fail_connect)
    toolkit = ApprovalRequiredMCPTools(
        capability_id="mcp:cancelled-transport",
        name="cancelled-transport",
        url="http://offline.invalid/mcp",
        refresh_connection=False,
    )

    await asyncio.wait_for(toolkit.connect(), timeout=1)

    assert toolkit.initialized is False
    assert toolkit.connection_status == "unavailable"
    assert toolkit.connection_error


@pytest.mark.asyncio
async def test_real_unreachable_mcp_connection_degrades_cleanly() -> None:
    toolkit = ApprovalRequiredMCPTools(
        capability_id="mcp:unreachable-local",
        name="unreachable-local",
        url="http://127.0.0.1:9/mcp",
        timeout_seconds=1,
        refresh_connection=False,
    )

    await asyncio.wait_for(toolkit.connect(), timeout=3)

    assert toolkit.initialized is False
    assert toolkit.connection_status == "unavailable"
    assert toolkit.functions == {}
