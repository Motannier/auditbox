import csv
import io
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from auditbox.audit import logger as logger_module
from auditbox.audit.behavior import bounded_details
from auditbox.audit.store import AuditStore
from auditbox.gateway import app as gateway
from auditbox.gateway.auth import Principal, ROLE_ADMIN
from auditbox.relay.command_gate import CommandGate


@pytest.fixture
def audit(tmp_path, monkeypatch):
    store = AuditStore(tmp_path / "audit.sqlite3")
    monkeypatch.setattr(logger_module, "AUDIT_DIR", tmp_path / "logs")
    monkeypatch.setattr(logger_module, "AuditStore", lambda: store)
    monkeypatch.setattr(logger_module, "CommandAuditRepository", lambda: SimpleNamespace(create=lambda _: None))
    monkeypatch.setattr(gateway, "DB_PATH", store.path)
    monkeypatch.setattr(gateway, "AUDIT_DIR", tmp_path / "logs")
    monkeypatch.setattr(gateway, "require_authenticated", lambda _: Principal("alice", ROLE_ADMIN, "login1"))
    return store, tmp_path / "logs"


def test_automatic_requests_do_not_create_user_behaviors(audit, monkeypatch):
    store, logs = audit
    monkeypatch.setattr(gateway, "_synchronize_browser_clock", lambda _: {"synchronized": False})

    async def agent(*args, **kwargs):
        return {"status": "ok", "model_ready": True}

    monkeypatch.setattr(gateway, "_request_agent_json", agent)
    client = TestClient(gateway.app)
    for _ in range(3):
        assert client.post("/api/system/clock/sync", json={"client_time_ms": 1_800_000_000_000}).status_code == 200
        assert client.get("/api/assistant/status").status_code == 200
        assert client.post("/api/assistant/conversations/one/title", json={"first_message": "自动标题"}).status_code == 200
        assert client.post("/api/assistant/conversations", json={"title": "自动初始会话"}).status_code == 201
    assert store.records("behaviors")["total"] == 0
    assert not list(logs.glob("*.jsonl"))


def test_ui_actions_keep_format_actor_and_redact_in_db_and_export(audit, monkeypatch):
    store, logs = audit
    client = TestClient(gateway.app)
    monkeypatch.setitem(gateway._active_terminal_sessions, "other-session", SimpleNamespace(
        audit_context={"account": "bob", "login_id": "other-login", "session_id": "other-session"}))
    events = [
        {"action": "ui_click", "label": "新建安全连接", "account": "forged", "session_id": "other-session"},
        {"action": "ui_input", "field": "设备名称", "value": "核心交换机"},
        {"action": "ui_input", "field": "登录密码", "value": "test-only-password", "redacted": False},
        {"action": "terminal_suggestion_applied", "field": "command", "value": "display version"},
        {"action": "terminal_paste", "field": "command", "value": "unmarked-clipboard-secret"},
        {"action": "ui_input", "field": "MCP 请求头", "value": '{"Authorization":"Bearer unfinished-header-secret'},
    ]
    assert client.post("/api/audit/behavior", json={"events": events}).status_code == 204
    rows = store.records("behaviors")["items"]
    assert len(rows) == 6 and all(row["account"] == "alice" and row["login_id"] == "login1" for row in rows)
    assert all(not row.get("session_id") for row in rows)
    exported = "".join(gateway._iter_audit_csv(logs))
    assert exported.startswith("\ufeff账号,时间,会话 ID,目标,协议,事件,命令,风险等级,说明,内容,详情")
    combined = exported + json.dumps(store.changes())
    for secret in ("test-only-password", "unmarked-clipboard-secret", "unfinished-header-secret"):
        assert secret not in combined
    assert "核心交换机" in exported and "display version" in exported
    assert len(list(csv.reader(io.StringIO(exported.lstrip("\ufeff"))))) == 7
    assert client.post("/api/audit/behavior", json={"events": [{"action": "security_model_review"}]}).status_code == 422
    assert store.records("behaviors")["total"] == 6


def test_ui_audit_requires_authentication(audit, monkeypatch):
    from auditbox.gateway.auth import require_authenticated
    monkeypatch.setattr(gateway, "require_authenticated", require_authenticated)
    assert TestClient(gateway.app).post("/api/audit/behavior", json={"events": [{"action": "ui_click"}]}).status_code == 401
    assert audit[0].records("behaviors")["total"] == 0


def test_api_user_changes_and_failed_actions_are_recorded(audit, monkeypatch):
    store, logs = audit

    async def agent(*args, **kwargs):
        return {"enabled": False}

    monkeypatch.setattr(gateway, "_request_agent_json", agent)
    client = TestClient(gateway.app)
    assert client.patch("/api/assistant/capabilities/test", json={"enabled": False}).status_code == 200
    assert client.patch("/api/assistant/capabilities/test", json={"enabled": "invalid"}).status_code == 422
    rows = store.records("behaviors")["items"]
    assert {row["result"] for row in rows} == {"success", "failed"}
    assert all(row["details"]["http_status"] in {200, 422} for row in rows)
    assert "PATCH /api/assistant/capabilities/test" in "".join(gateway._iter_audit_csv(logs))


def test_reviews_enrich_commands_without_autonomous_actions(audit):
    store, logs = audit
    logger = logger_module.AuditLogger("one", "switch:22", "ssh", account="alice", login_id="login1")
    logger.log_command("display version")
    logger.log_operation("security_rule_review", {"risk": "low", "evidence": [1]})
    logger.log_operation("security_terminal_input_recovery_started", {"status": "started"})
    logger.log_operation("guide_command_completed", {"command": "display version"})
    logger.log_command_result("display version", success=True)
    logger.log_operation("guide_scenario_opened", {"scenario_id": "interfaces", "account": "forged"})
    logger.close()
    assert store.records("executions")["items"][0]["risk"] == "low"
    assert [row["action"] for row in store.records("behaviors")["items"]] == ["guide_scenario_opened"]
    records = [json.loads(line) for line in (logs / "one.jsonl").read_text().splitlines()]
    assert not any(row["event"].startswith("security_") or row["event"] in {"command_result", "guide_command_completed", "session_end"} for row in records)
    assert any(row["event"] == "command" and row["command"] == "display version" for row in records)


def test_terminal_controls_and_cancelled_command_are_user_actions(audit):
    store, logs = audit
    logger = logger_module.AuditLogger("one", "switch:22", "ssh", account="alice")
    gate = CommandGate(logger, lambda _: None, lambda _: None)
    gate.feed(b"display version\x03")
    logger.log_terminal_state({"mode": "pager", "version": 1})
    logger.log_terminal_input(b" ")
    logger.log_terminal_state({"mode": "secret", "version": 2})
    logger.log_terminal_input(b"private-password\r", sensitive=True)
    logger.close()
    text = (logs / "one.jsonl").read_text()
    records = [json.loads(line) for line in text.splitlines()]
    assert "private-password" not in text
    assert any(row["event"] == "terminal_input_cancelled" and row["details"]["command"] == "display version" for row in records)
    assert any(row["event"] == "terminal_input" and row["mode"] == "pager" and row["data"] == " " for row in records)
    assert store.records("executions")["total"] == 0


def test_nested_and_structured_credentials_are_redacted():
    value = bounded_details({"tools": [{"tool_args": {"target_password": "private-password"}}],
                             "value": '{"api_key":"private-api-key"}'})
    assert "private-" not in json.dumps(value)
