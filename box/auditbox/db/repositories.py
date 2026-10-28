"""Repository objects for SQLite-backed models."""

from __future__ import annotations

import sqlite3
import secrets
from pathlib import Path

from auditbox.db.config import DB_PATH
from auditbox.db.connection import connect, init_db
from auditbox.db.models import CommandAudit, RecentConnection, Target, TargetLock
from auditbox.device_profiles import require_device_profile

LEGACY_SEED_IDS = ("core-switch", "lab-router")
RECENT_CONNECTION_STORAGE_LIMIT = 20


class PageResult:
    def __init__(self, items: list[Target], total: int, page: int, page_size: int):
        self.items = items
        self.total = total
        self.page = page
        self.page_size = page_size


class TargetRepository:
    def __init__(self, db_path: str | Path = DB_PATH):
        self._db_path = db_path
        init_db(db_path)

    def list(self) -> list[Target]:
        with connect(self._db_path) as conn:
            rows = conn.execute("""
                SELECT id, protocol, device_type, device_profile, host, port, remark, notes,
                       status, username, last_connected_at
                FROM targets
                ORDER BY created_at ASC, id ASC
            """).fetchall()
        return [_row_to_target(row) for row in rows]

    def list_page(
        self,
        page: int,
        page_size: int,
        search: str = "",
        connected_only: bool = False,
    ) -> PageResult:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        offset = (page - 1) * page_size
        query = f"%{search.strip()}%"

        where_parts = []
        args: list[str | int] = []
        if connected_only:
            where_parts.append("last_connected_at IS NOT NULL")
        if search.strip():
            where_parts.append("(host LIKE ? OR remark LIKE ? OR notes LIKE ?)")
            args.extend([query, query, query])
        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        with connect(self._db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM targets {where_sql}",
                args,
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT id, protocol, device_type, device_profile, host, port, remark, notes,
                       status, username, last_connected_at
                FROM targets
                {where_sql}
                ORDER BY COALESCE(last_connected_at, created_at) DESC, created_at DESC, id ASC
                LIMIT ? OFFSET ?
                """,
                [*args, page_size, offset],
            ).fetchall()
        return PageResult(
            items=[_row_to_target(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    def create(self, target: Target) -> Target:
        require_device_profile(target.device_type, target.device_profile)
        if self.find_by_endpoint(target.protocol, target.host, target.port):
            raise ValueError("相同协议和目标参数的设备已存在")
        target_id = target.id or secrets.token_hex(8)
        with connect(self._db_path) as conn:
            try:
                conn.execute("""
                    INSERT INTO targets (
                        id, protocol, device_type, device_profile, host, port, remark, notes,
                        username, status, last_connected_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    target_id,
                    target.protocol,
                    target.device_type,
                    target.device_profile,
                    target.host,
                    target.port,
                    target.remark,
                    target.notes,
                    target.username,
                    target.status,
                    target.last_connected_at,
                ))
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError(str(exc)) from exc
        return Target(
            id=target_id,
            protocol=target.protocol,
            device_type=target.device_type,
            device_profile=target.device_profile,
            host=target.host,
            port=target.port,
            remark=target.remark,
            status=target.status,
            notes=target.notes,
            username=target.username,
            last_connected_at=target.last_connected_at,
        )

    def save_connected(self, target: Target) -> Target:
        existing = self.find_by_endpoint(target.protocol, target.host, target.port)
        if existing:
            return self._update_connected(existing.id, target)
        return self.create(target)

    def get(self, target_id: str) -> Target | None:
        with connect(self._db_path) as conn:
            row = conn.execute("""
                SELECT id, protocol, device_type, device_profile, host, port, remark, notes,
                       status, username, last_connected_at
                FROM targets
                WHERE id = ?
            """, (target_id,)).fetchone()
        return _row_to_target(row) if row else None

    def find_by_endpoint(self, protocol: str, host: str, port: int) -> Target | None:
        with connect(self._db_path) as conn:
            row = conn.execute("""
                SELECT id, protocol, device_type, device_profile, host, port, remark, notes,
                       status, username, last_connected_at
                FROM targets
                WHERE protocol = ? AND host = ? AND port = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (protocol, host, port)).fetchone()
        if not row:
            return None
        return _row_to_target(row)

    def update(self, target_id: str, target: Target) -> Target:
        require_device_profile(target.device_type, target.device_profile)
        existing = self.find_by_endpoint(target.protocol, target.host, target.port)
        if existing and existing.id != target_id:
            raise ValueError("相同协议和目标参数的设备已存在")

        with connect(self._db_path) as conn:
            cursor = conn.execute("""
                UPDATE targets
                SET protocol = ?, device_type = ?, device_profile = ?, host = ?, port = ?,
                    remark = ?, notes = ?, username = ?
                WHERE id = ?
            """, (
                target.protocol,
                target.device_type,
                target.device_profile,
                target.host,
                target.port,
                target.remark,
                target.notes,
                target.username,
                target_id,
            ))
            if cursor.rowcount == 0:
                raise LookupError("设备不存在")
            conn.commit()
            row = conn.execute("""
                SELECT id, protocol, device_type, device_profile, host, port, remark, notes,
                       status, username, last_connected_at
                FROM targets
                WHERE id = ?
            """, (target_id,)).fetchone()
        return _row_to_target(row)

    def mark_connected(self, target_id: str, connected_at: str) -> Target | None:
        with connect(self._db_path) as conn:
            conn.execute("""
                UPDATE targets
                SET status = 'online', last_connected_at = ?
                WHERE id = ?
            """, (connected_at, target_id))
            conn.commit()
        return self.get(target_id)

    def remove_legacy_seed_targets(self) -> None:
        placeholders = ",".join("?" for _ in LEGACY_SEED_IDS)
        with connect(self._db_path) as conn:
            conn.execute(
                f"""
                DELETE FROM targets
                WHERE id IN ({placeholders})
                  AND status = 'template'
                  AND last_connected_at IS NULL
                """,
                LEGACY_SEED_IDS,
            )
            conn.commit()

    def _update_connected(self, target_id: str, target: Target) -> Target:
        require_device_profile(target.device_type, target.device_profile)
        with connect(self._db_path) as conn:
            conn.execute("""
                UPDATE targets
                SET device_type = ?,
                    device_profile = ?,
                    remark = ?,
                    notes = ?,
                    username = ?,
                    status = ?,
                    last_connected_at = ?
                WHERE id = ?
            """, (
                target.device_type,
                target.device_profile,
                target.remark,
                target.notes,
                target.username,
                target.status,
                target.last_connected_at,
                target_id,
            ))
            conn.commit()
            row = conn.execute("""
                SELECT id, protocol, device_type, device_profile, host, port, remark, notes,
                       status, username, last_connected_at
                FROM targets
                WHERE id = ?
            """, (target_id,)).fetchone()
        return _row_to_target(row)


def _row_to_target(row: sqlite3.Row) -> Target:
    return Target(
        id=row["id"],
        protocol=row["protocol"],
        device_type=row["device_type"],
        device_profile=row["device_profile"],
        host=row["host"],
        port=row["port"],
        remark=row["remark"],
        status=row["status"],
        notes=row["notes"],
        username=row["username"],
        last_connected_at=row["last_connected_at"],
    )


class RecentConnectionRepository:
    def __init__(self, db_path: str | Path = DB_PATH):
        self._db_path = db_path
        init_db(db_path)

    def list(self, limit: int = 4) -> list[RecentConnection]:
        limit = min(max(limit, 1), RECENT_CONNECTION_STORAGE_LIMIT)
        with connect(self._db_path) as conn:
            rows = conn.execute("""
                SELECT
                    recent.id,
                    target.id AS device_id,
                    CASE WHEN target.id IS NULL THEN recent.protocol ELSE target.protocol END
                        AS protocol,
                    CASE WHEN target.id IS NULL THEN recent.device_type ELSE target.device_type END
                        AS device_type,
                    CASE WHEN target.id IS NULL THEN recent.device_profile ELSE target.device_profile END
                        AS device_profile,
                    CASE WHEN target.id IS NULL THEN recent.host ELSE target.host END
                        AS host,
                    CASE WHEN target.id IS NULL THEN recent.port ELSE target.port END
                        AS port,
                    CASE WHEN target.id IS NULL THEN recent.remark ELSE target.remark END
                        AS remark,
                    CASE WHEN target.id IS NULL THEN recent.username ELSE target.username END
                        AS username,
                    recent.last_opened_at,
                    recent.last_operation_at,
                    recent.last_operation_result,
                    recent.operation_risk
                FROM recent_connections AS recent
                LEFT JOIN targets AS target ON target.id = COALESCE(
                    recent.device_id,
                    (SELECT saved.id FROM targets AS saved
                     WHERE saved.protocol = recent.protocol
                       AND saved.host = recent.host AND saved.port = recent.port
                     ORDER BY saved.created_at DESC LIMIT 1)
                )
                WHERE recent.device_id IS NULL OR target.id IS NOT NULL
                ORDER BY recent.last_opened_at DESC, recent.id DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [_row_to_recent_connection(row) for row in rows]

    def record_operation(
        self,
        *,
        protocol: str,
        host: str,
        port: int,
        operated_at: str,
        result: str,
        risk: bool,
    ) -> bool:
        """Persist the latest result emitted by the real terminal review channel."""
        with connect(self._db_path) as conn:
            cursor = conn.execute("""
                UPDATE recent_connections
                SET last_operation_at = ?,
                    last_operation_result = ?,
                    operation_risk = ?
                WHERE protocol = ? AND host = ? AND port = ?
            """, (
                operated_at,
                result[:120],
                1 if risk else 0,
                protocol.lower(),
                host,
                port,
            ))
            conn.commit()
        return cursor.rowcount > 0

    def remember(
        self,
        target: Target,
        *,
        device_id: str | None,
        opened_at: str,
    ) -> None:
        with connect(self._db_path) as conn:
            resolved_device_id = device_id or self._find_device_id(conn, target)
            if resolved_device_id:
                self._remember_saved(
                    conn,
                    target,
                    device_id=resolved_device_id,
                    opened_at=opened_at,
                )
            else:
                self._remember_direct(conn, target, opened_at=opened_at)
            self._prune(conn)
            conn.commit()

    def sync_device(self, target: Target) -> None:
        """Attach matching direct history and keep saved-device details current."""
        with connect(self._db_path) as conn:
            rows = conn.execute("""
                SELECT id, device_id, last_opened_at
                FROM recent_connections
                WHERE device_id = ?
                   OR (protocol = ? AND host = ? AND port = ?)
                ORDER BY last_opened_at DESC, id DESC
            """, (
                target.id,
                target.protocol,
                target.host,
                target.port,
            )).fetchall()
            if not rows:
                return

            keep = next((row for row in rows if row["device_id"] == target.id), rows[0])
            latest_opened_at = max(str(row["last_opened_at"]) for row in rows)
            discard_ids = [row["id"] for row in rows if row["id"] != keep["id"]]
            if discard_ids:
                placeholders = ",".join("?" for _ in discard_ids)
                conn.execute(
                    f"DELETE FROM recent_connections WHERE id IN ({placeholders})",
                    discard_ids,
                )
            conn.execute("""
                UPDATE recent_connections
                SET device_id = ?,
                    protocol = ?,
                    device_type = ?,
                    device_profile = ?,
                    host = ?,
                    port = ?,
                    remark = ?,
                    username = ?,
                    last_opened_at = ?
                WHERE id = ?
            """, (
                target.id,
                target.protocol,
                target.device_type,
                target.device_profile,
                target.host,
                target.port,
                target.remark,
                target.username,
                latest_opened_at,
                keep["id"],
            ))
            conn.commit()

    @staticmethod
    def _find_device_id(conn: sqlite3.Connection, target: Target) -> str | None:
        row = conn.execute("""
            SELECT id
            FROM targets
            WHERE protocol = ? AND host = ? AND port = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (target.protocol, target.host, target.port)).fetchone()
        return str(row["id"]) if row else None

    @staticmethod
    def _remember_saved(
        conn: sqlite3.Connection,
        target: Target,
        *,
        device_id: str,
        opened_at: str,
    ) -> None:
        rows = conn.execute("""
            SELECT id
            FROM recent_connections
            WHERE device_id = ?
               OR (protocol = ? AND host = ? AND port = ?)
            ORDER BY last_opened_at DESC, id DESC
        """, (
            device_id,
            target.protocol,
            target.host,
            target.port,
        )).fetchall()
        record_id = str(rows[0]["id"]) if rows else secrets.token_hex(8)
        duplicate_ids = [str(row["id"]) for row in rows[1:]]
        if duplicate_ids:
            placeholders = ",".join("?" for _ in duplicate_ids)
            conn.execute(
                f"DELETE FROM recent_connections WHERE id IN ({placeholders})",
                duplicate_ids,
            )
        if rows:
            conn.execute("""
                UPDATE recent_connections
                SET device_id = ?,
                    protocol = ?,
                    device_type = ?,
                    device_profile = ?,
                    host = ?,
                    port = ?,
                    remark = ?,
                    username = ?,
                    last_opened_at = ?
                WHERE id = ?
            """, (
                device_id,
                target.protocol,
                target.device_type,
                target.device_profile,
                target.host,
                target.port,
                target.remark,
                target.username,
                opened_at,
                record_id,
            ))
        else:
            conn.execute("""
                INSERT INTO recent_connections (
                    id, device_id, protocol, device_type, device_profile, host, port,
                    remark, username, last_opened_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record_id,
                device_id,
                target.protocol,
                target.device_type,
                target.device_profile,
                target.host,
                target.port,
                target.remark,
                target.username,
                opened_at,
            ))

    @staticmethod
    def _remember_direct(
        conn: sqlite3.Connection,
        target: Target,
        *,
        opened_at: str,
    ) -> None:
        row = conn.execute("""
            SELECT id
            FROM recent_connections
            WHERE protocol = ? AND host = ? AND port = ?
            LIMIT 1
        """, (target.protocol, target.host, target.port)).fetchone()
        if row:
            conn.execute("""
                UPDATE recent_connections
                SET device_type = ?,
                    device_profile = ?,
                    remark = ?,
                    username = ?,
                    last_opened_at = ?
                WHERE id = ?
            """, (
                target.device_type,
                target.device_profile,
                target.remark,
                target.username,
                opened_at,
                row["id"],
            ))
            return
        conn.execute("""
            INSERT INTO recent_connections (
                id, device_id, protocol, device_type, device_profile, host, port,
                remark, username, last_opened_at
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            secrets.token_hex(8),
            target.protocol,
            target.device_type,
            target.device_profile,
            target.host,
            target.port,
            target.remark,
            target.username,
            opened_at,
        ))

    @staticmethod
    def _prune(conn: sqlite3.Connection) -> None:
        conn.execute("""
            DELETE FROM recent_connections
            WHERE id NOT IN (
                SELECT id
                FROM recent_connections
                ORDER BY last_opened_at DESC, id DESC
                LIMIT ?
            )
        """, (RECENT_CONNECTION_STORAGE_LIMIT,))


def _row_to_recent_connection(row: sqlite3.Row) -> RecentConnection:
    return RecentConnection(
        id=row["id"],
        device_id=row["device_id"],
        protocol=row["protocol"],
        device_type=row["device_type"],
        device_profile=row["device_profile"],
        host=row["host"],
        port=row["port"],
        remark=row["remark"],
        username=row["username"],
        last_opened_at=row["last_opened_at"],
        last_operation_at=row["last_operation_at"],
        last_operation_result=row["last_operation_result"],
        operation_risk=bool(row["operation_risk"]),
    )


class CommandAuditRepository:
    def __init__(self, db_path: str | Path = DB_PATH):
        self._db_path = db_path
        init_db(db_path)

    def create(self, record: CommandAudit) -> CommandAudit:
        with connect(self._db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO command_audits (session_id, target, protocol, command, ts)
                VALUES (?, ?, ?, ?, ?)
            """, (
                record.session_id,
                record.target,
                record.protocol,
                record.command,
                record.ts,
            ))
            conn.commit()
            record_id = cursor.lastrowid
        return CommandAudit(
            id=record_id,
            session_id=record.session_id,
            target=record.target,
            protocol=record.protocol,
            command=record.command,
            ts=record.ts,
        )

    def list_by_session(self, session_id: str) -> list[CommandAudit]:
        with connect(self._db_path) as conn:
            rows = conn.execute("""
                SELECT id, session_id, target, protocol, command, ts
                FROM command_audits
                WHERE session_id = ?
                ORDER BY ts ASC, id ASC
            """, (session_id,)).fetchall()
        return [_row_to_command_audit(row) for row in rows]

    def suggest(
        self,
        prefix: str,
        target: str = "",
        protocol: str = "",
        limit: int = 8,
    ) -> list[dict]:
        prefix = prefix.strip()
        target = target.strip()
        protocol = protocol.strip().lower()
        limit = min(max(limit, 1), 20)
        if not prefix:
            return []

        escaped_prefix = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where_parts = ["command LIKE ? ESCAPE '\\'"]
        where_args: list[str | int] = [f"{escaped_prefix}%"]
        if protocol:
            where_parts.append("protocol = ?")
            where_args.append(protocol)
        if target:
            where_parts.append("target = ?")
            where_args.append(target)

        where_sql = " AND ".join(where_parts)
        with connect(self._db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT command,
                       COUNT(*) AS usage_count,
                       MAX(ts) AS last_used_at
                FROM command_audits
                WHERE {where_sql}
                GROUP BY command
                ORDER BY usage_count DESC, last_used_at DESC, command ASC
                LIMIT ?
                """,
                [*where_args, limit],
            ).fetchall()
        return [
            {
                "command": row["command"],
                "usage_count": row["usage_count"],
                "last_used_at": row["last_used_at"],
            }
            for row in rows
        ]


def _row_to_command_audit(row: sqlite3.Row) -> CommandAudit:
    return CommandAudit(
        id=row["id"],
        session_id=row["session_id"],
        target=row["target"],
        protocol=row["protocol"],
        command=row["command"],
        ts=row["ts"],
    )


class TargetLockRepository:
    def __init__(self, db_path: str | Path = DB_PATH):
        self._db_path = db_path
        init_db(db_path)

    def list(self) -> list[TargetLock]:
        with connect(self._db_path) as conn:
            rows = conn.execute("""
                SELECT target_key, reason, intent, evidence_json,
                       source_session_id, locked_at
                FROM terminal_locks
                ORDER BY locked_at ASC
            """).fetchall()
        return [_row_to_target_lock(row) for row in rows]

    def save(self, lock: TargetLock) -> TargetLock:
        with connect(self._db_path) as conn:
            conn.execute("""
                INSERT INTO terminal_locks (
                    target_key, reason, intent, evidence_json,
                    source_session_id, locked_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_key) DO UPDATE SET
                    reason = excluded.reason,
                    intent = excluded.intent,
                    evidence_json = excluded.evidence_json,
                    source_session_id = excluded.source_session_id,
                    locked_at = excluded.locked_at
            """, (
                lock.target_key,
                lock.reason,
                lock.intent,
                lock.evidence_json,
                lock.source_session_id,
                lock.locked_at,
            ))
            conn.commit()
        return lock

    def remove(self, target_key: str) -> bool:
        with connect(self._db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM terminal_locks WHERE target_key = ?",
                (target_key,),
            )
            conn.commit()
        return cursor.rowcount > 0


def _row_to_target_lock(row: sqlite3.Row) -> TargetLock:
    return TargetLock(
        target_key=row["target_key"],
        reason=row["reason"],
        intent=row["intent"],
        evidence_json=row["evidence_json"],
        source_session_id=row["source_session_id"],
        locked_at=row["locked_at"],
    )
