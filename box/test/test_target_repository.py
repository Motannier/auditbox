from pathlib import Path
import sqlite3

import pytest

from auditbox.db.models import CommandAudit, Target
from auditbox.db.repositories import (
    CommandAuditRepository,
    RecentConnectionRepository,
    TargetRepository,
)


def target(
    *,
    target_id: str = "",
    host: str = "192.168.88.10",
    port: int = 22,
    protocol: str = "ssh",
    device_type: str = "switch",
    device_profile: str | None = None,
    remark: str = "交换机",
    notes: str = "",
    status: str = "saved",
    last_connected_at: str | None = None,
) -> Target:
    return Target(
        id=target_id,
        protocol=protocol,
        device_type=device_type,
        device_profile=device_profile or ("h3c_comware" if device_type == "switch" else "linux"),
        host=host,
        port=port,
        remark=remark,
        notes=notes,
        status=status,
        username="admin" if protocol == "ssh" else None,
        last_connected_at=last_connected_at,
    )


def test_device_update_keeps_stable_id(tmp_path: Path) -> None:
    repository = TargetRepository(tmp_path / "auditbox.db")
    created = repository.create(target())

    updated = repository.update(
        created.id,
        target(
            host="192.168.88.11",
            port=23,
            protocol="telnet",
            remark="新名称",
            notes="边界网络设备",
        ),
    )

    assert updated.id == created.id
    assert updated.host == "192.168.88.11"
    assert updated.port == 23
    assert updated.protocol == "telnet"
    assert updated.device_type == "switch"
    assert updated.device_profile == "h3c_comware"
    assert updated.remark == "新名称"
    assert updated.notes == "边界网络设备"


def test_duplicate_endpoint_is_rejected_on_create_and_update(tmp_path: Path) -> None:
    repository = TargetRepository(tmp_path / "auditbox.db")
    first = repository.create(target(host="192.168.88.20"))
    second = repository.create(target(host="192.168.88.21"))

    with pytest.raises(ValueError, match="相同协议"):
        repository.create(target(host="192.168.88.20"))

    with pytest.raises(ValueError, match="相同协议"):
        repository.update(second.id, target(host=first.host))


def test_repository_rejects_mismatched_device_classification(tmp_path: Path) -> None:
    repository = TargetRepository(tmp_path / "auditbox.db")

    with pytest.raises(ValueError, match="不匹配"):
        repository.create(target(device_type="switch", device_profile="linux"))


