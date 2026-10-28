import json
from pathlib import Path

import pytest

from auditbox.audit import store as module


@pytest.mark.parametrize("cpuinfo,tree,expected", [
    ("Processor : AArch64\nSerial : AABB001122334455\n", "1122334455667788\0", "aabb001122334455"),
    ("CPU part : 0xd0b\n", "1122334455667788\0", "1122334455667788"),
    ("Serial : 0000000000000000\n", "0000000000000000\0", None),
    ("Serial : unknown\n", "unknown\0", None),
])
def test_cpu_serial_sources(monkeypatch, cpuinfo, tree, expected):
    monkeypatch.setattr(Path, "read_text", lambda path: cpuinfo if str(path) == "/proc/cpuinfo" else tree)
    assert module.hardware_id() == expected


def test_upgrade_identity_preserves_history_links_and_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "hardware_id", lambda: None)
    store = module.AuditStore(tmp_path / "db.sqlite3")
    old_id = store.info()["box_id"]
    store.mark_updated("2026-09-21T01:00:00Z")
    execution = store.execution({"login_id": "login1"}, "display version", "human")
    cursor = store.changes()["next_cursor"]
    monkeypatch.setattr(module, "hardware_id", lambda: "1122334455667788")
    upgraded = module.AuditStore(store.path)
    info = upgraded.info()
    assert info["box_id"] == "1122334455667788"
    assert info["legacy_box_ids"] == [old_id]
    assert info["last_system_update_at"] == "2026-09-21T01:00:00.000000+00:00"
    row = upgraded.get("executions", execution["id"])
    assert row == {**execution, "box_id": info["box_id"]}
    assert upgraded.get("inputs", row["input_id"])["execution_id"] == row["id"]
    changes = upgraded.changes(cursor)
    assert len(changes["items"]) == 2
    assert all(r["record"]["box_id"] == info["box_id"] for r in upgraded.changes()["items"])
    # Original journal entries remain intact; repeated startup is idempotent.
    with upgraded.connect() as db:
        assert json.loads(db.execute("SELECT data FROM audit_changes ORDER BY seq LIMIT 1").fetchone()[0])["box_id"] == old_id
    repeated = module.AuditStore(store.path)
    assert repeated.changes(changes["next_cursor"])["items"] == []
    monkeypatch.setattr(module, "hardware_id", lambda: None)
    assert module.AuditStore(store.path).info() == info
    monkeypatch.setattr(module, "hardware_id", lambda: "8877665544332211")
    with pytest.raises(ValueError, match="CPU"):
        module.AuditStore(store.path)
