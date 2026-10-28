import json
import sqlite3
from pathlib import Path

from auditbox.prediction.engine import PredictionEngine


class CaptureSocket:
    def __init__(self) -> None:
        self.messages: list[bytes] = []
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.messages.append(data)

    def close(self) -> None:
        self.closed = True


def test_confirmation_control_is_scoped_to_registered_session(tmp_path: Path) -> None:
    engine = PredictionEngine(tmp_path / "prediction.db")
    calls = []
    try:
        engine.register_session("session-one", "ssh")
        engine.register_confirmation_handler(
            "session-one",
            lambda token: calls.append(("confirm", token)) or True,
            lambda token: calls.append(("cancel", token)) or True,
        )

        assert engine.handle_control("session-one", {
            "type": "command_confirmation",
            "confirmation_id": "token-one",
        }) is True
        assert engine.handle_control("session-one", {
            "type": "command_confirmation_cancel",
            "confirmation_id": "token-two",
        }) is True
        assert engine.handle_control("another-session", {
            "type": "command_confirmation",
            "confirmation_id": "token-three",
        }) is False
        assert calls == [("confirm", "token-one"), ("cancel", "token-two")]
    finally:
        engine.unregister_session("session-one")
        engine.close()


def test_guide_audit_control_is_scoped_and_validated(tmp_path: Path) -> None:
    engine = PredictionEngine(tmp_path / "prediction.db")
    calls = []
    try:
        engine.register_session("session-one", "ssh")
        engine.register_audit_handler(
            "session-one",
            lambda event, details: calls.append((event, details)),
        )

        assert engine.handle_control("session-one", {
            "type": "guide_audit_event",
            "event": "guide_command_filled",
            "details": {"batch_id": "batch-one", "index": 0},
        }) is True
        assert engine.handle_control("session-one", {
            "type": "guide_audit_event",
            "event": "untrusted_event",
            "details": {},
        }) is False
        assert engine.handle_control("another-session", {
            "type": "guide_audit_event",
            "event": "guide_command_filled",
            "details": {},
        }) is False
        assert calls == [("guide_command_filled", {"batch_id": "batch-one", "index": 0})]
    finally:
        engine.unregister_session("session-one")
        engine.close()


def test_prediction_filters_permanently_blocked_commands(tmp_path: Path) -> None:
    engine = PredictionEngine(tmp_path / "prediction.db")
    try:
        suggestions = engine._safe_suggestions([
            "show version",
            "sudo reboot",
            "rm -rf /",
        ])
    finally:
        engine.close()

    assert suggestions == [
        {"command": "show version", "source": "prediction", "risk": "normal"},
        {"command": "sudo reboot", "source": "prediction", "risk": "confirm"},
    ]


def test_factory_recommendations_are_pushed_and_stale_results_are_cleared(tmp_path: Path) -> None:
    engine = PredictionEngine(tmp_path / "prediction.db")
    subscriber = CaptureSocket()
    try:
        engine.register_session("session-one", "ssh")
        assert engine.add_subscriber("session-one", subscriber) is True

        engine.on_command("session-one", "cd /var/log")
        review = next(
            json.loads(message)
            for message in reversed(subscriber.messages)
            if json.loads(message).get("type") == "operation_review"
        )
        assert review["status"] == "queued"
        assert [step["state"] for step in review["process"]] == [
            "complete",
            "complete",
            "active",
            "pending",
        ]
        recommendation = json.loads(subscriber.messages[-1])
        assert recommendation["type"] == "recommendation"
        assert len(recommendation["suggestions"]) == 5
        assert recommendation["suggestions"][0]["command"] == "ls -lah"
        assert recommendation["suggestions"][0]["source"] == "factory"

        engine.on_command("session-one", "LS")
        cleared = json.loads(subscriber.messages[-1])
        assert cleared == {"type": "recommendation", "suggestions": [], "input_source": "human"}
    finally:
        engine.unregister_session("session-one")
        engine.close()