def test_legacy_target_schema_is_migrated_without_losing_saved_devices(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE targets (
                id TEXT PRIMARY KEY,
                protocol TEXT NOT NULL CHECK (protocol IN ('ssh', 'telnet')),
                host TEXT NOT NULL,
                port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
                remark TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'template',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO targets (id, protocol, host, port, remark, status)
            VALUES ('saved-switch', 'ssh', '192.168.88.20', 22, '核心交换机', 'saved');
        """)

    repository = TargetRepository(database)
    console_target = repository.create(target(
        host="/dev/serial/by-id/usb-FTDI_Test-if00-port0",
        port=115200,
        protocol="console",
        remark="核心交换机 Console",
    ))

    saved = repository.get("saved-switch")
    assert saved is not None
    assert saved.remark == "核心交换机"
    assert saved.device_type == "server"
    assert saved.device_profile == "linux"
    assert console_target.protocol == "console"
    assert console_target.port == 115200


def test_current_schema_adds_device_type_without_losing_review_data(tmp_path: Path) -> None:
    database = tmp_path / "current.db"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE targets (
                id TEXT PRIMARY KEY,
                protocol TEXT NOT NULL CHECK (protocol IN ('ssh', 'telnet', 'console')),
                host TEXT NOT NULL,
                port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 4000000),
                remark TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                username TEXT,
                status TEXT NOT NULL DEFAULT 'template',
                last_connected_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(protocol, host, port)
            );
            CREATE TABLE recent_connections (
                id TEXT PRIMARY KEY,
                device_id TEXT UNIQUE REFERENCES targets(id) ON DELETE CASCADE,
                protocol TEXT NOT NULL CHECK (protocol IN ('ssh', 'telnet', 'console')),
                host TEXT NOT NULL,
                port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 4000000),
                remark TEXT NOT NULL DEFAULT '',
                username TEXT,
                last_opened_at TEXT NOT NULL,
                last_operation_at TEXT,
                last_operation_result TEXT NOT NULL DEFAULT '',
                operation_risk INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(protocol, host, port)
            );
            INSERT INTO targets (
                id, protocol, host, port, remark, notes, username, status, last_connected_at
            ) VALUES (
                'current-device', 'ssh', '192.168.88.30', 22, '现网设备',
                '最新版备注', 'admin', 'online', '2026-08-25T08:00:00Z'
            );
            INSERT INTO recent_connections (
                id, device_id, protocol, host, port, remark, username, last_opened_at,
                last_operation_at, last_operation_result, operation_risk
            ) VALUES (
                'current-recent', 'current-device', 'ssh', '192.168.88.30', 22,
                '现网设备', 'admin', '2026-08-25T08:00:00Z',
                '2026-08-25T08:01:00Z', '发现高风险操作', 1
            );
        """)

    saved = TargetRepository(database).get("current-device")
    recent = RecentConnectionRepository(database).list(limit=1)[0]

    assert saved is not None
    assert saved.device_type == "server"
    assert saved.device_profile == "linux"
    assert saved.notes == "最新版备注"
    assert recent.device_type == "server"
    assert recent.device_profile == "linux"
    assert recent.last_operation_at == "2026-08-25T08:01:00Z"
    assert recent.last_operation_result == "发现高风险操作"
    assert recent.operation_risk is True


def test_profile_migration_maps_existing_switch_records_without_deleting_them(tmp_path: Path) -> None:
    database = tmp_path / "device-type-only.db"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE targets (
                id TEXT PRIMARY KEY,
                protocol TEXT NOT NULL CHECK (protocol IN ('ssh', 'telnet', 'console')),
                device_type TEXT NOT NULL CHECK (device_type IN ('server', 'switch')),
                host TEXT NOT NULL,
                port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 4000000),
                remark TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                username TEXT,
                status TEXT NOT NULL DEFAULT 'template',
                last_connected_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE recent_connections (
                id TEXT PRIMARY KEY,
                device_id TEXT UNIQUE REFERENCES targets(id) ON DELETE CASCADE,
                protocol TEXT NOT NULL CHECK (protocol IN ('ssh', 'telnet', 'console')),
                device_type TEXT NOT NULL CHECK (device_type IN ('server', 'switch')),
                host TEXT NOT NULL,
                port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 4000000),
                remark TEXT NOT NULL DEFAULT '',
                username TEXT,
                last_opened_at TEXT NOT NULL,
                last_operation_at TEXT,
                last_operation_result TEXT NOT NULL DEFAULT '',
                operation_risk INTEGER NOT NULL DEFAULT 0,
                UNIQUE(protocol, host, port)
            );
            INSERT INTO targets (
                id, protocol, device_type, host, port, remark, status, last_connected_at
            ) VALUES (
                'h3c-switch', 'console', 'switch',
                '/dev/serial/by-id/usb-Prolific_Test-if00-port0', 9600,
                'H3C-console', 'online', '2026-08-25T08:00:00Z'
            );
            INSERT INTO recent_connections (
                id, device_id, protocol, device_type, host, port, remark, last_opened_at
            ) VALUES (
                'h3c-recent', 'h3c-switch', 'console', 'switch',
                '/dev/serial/by-id/usb-Prolific_Test-if00-port0', 9600,
                'H3C-console', '2026-08-25T08:00:00Z'
            );
        """)

    saved = TargetRepository(database).get("h3c-switch")
    recent = RecentConnectionRepository(database).list(limit=1)[0]

    assert saved is not None
    assert saved.id == "h3c-switch"
    assert saved.device_type == "switch"
    assert saved.device_profile == "h3c_comware"
    assert recent.device_id == "h3c-switch"
    assert recent.device_profile == "h3c_comware"


def test_legacy_templates_are_removed_without_deleting_real_devices(tmp_path: Path) -> None:
    repository = TargetRepository(tmp_path / "auditbox.db")
    repository.create(target(target_id="core-switch", status="template"))
    repository.create(
        target(
            target_id="lab-router",
            host="192.168.88.12",
            status="online",
            last_connected_at="2026-07-14T08:00:00Z",
        ),
    )

    repository.remove_legacy_seed_targets()

    assert repository.get("core-switch") is None
    assert repository.get("lab-router") is not None


def test_recent_connection_persists_real_operation_result(tmp_path: Path) -> None:
    database = tmp_path / "auditbox.db"
    target_repository = TargetRepository(database)
    saved = target_repository.create(target())
    recent_repository = RecentConnectionRepository(database)
    recent_repository.remember(
        saved,
        device_id=saved.id,
        opened_at="2026-08-11T09:00:00Z",
    )

    changed = recent_repository.record_operation(
        protocol=saved.protocol,
        host=saved.host,
        port=saved.port,
        operated_at="2026-08-11T09:01:00Z",
        result="未发现风险",
        risk=False,
    )

    recent = recent_repository.list(limit=1)[0]
    assert changed is True
    assert recent.last_operation_at == "2026-08-11T09:01:00Z"
    assert recent.last_operation_result == "未发现风险"
    assert recent.operation_risk is False


@pytest.mark.parametrize("protocol,host,port", [
    ("ssh", "192.0.2.1", 22), ("telnet", "192.0.2.1", 23),
    ("console", "/dev/ttyUSB0", 9600), ("console", "/dev/ttyUSB0", 115200),
])
def test_legacy_direct_recent_resolves_saved_device_without_rewriting_history(tmp_path, protocol, host, port):
    database = tmp_path / "recent.db"
    targets = TargetRepository(database)
    recents = RecentConnectionRepository(database)
    recents.remember(target(protocol=protocol, host=host, port=port, device_type="server", remark="旧名称"),
                     device_id=None, opened_at="2026-09-16T08:00:00Z")
    recents.record_operation(protocol=protocol, host=host, port=port,
                             operated_at="2026-09-16T08:01:00Z", result="未发现风险", risk=False)
    before = recents.list()[0]
    saved = targets.create(target(protocol=protocol, host=host, port=port, remark="保存的名称"))
    # Simulate an old unbound recent entry (no sync_device call).
    resolved = recents.list()[0]
    assert resolved.id == before.id
    assert resolved.device_id == saved.id
    assert (resolved.remark, resolved.device_type, resolved.device_profile, resolved.username) == (
        saved.remark, saved.device_type, saved.device_profile, saved.username,
    )
    assert resolved.last_opened_at == before.last_opened_at
    assert resolved.last_operation_at == before.last_operation_at
    assert resolved.last_operation_result == before.last_operation_result
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT device_id FROM recent_connections").fetchone()[0] is None


def test_recent_explicit_device_id_wins_over_changed_endpoint(tmp_path):
    database = tmp_path / "recent.db"
    targets = TargetRepository(database)
    recents = RecentConnectionRepository(database)
    saved = targets.create(target())
    recents.remember(saved, device_id=saved.id, opened_at="2026-09-16T08:00:00Z")
    moved = targets.update(saved.id, target(host="192.0.2.2", remark="新的设备参数"))
    targets.create(target())  # A different saved device now uses the old endpoint.
    resolved = recents.list()[0]
    assert resolved.device_id == saved.id
    assert resolved.host == moved.host
    assert resolved.remark == moved.remark


@pytest.mark.parametrize("protocol,host,port", [
    ("ssh", "192.168.88.11", 22), ("ssh", "192.168.88.10", 2222),
    ("telnet", "192.168.88.10", 22), ("console", "/dev/ttyUSB0", 115200),
])
def test_recent_different_endpoint_is_not_attached(tmp_path, protocol, host, port):
    database = tmp_path / "recent.db"
    targets = TargetRepository(database)
    recents = RecentConnectionRepository(database)
    targets.create(target(protocol="console", host="/dev/ttyUSB0", port=9600)
                   if protocol == "console" else target())
    recents.remember(target(protocol=protocol, host=host, port=port, remark="临时"),
                     device_id=None, opened_at="2026-09-16T08:00:00Z")
    resolved = recents.list()[0]
    assert resolved.device_id is None
    assert resolved.remark == "临时"


def test_command_suggestions_are_strictly_scoped_to_current_target(tmp_path: Path) -> None:
    repository = CommandAuditRepository(tmp_path / "auditbox.db")
    repository.create(CommandAudit(
        session_id="one",
        target="192.168.88.10:22",
        protocol="ssh",
        command="show version",
        ts="2026-07-14T08:00:00Z",
    ))
    repository.create(CommandAudit(
        session_id="two",
        target="192.168.88.11:22",
        protocol="ssh",
        command="show running-config",
        ts="2026-07-14T08:01:00Z",
    ))

    suggestions = repository.suggest(
        prefix="show ",
        target="192.168.88.10:22",
        protocol="ssh",
    )

    assert [item["command"] for item in suggestions] == ["show version"]


def test_command_suggestion_prefix_treats_sql_wildcards_as_text(tmp_path: Path) -> None:
    repository = CommandAuditRepository(tmp_path / "auditbox.db")
    repository.create(CommandAudit(
        session_id="one",
        target="192.168.88.10:22",
        protocol="ssh",
        command="show_version",
        ts="2026-07-14T08:00:00Z",
    ))
    repository.create(CommandAudit(
        session_id="one",
        target="192.168.88.10:22",
        protocol="ssh",
        command="showXversion",
        ts="2026-07-14T08:01:00Z",
    ))

    suggestions = repository.suggest(prefix="show_", target="192.168.88.10:22")

    assert [item["command"] for item in suggestions] == ["show_version"]
