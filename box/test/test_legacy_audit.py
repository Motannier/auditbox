import base64
import hashlib
import json

import pytest
from auditbox.audit.legacy import convert_session, migrate
from auditbox.audit.store import AuditStore


def event(name, **values):
    return {
        "event": name,
        "ts": "2026-06-05T13:00:00Z",
        "account": "opsadmin",
        **values,
    }


def converted(*events):
    rows = [
        event("session_start", session_id="old1", target="switch:22", protocol="ssh"),
        *events,
    ]
    result = convert_session("box-test", "old1", rows, "source-hash")
    return {
        kind: [row for k, row in result if k == kind]
        for kind in ("executions", "inputs", "behaviors", "logins")
    }


def write_source(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_terminal_is_not_login_and_missing_source_is_unknown():
    rows = converted(event("command", command="ls"), event("session_end"))
    assert rows["logins"] == []
    exe, inp = rows["executions"][0], rows["inputs"][0]
    assert exe["login_id"] is None and exe["source"] == "unknown"
    assert inp["label"] == "历史未记录输入来源"
    assert exe["status"] == "unknown" and exe["target"] == "switch:22"
    assert inp["execution_id"] == exe["id"] and exe["input_id"] == inp["id"]
    assert exe["ts"] == "2026-06-05T13:00:00.000000+00:00"
    assert rows["behaviors"][0]["action"] == "legacy_terminal_session_started"


def test_confirmation_does_not_duplicate_command_but_repeated_commands_are_distinct():
    rows = converted(
        event("command_confirmation_required", command="reboot", level="danger"),
        event("command_confirmed", command="reboot"),
        event("command", command="reboot", authorized=True),
        event("command", command="reboot"),
    )
    assert len(rows["executions"]) == len(rows["inputs"]) == 2
    a, b = rows["executions"]
    assert a["id"] != b["id"] and a["authorized"] and a["risk"] == "danger"
    assert {r["execution_id"] for r in rows["behaviors"][1:]} == {a["id"]}


def test_cancel_and_block_are_not_sent_and_fill_is_not_execution():
    rows = converted(
        event("guide_command_filled", details={"command": "reboot"}),
        event("command_confirmation_required", command="reboot"),
        event("command_confirmation_cancelled", command="reboot"),
        event("blocked_command", command="reset"),
    )
    assert [r["status"] for r in rows["executions"]] == ["cancelled", "blocked"]


def test_outputs_are_merged_decoded_bounded_and_redacted():
    secret = "password simple private-value\n版本正常".encode()
    rows = converted(
        event("command", command="show version", input_source="human"),
        event("terminal_output", data_b64=base64.b64encode(secret[:-2]).decode()),
        event("terminal_output", data_b64=base64.b64encode(secret[-2:]).decode()),
        event("command_result", command="show version", status="succeeded"),
        event("command", command="again"),
        event("terminal_output", data_b64=base64.b64encode(b"a" * 90000).decode()),
    )
    first, second = rows["executions"]
    assert "版本正常" in first["output"] and "private-value" not in json.dumps(rows)
    assert first["status"] == "unknown" and "退出码" in first["result_basis"]
    assert second["output"] == "a" * 16000 and second["output_truncated"]


def test_results_only_match_current_command_and_model_evidence_accepts_string_seq():
    rows = converted(
        event("command", command="a"),
        event("command", command="b"),
        event("command_result", command="a", status="failed"),
        event(
            "security_model_review",
            details={"risk": "high", "reason": "理由", "evidence": ["1"]},
        ),
        event("command_result", command="b", status="failed"),
    )
    first, second = rows["executions"]
    assert first["status"] == "unknown" and second["status"] == "failed"
    assert first["risk"] == "high" and second["risk"] == "unknown"
    review = next(
        r for r in rows["behaviors"] if r["action"] == "security_model_review"
    )
    assert review["execution_id"] == first["id"]


def test_ai_flag_does_not_invent_chat_link_and_orphan_confirmation_is_unknown():
    rows = converted(
        event("command", command="ls", input_source="agent"),
        event("command_confirmation_required", command="reset"),
        event("session_end"),
    )
    assert rows["inputs"][0]["source"] == "agent"
    assert "conversation_id" not in rows["inputs"][0]
    assert rows["executions"][1]["status"] == "unknown"


def test_scoped_jsonl_model_review_preserves_individual_grades_on_reimport():
    rows = converted(
        event("command", command="systemctl stop nginx"),
        event("security_model_review", details={"risk": "high", "evidence": ["1"],
            "current": {"seq": "1", "risk": "high", "reason": "停止服务"},
            "findings": ["seq=1 risk=high status=executed rule=model_current"]}),
        event("command", command="uptime"),
        event("security_model_review", details={"risk": "high", "evidence": ["1", "2"],
            "current": {"seq": "2", "risk": "low", "reason": "只读查询"},
            "findings": ["seq=2 risk=low status=executed rule=model_current"]}),
    )
    first, second = rows["executions"]
    assert first["risk"] == "high" and second["risk"] == "low"
    assert second["risk_reason"] == "只读查询" and second["review_source"] == "model"


def test_conversion_is_atomic_idempotent_and_preserves_originals(tmp_path, monkeypatch):
    store = AuditStore(tmp_path / "db.sqlite3")
    directory = tmp_path / "jsonl"
    directory.mkdir()
    source = directory / "one.jsonl"
    write_source(source, [event("command", command="ls")])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert migrate(store, directory)["records"] == {"executions": 1, "inputs": 1}
    assert not store.changes()["items"]
    original = store._put

    def fail(conn, kind, value):
        if kind == "inputs":
            raise RuntimeError("simulated interruption")
        return original(conn, kind, value)

    monkeypatch.setattr(store, "_put", fail)
    with pytest.raises(RuntimeError):
        migrate(store, directory, apply=True)
    assert not store.changes()["items"]
    monkeypatch.setattr(store, "_put", original)
    migrate(store, directory, apply=True)
    cursor = store.changes()["next_cursor"]
    assert migrate(store, directory, apply=True)["already_converted"] == 1
    assert not store.changes(cursor)["items"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    write_source(source, [event("command", command="changed")])
    with pytest.raises(ValueError, match="source changed"):
        migrate(store, directory, apply=True)


def test_native_records_and_malformed_sources_are_not_double_imported(tmp_path):
    store = AuditStore(tmp_path / "db.sqlite3")
    directory = tmp_path / "jsonl"
    directory.mkdir()
    store.execution({"session_id": "native"}, "ls", "human")
    write_source(directory / "native.jsonl", [event("command", command="ls")])
    assert migrate(store, directory, apply=True)["native_sessions_skipped"] == 1
    assert store.records("executions")["total"] == 1
    (directory / "broken.jsonl").write_text('{"event":')
    with pytest.raises(ValueError, match="Invalid legacy"):
        migrate(store, directory, apply=True)
    assert store.records("executions")["total"] == 1
