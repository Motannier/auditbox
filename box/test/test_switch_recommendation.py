"""Real packaged catalogs, multi-task state, safety, HTTP and relay regressions."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from auditbox.device_profiles import REFERENCE_PROFILES
from auditbox.gateway.app import app
from auditbox.prediction.engine import PredictionEngine
from auditbox.recommendation import (
    advance_recommendation_session, complete_commands, create_recommendation_session,
    recommend_next_commands,
)
from auditbox.recommendation.switch_catalog import catalog_for, completion_root
from auditbox.relay.command_gate import CommandGate
from auditbox.security.alert import check
from auditbox.security.switch_rules import command_policy
from auditbox.switch_commands import BEHAVIOR_LABELS, queries
from test_command_gate import FakeAudit


CASES = [
    ("huawei_vrp", "display version", "display interface", "GigabitEthernet0/0/1", "system-view"),
    ("cisco_ios", "show version", "show interfaces", "GigabitEthernet1/0/1", "configure terminal"),
    ("zte_zxr10", "show version", "show interface", "gei-1/1/1", "configure terminal"),
]


@pytest.mark.parametrize("profile,count", [("huawei_vrp", 31736), ("cisco_ios", 2286), ("zte_zxr10", 12673)])
def test_full_graph_keeps_all_variants_and_risk_independent_of_policy(profile, count):
    catalog = catalog_for(profile)
    assert len(catalog.variants) == count
    linked = [v for nodes in catalog.behavior_graph.values() for v in nodes]
    assert len(linked) == count
    assert {v.id for v in linked} == {v.id for v in catalog.variants}
    assert set(catalog.behavior_graph) <= set(BEHAVIOR_LABELS)
    assert len(catalog.behavior_graph) >= 25
    assert "shutdown" in catalog.roots
    assert any(v.risk for v in catalog.roots["shutdown"])
    assert all(v.syntax and v.sources and v.models for v in linked)


@pytest.mark.parametrize("profile,version,interface,port,enter", CASES)
def test_completion_is_full_brand_scoped_and_preserves_metadata(profile, version, interface, port, enter):
    for prefix in (version[:5], "vla", "int", "shu", "osp" if profile == "huawei_vrp" else "no shu"):
        rows = complete_commands(prefix, device_profile=profile)
        assert rows, (profile, prefix)
        assert len(rows) <= 8
        assert all(item["command"].startswith(prefix) for item in rows)
        assert all(not re.search(r"[<>{}\[\]\n]", item["command"]) for item in rows)
    shutdown = complete_commands("shutdown", device_profile=profile)[0]
    assert complete_commands("shu", device_profile=profile)[0]["command"] == "shutdown"
    assert shutdown["command"] == "shutdown" and shutdown["risk"] == "confirm"
    assert shutdown["variant_count"] > 0 and shutdown["models"] and shutdown["view"]
    assert "关闭" in shutdown["risk_reason"]
    assert complete_commands(version, device_profile=profile)[0]["risk"] == "normal"
    assert complete_commands("systemctl", device_profile=profile) == []
    assert complete_commands("show version; reload", device_profile=profile) == []
    assert recommend_next_commands("ls", device_profile=profile, limit=5) == []
    assert complete_commands("display ", device_profile="cisco_ios") == []
    assert complete_commands("show ", device_profile="huawei_vrp") == []


@pytest.mark.parametrize("profile,version,interface,port,enter", CASES)
def test_every_curated_query_has_safe_related_recommendations(profile, version, interface, port, enter):
    for query in queries(profile):
        command = query.command.format(interface=port, vlan="100")
        assert check(command, device_profile=profile) is None
        rows = recommend_next_commands(command, device_profile=profile, limit=5)
        assert rows, command
        assert len(rows) <= 5 and len({r["command"] for r in rows}) == len(rows)
        assert all(r["command"] != command.casefold() for r in rows)
        for row in rows:
            assert row["risk"] == "normal" and check(row["command"], device_profile=profile) is None
            assert not any(char in row["command"] for char in "{};<>")
    restart = "reboot" if profile == "huawei_vrp" else "reload"
    route = "ip route-static 0.0.0.0 0 192.0.2.1" if profile == "huawei_vrp" else "ip route 0.0.0.0 0.0.0.0 192.0.2.1"
    for command in ("shutdown", "no shutdown", "undo shutdown", restart, "vlan 20", route):
        assert check(command, device_profile=profile)
    for suffix in ("; reload", "\nshutdown", " | redirect flash:/x", " > flash:/x", " && reboot"):
        assert check(version + suffix, device_profile=profile)
    assert check(version + " junk", device_profile=profile) is None
    assert command_policy(version + " junk", profile).state == "unknown"


@pytest.mark.parametrize("profile,version,interface,port,enter", CASES)
def test_multitask_state_isolated_and_never_invents_parameters(profile, version, interface, port, enter):
    state = create_recommendation_session(profile)
    advance_recommendation_session(state, enter)
    advance_recommendation_session(state, "interface " + port)
    advance_recommendation_session(state, "shutdown")
    assert state.current_intent.parameters == {"interface": port.casefold()}
    rows = recommend_next_commands("shutdown", device_profile=profile, limit=5, session_state=state)
    if profile == "cisco_ios":
        assert rows[0]["command"] == "do show interfaces " + port.casefold()
    elif profile == "zte_zxr10":
        assert [r["command"] for r in rows] == ["end"]
    else:
        assert rows[0]["command"] == "display interface " + port.casefold()
    # An explicit different task has no inherited interface or VLAN.
    advance_recommendation_session(state, version)
    assert state.current_intent.parameters == {}
    advance_recommendation_session(state, "vlan 200")
    assert state.current_intent.parameters == {"vlan": "200"}
    assert "interface" not in state.current_intent.parameters
    advance_recommendation_session(state, "end")
    assert not state.platform_state.stack and state.current_intent.parameters == {}
    before = state.snapshot()
    advance_recommendation_session(state, "vlan 300", status="cancelled")
    assert before == state
    advance_recommendation_session(state, "unknown-mode nonsense")
    assert recommend_next_commands("unknown-mode nonsense", device_profile=profile, limit=5, session_state=state) == []


@pytest.mark.parametrize("profile,version,interface,port,enter", CASES)
def test_sessions_and_failure_rollback_are_scoped(profile, version, interface, port, enter, tmp_path):
    engine = PredictionEngine(tmp_path / "predictions.db")
    try:
        engine.register_session("one", "ssh", device_type="switch", device_profile=profile)
        engine.register_session("two", "telnet", device_type="switch", device_profile=profile)
        engine.on_command("one", enter)
        engine.on_command_result("one", enter, success=True)
        before = engine._sessions["one"]["recommendation_state"].snapshot()
        engine.on_command("one", "interface " + port)
        engine.on_command_result("one", "interface " + port, success=False)
        assert engine._sessions["one"]["recommendation_state"] == before
        assert engine._sessions["one"]["history"][-1]["status"] == "failed"
        assert engine._sessions["two"]["recommendation_state"].current_intent is None
    finally:
        engine.close()


@pytest.mark.parametrize("profile,version,prompt,error", [
    ("huawei_vrp", "display version", "<Huawei>", "Error: Unrecognized command found at '^' position."),
    ("cisco_ios", "show version", "Switch#", "% Invalid input detected at '^' marker."),
    ("zte_zxr10", "show version", "ZXR10#", "% Unknown command."),
])
def test_relay_detects_realistic_vendor_errors_and_paging(profile, version, prompt, error):
    audit = FakeAudit()
    target = bytearray()
    gate = CommandGate(audit, target.extend, bytearray().extend, device_profile=profile)
    gate.feed((version + "\r").encode())
    gate.feed_output(("\r\n" + error + "\r\n" + prompt).encode())
    assert audit.command_results == [(version, False)]
    gate.feed_output(b"\r\n---- More ----")
    gate.feed(b"q")
    gate.feed_output(("\r\n" + prompt).encode())
    gate.feed((version + "\r").encode())
    gate.feed_output(("\r\n" + prompt).encode())
    assert audit.commands == [version, version]
    assert audit.command_results[-1] == (version, True)


@pytest.mark.parametrize("profile", REFERENCE_PROFILES)
def test_http_completion_routes_profile_and_keeps_applicability(profile):
    response = TestClient(app).get("/api/commands/suggestions", params={
        "device_type": "switch", "device_profile": profile, "prefix": "shutdown", "limit": 8,
    })
    assert response.status_code == 200
    row = response.json()[0]
    assert row["command"] == "shutdown" and row["risk"] == "confirm"
    assert row["models"] and row["variant_count"] and row["risk_reason"]


def test_completions_filter_prose_and_support_ios_do_queries():
    assert completion_root("add the configuration commands to the switch profile") == ""
    assert completion_root("interface <interface-name>") == "interface"
    rows = complete_commands("do show ver", device_profile="cisco_ios")
    assert rows[0]["command"] == "do show version"
    assert rows[0]["risk"] == "normal"
    assert check("show vlan id 100", device_profile="zte_zxr10") is None
    assert check("show vlan 100", device_profile="zte_zxr10") is None
    assert command_policy("show vlan 100", "zte_zxr10").state == "unknown"


def test_snapshot_from_other_brand_does_not_leak():
    state = create_recommendation_session("huawei_vrp")
    advance_recommendation_session(state, "vlan 321")
    rows = recommend_next_commands("show version", device_profile="cisco_ios", limit=5, session_state=state)
    assert rows and all("321" not in r["command"] and r["command"].startswith("show ") for r in rows)
