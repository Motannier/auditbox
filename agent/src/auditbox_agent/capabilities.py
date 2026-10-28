"""Local capability registry and safe Skill package handling."""

from __future__ import annotations

import hashlib
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

from auditbox_agent.config import Settings
from auditbox_agent.persistence import AgentRepository

DEFAULT_TOOLKITS = {
    "tool:coding": ("coding", "编码与本地文件", "Agno CodingTools"),
    "tool:docker": ("docker", "Docker 管理", "Agno DockerTools"),
    "tool:file-generation": ("file_generation", "文件生成", "Agno FileGenerationTools"),
    "tool:sql": ("sql", "SQL 数据库", "Agno SQLTools"),
}
DEFAULT_SKILLS = {
    "skill:skill-vetter": ("skill-vetter", "Skill Vetter", "ModelScope @spclaudehome/skill-vetter"),
    "skill:skill-creator": (
        "skill-creator",
        "Skill Creator",
        "ModelScope @anthropics/skill-creator",
    ),
}
SAFE_ID = re.compile(r"[^a-zA-Z0-9._-]+")
EXECUTION_CAPABILITY_ID = "control:agent-execution"


def seed_default_capabilities(settings: Settings, repository: AgentRepository) -> None:
    now = int(time.time())
    if repository.get_capability(EXECUTION_CAPABILITY_ID) is None:
        repository.upsert_capability(
            EXECUTION_CAPABILITY_ID, "control", "Agent 执行", settings.execution_enabled,
            {}, "system", now,
        )
    settings.skill_directory.mkdir(parents=True, exist_ok=True)
    settings.workspace_directory.mkdir(parents=True, exist_ok=True)
    settings.generated_file_directory.mkdir(parents=True, exist_ok=True)
    for capability_id, (tool_id, name, source) in DEFAULT_TOOLKITS.items():
        if repository.get_capability(capability_id) is None:
            repository.upsert_capability(
                capability_id, "tool", name, False, {"tool_id": tool_id}, source, now
            )
    for capability_id, (folder, name, source) in DEFAULT_SKILLS.items():
        target = settings.skill_directory / folder
        bundled = settings.default_skill_directory / folder
        if bundled.is_dir() and not target.exists():
            shutil.copytree(bundled, target)
        if repository.get_capability(capability_id) is None:
            repository.upsert_capability(
                capability_id,
                "skill",
                name,
                True,
                {"folder": folder, "bundled": True},
                source,
                now,
            )
    for server in settings.mcp_servers:
        capability_id = f"mcp:{slug(server.name)}"
        if repository.get_capability(capability_id) is None:
            repository.upsert_capability(
                capability_id,
                "mcp",
                server.name,
                server.enabled,
                {
                    "url": server.url,
                    "transport": server.transport,
                    "headers": server.headers,
                    "tools": server.tools,
                },
                "environment",
                now,
            )


def slug(value: str) -> str:
    result = SAFE_ID.sub("-", value.strip()).strip("-._").lower()
    if not result:
        result = f"connection-{hashlib.sha256(value.strip().encode('utf-8')).hexdigest()[:12]}"
    return result[:80]


def install_skill_zip(
    archive: Path, skill_root: Path, display_name: str | None = None
) -> tuple[str, str]:
    """Install one validated Skill ZIP without allowing path traversal or links."""
    with zipfile.ZipFile(archive) as package:
        members = [item for item in package.infolist() if not item.is_dir()]
        if not members or len(members) > 200:
            raise ValueError("Skill 压缩包为空或文件数量超过 200")
        if sum(item.file_size for item in members) > 10 * 1024 * 1024:
            raise ValueError("Skill 解压后不能超过 10MB")
        roots = {Path(item.filename).parts[0] for item in members if Path(item.filename).parts}
        if len(roots) != 1:
            raise ValueError("Skill ZIP 必须只包含一个顶层目录")
        root = next(iter(roots))
        relative_names: list[Path] = []
        for item in members:
            path = Path(item.filename)
            relative = Path(*path.parts[1:])
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Skill ZIP 包含非法路径")
            mode = item.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise ValueError("Skill ZIP 不允许包含符号链接")
            relative_names.append(relative)
        if Path("SKILL.md") not in relative_names:
            raise ValueError("Skill ZIP 顶层缺少 SKILL.md")
        folder = slug(display_name or Path(root).name)
        destination = skill_root / folder
        if destination.exists():
            raise ValueError("同名 Skill 已存在")
        destination.mkdir(parents=True)
        for item, relative in zip(members, relative_names, strict=True):
            output = destination / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            with package.open(item) as source, output.open("wb") as target:
                shutil.copyfileobj(source, target)
    return folder, display_name or folder


def install_skill_directory(
    source: Path, skill_root: Path, display_name: str | None = None
) -> tuple[str, str]:
    """Install one validated workspace Skill directory into the managed registry."""
    source = source.resolve()
    if not source.is_dir() or not (source / "SKILL.md").is_file():
        raise ValueError("Skill 目录不存在或顶层缺少 SKILL.md")
    files = [path for path in source.rglob("*") if path.is_file()]
    if not files or len(files) > 200:
        raise ValueError("Skill 目录为空或文件数量超过 200")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("Skill 目录不允许包含符号链接")
    if sum(path.stat().st_size for path in files) > 10 * 1024 * 1024:
        raise ValueError("Skill 目录不能超过 10MB")
    folder = slug(display_name or source.name)
    destination = skill_root / folder
    if destination.exists():
        raise ValueError("同名 Skill 已存在")
    shutil.copytree(source, destination)
    return folder, display_name or source.name


def public_capability(row: dict[str, Any]) -> dict[str, Any]:
    config = dict(row.get("config") or {})
    if config.get("headers"):
        config["headers"] = {key: "***" for key in config["headers"]}
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "config": config,
        "source": row.get("source"),
    }
