"""Bounded data-only runtime for manager-compiled device command packages.

No model, script, dynamic imports or package-provided regular expressions run here.
Built-in profiles keep their existing engines. New profiles are isolated namespaces.
"""

from __future__ import annotations

import hashlib
import gzip
import io
import json
import os
import re
import tarfile
import tempfile
from functools import lru_cache
from pathlib import Path

from auditbox.db.config import DB_PATH

SCHEMA = "auditbox-command-data/1"
GRAPH_SCHEMA = "auditbox-command-data/2"
MAX_GRAPH_BYTES = 64 * 1024 * 1024
ROOT = DB_PATH.parent / "command-packages"
MAX_BYTES = 12 * 1024 * 1024
BUILTINS = {"linux", "h3c_comware", "huawei_vrp", "cisco_ios", "zte_zxr10"}
ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")
RISKS = {"read_only", "ordinary_change", "dangerous_change", "pending"}


def metadata(profile):
    if not ID.fullmatch(str(profile)) or profile in BUILTINS:
        return None
    try:
        data = json.loads((ROOT / profile / "current").read_text())
        if data["profile"]["profile_id"] != profile or not re.fullmatch(
            r"[a-f0-9]{64}", data["sha256"]
        ):
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def registered(profile):
    return bool(
        ID.fullmatch(str(profile))
        and profile not in BUILTINS
        and (ROOT / profile / "current").exists()
    )


def validate(data):
    if isinstance(data, dict) and data.get("schema") == GRAPH_SCHEMA:
        from auditbox.command_graph_packages import validate as validate_graph
        return validate_graph(data)
    if (
        not isinstance(data, dict)
        or set(data) != {"schema", "profile", "commands", "coverage"}
        or data["schema"] != SCHEMA
    ):
        raise ValueError("不兼容的命令数据包")
    p = data["profile"]
    if not isinstance(p, dict) or set(p) != {
        "profile_id",
        "label",
        "device_type",
        "category_label",
        "vendor",
        "os_family",
        "version",
    }:
        raise ValueError("设备描述无效")
    if any(
        not isinstance(v, str) or not 1 <= len(v) <= 120 or re.search(r"[\x00-\x1f]", v)
        for v in p.values()
    ):
        raise ValueError("设备名称无效")
    if (
        not ID.fullmatch(p["profile_id"])
        or p["profile_id"] in BUILTINS
        or not ID.fullmatch(p["device_type"])
    ):
        raise ValueError("不可覆盖内置设备类型")
    if not re.fullmatch(r"\d+\.\d+\.\d+", p["version"]):
        raise ValueError("版本无效")
    commands = data["commands"]
    coverage = data["coverage"]
    if not isinstance(coverage, dict) or set(coverage) != {
        "sources",
        "read_pages",
        "total_pages",
        "runtime_validation",
    }:
        raise ValueError("研读覆盖说明无效")
    sources = coverage["sources"]
    if not isinstance(sources, list) or len(sources) != 1:
        raise ValueError("当前支持单本手册编译")
    source = sources[0]
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("id"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", str(source.get("sha256", "")))
        or type(source.get("pages")) is not int
        or not 1 <= source["pages"] <= 10000
    ):
        raise ValueError("手册来源无效")
    pages = coverage["read_pages"]
    if (
        coverage["total_pages"] != source["pages"]
        or coverage["runtime_validation"] != "not_run"
        or not isinstance(pages, list)
        or not pages
        or len(pages) > source["pages"]
        or any(type(p) is not int or not 1 <= p <= source["pages"] for p in pages)
        or len(set(pages)) != len(pages)
    ):
        raise ValueError("研读页码无效")
    if not isinstance(commands, list) or not 1 <= len(commands) <= 50000:
        raise ValueError("命令数量无效")
    seen = set()
    for c in commands:
        if not isinstance(c, dict) or set(c) != {
            "id",
            "family",
            "syntax",
            "summary",
            "risk",
            "reason",
            "template",
            "behavior",
            "views",
            "models",
            "evidence",
        }:
            raise ValueError("命令字段无效")
        for key in ("id", "family", "syntax", "summary", "risk", "reason", "template", "behavior"):
            if not isinstance(c[key], str) or len(c[key]) > 4096 or "\x00" in c[key]:
                raise ValueError("命令文本无效")
        if (
            not c["id"]
            or c["id"] in seen
            or not c["family"]
            or not c["syntax"]
            or c["risk"] not in RISKS
        ):
            raise ValueError("命令标识或风险无效")
        seen.add(c["id"])
        if c["risk"] != "read_only" and not c["reason"].strip():
            raise ValueError("风险命令缺少原因")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _./:-]{0,180}", c["family"]):
            raise ValueError("命令族必须为文字关键字")
        for key in ("views", "models"):
            if (
                not isinstance(c[key], list)
                or len(c[key]) > 100
                or any(not isinstance(x, str) or len(x) > 500 for x in c[key])
            ):
                raise ValueError("适用条件无效")
        if not isinstance(c["evidence"], list) or not c["evidence"]:
            raise ValueError("缺少文档证据")
        if len(c["evidence"]) > 10000:
            raise ValueError("证据过多")
        for item in c["evidence"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"source_id", "pages", "note"}
                or item["source_id"] != source["id"]
                or not isinstance(item["note"], str)
                or len(item["note"]) > 8192
                or not isinstance(item["pages"], list)
                or not item["pages"]
                or len(item["pages"]) > 10000
                or any(type(p) is not int or not 1 <= p <= source["pages"] for p in item["pages"])
                or not set(item["pages"]).intersection(pages)
            ):
                raise ValueError("证据引用无效")
        # A template can only fill the input box, never submit a second line.
        if c["template"] and re.search(r"[\x00-\x1f\x7f;&|`$\\]", c["template"]):
            raise ValueError("模板包含控制字符或组合命令")
    if not isinstance(data["coverage"], dict):
        raise ValueError("缺少研读覆盖说明")
    return data


