"""Reference completeness, brand isolation, and non-destructive subtype migration."""

import gzip
import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from auditbox.db import connection
from auditbox.db.models import SCHEMA, PROFILE_SQL, SWITCH_PROFILE_SQL, Target
from auditbox.db.repositories import TargetRepository, RecentConnectionRepository
from auditbox.device_profiles import REFERENCE_PROFILES, require_device_profile
from auditbox.gateway import app as gateway
from auditbox.recommendation import complete_commands, recommend_next_commands
from auditbox.reference.catalog import DATA_DIR, _catalog, search_reference
from auditbox.security.alert import check
from auditbox.security.history_review import _PendingReview, _review_history


@pytest.mark.parametrize("profile,variants,risk,safe", [
    ("huawei_vrp", 31736, 25716, 6020), ("cisco_ios", 2286, 1562, 724),
    ("zte_zxr10", 12673, 10211, 2462),
])
def test_packaged_reference_keeps_every_reviewed_variant(profile, variants, risk, safe):
    metadata = json.loads((DATA_DIR / "manifest.json").read_text())["profiles"][profile]
    payload = (DATA_DIR / f"{profile}.json.gz").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == metadata["sha256"]
    data = json.loads(gzip.decompress(payload))
    assert len(data["entries"]) == variants == metadata["variants"]
    assert sum(row[3] for row in data["entries"]) == risk
    assert variants - risk == safe
    assert len({row[0] for row in data["entries"]}) == variants
    assert len({row[1] for row in data["entries"]}) == metadata["command_names"]
    for row in data["entries"]:
        assert all(data["strings"][row[index]] for index in (1, 2, 5, 7))
        assert bool(data["strings"][row[4]]) == bool(row[3])


@pytest.mark.parametrize("profile", REFERENCE_PROFILES)
def test_reference_pagination_filters_and_scope(profile):
    first = search_reference(profile)
    second = search_reference(profile, page=2)
    assert len(first["items"]) == len(second["items"]) == 20
    assert {item["command"] for item in first["items"]}.isdisjoint(item["command"] for item in second["items"])
    for risk in ("risk", "safe"):
        grouped = search_reference(profile, risk=risk)
        assert grouped["matching_variants"] == grouped["catalog"][risk]
        group = grouped["items"][0]
        forms = search_reference(profile, risk=risk, command=group["command"])
        assert forms["total"] == group["variants"]
        for item in forms["items"]:
            assert item["risk"] == risk and item["command"] == group["command"]
            assert bool(item["reason"]) == (risk == "risk")
            assert item["sources"] and item["models"]
    assert not search_reference(profile, q="no-such-command-reference-987654")["items"]
    assert not search_reference(profile, page=100000)["items"]
    assert _catalog.cache_info().currsize == 1


def test_reference_search_models_and_brand_isolation():
    assert search_reference("huawei_vrp", q="CloudEngine")["total"] > 0
    assert search_reference("zte_zxr10", q="5960")["total"] > 0
    for profile in ("linux", "h3c_comware", "../../etc/passwd"):
        with pytest.raises(ValueError):
            search_reference(profile)


def test_reference_api_auth_validation_and_pagination(monkeypatch):
    client = TestClient(gateway.app)  # No lifespan, real devices/services are untouched.
    assert client.get("/api/commands/reference?device_profile=cisco_ios").status_code == 401
    monkeypatch.setattr(gateway, "require_authenticated", lambda request: object())
    response = client.get("/api/commands/reference", params={"device_profile": "cisco_ios", "limit": 2, "risk": "safe"})
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    for params in ({"device_profile": "linux"}, {"device_profile": "cisco_ios", "limit": 51},
                   {"device_profile": "cisco_ios", "risk": "unknown"}, {"device_profile": "cisco_ios", "page": 0}):
        assert client.get("/api/commands/reference", params=params).status_code == 422


def test_new_subtypes_device_api_create_edit_and_list(tmp_path, monkeypatch):
    path = tmp_path / "api.db"
    monkeypatch.setattr(gateway, "TargetRepository", lambda: TargetRepository(path))
    monkeypatch.setattr(gateway, "RecentConnectionRepository", lambda: RecentConnectionRepository(path))
    client = TestClient(gateway.app)
    ids = []
    for index, profile in enumerate(REFERENCE_PROFILES):
        payload = {"protocol": "ssh", "device_type": "switch", "device_profile": profile,
                   "address": f"192.0.2.{index + 1}", "port": 22, "remark": profile, "username": "admin"}
        created = client.post("/api/devices", json=payload)
        assert created.status_code == 201, created.text
        device_id = created.json()["id"]
        ids.append(device_id)
        updated = client.patch(f"/api/devices/{device_id}", json={**payload, "notes": "保存及编辑测试"})
        assert updated.status_code == 200 and updated.json()["device_profile"] == profile
    listed = client.get("/api/devices").json()["items"]
    assert {item["device_profile"] for item in listed} == set(REFERENCE_PROFILES)
    assert {item["id"] for item in listed} == set(ids)


