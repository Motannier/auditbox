"""Current command grades never inherit window risk or stale async snapshots."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from auditbox.gateway import app as gateway
from auditbox.inference import routing
from auditbox.prediction import engine as engine_module
from auditbox.prediction.engine import PredictionEngine
from auditbox.security import history_review
from auditbox.security.history_review import _PendingReview, _review_history
from auditbox.security.review_result import current_command_review
from auditbox.security.switch_history_review import RULE_REVIEW_PROFILES, review_switch_history
from test_prediction_engine import CaptureSocket
from test_switch_history_review import CaptureReviewer, entry


@pytest.mark.parametrize("profile", RULE_REVIEW_PROFILES)
@pytest.mark.parametrize("status", ["cancelled", "blocked", "failed", "executed"])
def test_rule_history_retains_danger_but_latest_query_is_low(profile, status):
    query = "display version" if profile == "huawei_vrp" else "show version"
    history = [entry("shutdown", 1, status=status), entry(query, 2)]
    result = review_switch_history(history, profile)
    assert result["risk"] == "high" and result["evidence"] == [1]
    assert current_command_review({**result, "history": history})["risk"] == "low"
    assert [item["risk"] for item in result["findings"]] == ["high", "low"]


@pytest.mark.parametrize("profile", ["linux", "h3c_comware", "cisco_ios"])
def test_queue_reset_and_snapshot_revisions_prevent_stale_risk(tmp_path, monkeypatch, profile):
    monkeypatch.setattr(engine_module, "AUDITBOX_HISTORY_REVIEW_ENABLED", False)
    engine = PredictionEngine(tmp_path / "review.db")
    engine._reviewer, engine._rules_reviewer = CaptureReviewer(), CaptureReviewer()
    socket = CaptureSocket()
    try:
        engine.register_session("one", "ssh", device_type="server" if profile == "linux" else "switch", device_profile=profile)
        engine.add_subscriber("one", socket)
        engine.on_command("one", "shutdown", {"status": "cancelled"})
        token = engine._sessions["one"]["review"]["review_token"]
        dangerous = {"status": "ready", "risk": "high", "reason": "历史关闭接口",
                     "evidence": [1], "reviewed_through_seq": 1, "review_token": token,
                     "current": {"seq": 1, "risk": "high", "reason": "关闭接口"},
                     "findings": [{"seq": 1, "risk": "high", "reason": "关闭接口", "status": "cancelled", "code": "test"}]}
        engine._on_review_result("one", dangerous)
        engine.on_command("one", "display version")
        queued = engine._sessions["one"]["review"]
        assert queued["risk"] == queued["current"]["risk"] == "unknown"
        assert queued["evidence"] == [] and queued["context_review"]["risk"] == "high"
        before = len(socket.messages)
        engine._on_review_result("one", dangerous)
        assert len(socket.messages) == before and engine._sessions["one"]["review"] == queued
        latest_token = queued["review_token"]
        engine.on_command_result("one", "display version", success=False)
        assert engine._sessions["one"]["review"]["review_token"] != latest_token
        engine._on_review_result("one", {**dangerous, "review_token": latest_token, "reviewed_through_seq": 2})
        assert engine._sessions["one"]["review"]["status"] == "queued"
        engine.unregister_session("one")
        engine.register_session("one", "ssh", device_type="server" if profile == "linux" else "switch", device_profile=profile)
        engine.on_command("one", "shutdown")
        engine._on_review_result("one", dangerous)
        assert engine._sessions["one"]["review"]["status"] == "queued"
    finally:
        engine.close()


@pytest.mark.parametrize("profile,query", [("linux", "uptime"), ("h3c_comware", "display version")])
@pytest.mark.parametrize("current", [None, {"seq": 1, "risk": "high"}, {"seq": 2, "risk": "low", "reason": "只读查询"}])
def test_model_separates_chain_risk_and_rejects_unbound_current(profile, query, current, monkeypatch):
    monkeypatch.setattr(routing, "AUDITBOX_INFERENCE_PROFILES", ["linux", "h3c_comware"])
    captured = []
    class Response:
        def __init__(self, isolated): self.isolated = isolated
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def __iter__(self):
            result = ({"risk": "low", "reason": "独立只读查询", "evidence": [2]} if self.isolated else
                      {"risk": "high", "intent": "历史危险意图", "reason": "取消的重启", "evidence": [1, 999], "current": current})
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": json.dumps(result)}}]}) + "\n").encode()
            yield b"data: [DONE]\n"
    def urlopen(request, **_):
        captured.append(json.loads(request.data))
        return Response(len(json.loads(captured[-1]["messages"][1]["content"])["history"]) == 1)
    monkeypatch.setattr(history_review.urllib.request, "urlopen", urlopen)
    history = [entry("reboot", 1, status="cancelled"), entry(query, 2)]
    result = _review_history(_PendingReview("one", "ssh", profile, profile, history, 0, 0))
    assert result["risk"] == "high" and result["evidence"] == [1]
    expected = "low"
    assert result["current"]["risk"] == expected and result["current"]["seq"] == 2
    assert result["findings"][0]["risk"] == expected
    assert json.loads(captured[0]["messages"][1]["content"])["current_seq"] == 2
    assert len(captured) == (1 if current and current["seq"] == 2 else 2)


@pytest.mark.parametrize("profile", ["linux", "h3c_comware"])
def test_model_ignoring_scope_is_rechecked_without_history_and_failure_is_unknown(profile, monkeypatch):
    monkeypatch.setattr(routing, "AUDITBOX_INFERENCE_PROFILES", ["linux", "h3c_comware"])
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def __iter__(self):
            result = {"risk": "critical", "reason": "历史危险意图", "evidence": [1],
                      "current": {"seq": 2, "risk": "critical", "reason": "继承历史最高风险"}}
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": json.dumps(result)}}]}) + "\n").encode()
            yield b"data: [DONE]\n"
    def urlopen(request, **_):
        calls.append(json.loads(json.loads(request.data)["messages"][1]["content"])["history"])
        if len(calls) == 2: raise OSError("isolated review unavailable")
        return Response()
    monkeypatch.setattr(history_review.urllib.request, "urlopen", urlopen)
    history = [entry("reboot", 1, status="cancelled"), entry("df -h" if profile == "linux" else "display version", 2)]
    result = _review_history(_PendingReview("one", "ssh", profile, profile, history, 0, 0))
    assert result["risk"] == "critical" and result["current"]["risk"] == "unknown"
    assert len(calls) == 2 and len(calls[0]) == 2 and calls[1] == [calls[0][-1]]


def test_recent_connection_uses_current_verdict_not_window_risk(monkeypatch):
    records = []
    monkeypatch.setattr(gateway, "RecentConnectionRepository", lambda: SimpleNamespace(record_operation=lambda **data: records.append(data)))
    request = SimpleNamespace(protocol="ssh", host="192.0.2.1", port=22)
    payload = {"type": "operation_review", "status": "ready", "risk": "critical",
               "history": [entry("reboot"), entry("display version", 2)],
               "current": {"seq": 2, "risk": "low", "reason": "查询"}}
    gateway._record_recent_operation(request, payload)
    assert records[-1]["result"] == "未发现风险" and not records[-1]["risk"]
    gateway._record_recent_operation(request, {**payload, "current": {"seq": 1, "risk": "critical"}})
    assert records[-1]["result"] == "风险待核验"


def test_gateway_drops_late_wire_results_before_browser_and_recent_activity(monkeypatch):
    delivered, recent = [], []
    monkeypatch.setattr(gateway, "_record_recent_operation", lambda request, payload: recent.append(payload))
    async def send_json(payload): delivered.append(payload)
    async def run():
        reader = asyncio.StreamReader()
        for revision, status in [(2, "queued"), (1, "ready"), (2, "ready"), (2, "queued")]:
            reader.feed_data((json.dumps({"type": "operation_review", "review_revision": revision, "status": status})+"\n").encode())
        reader.feed_eof()
        await gateway._prediction_to_websocket(reader, SimpleNamespace(send_json=send_json), None)
    asyncio.run(run())
    assert [(item["review_revision"], item["status"]) for item in delivered] == [(2, "queued"), (2, "ready")]
    assert delivered == recent