@lru_cache(maxsize=16)
def _load(profile, sha):
    with (ROOT / profile / (sha + ".json")).open("rb") as source:
        raw = source.read(MAX_GRAPH_BYTES + 1)
    if len(raw) > MAX_GRAPH_BYTES or hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError("能力包校验失败")
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get("schema") != GRAPH_SCHEMA) and len(raw) > MAX_BYTES:
        raise ValueError("能力包校验失败")
    return validate(data)


def load(profile):
    meta = metadata(profile)
    if meta is None:
        raise ValueError("能力包未安装")
    data = _load(profile, meta["sha256"])
    if data["schema"] == GRAPH_SCHEMA:
        from auditbox.command_graph_packages import verify_integrity
        verify_integrity(profile, meta["sha256"])
    return data


def profiles():
    result = []
    if not ROOT.exists():
        return result
    for folder in sorted(ROOT.iterdir()):
        meta = metadata(folder.name)
        if not meta:
            continue
        installed, reason, count, ready = True, "", 0, True
        try:
            if (meta.get("schema") == GRAPH_SCHEMA and type(meta.get("commands_count")) is int
                    and 0 <= meta["commands_count"] <= 50000):
                # The public menu must not parse every large graph during the
                # frontend's three-second bootstrap request.
                from auditbox.command_graph_packages import verify_integrity
                verify_integrity(folder.name, meta["sha256"])
                count = meta["commands_count"]
                availability = meta.get("availability", {})
            else:
                data = load(folder.name)
                count = len(data["commands"])
                availability = data.get("availability", {})
            if availability.get("cli") is False:
                ready = False
                reason = availability["reason"]
        except (OSError, ValueError, TypeError, KeyError):
            installed, ready, reason = False, False, "命令包缺失或损坏，执行前需要确认"
        result.append(
            {
                **meta["profile"],
                "installed": installed,
                "engine_ready": ready,
                "reason": reason,
                "counts": {"commands": count},
                "version": meta["sha256"],
            }
        )
    return result


def unpack(payload):
    if len(payload) > MAX_BYTES:
        raise ValueError("包过大")
    # Bound the whole tar stream, including headers/PAX records and padding,
    # before tarfile can allocate memory for attacker-controlled metadata.
    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
        unpacked = stream.read(MAX_GRAPH_BYTES + 65536 + 1)
    if len(unpacked) > MAX_GRAPH_BYTES + 65536:
        raise ValueError("包展开后过大")
    with tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:") as archive:
        first = archive.next()
        if (
            first is None
            or first.name != "runtime.json"
            or not first.isfile()
            or not 0 < first.size <= MAX_GRAPH_BYTES
        ):
            raise ValueError("命令包只能包含 runtime.json 普通数据文件")
        raw = archive.extractfile(first).read(MAX_GRAPH_BYTES + 1)
        if archive.next() is not None:
            raise ValueError("命令包只能包含一个数据文件")
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get("schema") != GRAPH_SCHEMA) and len(raw) > MAX_BYTES:
        raise ValueError("包过大")
    return validate(data), raw


def install(payload):
    data, raw = unpack(payload)
    profile = data["profile"]["profile_id"]
    sha = hashlib.sha256(raw).hexdigest()
    before = metadata(profile)
    if before:
        if before["sha256"] != sha:
            raise ValueError("此类型已有其他版本；请使用新类型标识，避免修改活动会话")
        load(profile)
        return {"profile_id": profile, "version": sha, "installed": True, "already_installed": True}
    folder = ROOT / profile
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder, prefix="staging-") as temp:
        staged = Path(temp) / "data.json"
        staged.write_bytes(raw)
        os.replace(staged, folder / (sha + ".json"))
        pointer = Path(temp) / "current"
        info = {"profile": data["profile"], "sha256": sha}
        if data["schema"] == GRAPH_SCHEMA:
            info.update(schema=GRAPH_SCHEMA, commands_count=len(data["commands"]))
            if "availability" in data:
                info["availability"] = data["availability"]
        pointer.write_text(json.dumps(info))
        os.replace(pointer, folder / "current")
    return {"profile_id": profile, "version": sha, "installed": True, "already_installed": False}


