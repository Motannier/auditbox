import asyncio
import json

import pytest
from auditbox.audit import logger as logger_module
from auditbox.audit.store import AuditStore, redact
from auditbox.gateway import app as gateway
from auditbox.gateway import auth
from auditbox.relay.command_gate import CommandGate
from auditbox.relay.input_frames import InputDecoder, encode_input
from fastapi.testclient import TestClient


@pytest.fixture
def store(tmp_path, monkeypatch):
    db = AuditStore(tmp_path / "audit.sqlite3")
    monkeypatch.setattr(logger_module, "AUDIT_DIR", str(tmp_path / "jsonl"))
    monkeypatch.setattr(logger_module, "AuditStore", lambda: db)
    monkeypatch.setattr(gateway, "DB_PATH", tmp_path / "audit.sqlite3")
    return db


def logger(store, **kwargs):
    return logger_module.AuditLogger(
        "session1",
        "switch:22",
        "ssh",
        account="alice",
        login_id="login1",
        device_id="switch1",
        **kwargs,
    )


def test_identity_and_update_time_are_persistent(store):
    first = store.info()
    assert first["last_system_update_at"] is None
    store.mark_updated("2026-09-21T09:00:00+08:00")
    assert AuditStore(store.path).info() == {
        "box_id": first["box_id"],
        "last_system_update_at": "2026-09-21T01:00:00.000000+00:00",
    }


def test_login_links_token_and_refresh_does_not_create_login(store, monkeypatch):
    monkeypatch.setattr(auth, "AUDITBOX_SUPER_ADMIN_PASSWORD", "test-password")
    client = TestClient(gateway.app)
    failed = client.post(
        "/api/auth/login", json={"username": "superadmin", "password": "wrong"}
    )
    assert failed.status_code == 401
    result = client.post(
        "/api/auth/login",
        json={"username": "superadmin", "password": "test-password"},
        headers={"User-Agent": "test-browser"},
    )
    principal = auth.decode_access_token(result.json()["token"])
    assert store.get("logins", principal.login_id)["success"]
    refreshed = auth.issue_access_token(principal)
    assert auth.decode_access_token(refreshed).login_id == principal.login_id
    assert store.records("logins")["total"] == 2
    assert "test-password" not in json.dumps(store.changes())
    headers = {"Authorization": f"Bearer {refreshed}"}
    assert (
        client.get("/api/box/info", headers=headers).json()["box_id"]
        == store.info()["box_id"]
    )
    assert client.get("/api/audit/v2/changes", headers=headers).status_code == 200
    assert client.get("/api/audit/v2/logins").status_code == 401


def test_manual_repeated_commands_keep_distinct_executions(store):
    audit = logger(store)
    audit.log_command("display version")
    audit.log_terminal_output(b"version output\npassword simple sample-secret\n")
    audit.log_command_result("display version", success=True)
    audit.log_command("display version")
    audit.close()
    records = store.records("executions", login_id="login1")["items"]
    assert len(records) == 2 and len({r["id"] for r in records}) == 2
    assert all(r["status"] == "unknown" for r in records)
    assert "sample-secret" not in json.dumps(store.changes())
    for record in records:
        source = store.get("inputs", record["input_id"])
        assert source["label"] == "手动输入" and source["execution_id"] == record["id"]


def test_window_high_risk_does_not_overwrite_per_command_execution_grades(store):
    audit = logger(store)
    audit.log_command("systemctl stop nginx")
    audit.log_operation("security_model_review", {
        "risk": "high", "evidence": [1], "current": {"seq": 1, "risk": "high", "reason": "停止服务"},
        "findings": ["seq=1 risk=high status=executed rule=model_current"],
    })
    audit.log_command("uptime")
    audit.log_operation("security_model_review", {
        "risk": "high", "reason": "历史存在停止服务", "evidence": [1, 2],
        "current": {"seq": 2, "risk": "low", "reason": "只读查询"},
        "findings": ["seq=2 risk=low status=executed rule=model_current"],
    })
    audit.close()
    records = {item["command"]: item for item in store.records("executions")["items"]}
    assert records["systemctl stop nginx"]["risk"] == "high"
    assert records["uptime"]["risk"] == "low" and records["uptime"]["risk_reason"] == "只读查询"
    assert records["uptime"]["review_source"] == "model"