def test_detected_h3c_session_uses_only_the_h3c_factory_graph(tmp_path: Path) -> None:
    engine = PredictionEngine(tmp_path / "prediction.db")
    subscriber = CaptureSocket()
    try:
        engine.register_session(
            "session-h3c",
            "ssh",
            device_type="switch",
            device_profile="h3c_comware",
        )
        assert engine.add_subscriber("session-h3c", subscriber) is True
        engine.update_device_type("session-h3c", "h3c")

        device_event = json.loads(subscriber.messages[-1])
        assert device_event["device_type"] == "h3c"
        assert device_event["device_profile"] == "h3c_comware"
        assert device_event["verified"] is True

        engine.on_command("session-h3c", "ping 192.0.2.1")
        recommendation = json.loads(subscriber.messages[-1])

        assert recommendation["type"] == "recommendation"
        assert recommendation["suggestions"][0]["command"] == "display ip routing-table 192.0.2.1"
        assert all(
            suggestion["behavior"] == "network.diagnostics"
            for suggestion in recommendation["suggestions"]
        )
        assert all(
            not suggestion["command"].startswith("ip route get")
            for suggestion in recommendation["suggestions"]
        )
    finally:
        engine.unregister_session("session-h3c")
        engine.close()


def test_selected_switch_keeps_h3c_recommendations_when_banner_looks_linux(tmp_path: Path) -> None:
    engine = PredictionEngine(tmp_path / "prediction.db")
    subscriber = CaptureSocket()
    try:
        engine.register_session(
            "selected-switch",
            "ssh",
            device_type="switch",
            device_profile="h3c_comware",
        )
        assert engine.add_subscriber("selected-switch", subscriber) is True
        engine.update_device_type("selected-switch", "linux")

        device_event = json.loads(subscriber.messages[-1])
        assert device_event["device_type"] == "h3c"
        assert device_event["device_profile"] == "h3c_comware"
        assert device_event["verified"] is False

        engine.on_command("selected-switch", "ping 192.0.2.1")
        recommendation = json.loads(subscriber.messages[-1])
        assert recommendation["suggestions"][0]["command"] == "display ip routing-table 192.0.2.1"
    finally:
        engine.unregister_session("selected-switch")
        engine.close()


def test_selected_server_keeps_linux_recommendations_when_banner_looks_h3c(tmp_path: Path) -> None:
    engine = PredictionEngine(tmp_path / "prediction.db")
    subscriber = CaptureSocket()
    try:
        engine.register_session(
            "selected-server",
            "ssh",
            device_type="server",
            device_profile="linux",
        )
        assert engine.add_subscriber("selected-server", subscriber) is True
        engine.update_device_type("selected-server", "h3c")

        device_event = json.loads(subscriber.messages[-1])
        assert device_event["device_type"] == "linux"
        assert device_event["device_profile"] == "linux"
        assert device_event["verified"] is False

        engine.on_command("selected-server", "cd /var/log")
        recommendation = json.loads(subscriber.messages[-1])
        assert recommendation["suggestions"][0]["command"] == "ls -lah"
    finally:
        engine.unregister_session("selected-server")
        engine.close()


def test_h3c_factory_uses_executed_history_to_track_the_active_view(tmp_path: Path) -> None:
    engine = PredictionEngine(tmp_path / "prediction.db")
    subscriber = CaptureSocket()
    try:
        engine.register_session(
            "session-h3c",
            "ssh",
            device_type="switch",
            device_profile="h3c_comware",
        )
        assert engine.add_subscriber("session-h3c", subscriber) is True
        engine.update_device_type("session-h3c", "h3c")

        engine.on_command("session-h3c", "system-view")
        engine.on_command("session-h3c", "rip 7")
        engine.on_command(
            "session-h3c",
            "silent-interface GigabitEthernet1/0/1",
        )
        recommendation = json.loads(subscriber.messages[-1])

        assert recommendation["type"] == "recommendation"
        assert recommendation["suggestions"][0]["behavior"] == "routing.rip"
        assert recommendation["suggestions"][0]["command"] == "display rip 7 neighbor"
    finally:
        engine.unregister_session("session-h3c")
        engine.close()


