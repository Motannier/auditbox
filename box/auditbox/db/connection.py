"""SQLite connection and schema initialization."""

from __future__ import annotations

import sqlite3
import re
from pathlib import Path

from auditbox.db.config import DB_PATH, ensure_db_dir
from auditbox.db.models import SCHEMA, PROFILE_SQL, DEVICE_PAIR_CHECK


def connect(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    ensure_db_dir()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str | Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        # Rebuilding a parent table with foreign keys enabled would cascade-delete
        # recent history. Disable enforcement outside the transaction, then check
        # all references before committing the atomic migration.
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            _migrate_targets(conn)
            _migrate_recent_connections(conn)
            _backfill_recent_connections(conn)
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise sqlite3.IntegrityError("Device migration found invalid foreign keys")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")


def _expand_device_constraints(conn: sqlite3.Connection, table: str) -> None:
    """Expand only capability checks, preserving legacy columns, indexes and triggers."""
    if table not in {"targets", "recent_connections"}:
        raise ValueError("Unsupported migration table")
    original = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()[0]
    if DEVICE_PAIR_CHECK in original:
        return
    # Replace the historical built-in pairing constraint, without consuming the
    # table's closing parenthesis (both single-line and multiline DDL exist).
    updated = original
    pairing = re.search(r",\s*CHECK\s*\(\s*\(device_type\s*=\s*'server'", updated, re.I)
    if pairing:
        opening = updated.index('(', pairing.start())
        depth = 0
        for offset in range(opening, len(updated)):
            depth += (updated[offset] == '(') - (updated[offset] == ')')
            if depth == 0:
                updated = updated[:pairing.start()] + updated[offset + 1:]
                break
        else:
            raise ValueError('Invalid legacy device constraint')
    updated = re.sub(r"device_type\s+IN\s*\([^)]*\)",
                     "length(device_type) BETWEEN 1 AND 64", updated, flags=re.I)
    updated = re.sub(
        r"device_profile\s+IN\s*\([^)]*\)",
        "length(device_profile) BETWEEN 1 AND 64", updated, flags=re.I,
    )
    updated = re.sub(r"device_profile\s*=\s*'h3c_comware'",
                     "length(device_profile) BETWEEN 1 AND 64", updated, flags=re.I)
    updated = re.sub(r"protocol\s+IN\s*\([^)]*\)",
                     "protocol IN ('ssh', 'telnet', 'console')", updated, flags=re.I)
    updated = re.sub(r"port\s+BETWEEN\s+1\s+AND\s+65535\b",
                     "port BETWEEN 1 AND 4000000", updated, flags=re.I)
    closing = updated.rfind(')')
    updated = updated[:closing].rstrip() + ',\n    ' + DEVICE_PAIR_CHECK + '\n' + updated[closing:]
    if updated == original:
        return
    objects = conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name = ? "
        "AND type IN ('index', 'trigger') AND sql IS NOT NULL", (table,)
    ).fetchall()
    columns = ", ".join('"' + row['name'].replace('"', '""') + '"'
                        for row in conn.execute(f'PRAGMA table_info("{table}")'))
    staging = f"_auditbox_migrate_{table}"
    conn.execute(f'CREATE TEMP TABLE "{staging}" AS SELECT {columns} FROM "{table}"')
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(updated)
    conn.execute(f'INSERT INTO "{table}" ({columns}) SELECT {columns} FROM temp."{staging}"')
    conn.execute(f'DROP TABLE temp."{staging}"')
    for row in objects:
        conn.execute(row[0])


def _migrate_targets(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(targets)").fetchall()
    }
    if "username" not in columns:
        conn.execute("ALTER TABLE targets ADD COLUMN username TEXT")
    if "notes" not in columns:
        conn.execute("ALTER TABLE targets ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
    if "last_connected_at" not in columns:
        conn.execute("ALTER TABLE targets ADD COLUMN last_connected_at TEXT")
    if "device_type" not in columns:
        conn.execute("""
            ALTER TABLE targets ADD COLUMN device_type TEXT NOT NULL DEFAULT 'server'
                CHECK (device_type IN ('server', 'switch'))
        """)
    if "device_profile" not in columns:
        conn.execute(f"""
            ALTER TABLE targets ADD COLUMN device_profile TEXT NOT NULL DEFAULT 'linux'
                CHECK (device_profile IN ({PROFILE_SQL}))
        """)
    conn.execute("""
        UPDATE targets
        SET device_profile = CASE
            WHEN device_type = 'switch' THEN 'h3c_comware'
            ELSE 'linux'
        END
        WHERE device_profile IS NULL
           OR device_profile = ''
           OR (device_type = 'switch' AND device_profile = 'linux')
           OR (device_type = 'server' AND device_profile = 'h3c_comware')
    """)
    # Backfill the legacy default before installing the stricter pairing check.
    _expand_device_constraints(conn, "targets")

def _migrate_recent_connections(conn: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(recent_connections)").fetchall()
    }
    if "device_type" not in columns:
        conn.execute("""
            ALTER TABLE recent_connections ADD COLUMN device_type TEXT NOT NULL DEFAULT 'server'
                CHECK (device_type IN ('server', 'switch'))
        """)
    if "device_profile" not in columns:
        conn.execute(f"""
            ALTER TABLE recent_connections ADD COLUMN device_profile TEXT NOT NULL DEFAULT 'linux'
                CHECK (device_profile IN ({PROFILE_SQL}))
        """)
    conn.execute("""
        UPDATE recent_connections
        SET device_profile = CASE
            WHEN device_type = 'switch' THEN 'h3c_comware'
            ELSE 'linux'
        END
        WHERE device_profile IS NULL
           OR device_profile = ''
           OR (device_type = 'switch' AND device_profile = 'linux')
           OR (device_type = 'server' AND device_profile = 'h3c_comware')
    """)
    _expand_device_constraints(conn, "recent_connections")
    if "last_operation_at" not in columns:
        conn.execute("ALTER TABLE recent_connections ADD COLUMN last_operation_at TEXT")
    if "last_operation_result" not in columns:
        conn.execute(
            "ALTER TABLE recent_connections ADD COLUMN last_operation_result TEXT NOT NULL DEFAULT ''"
        )
    if "operation_risk" not in columns:
        conn.execute(
            "ALTER TABLE recent_connections ADD COLUMN operation_risk INTEGER NOT NULL DEFAULT 0"
        )


def _backfill_recent_connections(conn: sqlite3.Connection) -> None:
    """Preserve server-side recent history created before the dedicated table."""
    existing = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM recent_connections LIMIT 1)"
    ).fetchone()[0]
    if existing:
        return
    conn.execute("""
        INSERT OR IGNORE INTO recent_connections (
            id, device_id, protocol, device_type, device_profile, host, port,
            remark, username, last_opened_at
        )
        SELECT
            lower(hex(randomblob(8))),
            id,
            protocol,
            device_type,
            device_profile,
            host,
            port,
            remark,
            username,
            last_connected_at
        FROM targets
        WHERE last_connected_at IS NOT NULL
        ORDER BY last_connected_at DESC
        LIMIT 20
    """)