def matched(command, profile):
    from auditbox.security.cli_syntax import matches

    if load(profile)["schema"] == GRAPH_SCHEMA:
        from auditbox.command_graph_packages import matched as matched_graph
        return matched_graph(command, profile)

    value = " ".join(command.strip().casefold().split())
    all_commands = load(profile)["commands"]
    variants = [value]
    if value.startswith(("no ", "undo ", "default ")):
        variants.append(value.split(" ", 1)[1])
    candidates = [
        c
        for c in all_commands
        if any(
            v == c["family"].casefold() or v.startswith(c["family"].casefold() + " ")
            for v in variants
        )
    ]
    exact = [
        c for c in candidates if value == c["syntax"].casefold() or matches(c["syntax"], value)
    ]
    return exact or candidates


def grade(command, profile):
    try:
        graph = load(profile)["schema"] == GRAPH_SCHEMA
    except (OSError, ValueError, KeyError, TypeError):
        graph = False  # The existing unavailable/confirmation handling follows.
    if graph:
        from auditbox.command_graph_packages import grade as grade_graph
        return grade_graph(command, profile)
    if len(command) > 8192 or re.search(r"[\x00-\x1f\x7f;&|<>`$\\]", command):
        return {
            "risk": "unknown",
            "code": "pending",
            "action": "confirm",
            "reason": "包含组合命令、管道或未核验语法，需要二次确认",
        }
    try:
        candidates = matched(command, profile)
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "risk": "unknown",
            "code": "unavailable",
            "action": "confirm",
            "reason": "命令规则包缺失或损坏，需要二次确认",
        }
    if not candidates:
        return {
            "risk": "unknown",
            "code": "unknown",
            "action": "allow",
            "reason": "未收录命令按策略放行，不代表已核验安全",
        }
    from auditbox.security.cli_syntax import matches

    # Prefer risky facts even when a broad safe form matches the same command.
    order = {"read_only": 0, "ordinary_change": 1, "dangerous_change": 2, "pending": 3}
    c = max(candidates, key=lambda c: order[c["risk"]])
    exact = command.strip().casefold() == c["syntax"].casefold() or matches(c["syntax"], command)
    if c["risk"] == "read_only" and not exact:
        return {
            "risk": "unknown",
            "code": "unknown",
            "action": "allow",
            "reason": "参数未匹配已核验语法，不代表安全",
        }
    return {
        "risk": {
            "read_only": "low",
            "ordinary_change": "medium",
            "dangerous_change": "high",
            "pending": "unknown",
        }[c["risk"]],
        "code": c["risk"],
        "action": "allow" if c["risk"] == "read_only" else "confirm",
        "reason": c["reason"] or "匹配文档只读命令",
        "rule_id": c["id"],
        "evidence": c["evidence"],
        "match_type": "syntax" if exact else "family",
    }


def _candidate(c):
    return {
        "command": c["template"] or c["family"],
        "source": "catalog",
        "usage_count": 0,
        "last_used_at": None,
        "risk": "normal" if c["risk"] == "read_only" else "confirm",
        "risk_reason": c["reason"],
        "behavior": c["behavior"],
        "behavior_label": c["summary"],
        "view": " / ".join(c["views"]),
        "models": " / ".join(c["models"]),
        "variant_count": 1,
    }


def complete(prefix, profile, limit=8):
    try:
        rows = load(profile)["commands"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if load(profile)["schema"] == GRAPH_SCHEMA:
        from auditbox.command_graph_packages import complete as complete_graph
        return complete_graph(prefix, profile, limit)
    value = prefix.strip().casefold()
    result, seen = [], set()
    for c in rows:
        item = _candidate(c)
        if item["command"].casefold().startswith(value) and item["command"] not in seen:
            result.append(item)
            seen.add(item["command"])
            if len(result) >= limit:
                break
    return result


def recommend(command, profile, limit=6, history=(), *, goal="", outcome="", connected=True):
    try:
        graph = load(profile)["schema"] == GRAPH_SCHEMA
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if graph:
        from auditbox.command_graph_packages import recommend as recommend_graph
        return recommend_graph(command, profile, limit, history,
                               goal=goal, outcome=outcome, connected=connected)
    try:
        rows, anchors = load(profile)["commands"], matched(command, profile)
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if not anchors:
        return []
    behaviors = {c["behavior"] for c in anchors}
    models = {m for c in anchors for m in c["models"]}
    used = {str(c).strip().casefold() for c in [command, *history[-5:]]}
    candidates = [
        c
        for c in rows
        if c["behavior"] in behaviors
        and (not models or not c["models"] or models.intersection(c["models"]))
    ]
    # The graph retains modifying commands; ordering queries first is a consumer policy.
    candidates.sort(key=lambda c: c["risk"] != "read_only")
    result = []
    for c in candidates:
        item = _candidate(c)
        if item["command"].casefold() in used:
            continue
        used.add(item["command"].casefold())
        result.append({**item, "source": "factory", "score": round(1 - len(result) * 0.05, 2)})
        if len(result) >= limit:
            break
    return result
