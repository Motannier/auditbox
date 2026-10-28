"""Rebuild existing knowledge vectors with the configured chunking policy."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from auditbox_agent.config import Settings
from auditbox_agent.knowledge_ingestion import build_file_reader
from auditbox_agent.runtime import AgentRuntime


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def reindex_all(runtime: AgentRuntime) -> dict[str, Any]:
    """Reindex files and collapse byte-identical records without deleting sources."""
    if runtime.knowledge is None:
        raise RuntimeError("知识库未启用")
    contents, _ = runtime.repository.list_knowledge(page=1, limit=10_000)
    groups: dict[str, list[tuple[Any, Path]]] = defaultdict(list)
    skipped: list[dict[str, str]] = []
    for content in contents:
        metadata = content.metadata or {}
        path = Path(str(metadata.get("stored_path") or ""))
        if metadata.get("source") != "file" or not path.is_file():
            skipped.append({"id": str(content.id), "reason": "source_file_missing"})
            continue
        groups[_sha256(path)].append((content, path))
    rebuilt: list[dict[str, Any]] = []
    duplicates_removed: list[dict[str, str]] = []
    for sha256, members in groups.items():
        members.sort(key=lambda item: item[0].created_at or 0)
        canonical, source_path = members[0]
        expected_policy = {
            "strategy": "structure_aware_character_window",
            "size": runtime.settings.knowledge_chunk_size,
            "overlap": runtime.settings.knowledge_chunk_overlap,
        }
        if (
            len(members) == 1
            and (canonical.metadata or {}).get("sha256") == sha256
            and (canonical.metadata or {}).get("chunk_policy") == expected_policy
        ):
            skipped.append({"id": str(canonical.id), "reason": "already_current"})
            continue
        metadata = {
            **(canonical.metadata or {}),
            "sha256": sha256,
            "chunk_policy": expected_policy,
        }
        display_name = str(metadata.get("display_name") or canonical.name or source_path.name)
        runtime.knowledge.insert(
            name=display_name,
            description=canonical.description,
            path=str(source_path),
            metadata=metadata,
            reader=build_file_reader(source_path.suffix.casefold(), runtime.settings),
        )
        inserted = runtime.repository.latest_knowledge_by_name(display_name)
        if inserted is None or (inserted.metadata or {}).get("sha256") != sha256:
            raise RuntimeError(f"重建后无法验证知识记录：{display_name}")
        for old, _ in members:
            if old.id and old.id != inserted.id:
                runtime.knowledge.remove_content_by_id(old.id)
                if old is not canonical:
                    duplicates_removed.append({"id": str(old.id), "name": str(old.name or "")})
        rebuilt.append({"id": str(inserted.id), "name": display_name, "sha256": sha256})
    return {"rebuilt": rebuilt, "duplicates_removed": duplicates_removed, "skipped": skipped}


def main() -> None:
    runtime = AgentRuntime(Settings())
    try:
        print(json.dumps(reindex_all(runtime), ensure_ascii=False, indent=2))
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
