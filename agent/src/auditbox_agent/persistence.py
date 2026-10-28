"""Stable local queries not exposed by Agno's public database API."""

from __future__ import annotations

import json
from typing import Any

from agno.knowledge.content import Content
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.engine import Engine, RowMapping

from auditbox_agent.config import Settings


class AgentRepository:
    """Small SQLite query adapter for conversations and knowledge metadata."""

    def __init__(self, settings: Settings):
        metadata = MetaData()
        self.sessions = Table(
            "agent_sessions",
            metadata,
            Column("session_id", String, primary_key=True),
            Column("session_type", String),
            Column("agent_id", String),
            Column("team_id", String),
            Column("workflow_id", String),
            Column("user_id", String),
            Column("session_data", JSON),
            Column("agent_data", JSON),
            Column("team_data", JSON),
            Column("workflow_data", JSON),
            Column("metadata", JSON),
            Column("runs", JSON),
            Column("summary", JSON),
            Column("created_at", BigInteger),
            Column("updated_at", BigInteger),
        )
        self.knowledge = Table(
            "knowledge_contents",
            metadata,
            Column("id", String, primary_key=True),
            Column("name", String),
            Column("description", String),
            Column("metadata", JSON),
            Column("type", String),
            Column("size", BigInteger),
            Column("linked_to", String),
            Column("access_count", BigInteger),
            Column("status", String),
            Column("status_message", String),
            Column("created_at", BigInteger),
            Column("updated_at", BigInteger),
            Column("external_id", String),
        )
        self.capabilities = Table(
            "agent_capabilities",
            metadata,
            Column("id", String, primary_key=True),
            Column("kind", String, nullable=False),
            Column("name", String, nullable=False),
            Column("enabled", Boolean, nullable=False, default=False),
            Column("config", JSON, nullable=False, default=dict),
            Column("source", String),
            Column("created_at", BigInteger, nullable=False),
            Column("updated_at", BigInteger, nullable=False),
        )
        self.conversation_files = Table(
            "agent_conversation_files",
            metadata,
            Column("id", String, primary_key=True),
            Column("session_id", String, nullable=False, index=True),
            Column("name", String, nullable=False),
            Column("suffix", String, nullable=False),
            Column("content_type", String),
            Column("size", BigInteger, nullable=False),
            Column("sha256", String, nullable=False),
            Column("original_path", String, nullable=False),
            Column("text_path", String, nullable=False),
            Column("character_count", BigInteger, nullable=False),
            Column("outline", JSON, nullable=False, default=list),
            Column("created_at", BigInteger, nullable=False),
        )
        self.engine: Engine = create_engine(settings.database_url)
        metadata.create_all(self.engine, checkfirst=True)

    @staticmethod
    def _json_value(value: Any) -> Any:
        for _ in range(2):
            if not isinstance(value, str):
                break
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                break
        return value

    def list_conversations(
        self,
        user_id: str,
        agent_id: str,
        title: str | None,
        page: int,
        limit: int,
        device_id: str | None = None,
        include_next: bool = False,
    ) -> list[dict[str, Any]]:
        conditions = [
            self.sessions.c.user_id == user_id,
            self.sessions.c.agent_id == agent_id,
        ]
        if title:
            conditions.append(
                func.lower(func.json_extract(self.sessions.c.session_data, "$.session_name")).like(
                    f"%{title.casefold()}%"
                )
            )
        direct_device_id = func.json_extract(self.sessions.c.session_data, "$.device_id")
        nested_device_id = func.json_extract(
            func.json_extract(self.sessions.c.session_data, "$"), "$.device_id"
        )
        stored_device_id = func.coalesce(direct_device_id, nested_device_id)
        conditions.append(
            stored_device_id == device_id if device_id else stored_device_id.is_(None)
        )
        statement = (
            select(
                self.sessions.c.session_id,
                self.sessions.c.session_data,
                self.sessions.c.created_at,
                self.sessions.c.updated_at,
            )
            .where(*conditions)
            # A clock rollback must not place an updated session before its
            # own creation. Keep deterministic descending order across pages.
            .order_by(
                func.max(
                    func.coalesce(self.sessions.c.updated_at, 0),
                    func.coalesce(self.sessions.c.created_at, 0),
                ).desc(),
                self.sessions.c.session_id.desc(),
            )
            .offset((page - 1) * limit)
            .limit(limit + int(include_next))
        )
        with self.engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(statement).mappings()]
        for row in rows:
            row["session_data"] = self._json_value(row.get("session_data"))
        return rows

    def list_capabilities(self, kind: str | None = None) -> list[dict[str, Any]]:
        statement = select(self.capabilities)
        if kind:
            statement = statement.where(self.capabilities.c.kind == kind)
        statement = statement.order_by(self.capabilities.c.kind, self.capabilities.c.name)
        with self.engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(statement).mappings()]
        for row in rows:
            row["config"] = self._json_value(row.get("config")) or {}
            row["enabled"] = bool(row.get("enabled"))
        return rows

    def get_capability(self, capability_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(self.capabilities).where(self.capabilities.c.id == capability_id)
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        result = dict(row)
        result["config"] = self._json_value(result.get("config")) or {}
        result["enabled"] = bool(result.get("enabled"))
        return result

    def upsert_capability(
        self,
        capability_id: str,
        kind: str,
        name: str,
        enabled: bool,
        config: dict[str, Any],
        source: str | None,
        now: int,
    ) -> dict[str, Any]:
        existing = self.get_capability(capability_id)
        with self.engine.begin() as connection:
            if existing is None:
                connection.execute(
                    self.capabilities.insert().values(
                        id=capability_id,
                        kind=kind,
                        name=name,
                        enabled=enabled,
                        config=config,
                        source=source,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                connection.execute(
                    update(self.capabilities)
                    .where(self.capabilities.c.id == capability_id)
                    .values(
                        kind=kind,
                        name=name,
                        enabled=enabled,
                        config=config,
                        source=source,
                        updated_at=now,
                    )
                )
        return self.get_capability(capability_id) or {}

    def delete_capability(self, capability_id: str) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                delete(self.capabilities).where(self.capabilities.c.id == capability_id)
            )
        return bool(result.rowcount)

    def list_conversation_files(self, session_id: str) -> list[dict[str, Any]]:
        statement = (
            select(self.conversation_files)
            .where(self.conversation_files.c.session_id == session_id)
            .order_by(self.conversation_files.c.created_at, self.conversation_files.c.name)
        )
        with self.engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(statement).mappings()]
        for row in rows:
            row["outline"] = self._json_value(row.get("outline")) or []
        return rows

    def get_conversation_file(self, session_id: str, file_id: str) -> dict[str, Any] | None:
        statement = select(self.conversation_files).where(
            self.conversation_files.c.session_id == session_id,
            self.conversation_files.c.id == file_id,
        )
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        if row is None:
            return None
        result = dict(row)
        result["outline"] = self._json_value(result.get("outline")) or []
        return result

    def find_conversation_file_by_hash(
        self, session_id: str, sha256: str
    ) -> dict[str, Any] | None:
        statement = (
            select(self.conversation_files)
            .where(
                self.conversation_files.c.session_id == session_id,
                self.conversation_files.c.sha256 == sha256,
            )
            .limit(1)
        )
        with self.engine.connect() as connection:
            row = connection.execute(statement).mappings().first()
        return dict(row) if row is not None else None

    def insert_conversation_file(self, value: dict[str, Any]) -> dict[str, Any]:
        with self.engine.begin() as connection:
            connection.execute(self.conversation_files.insert().values(**value))
        return self.get_conversation_file(str(value["session_id"]), str(value["id"])) or {}

    def delete_conversation_file(self, session_id: str, file_id: str) -> dict[str, Any] | None:
        existing = self.get_conversation_file(session_id, file_id)
        if existing is None:
            return None
        with self.engine.begin() as connection:
            connection.execute(
                delete(self.conversation_files).where(
                    self.conversation_files.c.session_id == session_id,
                    self.conversation_files.c.id == file_id,
                )
            )
        return existing

    def delete_conversation_files(self, session_id: str) -> list[dict[str, Any]]:
        existing = self.list_conversation_files(session_id)
        with self.engine.begin() as connection:
            connection.execute(
                delete(self.conversation_files).where(
                    self.conversation_files.c.session_id == session_id
                )
            )
        return existing

    @staticmethod
    def _content(row: RowMapping) -> Content:
        data = dict(row)
        data["file_type"] = data.pop("type", None)
        return Content.from_dict(data)

    def list_knowledge(self, page: int, limit: int) -> tuple[list[Content], int]:
        with self.engine.connect() as connection:
            total = int(connection.scalar(select(func.count()).select_from(self.knowledge)) or 0)
            rows = connection.execute(
                select(self.knowledge)
                .order_by(self.knowledge.c.created_at.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            ).mappings()
            return [self._content(row) for row in rows], total

    def latest_knowledge_by_name(self, display_name: str) -> Content | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(self.knowledge)
                    .where(
                        func.json_extract(self.knowledge.c.metadata, "$.display_name")
                        == display_name
                    )
                    .order_by(self.knowledge.c.created_at.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return self._content(row) if row is not None else None

    def knowledge_by_sha256(self, sha256: str) -> Content | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(self.knowledge)
                    .where(func.json_extract(self.knowledge.c.metadata, "$.sha256") == sha256)
                    .order_by(self.knowledge.c.created_at.asc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return self._content(row) if row is not None else None

    def close(self) -> None:
        self.engine.dispose()