def test_recommendation_state_is_isolated_by_session_and_device_profile(tmp_path: Path) -> None:
    engine = PredictionEngine(tmp_path / "prediction.db")
    ospf_subscriber = CaptureSocket()
    vlan_subscriber = CaptureSocket()
    linux_subscriber = CaptureSocket()
    try:
        engine.register_session(
            "ospf-switch",
            "ssh",
            device_type="switch",
            device_profile="h3c_comware",
        )
        engine.register_session(
            "vlan-switch",
            "ssh",
            device_type="switch",
            device_profile="h3c_comware",
        )
        engine.register_session(
            "linux-server",
            "ssh",
            device_type="server",
            device_profile="linux",
        )
        assert engine.add_subscriber("ospf-switch", ospf_subscriber)
        assert engine.add_subscriber("vlan-switch", vlan_subscriber)
        assert engine.add_subscriber("linux-server", linux_subscriber)

        for command in ("system-view", "ospf 7"):
            engine.on_command("ospf-switch", command)
        for command in ("system-view", "vlan 100"):
            engine.on_command("vlan-switch", command)
        engine.on_command("ospf-switch", "silent-interface GigabitEthernet1/0/1")
        engine.on_command("vlan-switch", "description users")
        engine.on_command("linux-server", "apt install nginx")

        ospf = json.loads(ospf_subscriber.messages[-1])["suggestions"]
        vlan = json.loads(vlan_subscriber.messages[-1])["suggestions"]
        linux = json.loads(linux_subscriber.messages[-1])["suggestions"]
        assert ospf[0]["command"] == "display ospf 7 peer"
        assert vlan[0]["command"] == "display vlan 100"
        assert linux[0]["command"] == "apt-cache policy nginx"
        assert all(not item["command"].startswith("display ") for item in linux)
        assert all(not item["command"].startswith(("apt", "dpkg")) for item in ospf + vlan)
    finally:
        for session_id in ("ospf-switch", "vlan-switch", "linux-server"):
            engine.unregister_session(session_id)
        engine.close()


def test_explicit_h3c_error_rolls_back_latest_view_transition(tmp_path: Path) -> None:
    engine = PredictionEngine(tmp_path / "prediction.db")
    subscriber = CaptureSocket()
    try:
        engine.register_session(
            "switch",
            "ssh",
            device_type="switch",
            device_profile="h3c_comware",
        )
        assert engine.add_subscriber("switch", subscriber)

        engine.on_command("switch", "system-view")
        engine.on_command_result("switch", "system-view", success=True)
        engine.on_command("switch", "interface invalid")
        assert len(
            engine._sessions["switch"]["recommendation_state"]
            .platform_state.view_stack
        ) == 2

        engine.on_command_result("switch", "interface invalid", success=False)

        session = engine._sessions["switch"]
        assert [
            frame.entry_command
            for frame in session["recommendation_state"].platform_state.view_stack
        ] == ["system-view"]
        assert session["history"][-1]["status"] == "failed"
        assert json.loads(subscriber.messages[-1]) == {
            "type": "recommendation",
            "suggestions": [],
            "input_source": "human",
        }
    finally:
        engine.unregister_session("switch")
        engine.close()


def test_markov_is_retained_but_disabled_by_default(tmp_path: Path) -> None:
    db_path = tmp_path / "prediction.db"
    engine = PredictionEngine(db_path)
    try:
        engine.register_session("session-one", "ssh")
        engine.on_command("session-one", "cd /tmp")
        engine.on_command("session-one", "ls")
    finally:
        engine.unregister_session("session-one")
        engine.close()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM transitions").fetchone()[0] == 0
