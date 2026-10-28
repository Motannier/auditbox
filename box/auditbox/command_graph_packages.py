"""Version 2, data-only directed behavior graph consumer.

This is a recommendation surface, not an execution workflow. It never fills
guessed target parameters, advances a device, or treats a recommendation as
authorization. Existing v1 and built-in adapters remain in their own paths.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

SCHEMA = "auditbox-command-data/2"
MAX_COMMANDS = 50000
MAX_EDGES = 100000
_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")
_RISKS = {"read_only", "ordinary_change", "dangerous_change", "pending"}
_RELATIONS = {"inspect_before", "verify_after", "requires_before", "cleanup_after",
              "workflow_related", "diagnose_next"}
_SLOT = re.compile(r"<[^<>\r\n]{1,80}>|\{[a-zA-Z][a-zA-Z0-9_-]{0,79}\}")
_BUILTINS = {"linux", "h3c_comware", "huawei_vrp", "cisco_ios", "zte_zxr10"}


@lru_cache(maxsize=50000)
def _has_command_anchor(syntax):
    """An orphan parameter like sip-agent-profile-name is not a command.

    This does not rewrite the fact. Grammar whose command prefix cannot be
    established stays available as evidence, not a wildcard for all input.
    """
    from auditbox.security.cli_syntax import _parameter
    value = syntax.strip().casefold()
    value = re.sub(r"^\[\s*(?:no|undo|default)\s*\]\s*", "", value)
    words = value.split()
    while words and words[0] in {"no", "undo", "default"}:
        words.pop(0)
    return bool(words and re.fullmatch(r"[a-z][a-z0-9_./:-]*", words[0])
                and _parameter(words[0]) is None)


def _text(value, maximum=4096, *, nonempty=False):
    return (isinstance(value, str) and len(value) <= maximum and "\0" not in value
            and (not nonempty or bool(value.strip())))


def _unique_ids(values, universe=None, *, allow_empty=False, maximum=MAX_COMMANDS):
    return (isinstance(values, list) and len(values) <= maximum
            and (allow_empty or bool(values))
            and all(_text(value, 512, nonempty=True) for value in values)
            and len(set(values)) == len(values)
            and (universe is None or all(value in universe for value in values)))


def validate(data):
    if (not isinstance(data, dict)
            or not {"schema", "profile", "commands", "coverage", "behaviors"}.issubset(data)
            or set(data) - {"schema", "profile", "commands", "coverage", "behaviors", "availability", "reference"}
            or data["schema"] != SCHEMA):
        raise ValueError("不兼容的行为图数据包")
    profile = data["profile"]
    if (not isinstance(profile, dict) or set(profile) != {
            "profile_id", "label", "device_type", "category_label", "vendor", "os_family", "version"}
            or any(not _text(v, 120, nonempty=True) or re.search(r"[\x00-\x1f]", v)
                   for v in profile.values())
            or not _ID.fullmatch(profile["profile_id"])
            or profile["profile_id"] in _BUILTINS
            or not _ID.fullmatch(profile["device_type"])
            or not re.fullmatch(r"\d+\.\d+\.\d+", profile["version"])):
        raise ValueError("行为图设备描述无效，不能覆盖内置类型")

    coverage = data["coverage"]
    if (not isinstance(coverage, dict) or set(coverage) != {
            "sources", "read_pages", "total_pages", "runtime_validation", "documentation_status"}
            or coverage["runtime_validation"] != "not_run"
            or coverage["documentation_status"] != "inherited_partial_review"
            or not isinstance(coverage["sources"], list)
            or not 1 <= len(coverage["sources"]) <= 200):
        raise ValueError("行为图来源覆盖说明无效")
    sources = {}
    for source in coverage["sources"]:
        if (not isinstance(source, dict) or set(source) != {"id", "sha256", "pages"}
                or not _text(source["id"], 512, nonempty=True) or source["id"] in sources
                or not isinstance(source["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", source["sha256"])
                or type(source["pages"]) is not int or not 1 <= source["pages"] <= 100000):
            raise ValueError("行为图手册来源无效")
        sources[source["id"]] = source
    read = coverage["read_pages"]
    if (not isinstance(read, dict) or set(read) != set(sources)
            or type(coverage["total_pages"]) is not int
            or coverage["total_pages"] != sum(s["pages"] for s in sources.values())):
        raise ValueError("行为图研读页码映射无效")
    for source_id, pages in read.items():
        if (not isinstance(pages, list) or len(pages) > sources[source_id]["pages"]
                or any(type(p) is not int or not 1 <= p <= sources[source_id]["pages"] for p in pages)
                or len(set(pages)) != len(pages)):
            raise ValueError("行为图研读页码无效")

    commands = data["commands"]
    if "availability" in data:
        availability = data["availability"]
        if (not isinstance(availability, dict) or set(availability) != {"cli", "reason"}
                or availability["cli"] is not False
                or not _text(availability["reason"], 2000, nonempty=True)
                or commands != [] or data["behaviors"] != {"nodes": [], "edges": []}):
            raise ValueError("未提供 CLI 规则的品牌不能包含可执行命令或行为边")
        if "reference" in data:
            from auditbox.reference.package_reference import validate_reference
            validate_reference(data["reference"], commands, sources)
        return data
    if not isinstance(commands, list) or not 1 <= len(commands) <= MAX_COMMANDS:
        raise ValueError("行为图命令数量无效")
    by_id, command_sources = {}, {}
    for command in commands:
        if (not isinstance(command, dict) or set(command) != {
                "id", "family", "syntax", "summary", "risk", "reason", "template", "behavior",
                "views", "models", "evidence", "render_state"}
                or any(not _text(command[k]) for k in (
                    "id", "family", "syntax", "summary", "risk", "reason", "template", "behavior", "render_state"))
                or not command["id"] or len(command["id"]) > 512 or command["id"] in by_id
                or not command["syntax"].strip() or command["risk"] not in _RISKS
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _./:-]{0,180}", command["family"])):
            raise ValueError("行为图命令字段无效")
        if command["risk"] != "read_only" and not command["reason"].strip():
            raise ValueError("风险命令缺少原因")
        for key in ("views", "models"):
            if (not isinstance(command[key], list) or len(command[key]) > 100
                    or any(not _text(v, 500) for v in command[key])):
                raise ValueError("行为图命令适用条件无效")
        evidence = command["evidence"]
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 10000:
            raise ValueError("行为图命令缺少证据")
        command_sources[command["id"]] = set()
        for item in evidence:
            if (not isinstance(item, dict) or set(item) != {"source_id", "pages", "note"}
                    or not isinstance(item["source_id"], str) or item["source_id"] not in sources
                    or not _text(item["note"], 8192)
                    or not isinstance(item["pages"], list) or not 1 <= len(item["pages"]) <= 10000
                    or any(type(p) is not int or not 1 <= p <= sources[item["source_id"]]["pages"]
                           for p in item["pages"])):
                raise ValueError("行为图证据引用无效")
            command_sources[command["id"]].add(item["source_id"])
        state, template = command["render_state"], command["template"]
        if state not in {"template", "needs_parameters", "unsupported"}:
            raise ValueError("命令填充状态无效")
        if (state == "unsupported" and template) or (command["risk"] == "pending" and state != "unsupported"):
            raise ValueError("未支持或待核验命令不能提供执行模板")
        if state != "unsupported" and (not template.strip() or re.search(r"[\x00-\x1f\x7f;&|`$\\\[\]]", template)
                or (state == "template" and re.search(r"[<>{}]", template))
                or (state == "needs_parameters" and not _SLOT.search(template))
                or (state == "needs_parameters" and re.search(r"[<>{}]", _SLOT.sub("", template)))):
            raise ValueError("命令模板包含未解析语法或控制字符")
        by_id[command["id"]] = command

    behaviors = data["behaviors"]
    if (not isinstance(behaviors, dict) or set(behaviors) != {"nodes", "edges"}
            or not isinstance(behaviors["nodes"], list)
            or not 1 <= len(behaviors["nodes"]) <= MAX_COMMANDS
            or not isinstance(behaviors["edges"], list) or len(behaviors["edges"]) > MAX_EDGES):
        raise ValueError("行为图节点或边无效")
    nodes, memberships = {}, defaultdict(set)
    for node in behaviors["nodes"]:
        if (not isinstance(node, dict) or set(node) != {"id", "label", "command_ids"}
                or not _text(node["id"], 512, nonempty=True) or node["id"] in nodes
                or not _text(node["label"], 4096, nonempty=True)
                or not _unique_ids(node["command_ids"], by_id)):
            raise ValueError("行为图节点引用无效")
        nodes[node["id"]] = set(node["command_ids"])
        for command_id in node["command_ids"]:
            memberships[command_id].add(node["id"])
    if set(memberships) != set(by_id) or any(c["behavior"] not in memberships[c["id"]] for c in commands):
        raise ValueError("命令与行为节点必须双向引用")
    edge_ids = set()
    for edge in behaviors["edges"]:
        if (not isinstance(edge, dict) or set(edge) != {
                "id", "from", "to", "from_command_ids", "to_command_ids", "relation", "condition",
                "requires_explicit_goal", "requires_confirmation", "success_must_be_observed", "basis"}
                or not _text(edge["id"], 512, nonempty=True) or edge["id"] in edge_ids
                or not isinstance(edge["from"], str) or edge["from"] not in nodes
                or not isinstance(edge["to"], str) or edge["to"] not in nodes
                or not _unique_ids(edge["from_command_ids"], nodes[edge["from"]])
                or not _unique_ids(edge["to_command_ids"], nodes[edge["to"]])
                or edge["relation"] not in _RELATIONS
                or not _text(edge["condition"], nonempty=True) or not _text(edge["basis"], nonempty=True)
                or any(type(edge[k]) is not bool for k in (
                    "requires_explicit_goal", "requires_confirmation", "success_must_be_observed"))):
            raise ValueError("行为图有向边无效")
        # Collapse identical source sets before checking pairs. A malicious
        # selector list must not cause a 50,000 x 50,000 validation loop.
        from_sources = {frozenset(command_sources[c]) for c in edge["from_command_ids"]}
        to_sources = {frozenset(command_sources[c]) for c in edge["to_command_ids"]}
        if len(from_sources) * len(to_sources) > 4096:
            raise ValueError("行为边来源组合过多")
        if any(not left.intersection(right) for left in from_sources for right in to_sources):
            raise ValueError("行为关系不能跨无共同来源的手册")
        targets = [by_id[c] for c in edge["to_command_ids"]]
        if any(c["risk"] != "read_only" for c in targets) and not (
                edge["requires_explicit_goal"] and edge["requires_confirmation"]):
            raise ValueError("状态变更边必须要求明确目标和二次确认")
        if edge["relation"] == "verify_after" and any(c["risk"] != "read_only" for c in targets):
            raise ValueError("验证边只能指向只读命令")
        edge_ids.add(edge["id"])
    if "reference" in data:
        from auditbox.reference.package_reference import validate_reference
        validate_reference(data["reference"], commands, sources)
    return data


def _norm(command):
    return " ".join(str(command).strip().casefold().split())


@lru_cache(maxsize=32)
def _verify_file(filename, sha, size, mtime, ctime):
    del mtime, ctime  # Stat keys invalidate a previous successful verification.
    if size > 64 * 1024 * 1024:
        raise ValueError("行为图超过大小限制")
    digest = hashlib.sha256()
    total = 0
    with Path(filename).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > 64 * 1024 * 1024:
                raise ValueError("行为图超过大小限制")
            digest.update(chunk)
    if digest.hexdigest() != sha:
        raise ValueError("行为图文件校验失败")


def verify_integrity(profile, sha):
    from auditbox import command_packages as engine
    path = engine.ROOT / profile / (sha + ".json")
    stat = path.stat()
    _verify_file(str(path), sha, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=16)
def _indexed(profile, sha):
    from auditbox import command_packages as engine
    data = engine._load(profile, sha)
    rows = data["commands"]
    by_id = {c["id"]: c for c in rows}
    first_words = defaultdict(list)
    risky_families = defaultdict(list)
    from auditbox.security.cli_syntax import _parameter, _parse

    def starts(node):
        if node is None:
            return set(), False
        kind = node[0]
        if kind == "token":
            return ({node[1]} if _parameter(node[1]) is None else {"*"}), False
        if kind == "choice":
            children = [starts(c) for c in node[1]]
            return set().union(*(x[0] for x in children)), any(x[1] for x in children)
        if kind == "repeat":
            words, nullable = starts(node[1])
            return words, nullable or node[2] == 0
        words, nullable = set(), True
        for child in node[1]:
            child_words, child_nullable = starts(child)
            words.update(child_words)
            if not child_nullable:
                nullable = False
                break
        return words, nullable

    def required(node):
        if node is None:
            return frozenset()
        if node[0] == "token":
            return frozenset({node[1]}) if _parameter(node[1]) is None else frozenset()
        if node[0] == "repeat":
            return required(node[1]) if node[2] else frozenset()
        children = [required(c) for c in node[1]]
        if node[0] == "choice":
            return frozenset.intersection(*children)
        return frozenset.union(*children)

    for row in rows:
        if row["risk"] != "read_only":
            risky_families[row["family"].casefold().split()[0]].append(row)
        if not _has_command_anchor(row["syntax"]):
            continue
        parsed = _parse(row["syntax"])
        words, _ = starts(parsed)
        words.add(row["syntax"].casefold().split()[0])
        literals = required(parsed)
        for word in words:
            first_words[word].append((row, literals))
    outgoing = defaultdict(list)
    for edge in data["behaviors"]["edges"]:
        for command_id in edge["from_command_ids"]:
            outgoing[command_id].append(edge)
    return data, by_id, first_words, outgoing, risky_families


def _catalog(profile):
    from auditbox import command_packages as engine
    meta = engine.metadata(profile)
    if not meta:
        raise ValueError("行为图未安装")
    # Re-enter the integrity-checked data loader before using any derived cache.
    engine.load(profile)
    return _indexed(profile, meta["sha256"])


def matched(command, profile):
    from auditbox.security.cli_syntax import matches
    value = _norm(command)
    if not value or len(value) > 8192:
        return []
    _, _, first_words, _, _ = _catalog(profile)
    candidates = first_words.get(value.split()[0], []) + first_words.get("*", [])
    words = set(value.split())
    return [c for c, literals in candidates if value == _norm(c["syntax"])
            or (literals.issubset(words) and matches(c["syntax"], value))]


def _family_matches(command, family):
    """Ordered keyword fallback proves possible risk, never read-only safety."""
    values = _norm(command).split()
    keys = _norm(family).split()
    if not keys or not values:
        return False
    if values[0] in {"no", "undo", "default"} and keys[0] != values[0]:
        values = values[1:]
    if not values or values[0] != keys[0]:
        return False
    cursor = iter(values)
    return all(any(value == key for value in cursor) for key in keys)


@lru_cache(maxsize=16)
def _compiled_risk_forms(profile, sha):
    from auditbox import command_packages as engine
    from auditbox.reference.guide_cli import compile_cli
    data = engine._load(profile, sha)
    metadata = {row["command_id"]: row for row in data.get("reference", {}).get("cli_syntax", [])}
    forms = defaultdict(list)
    for row in data["commands"]:
        if row["risk"] == "read_only" or row["id"] not in metadata or not _has_command_anchor(row["syntax"]): continue
        compiled = compile_cli(row, metadata[row["id"]])
        if compiled:
            forms[compiled["template"].split()[0]].append((row, compiled))
    return forms


def _compiled_risk_matches(command, profile):
    from auditbox import command_packages as engine
    from auditbox.reference.guide_cli import matches_compiled
    value = _norm(command)
    if not value: return []
    meta = engine.metadata(profile)
    forms = _compiled_risk_forms(profile, meta["sha256"])
    return [row for row, compiled in forms.get(value.split()[0], []) if matches_compiled(value, compiled)]


def grade(command, profile):
    if len(command) > 8192 or re.search(r"[\x00-\x1f\x7f;&|<>`$\\]", command):
        return {"risk": "unknown", "code": "pending", "action": "confirm",
                "reason": "包含组合命令、管道或未核验语法，需要二次确认"}
    try:
        data, _, _, _, risky_families = _catalog(profile)
        if data.get("availability", {}).get("cli") is False:
            return {"risk": "unknown", "code": "unavailable", "action": "confirm",
                    "reason": data["availability"]["reason"]}
        candidates = matched(command, profile)
        # Source parameter declarations also cover bare/composite vendor
        # arguments that the old grammar matcher could not recognize. Add
        # only risk-bearing exact forms: never turn an unsupported query into
        # a new read-only allowance or change the unknown-command policy.
        candidates += _compiled_risk_matches(command, profile)
        exact = bool(candidates)
        if not candidates:
            words = _norm(command).split()
            keys = {words[0]} if words else set()
            if len(words) > 1 and words[0] in {"no", "undo", "default"}:
                keys.add(words[1])
            candidates = [c for key in keys for c in risky_families.get(key, ())
                          if _family_matches(command, c["family"])]
    except (OSError, ValueError, TypeError, KeyError):
        return {"risk": "unknown", "code": "unavailable", "action": "confirm",
                "reason": "命令规则包缺失或损坏，需要二次确认"}
    if not candidates:
        return {"risk": "unknown", "code": "unknown", "action": "allow",
                "reason": "未匹配已核验语法，按未知命令策略放行，不代表已核验安全"}
    # An ambiguous fact must never downgrade an explicitly known destructive
    # effect to "unknown" in side audit. v1 retains its original ranking.
    order = {"read_only": 0, "ordinary_change": 1, "pending": 2, "dangerous_change": 3}
    row = max(candidates, key=lambda c: order[c["risk"]])
    return {"risk": {"read_only": "low", "ordinary_change": "medium", "dangerous_change": "high",
                     "pending": "unknown"}[row["risk"]],
            "code": row["risk"], "action": "allow" if row["risk"] == "read_only" else "confirm",
            "reason": row["reason"] or "匹配文档只读语法", "rule_id": row["id"],
            "evidence": row["evidence"], "match_type": "syntax" if exact else "family"}


def _candidate(row):
    return {"command": row["template"] or row["family"], "source": "catalog", "usage_count": 0,
            "last_used_at": None, "risk": "normal" if row["risk"] == "read_only" else "confirm",
            "risk_reason": row["reason"], "behavior": row["behavior"], "behavior_label": row["summary"],
            "view": " / ".join(row["views"]), "models": " / ".join(row["models"]), "variant_count": 1,
            "command_id": row["id"], "render_state": row["render_state"],
            "requires_parameters": row["render_state"] != "template", "fill_only": True,
            "execution_authorized": False}


def complete(prefix, profile, limit=8):
    if limit <= 0:
        return []
    try:
        data, _, _, _, _ = _catalog(profile)
    except (OSError, ValueError, TypeError, KeyError):
        return []
    value, seen, result = _norm(prefix), set(), []
    for row in data["commands"]:
        if (row["render_state"] == "unsupported" or row["family"].startswith("unresolved-")
                or not _has_command_anchor(row["syntax"])):
            continue
        item = _candidate(row)
        key = _norm(item["command"])
        if key.startswith(value) and key not in seen:
            result.append(item)
            seen.add(key)
            if len(result) >= limit:
                break
    return result


def recommend(command, profile, limit=6, history=(), *, goal="", outcome="", connected=True):
    if not connected or outcome in {"failed", "cancelled", "blocked", "disconnected"} or limit <= 0:
        return []
    try:
        _, by_id, _, outgoing, _ = _catalog(profile)
        anchors = matched(command, profile)
    except (OSError, ValueError, TypeError, KeyError):
        return []
    used = {_norm(c) for c in [command, *list(history)[-5:]]}
    # Do not suggest the same action again merely because its template contains
    # placeholders whereas the current command contains concrete parameters.
    used_ids = {c["id"] for c in anchors}
    result, seen_edges = [], set()
    for anchor in anchors:
        for edge in outgoing[anchor["id"]]:
            if edge["id"] in seen_edges:
                continue
            seen_edges.add(edge["id"])
            explicit_goal = goal in {edge["id"], edge["to"]}
            if edge["requires_explicit_goal"] and not explicit_goal:
                continue
            for target_id in edge["to_command_ids"]:
                row = by_id[target_id]
                if (target_id in used_ids or row["render_state"] == "unsupported"
                        or not _has_command_anchor(row["syntax"])
                        or row["risk"] == "pending"
                        or (row["risk"] != "read_only" and not explicit_goal)):
                    continue
                item = _candidate(row)
                key = _norm(item["command"])
                if key in used:
                    continue
                used.add(key)
                result.append({**item, "source": "factory", "score": round(1 - len(result) * 0.05, 2),
                               "relation": edge["relation"], "condition": edge["condition"],
                               "basis": edge["basis"], "edge_id": edge["id"],
                               "requires_confirmation": edge["requires_confirmation"] or row["risk"] != "read_only",
                               "recommendation_only": True,
                               "awaiting_success": edge["success_must_be_observed"] and outcome != "success"})
                if len(result) >= limit:
                    return result
    return result