def test_confirmation_one_record_and_related_behavior(store):
    audit = logger(store)
    audit.log_confirmation_required(
        "reboot", {"level": "danger", "reason": "重启"}, "opaque"
    )
    record = store.records("executions")["items"][0]
    audit.log_confirmation_result("reboot", False)
    audit.close()
    assert store.records("executions")["total"] == 1
    assert store.get("executions", record["id"])["status"] == "cancelled"
    assert (
        store.records("behaviors", execution_id=record["id"])["items"][0]["action"]
        == "command_cancelled"
    )


def test_confirmed_execution_remains_linked_and_failure_is_not_success(store):
    audit = logger(store)
    audit.log_confirmation_required("reboot", {"level": "danger"}, "opaque")
    record = store.records("executions")["items"][0]
    audit.log_confirmation_result("reboot", True)
    audit.log_authorized_command("reboot")
    audit.log_command_result("reboot", success=False)
    audit.close()
    updated = store.get("executions", record["id"])
    assert updated["authorized"] and updated["status"] == "failed"
    assert store.records("executions")["total"] == 1


def test_ai_origin_travels_with_input_not_browser_metadata(store):
    origin = {
        "conversation_id": "chat1",
        "run_id": "run1",
        "question": "查看版本",
        "actor": {"account": "bob", "login_id": "login2"},
    }
    audit = logger(store)
    gate = CommandGate(audit, lambda data: None, lambda data: None, input_framing=True)
    gate.feed_transport(encode_input(b"display version\n", "agent", origin))
    audit.close()
    record = store.records("executions")["items"][0]
    assert record["account"] == "bob" and record["login_id"] == "login2"
    source = store.get("inputs", record["input_id"])
    assert source["conversation_id"] == "chat1" and source["run_id"] == "run1"
    assert InputDecoder(True).feed(encode_input(b"hi", "human")) == [(b"hi", "human")]


def test_incremental_changes_include_late_results_and_preserve_order(store):
    row = store.execution({"login_id": "l"}, "ls", "human")
    first = store.changes(limit=1)
    second = store.changes(first["next_cursor"])
    assert first["has_more"] and second["items"][0]["kind"] == "executions"
    store.update("executions", row["id"], status="failed")
    assert (
        store.changes(second["next_cursor"])["items"][0]["record"]["status"] == "failed"
    )
    with pytest.raises(ValueError):
        store.changes(100000)


def test_source_snapshot_survives_deleted_conversation(store, monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(gateway, "require_super_admin", lambda request: None)

    async def missing(*args, **kwargs):
        raise HTTPException(404)

    monkeypatch.setattr(gateway, "_request_agent_json", missing)
    inp = store.put(
        "inputs",
        {"source": "agent", "conversation_id": "chat1", "question": "看看版本"},
    )
    result = asyncio.run(gateway.audit_conversation(inp["id"], None))
    assert not result["available"] and result["snapshot"]["question"] == "看看版本"


def test_redaction_covers_switch_and_cli_values():
    assert "secret-value" not in redact("password simple secret-value")
    assert "secret-value" not in redact('curl --token="secret-value"')
    assert redact({"password": "secret-value"}) == {"password": "[已隐藏]"}


def test_restart_marks_unfinished_commands_without_inventing_success(store):
    submitted = store.execution({"session_id": "lost"}, "show version", "human")
    pending = store.execution(
        {"session_id": "lost"}, "reboot", "human", status="awaiting_confirmation"
    )
    store.recover_interrupted()
    assert store.get("executions", submitted["id"])["status"] == "unknown"
    assert store.get("executions", pending["id"])["status"] == "cancelled"
    cursor = store.changes()["next_cursor"]
    store.recover_interrupted()
    assert not store.changes(cursor)["items"]


def test_output_flush_does_not_mix_commands_and_close_stops_timer(store):
    audit = logger(store)
    audit.log_command("first")
    audit.log_terminal_output(b"first output")
    audit.log_command("second")
    audit.log_terminal_output(b"second output")
    audit.close()
    rows = {row["command"]: row for row in store.records("executions")["items"]}
    assert rows["first"]["output"] == "first output"
    assert rows["second"]["output"] == "second output"
    cursor = store.changes()["next_cursor"]
    audit._persist_output()
    assert not store.changes(cursor)["items"]