@pytest.mark.parametrize("profile", REFERENCE_PROFILES)
def test_subtypes_validate_and_do_not_inherit_linux_or_h3c(profile, tmp_path):
    assert require_device_profile("switch", profile).key == profile
    payload = {"protocol": "ssh", "device_type": "switch", "device_profile": profile, "host": "192.0.2.1", "port": 22}
    assert gateway.ConnectRequest(**payload).device_profile == profile
    with pytest.raises(ValidationError):
        gateway.ConnectRequest(**{**payload, "device_type": "server"})
    completions = complete_commands("s", device_profile=profile, limit=8)
    assert completions and all(item["command"] != "systemctl" for item in completions)
    assert recommend_next_commands("ls", device_profile=profile, limit=5) == []
    save_command = {"huawei_vrp": "save", "cisco_ios": "copy running-config startup-config", "zte_zxr10": "write"}[profile]
    for command in ("shutdown", "vlan 100", save_command, "show version; reload", "show version > flash:/test"):
        assert check(command, device_profile=profile)["action"] == "confirm"
    if profile == "zte_zxr10":
        # A different brand's unlisted form is unknown, not an implicit risk.
        assert check("save", device_profile=profile) is None
    review = _review_history(_PendingReview("test", "ssh", "switch", profile, [{"seq": 1, "command": "show version"}], 0, 0))
    assert review["status"] == "ready" and not review["lock"]
    assert review["review_source"] == "rules" and review["findings"]
    repo = TargetRepository(tmp_path / "new.db")
    created = repo.create(Target(id="", protocol="ssh", device_type="switch", device_profile=profile,
                                 host="192.0.2.1", port=22, remark=profile, status="saved"))
    recent = RecentConnectionRepository(tmp_path / "new.db")
    recent.remember(created, device_id=created.id, opened_at="2026-09-15T01:00:00Z")
    assert repo.get(created.id).device_profile == profile
    assert recent.list()[0].device_profile == profile


def make_legacy_database(path):
    legacy = SCHEMA.replace(PROFILE_SQL, "'linux', 'h3c_comware'").replace(
        f"device_profile IN ({SWITCH_PROFILE_SQL})", "device_profile = 'h3c_comware'")
    with sqlite3.connect(path) as conn:
        conn.executescript(legacy)
        conn.execute("ALTER TABLE recent_connections ADD COLUMN legacy_metadata TEXT DEFAULT 'preserve me'")
        conn.execute("CREATE INDEX custom_target_host ON targets(host)")
        conn.execute("INSERT INTO targets(id,protocol,device_type,device_profile,host,port,notes) "
                     "VALUES('old-h3c','ssh','switch','h3c_comware','192.0.2.2',22,'keep notes')")
        conn.execute("INSERT INTO recent_connections(id,device_id,protocol,device_type,device_profile,host,port,last_opened_at,operation_risk) "
                     "VALUES('old-history','old-h3c','ssh','switch','h3c_comware','192.0.2.2',22,'yesterday',1)")
    return legacy


def test_old_constraints_expand_without_losing_history_or_metadata(tmp_path):
    database = tmp_path / "legacy.db"
    make_legacy_database(database)
    connection.init_db(database)
    repo = TargetRepository(database)
    old = repo.get("old-h3c")
    assert old.notes == "keep notes" and old.device_profile == "h3c_comware"
    for index, profile in enumerate(REFERENCE_PROFILES):
        created = repo.create(replace(old, id="", host=f"192.0.2.{index + 10}", device_profile=profile))
        RecentConnectionRepository(database).remember(created, device_id=created.id, opened_at="today")
        connection.init_db(database)  # Idempotent, including newly stored profiles.
        assert repo.get(created.id).device_profile == profile
    with connection.connect(database) as conn:
        row = conn.execute("SELECT * FROM recent_connections WHERE id='old-history'").fetchone()
        assert row["operation_risk"] == 1 and row["legacy_metadata"] == "preserve me"
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='custom_target_host'").fetchone()
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='targets_set_updated_at'").fetchone()
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE targets SET device_type='server' WHERE id != 'old-h3c'")


def test_migration_failure_rolls_back_parent_rebuild(tmp_path, monkeypatch):
    database = tmp_path / "rollback.db"
    make_legacy_database(database)
    def fail(_conn):
        raise RuntimeError("injected migration failure")
    monkeypatch.setattr(connection, "_migrate_recent_connections", fail)
    with pytest.raises(RuntimeError):
        connection.init_db(database)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM recent_connections").fetchone()[0] == 1
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='targets'").fetchone()[0]
        assert "huawei_vrp" not in ddl
