"""Persistence models and table schemas."""

from __future__ import annotations

from dataclasses import dataclass
from auditbox.device_profiles import DEVICE_PROFILES, SWITCH_PROFILES

PROFILE_SQL = ", ".join(f"'{key}'" for key in DEVICE_PROFILES)
SWITCH_PROFILE_SQL = ", ".join(f"'{key}'" for key in SWITCH_PROFILES)
DEVICE_PAIR_CHECK = f"CHECK ((device_type = 'server' AND device_profile = 'linux') OR (device_type = 'switch' AND device_profile IN ({SWITCH_PROFILE_SQL})) OR device_profile NOT IN ({PROFILE_SQL}))"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS targets (
    id TEXT PRIMARY KEY,
    protocol TEXT NOT NULL CHECK (protocol IN ('ssh', 'telnet', 'console')),
    device_type TEXT NOT NULL DEFAULT 'server'
        CHECK (length(device_type) BETWEEN 1 AND 64),
    device_profile TEXT NOT NULL DEFAULT 'linux'
        CHECK (length(device_profile) BETWEEN 1 AND 64),
    host TEXT NOT NULL,
    port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 4000000),
    remark TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    username TEXT,
    status TEXT NOT NULL DEFAULT 'template',
    last_connected_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    {DEVICE_PAIR_CHECK}
);

CREATE TRIGGER IF NOT EXISTS targets_set_updated_at
AFTER UPDATE ON targets
FOR EACH ROW
BEGIN
    UPDATE targets SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

CREATE TABLE IF NOT EXISTS recent_connections (
    id TEXT PRIMARY KEY,
    device_id TEXT UNIQUE,
    protocol TEXT NOT NULL CHECK (protocol IN ('ssh', 'telnet', 'console')),
    device_type TEXT NOT NULL DEFAULT 'server'
        CHECK (length(device_type) BETWEEN 1 AND 64),
    device_profile TEXT NOT NULL DEFAULT 'linux'
        CHECK (length(device_profile) BETWEEN 1 AND 64),
    host TEXT NOT NULL,
    port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 4000000),
    remark TEXT NOT NULL DEFAULT '',
    username TEXT,
    last_opened_at TEXT NOT NULL,
    last_operation_at TEXT,
    last_operation_result TEXT NOT NULL DEFAULT '',
    operation_risk INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (device_id) REFERENCES targets(id) ON DELETE CASCADE,
    UNIQUE (protocol, host, port),
    {DEVICE_PAIR_CHECK}
);

CREATE INDEX IF NOT EXISTS idx_recent_connections_last_opened_at
ON recent_connections(last_opened_at DESC);

CREATE TABLE IF NOT EXISTS command_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    target TEXT NOT NULL,
    protocol TEXT NOT NULL,
    command TEXT NOT NULL,
    ts TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_command_audits_session_id
ON command_audits(session_id);

CREATE INDEX IF NOT EXISTS idx_command_audits_ts
ON command_audits(ts);

CREATE TABLE IF NOT EXISTS terminal_locks (
    target_key TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    intent TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    source_session_id TEXT NOT NULL DEFAULT '',
    locked_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Target:
    id: str
    protocol: str
    device_type: str
    device_profile: str
    host: str
    port: int
    remark: str
    status: str
    notes: str = ""
    username: str | None = None
    last_connected_at: str | None = None


@dataclass(frozen=True)
class RecentConnection:
    id: str
    device_id: str | None
    protocol: str
    device_type: str
    device_profile: str
    host: str
    port: int
    remark: str
    username: str | None
    last_opened_at: str
    last_operation_at: str | None = None
    last_operation_result: str = ""
    operation_risk: bool = False


@dataclass(frozen=True)
class CommandAudit:
    session_id: str
    target: str
    protocol: str
    command: str
    ts: str
    id: int | None = None


@dataclass(frozen=True)
class TargetLock:
    target_key: str
    reason: str
    intent: str
    evidence_json: str
    source_session_id: str
    locked_at: str
