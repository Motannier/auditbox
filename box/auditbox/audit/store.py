"""Linked business audit records. No terminal keystrokes or credentials are stored here.

The four projections and their incremental export journal commit atomically. The
journal is also the stable ordering when the board's wall clock is corrected.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from auditbox.db.config import DB_PATH

log = logging.getLogger(__name__)
TABLES = {
    "logins": "audit_logins",
    "executions": "audit_executions",
    "inputs": "audit_inputs",
    "behaviors": "audit_behaviors",
}
SECRET = re.compile(
    r"(?i)((?:\b(?:password|passwd|passphrase|secret|token|api[_-]?key)\b)"
    r"(?:\s+(?:simple|cipher|irreversible-cipher))?\s*(?:=|:|\s)\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s;&|]+)"
)
AUTH = re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)\S+")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def redact(value):
    if isinstance(value, str):
        return AUTH.sub(r"\1[已隐藏]", SECRET.sub(r"\1[已隐藏]", value))
    if isinstance(value, dict):
        return {
            str(k): (
                "[已隐藏]"
                if re.fullmatch(
                    r"(?i)(password|passwd|passphrase|secret|token|api[_-]?key|authorization)",
                    str(k),
                )
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value[:100]]
    return value


def hardware_id():
    """Read the SoC serial itself, never a hash or the CPU model/part number."""
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text()
        match = re.search(r"^Serial\s*:\s*([0-9a-fA-F]+)\s*$", cpuinfo, re.MULTILINE)
        if match and 8 <= len(match[1]) <= 64 and int(match[1], 16):
            return match[1].lower()
    except (OSError, UnicodeError):
        pass
    for name in (
        "/sys/devices/soc0/serial_number",
        "/sys/bus/soc/devices/soc0/serial_number",
    ):
        try:
            serial = Path(name).read_text().strip("\x00\n \t\r")
            if re.fullmatch(r"[0-9a-fA-F]{8,64}", serial) and int(serial, 16):
                return serial.lower()
        except (OSError, UnicodeError):
            pass
    return None


class AuditStore:
    def __init__(self, path=None):
        self.path = Path(path or DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS box_info (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    box_id TEXT NOT NULL UNIQUE,
                    last_system_update_at TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_changes (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL, record_id TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS box_identity_aliases (
                    box_id TEXT PRIMARY KEY
                );
            """)
            for table in TABLES.values():
                conn.executescript(f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        id TEXT PRIMARY KEY, login_id TEXT, session_id TEXT,
                        execution_id TEXT, input_id TEXT, ts TEXT NOT NULL,
                        account TEXT, device_id TEXT, target TEXT, data TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_{table}_login ON {table}(login_id, ts);
                    CREATE INDEX IF NOT EXISTS idx_{table}_session ON {table}(session_id);
                    CREATE INDEX IF NOT EXISTS idx_{table}_execution ON {table}(execution_id);
                """)
            serial = hardware_id()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO box_info VALUES(1, ?, NULL)",
                (serial or "box-" + uuid4().hex[:24],),
            )
            previous = conn.execute("SELECT box_id FROM box_info WHERE singleton=1").fetchone()[0]
            # Only upgrade the former generated ID. A different real CPU serial
            # indicates a copied database, not another alias for the same board.
            if serial and previous.startswith("box-"):
                conn.execute("INSERT OR IGNORE INTO box_identity_aliases VALUES(?)", (previous,))
                conn.execute("UPDATE box_info SET box_id=? WHERE singleton=1", (serial,))
                for kind, table in TABLES.items():
                    for row in conn.execute(f"SELECT data FROM {table}").fetchall():
                        self._put(conn, kind, json.loads(row[0]))
            elif serial and previous != serial:
                raise ValueError("盒子数据库中的 CPU 序列号与当前硬件不一致")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def info(self):
        with self.connect() as conn:
            value = dict(
                conn.execute(
                    "SELECT box_id,last_system_update_at FROM box_info WHERE singleton=1"
                ).fetchone()
            )
            aliases = [r[0] for r in conn.execute("SELECT box_id FROM box_identity_aliases ORDER BY box_id")]
            if aliases:
                value["legacy_box_ids"] = aliases
            return value

    def mark_updated(self, timestamp):
        # Deployment calls this only after application health checks succeed.
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError("Update timestamp must include timezone")
        with self.connect() as conn:
            conn.execute(
                "UPDATE box_info SET last_system_update_at=? WHERE singleton=1",
                (value.astimezone(timezone.utc).isoformat(timespec="microseconds"),),
            )

    def _put(self, conn, kind, record):
        value = redact({"id": uuid4().hex, "ts": now(), **record})
        value["box_id"] = conn.execute(
            "SELECT box_id FROM box_info WHERE singleton=1"
        ).fetchone()[0]
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        columns = (
            "id",
            "login_id",
            "session_id",
            "execution_id",
            "input_id",
            "ts",
            "account",
            "device_id",
            "target",
        )
        conn.execute(
            f"INSERT INTO {TABLES[kind]} ({','.join(columns)},data) VALUES ({','.join('?' for _ in range(10))}) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (*[value.get(c) for c in columns], data),
        )
        conn.execute(
            "INSERT INTO audit_changes(kind,record_id,data) VALUES(?,?,?)",
            (kind, value["id"], data),
        )
        return value

    def put(self, kind, record):
        with self.connect() as conn:
            return self._put(conn, kind, record)

    def get(self, kind, record_id):
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT data FROM {TABLES[kind]} WHERE id=?", (record_id,)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def update(self, kind, record_id, **values):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT data FROM {TABLES[kind]} WHERE id=?", (record_id,)
            ).fetchone()
            if row:
                return self._put(
                    conn, kind, {**json.loads(row[0]), **values, "id": record_id}
                )
        return None

    def behavior(
        self, action, *, context=None, execution_id=None, result="success", **details
    ):
        return self.put(
            "behaviors",
            {
                **(context or {}),
                "action": action,
                "execution_id": execution_id,
                "result": result,
                "details": details,
            },
        )

    def execution(self, context, command, source, *, status="submitted", origin=None):
        input_id, execution_id = uuid4().hex, uuid4().hex
        with self.connect() as conn:
            inp = self._put(
                conn,
                "inputs",
                {
                    **context,
                    "id": input_id,
                    "execution_id": execution_id,
                    "source": source,
                    "label": "AI 会话" if source == "agent" else "手动输入",
                    **{
                        key: value
                        for key, value in (origin or {}).items()
                        if key in {"conversation_id", "run_id", "question", "purpose"}
                    },
                },
            )
            execution = self._put(
                conn,
                "executions",
                {
                    **context,
                    "id": execution_id,
                    "input_id": inp["id"],
                    "command": command,
                    "source": source,
                    "status": status,
                    "risk": "unknown",
                    "output": "",
                    "result_basis": "",
                },
            )
        return execution

    def records(
        self, kind, *, login_id="", execution_id="", session_id="", limit=50, offset=0
    ):
        where, args = [], []
        for key, value in (
            ("login_id", login_id),
            ("execution_id", execution_id),
            ("session_id", session_id),
        ):
            if value:
                where.append(key + "=?")
                args.append(value)
        condition = " WHERE " + " AND ".join(where) if where else ""
        with self.connect() as conn:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {TABLES[kind]}{condition}", args
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT data FROM {TABLES[kind]}{condition} ORDER BY ts DESC,rowid DESC LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        return {"items": [json.loads(r[0]) for r in rows], "total": count}

    def changes(self, cursor=0, limit=200):
        with self.connect() as conn:
            high = conn.execute(
                "SELECT COALESCE(MAX(seq),0) FROM audit_changes"
            ).fetchone()[0]
            if cursor > high:
                raise ValueError(
                    "日志游标超过盒子现有记录；数据库可能恢复过备份，请保留管理端历史后人工核对"
                )
            rows = conn.execute(
                "SELECT * FROM audit_changes WHERE seq>? ORDER BY seq LIMIT ?",
                (cursor, limit + 1),
            ).fetchall()
        items = [
            {"seq": r["seq"], "kind": r["kind"], "record": json.loads(r["data"])}
            for r in rows[:limit]
        ]
        identity = self.info()
        # Preserve the original journal while exporting historical revisions
        # under the current identity; cursors and record IDs stay stable.
        for item in items:
            if item["record"].get("box_id") in identity.get("legacy_box_ids", []):
                item["record"]["box_id"] = identity["box_id"]
        return {
            "schema_version": 2,
            **identity,
            "items": items,
            "next_cursor": items[-1]["seq"] if items else cursor,
            "has_more": len(rows) > limit,
        }

    def finish_session(self, session_id):
        # A disconnected terminal does not imply logout or command success.
        for record in self.records("executions", session_id=session_id, limit=100000)[
            "items"
        ]:
            if record.get("status") in {"submitted", "awaiting_confirmation"}:
                status = "unknown" if record["status"] == "submitted" else "cancelled"
                self.update(
                    "executions",
                    record["id"],
                    status=status,
                    result_basis="终端断开，未获得明确执行结果"
                    if status == "unknown"
                    else "终端断开，未确认下发",
                )

    def recover_interrupted(self):
        """Called once when the proxy starts, before accepting new connections."""
        with self.connect() as conn:
            sessions = conn.execute(
                "SELECT DISTINCT session_id FROM audit_executions"
            ).fetchall()
        for row in sessions:
            if row[0]:
                self.finish_session(row[0])


def safe_audit(callback, *args, **kwargs):
    """Audit failures must be visible in service logs, never mistaken for a cable failure."""
    try:
        return callback(*args, **kwargs)
    except Exception:
        log.exception("业务审计写入失败；本次记录可能不完整")
        return None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Update box deployment metadata after successful verification"
    )
    parser.add_argument(
        "--mark-system-updated", required=True, help="Timezone-aware ISO 8601 timestamp"
    )
    args = parser.parse_args()
    store = AuditStore()
    store.mark_updated(args.mark_system_updated)
    print(json.dumps(store.info(), ensure_ascii=False))
