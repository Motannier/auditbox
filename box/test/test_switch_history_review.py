"""Rule/model isolation, grading, audit evidence, and asynchronous delivery."""

import json
import threading

import pytest

from auditbox.inference import routing
from auditbox.prediction import engine as engine_module
from auditbox.prediction.engine import PredictionEngine
from auditbox.security import history_review, switch_rules
from auditbox.security.history_review import _PendingReview, _review_history
from auditbox.security.switch_history_review import RULE_REVIEW_PROFILES, grade_command, review_switch_history
from test_prediction_engine import CaptureSocket


def entry(command, sequence=1, **metadata):
    return {"seq": sequence, "command": command, "status": "executed", **metadata}


@pytest.mark.parametrize("profile", RULE_REVIEW_PROFILES)
@pytest.mark.parametrize("protocol", ["ssh", "telnet", "console"])
def test_rule_profiles_never_call_any_model(profile, protocol, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("A rule-only profile attempted to use a model")
    monkeypatch.setattr(history_review, "resolve_inference_route", forbidden)
    monkeypatch.setattr(history_review.urllib.request, "urlopen", forbidden)
    version = "display version" if profile == "huawei_vrp" else "show version"
    result = _review_history(_PendingReview("one", protocol, "switch", profile, [entry(version)], 0, 0))
    assert result["review_source"] == "rules" and result["device_profile"] == profile
    assert result["status"] == "ready" and result["risk"] == "low"
    assert result["evidence"] == [1] and result["reviewed_through_seq"] == 1
    assert not result["lock"] and result["lock_policy"]["code"] == "rule_audit_only"


@pytest.mark.parametrize("profile,model", [("linux", "command-guard"), ("h3c_comware", "h3c-risk")])
def test_existing_linux_and_h3c_models_payload_and_policy_are_preserved(profile, model, monkeypatch):
    monkeypatch.setattr(routing, "AUDITBOX_INFERENCE_PROFILES", ["linux", "h3c_comware"])
    def forbidden(*_args, **_kwargs):
        pytest.fail("Linux/H3C must retain their model history reviewer")
    monkeypatch.setattr(history_review, "review_switch_history", forbidden)
    captured = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def __iter__(self):
            data = {"risk": "medium", "intent": "模型结论", "reason": "模型风险原因",
                    "evidence": [1], "confidence": 0.91, "lock": False}
            event = {"choices": [{"delta": {"content": json.dumps(data)}}]}
            yield f"data: {json.dumps(event)}\n\n".encode()
            yield b"data: [DONE]\n\n"
    def urlopen(request, timeout):
        captured.append((request, timeout))
        return Response()
    monkeypatch.setattr(history_review.urllib.request, "urlopen", urlopen)
    command = "display version" if profile == "h3c_comware" else "ls -la"
    result = _review_history(_PendingReview("one", "ssh", "switch" if profile == "h3c_comware" else "server", profile, [entry(command)], 0, 0))
    assert result["risk"] == "medium" and result["reason"] == "模型风险原因"
    assert result["confidence"] == 0.91 and result["evidence"] == [1] and not result["lock"]
    request, timeout = captured[0]
    payload = json.loads(request.data)
    assert payload["model"] == model and payload["stream"] is True
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert json.loads(payload["messages"][1]["content"])["device_profile"] == profile
    assert dict((k.lower(), v) for k, v in request.header_items())["x-auditbox-device-profile"] == profile
    assert timeout == history_review.AUDITBOX_HISTORY_REVIEW_TIMEOUT


@pytest.mark.parametrize("profile,command", [("linux", "rm -rf /"), ("h3c_comware", "reset saved-configuration")])
@pytest.mark.parametrize("authorized", [False, True])
def test_model_critical_risk_is_preserved_without_locking_even_after_authorization(profile, command, authorized, monkeypatch):
    monkeypatch.setattr(routing, "AUDITBOX_INFERENCE_PROFILES", ["linux", "h3c_comware"])
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def __iter__(self):
            result = {"risk": "critical", "intent": "危险操作", "reason": "破坏系统或配置",
                      "evidence": [1], "confidence": 0.99, "lock": True}
            event = {"choices": [{"delta": {"content": json.dumps(result)}}]}
            yield f"data: {json.dumps(event)}\n\n".encode()
            yield b"data: [DONE]\n\n"
    monkeypatch.setattr(history_review.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    result = _review_history(_PendingReview("model", "ssh", "switch" if profile == "h3c_comware" else "server",
                                            profile, [entry(command, authorized=authorized)], 0, 0))
    assert not result["lock"] and not result["locked"] and not result["lock_policy"]["allow_lock"]
    assert result["lock_policy"]["code"] == "advisory_only"
    assert result["risk"] == "critical" and result["reason"] == "破坏系统或配置"
    assert result["confidence"] == 0.99 and result["evidence"] == [1]


@pytest.mark.parametrize("profile", RULE_REVIEW_PROFILES)
def test_severity_and_pending_are_distinct_from_execution_permission(profile):
    version = "display version" if profile == "huawei_vrp" else "show version"
    assert grade_command(version, profile)["risk"] == "low"
    assert grade_command("vlan 100", profile)["risk"] == "medium"
    high = grade_command("shutdown", profile)
    assert high["risk"] == "high" and high["action"] == "confirm" and high["rule_id"]
    assert grade_command("write erase", profile)["risk"] == "critical"
    unknown = grade_command("unverified-device-command", profile)
    assert unknown["risk"] == "unknown" and unknown["action"] == "allow"
    assert unknown["code"] == "unknown" and "放行" in unknown["reason"]
    assert grade_command("ls", profile)["action"] == "allow"
    assert grade_command(version + " | redirect flash:/output", profile)["risk"] == "unknown"
    for suffix in (" | include shutdown", " | include reload"):
        assert grade_command(version + suffix, profile)["risk"] == "low"


def test_parameter_branches_and_pending_provenance_are_preserved():
    assert grade_command("description reload", "huawei_vrp")["risk"] == "medium"
    assert grade_command("format bulkascii", "huawei_vrp")["risk"] == "medium"
    assert grade_command("format flash:", "huawei_vrp")["risk"] == "high"
    assert grade_command("show cos-queue gei-1/1/1", "zte_zxr10")["risk"] == "low"
    assert grade_command("show cos-queue gei-1/1/1 clear", "zte_zxr10")["risk"] == "medium"
    for profile, command in (("huawei_vrp", "system-view"), ("cisco_ios", "abort"),
                             ("zte_zxr10", "show ip protocol routing vrf sample")):
        result = grade_command(command, profile)
        assert result["risk"] == "unknown" and result["code"] == "pending" and result["rule_id"]
        assert result["action"] == "confirm"


@pytest.mark.parametrize("profile", RULE_REVIEW_PROFILES)
def test_cancelled_blocked_failed_and_authorized_operations_are_not_reexecuted_or_locked(profile):
    history = [entry("vlan 100", 1), entry("shutdown", 2, status="cancelled"),
               entry("write erase", 3, status="blocked"), entry("shutdown", 4, status="failed"),
               entry("shutdown", 5, authorized=True)]
    result = review_switch_history(history, profile)
    assert result["risk"] == "critical" and result["evidence"] == [1, 2, 3, 4, 5]
    assert not result["lock"] and not result["lock_policy"]["allow_lock"]
    assert result["findings"][1]["status"] == "cancelled"
    assert "未下发" in result["findings"][1]["execution_note"]
    assert "未下发" in result["findings"][2]["execution_note"]
    assert "未确认生效" in result["findings"][3]["execution_note"]
    assert "旁路仅记录和展示" in result["findings"][4]["execution_note"]
    assert history[3]["status"] == "failed"  # Input snapshots are not mutated.


def test_unknown_evidence_is_not_labeled_safe_or_hidden_behind_a_known_high_risk():
    result = review_switch_history([entry("show version"), entry("unknown-command", 2)], "cisco_ios")
    assert result["risk"] == "unknown" and result["evidence"] == [2]
    assert result["unverified_count"] == 1 and "待核验" in result["reason"]
    assert result["findings"][1]["action"] == "allow"
    assert "放行" in result["reason"] and "仍需确认" not in result["reason"]
    result = review_switch_history([entry("shutdown"), entry("unknown-command", 2)], "cisco_ios")
    assert result["risk"] == "high" and result["unverified_count"] == 1
    assert result["evidence"] == [1, 2] and result["findings"][1]["risk"] == "unknown"


def test_unknown_allowed_and_pending_confirmation_have_distinct_summary():
    result = review_switch_history([entry("ls"), entry("abort", 2)], "cisco_ios")
    assert [finding["action"] for finding in result["findings"]] == ["allow", "confirm"]
    assert result["risk"] == "unknown" and result["unverified_count"] == 2
    assert "1 条未知命令按策略直接放行" in result["reason"]
    assert "1 条待核验项执行前仍需确认" in result["reason"]


def test_missing_rules_cannot_report_successful_low_risk_review(monkeypatch):
    monkeypatch.setattr(switch_rules, "_catalog", lambda profile: None)
    result = review_switch_history([entry("show version")], "cisco_ios")
    assert result["status"] == "error" and result["risk"] == "unknown"
    assert result["findings"][0]["code"] == "unavailable" and not result["lock"]


def test_background_rule_error_reports_rules_not_a_missing_model(monkeypatch):
    done, results = threading.Event(), []
    def failed(_pending):
        raise OSError("test rule failure")
    def callback(_session, result):
        results.append(result)
        done.set()
    monkeypatch.setattr(history_review, "AUDITBOX_HISTORY_REVIEW_INTERVAL", 0)
    monkeypatch.setattr(history_review, "_review_history", failed)
    reviewer = history_review.HistoryRiskReviewer(callback, worker_name="test-rule-error")
    try:
        reviewer.submit("one", "ssh", "switch", [entry("show version")], device_profile="cisco_ios")
        assert done.wait(1)
        assert results[0]["status"] == "error" and results[0]["risk"] == "unknown"
        assert results[0]["review_source"] == "rules" and not results[0]["lock"]
        assert "规则审计" in results[0]["reason"] and "AI" not in results[0]["reason"]
    finally:
        reviewer.close()
        reviewer._worker.join(2)


def test_evidence_is_bounded_to_real_unique_sequences_and_empty_history_is_idle():
    assert review_switch_history([], "cisco_ios")["status"] == "idle"
    result = review_switch_history([entry("shutdown", "bad"), entry("shutdown", -1),
                                    entry("show version", 3), entry("shutdown", 3), entry("", 4)], "cisco_ios")
    assert result["evidence"] == [3] and result["risk"] == "low" and len(result["findings"]) == 1
    assert result["reviewed_through_seq"] == 4
    for profile in ("linux", "h3c_comware", "unknown"):
        with pytest.raises(ValueError):
            review_switch_history([entry("shutdown")], profile)


class CaptureReviewer:
    def __init__(self):
        self.submissions = []
        self.discarded = []
    def submit(self, session, protocol, device_type, history, **metadata):
        self.submissions.append((session, protocol, device_type, history, metadata))
    def discard(self, session):
        self.discarded.append(session)
    def close(self):
        pass


def test_engine_routes_profiles_separately_and_reaudits_failed_rule_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "AUDITBOX_HISTORY_REVIEW_ENABLED", False)
    engine = PredictionEngine(tmp_path / "review.db")
    engine._reviewer, engine._rules_reviewer = CaptureReviewer(), CaptureReviewer()
    try:
        for profile in ("linux", "h3c_comware", *RULE_REVIEW_PROFILES):
            engine.register_session(profile, "ssh", device_type="server" if profile == "linux" else "switch", device_profile=profile)
            engine.on_command(profile, "vlan 100" if profile != "linux" else "ls")
        assert [c[0] for c in engine._reviewer.submissions] == ["linux", "h3c_comware"]
        assert [c[0] for c in engine._rules_reviewer.submissions] == list(RULE_REVIEW_PROFILES)
        original = engine._rules_reviewer.submissions[0][3]
        engine.on_command_result("huawei_vrp", "vlan 100", success=False)
        assert engine._rules_reviewer.submissions[-1][3][0]["status"] == "failed"
        assert original[0]["status"] == "executed"
        engine.on_command_result("h3c_comware", "vlan 100", success=False)
        assert len(engine._reviewer.submissions) == 3  # Failed model commands need a fresh status snapshot too.
        engine.unregister_session("huawei_vrp")
        assert "huawei_vrp" in engine._rules_reviewer.discarded
    finally:
        engine.close()


def test_rule_worker_completes_while_model_worker_is_busy(tmp_path, monkeypatch):
    model_started, release_model, rules_done = threading.Event(), threading.Event(), threading.Event()
    original = history_review._review_history
    def review(pending):
        if pending.device_profile == "h3c_comware":
            model_started.set()
            assert release_model.wait(5)
            return {"status": "ready", "risk": "low", "lock": False, "reviewed_through_seq": 1}
        result = original(pending)
        rules_done.set()
        return result
    monkeypatch.setattr(engine_module, "AUDITBOX_HISTORY_REVIEW_ENABLED", True)
    monkeypatch.setattr(history_review, "AUDITBOX_HISTORY_REVIEW_INTERVAL", 0)
    monkeypatch.setattr(history_review, "_review_history", review)
    engine = PredictionEngine(tmp_path / "worker.db")
    try:
        engine.register_session("model", "ssh", device_type="switch", device_profile="h3c_comware")
        engine.register_session("rules", "ssh", device_type="switch", device_profile="cisco_ios")
        engine.on_command("model", "display version")
        assert model_started.wait(1)
        engine.on_command("rules", "show version")
        assert rules_done.wait(1), "Rule audit was stuck behind the model request"
        assert not release_model.is_set()
    finally:
        release_model.set()
        engine.unregister_session("model")
        engine.unregister_session("rules")
        engine.close()
        engine._reviewer._worker.join(2)
        engine._rules_reviewer._worker.join(2)


def test_rule_result_is_pushed_and_logged_only_to_its_session_without_locks(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_module, "AUDITBOX_HISTORY_REVIEW_ENABLED", False)
    engine = PredictionEngine(tmp_path / "delivery.db")
    one, two, events = CaptureSocket(), CaptureSocket(), []
    try:
        for session, profile, socket in (("one", "cisco_ios", one), ("two", "huawei_vrp", two)):
            engine.register_session(session, "ssh", target_key=session, device_type="switch", device_profile=profile)
            engine.add_subscriber(session, socket)
        engine.register_audit_handler("one", lambda *args: events.append(args))
        engine.on_command("one", "write erase", {"status": "blocked"})
        before = len(two.messages)
        result = review_switch_history(engine._sessions["one"]["history"], "cisco_ios")
        engine._on_review_result("one", {**result, "review_token": engine._sessions["one"]["review"]["review_token"]})
        event = json.loads(one.messages[-1])
        assert event["type"] == "operation_review" and event["review_source"] == "rules"
        assert event["risk"] == "critical" and not event["locked"]
        assert event["findings"][0]["status"] == "blocked"
        assert len(two.messages) == before
        assert events[0][0] == "security_rule_review" and events[0][1]["risk"] == "critical"
        assert "seq=1" in events[0][1]["findings"][0]
        assert "write erase" not in json.dumps(events)  # References, not duplicated command text.
        engine.unregister_session("one")
        engine._on_review_result("one", result)
        assert len(events) == 1
    finally:
        engine.close()
