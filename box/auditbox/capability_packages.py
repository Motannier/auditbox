"""Data-only packages for the existing native device adapters.

Authoring drafts and new CLI adapters require a separate compiler/adapter; they
are deliberately not advertised as installable runtime capabilities.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import tarfile
import tempfile
import zlib
from functools import lru_cache
from pathlib import Path

from auditbox.db.config import DB_PATH
from auditbox.device_profiles import DEVICE_PROFILES, REFERENCE_PROFILES

ROOT = Path(__file__).parent
PACKAGE_DIR = DB_PATH.parent / "device-packages"
SCHEMA = "auditbox-native-data/1"
MAX_PACKAGE = 12 * 1024 * 1024
MAX_JSON = 64 * 1024 * 1024
FILES = {
    key: ({} if key == "linux" else {"rules.json.gz": "rules"})
    | ({"reference.json.gz": "reference"} if key in REFERENCE_PROFILES else {})
    for key in DEVICE_PROFILES
}


def revision(profile):
    if profile not in FILES:
        raise ValueError("端侧尚未适配此设备类型")
    pointer = PACKAGE_DIR / profile / "current"
    try:
        value = pointer.read_text().strip()
    except FileNotFoundError:
        return "builtin"
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("能力包版本指针无效")
    return value


def data_path(profile, kind, default):
    version = revision(profile)
    if version == "builtin":
        return default
    name = "rules.json.gz" if kind == "rules" else "reference.json.gz"
    return PACKAGE_DIR / profile / version / name


def rules_metadata(profile, default):
    version = revision(profile)
    if version == "builtin":
        manifest = json.loads(default.read_text())
        if manifest.get("schema") != 1:
            raise ValueError("规则清单版本无效")
        return manifest["profiles"][profile]
    manifest = json.loads(
        (PACKAGE_DIR / profile / version / "manifest.json").read_text()
    )
    return manifest["files"]["rules.json.gz"]


def _decode(data, profile, rules):
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as source:
        decoded = source.read(MAX_JSON + 1)
    if len(decoded) > MAX_JSON:
        raise ValueError("能力数据超过大小限制")
    content = json.loads(decoded)
    if content.get("schema") != 1 or content.get("profile") != profile:
        raise ValueError("能力数据类型或版本不匹配")
    strings, entries = content.get("strings"), content.get("entries")
    if (
        not isinstance(strings, list)
        or not all(isinstance(s, str) for s in strings)
        or not isinstance(entries, list)
        or not entries
        or len(entries) > 100000
    ):
        raise ValueError("能力命令目录无效")
    if not rules and (
        not all(isinstance(content.get(k), str) for k in ("brand", "policy"))
        or any(
            type(content.get(k)) is not int
            for k in ("command_names", "variants", "risk", "safe")
        )
        or content["variants"] != len(entries)
    ):
        raise ValueError("能力目录统计信息无效")
    seen = set()
    for entry in entries:
        if (
            not isinstance(entry, list)
            or len(entry) != 8
            or not isinstance(entry[0], str)
            or entry[0] in seen
            or entry[3] not in ((0, 1, 2) if rules else (0, 1))
        ):
            raise ValueError("能力命令记录无效")
        seen.add(entry[0])
        for index in (1, 2, 4, 5, 6, 7):
            if type(entry[index]) is not int or not 0 <= entry[index] < len(strings):
                raise ValueError("能力命令引用无效")
    return len(entries)


@lru_cache(maxsize=32)
def _check_file(path, mtime, size, expected, profile, rules):
    content = Path(path).read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("能力数据校验失败")
    return _decode(content, profile, rules)


def inventory():
    result = []
    for key, profile in DEVICE_PROFILES.items():
        error = ""
        version = "builtin"
        engine_files = ["recommendation/dispatcher.py", "security/alert.py"]
        engine_files += (
            ["recommendation/linux_factory.py", "security/history_review.py"]
            if key == "linux"
            else [
                "recommendation/h3c_factory.py",
                "recommendation/h3c_catalog.py",
                "recommendation/h3c_views.py",
                "security/switch_rules.py",
                "security/history_review.py",
            ]
            if key == "h3c_comware"
            else [
                "recommendation/switch_factory.py",
                "recommendation/switch_catalog.py",
                "security/switch_rules.py",
                "security/switch_history_review.py",
            ]
        )
        engine_ready = all((ROOT / name).is_file() for name in engine_files)
        counts = {}
        try:
            version = revision(key)
            if not engine_ready:
                raise ValueError("端侧适配器缺失，需升级应用")
            for name, kind in FILES[key].items():
                folder = "security" if kind == "rules" else "reference"
                path = data_path(key, kind, ROOT / folder / "data" / f"{key}.json.gz")
                if version == "builtin":
                    meta = json.loads(
                        (ROOT / folder / "data/manifest.json").read_text()
                    )["profiles"][key]
                else:
                    meta = json.loads((path.parent / "manifest.json").read_text())[
                        "files"
                    ][name]
                stat = path.stat()
                counts[kind] = _check_file(
                    str(path),
                    stat.st_mtime_ns,
                    stat.st_size,
                    meta["sha256"],
                    key,
                    kind == "rules",
                )
        except (OSError, ValueError, KeyError, TypeError, EOFError, zlib.error) as exc:
            error = "能力数据缺失或无效" if engine_ready else str(exc)
        result.append(
            {
                "profile_id": key,
                "label": profile.label,
                "device_type": profile.device_type,
                "installed": not error,
                "engine_ready": engine_ready,
                "version": version,
                "reason": error,
                "counts": counts,
            }
        )
    from auditbox import command_packages
    return {"schema": SCHEMA, "profiles": result + command_packages.profiles(),
            "engines": [command_packages.SCHEMA, command_packages.GRAPH_SCHEMA]}


def validate_package(payload):
    if len(payload) > MAX_PACKAGE:
        raise ValueError("能力包超过大小限制")
    files = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for entry in archive:
            if (
                entry.name
                not in {"manifest.json", "rules.json.gz", "reference.json.gz"}
                or not entry.isfile()
                or entry.name in files
            ):
                raise ValueError("能力包包含不允许的文件或重复路径")
            total += entry.size
            if entry.size < 0 or total > MAX_PACKAGE:
                raise ValueError("能力包展开后超过大小限制")
            files[entry.name] = archive.extractfile(entry).read()
    manifest = json.loads(files.get("manifest.json", b"{}"))
    profile = manifest.get("profile_id")
    if (
        manifest.get("schema") != SCHEMA
        or profile not in FILES
        or manifest.get("engine") != "auditbox-native/v1"
        or not isinstance(manifest.get("version"), str)
        or not re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    ):
        raise ValueError("仅支持已适配的正式数据包，编制草稿不可直接注入")
    if set(files) != {"manifest.json", *FILES[profile]} or set(
        manifest.get("files", {})
    ) != set(FILES[profile]):
        raise ValueError("能力包文件不完整")
    for name in FILES[profile]:
        metadata = manifest["files"][name]
        if hashlib.sha256(files[name]).hexdigest() != metadata["sha256"]:
            raise ValueError("能力包文件哈希不一致")
        count = _decode(files[name], profile, name == "rules.json.gz")
        if count != metadata["variants"]:
            raise ValueError("能力包命令数不一致")
    return manifest, files


def install(payload):
    if len(payload) > MAX_PACKAGE:
        raise ValueError('能力包超过大小限制')
    # Inspect only the fixed first tar header; do not let PAX metadata allocate
    # unbounded memory before the command installer applies its stream budget.
    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as archive:
        header = archive.read(512)
    generic = header[:100].split(b'\0', 1)[0] == b'runtime.json'
    if generic:
        from auditbox.command_packages import install as install_commands
        return install_commands(payload)
    manifest, files = validate_package(payload)
    profile = manifest["profile_id"]
    current = next(
        row for row in inventory()["profiles"] if row["profile_id"] == profile
    )
    if current["installed"]:
        return {**current, "already_installed": True}
    if not current["engine_ready"]:
        raise ValueError("盒子缺少对应运行时适配器，请先升级应用")
    version = hashlib.sha256(payload).hexdigest()
    directory = PACKAGE_DIR / profile
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="staging-", dir=directory) as temp:
        staged = Path(temp) / "package"
        staged.mkdir()
        for name, content in files.items():
            (staged / name).write_bytes(content)
        final = directory / version
        if final.exists():
            if any(
                (final / name).read_bytes() != content
                for name, content in files.items()
            ):
                raise ValueError("已有能力包内容校验失败")
        else:
            os.rename(staged, final)
        pointer = Path(temp) / "current"
        pointer.write_text(version)
        os.replace(pointer, directory / "current")
    return {
        "profile_id": profile,
        "installed": True,
        "version": version,
        "already_installed": False,
    }
