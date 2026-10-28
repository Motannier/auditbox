"""Database path configuration."""

from __future__ import annotations

from auditbox.env import AUDITBOX_DB_DIR, AUDITBOX_DB_PATH

DB_DIR = AUDITBOX_DB_DIR
DB_PATH = AUDITBOX_DB_PATH


def ensure_db_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
