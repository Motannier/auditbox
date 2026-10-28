import copy
import io
import json
import tarfile

import pytest
from auditbox import command_packages as engine
from auditbox.device_profiles import require_device_profile
from auditbox.recommendation.dispatcher import complete_commands, recommend_next_commands
from auditbox.security.alert import check
from auditbox.security.switch_history_review import review_switch_history


def package_data():
    commands = []
    for syntax, risk in [
        ("show alarm", "read_only"),
        ("set alarm <number>", "ordinary_change"),
        ("reset alarm", "dangerous_change"),
        ("no alarm", "pending"),
    ]:
        commands.append(
            dict(
                id=str(len(commands)),
                family=syntax.replace(" <number>", ""),
                syntax=syntax,
                summary=syntax,
                risk=risk,
                reason="" if risk == "read_only" else "改变设备状态",
                template=syntax,
                behavior="alarm",
                views=["config"],
                models=["test"],
                evidence=[dict(source_id="manual-test", pages=[1], note="Test-only source")],
            )
        )
    return dict(
        schema=engine.SCHEMA,
        profile=dict(
            profile_id="test_cli",
            label="Test",
            device_type="firewall",
            category_label="防火墙",
            vendor="Test",
            os_family="TestOS",
            version="0.1.0",
        ),
        commands=commands,
        coverage=dict(
            sources=[dict(id="manual-test", sha256="a" * 64, pages=2)],
            read_pages=[1],
            total_pages=2,
            runtime_validation="not_run",
        ),
    )


def packed(data, extra=False):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        raw = json.dumps(data).encode()
        info = tarfile.TarInfo("runtime.json")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
        if extra:
            tar.addfile(tarfile.TarInfo("../evil.py"))
    return buffer.getvalue()


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ROOT", tmp_path / "packages")
    engine._load.cache_clear()
    data = package_data()
    engine.install(packed(data))
    yield data
    engine._load.cache_clear()


def test_all_four_consumers_and_no_lock(installed):
    require_device_profile("firewall", "test_cli")
    assert (
        complete_commands(prefix="set", device_profile="test_cli", limit=8)[0]["command"]
        == "set alarm <number>"
    )
    rec = recommend_next_commands("set alarm 10", device_profile="test_cli", limit=6)
    assert rec[0]["command"] == "show alarm"
    assert any(r["command"] == "reset alarm" for r in rec)
    assert check("show alarm", device_profile="test_cli") is None
    assert check("set alarm 10", device_profile="test_cli")["action"] == "confirm"
    assert check("no alarm", device_profile="test_cli")["action"] == "confirm"
    assert check("ls", device_profile="test_cli") is None
    assert engine.grade("ls", "test_cli")["risk"] == "unknown"
    assert engine.grade("show alarm; reset alarm", "test_cli")["action"] == "confirm"
    result = review_switch_history(
        [dict(seq=1, command="reset alarm", authorized=True)], "test_cli"
    )
    assert result["risk"] == "high"
    assert not result.get("lock_device")
    assert not any(
        r["command"] == "show alarm"
        for r in complete_commands(prefix="show alarm", device_profile="linux", limit=8)
    )
    with pytest.raises(ValueError):
        require_device_profile("server", "test_cli")


def test_immutable_and_invalid_packages(installed):
    assert engine.install(packed(installed))["already_installed"]
    changed = copy.deepcopy(installed)
    changed["commands"][0]["summary"] = "changed"
    with pytest.raises(ValueError, match="已有其他版本"):
        engine.install(packed(changed))
    with pytest.raises(ValueError):
        engine.install(packed(installed, extra=True))
    for mutate in (
        lambda d: d["profile"].update(profile_id="linux"),
        lambda d: d["commands"][0].update(template="show alarm\nreset alarm"),
        lambda d: d["commands"][0]["evidence"][0].update(pages=[999]),
    ):
        bad = copy.deepcopy(installed)
        mutate(bad)
        with pytest.raises(ValueError):
            engine.validate(bad)


def test_corrupt_rules_fail_closed(installed):
    meta = engine.metadata("test_cli")
    (engine.ROOT / "test_cli" / (meta["sha256"] + ".json")).write_text("{}")
    engine._load.cache_clear()
    assert engine.grade("show alarm", "test_cli")["action"] == "confirm"
    assert check("show alarm", device_profile="test_cli")["action"] == "confirm"
    assert engine.complete("show", "test_cli") == []


def test_dynamic_target_storage_preserves_builtin_constraints(installed, tmp_path):
    from auditbox.db.connection import connect, init_db
    from auditbox.db.models import Target
    from auditbox.db.repositories import TargetRepository

    path = tmp_path / "data.db"
    init_db(path)
    target = Target(
        id="",
        protocol="ssh",
        host="192.0.2.1",
        port=22,
        remark="",
        status="template",
        device_type="firewall",
        device_profile="test_cli",
    )
    stored = TargetRepository(path).create(target)
    init_db(path)
    assert TargetRepository(path).get(stored.id).device_profile == "test_cli"
    with connect(path) as db:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_prediction_uses_rule_worker_and_records_failed_result(installed, tmp_path, monkeypatch):
    from auditbox.prediction import engine as module
    from test_switch_history_review import CaptureReviewer

    monkeypatch.setattr(module, "AUDITBOX_HISTORY_REVIEW_ENABLED", False)
    runtime = module.PredictionEngine(tmp_path / "history.db")
    runtime._reviewer, runtime._rules_reviewer = CaptureReviewer(), CaptureReviewer()
    try:
        runtime.register_session(
            "dynamic", "ssh", device_type="firewall", device_profile="test_cli"
        )
        runtime.on_command("dynamic", "reset alarm")
        assert not runtime._reviewer.submissions
        assert runtime._rules_reviewer.submissions[0][4]["device_profile"] == "test_cli"
        assert runtime._review_payload_locked("dynamic")["review_source"] == "rules"
        runtime.on_command_result("dynamic", "reset alarm", success=False)
        assert runtime._rules_reviewer.submissions[-1][3][0]["status"] == "failed"
    finally:
        runtime.close()
