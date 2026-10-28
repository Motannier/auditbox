"""Behavior-oriented projection of installed facts, not a second command graph.

Only rendered facts are offered for fill. The source graph and risk gate stay
unchanged. Package text is data; none of it becomes executable code or regex.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache

from auditbox import command_packages
from auditbox.command_graph_packages import _has_command_anchor, verify_integrity
from auditbox.reference.guide_cli import compile_cli
from auditbox.reference.guide_operations import GROUPS, OPERATION_ORDER, operation_for

SCHEMA = "auditbox-command-guide/1"
_SLOT = re.compile(r"<([^<>\r\n]{1,80})>|\{([a-zA-Z][a-zA-Z0-9_-]{0,79})\}")
_RISK = {"read_only": 0, "ordinary_change": 1, "dangerous_change": 2, "pending": 3}
_PARAMETER_LABELS = {
    "port-id": "端口编号", "onu-id": "ONU 编号", "onu-list": "ONU 编号列表",
    "pon-id": "PON 端口编号", "interface": "接口名称", "interface-name": "接口名称",
    "vlan-id": "VLAN ID", "vlanid": "VLAN ID", "a.b.c.d": "IP 地址", "password": "密码",
    "gid": "保护组编号", "rtt": "最大往返时延", "time": "老化时间",
    "isisname": "IS-IS 进程名", "x:x::x:x": "IPv6 地址",
}

def _operation(row):
    template = row["template"].strip()
    if (re.search(r"[<>{}]", _SLOT.sub("", template))
            or re.search(r"[\x00-\x1f\x7f;&|`$\\\[\]]", template)
            or re.search(r"\b([a-z][a-z_-]+)\s+\1\b", template, re.IGNORECASE)
            or re.search(r"Raisecom Technolo|user manual|Description None|Command Mode", template, re.IGNORECASE)):
        return None
    return operation_for(template, row.get("summary", ""))

def _parameters(template):
    matches = list(_SLOT.finditer(template))
    counts = {}
    result = []
    for index, slot in enumerate(matches):
        name = slot.group(1) or slot.group(2)
        counts[name] = counts.get(name, 0) + 1
        label = _PARAMETER_LABELS.get(name.casefold(), name)
        if sum((s.group(1) or s.group(2)) == name for s in matches) > 1:
            label += f"（第 {counts[name]} 项）"
        result.append({"key": f"parameter{index}", "label": label, "slot": slot.group(),
                       "secret": bool(re.search(r"password|secret", name, re.IGNORECASE))})
    return result


def _variant(profile, behavior, item):
    rows = item["rows"]
    risk_row = max(rows, key=lambda row: _RISK[row["risk"]])
    template = item["template"]
    key = item.get("key") or _form_key(template)
    vid = hashlib.sha256((profile + ":" + behavior + ":" + key).encode()).hexdigest()[:20]
    evidence = {json.dumps(e, ensure_ascii=False, sort_keys=True): e for row in rows for e in row.get("evidence", [])}
    # Prefer a Chinese command-reference description to an example caption;
    # all original row IDs, views, models and evidence remain attached.
    purpose_row = max(rows, key=lambda row: (
        bool(re.search(r"此命令|命令用[于来]|^This command|^Show |^To (?:display|show)", row["summary"], re.IGNORECASE)),
        bool(re.search(r"[\u4e00-\u9fff]", row["summary"])), min(len(row["summary"]), 600)))
    result = {"id": f"form-{profile}-{vid}", "template": template, "purpose": purpose_row["summary"][:600],
            "parameters": item.get("parameters", _parameters(template)), "risk": risk_row["risk"],
            "risk_reason": risk_row.get("reason", "")[:600],
            "views": sorted({v for row in rows for v in row["views"]}),
            "models": sorted({v for row in rows for v in row["models"]}),
            "evidence": list(evidence.values()), "command_ids": sorted({row["id"] for row in rows}),
            "fill_only": True}
    if item.get("documentation_only"):
        result.update(documentation_only=True, syntax=rows[0]["syntax"], parameters=[],
                      syntaxes=list(dict.fromkeys(row["syntax"] for row in rows)))
    if item.get("interface"):
        result["interface"] = item["interface"]
    return result


def _form_key(template):
    # Slot labels/ranges are not different command forms. Original facts stay
    # in the pack, and every view/model/evidence is attached to this variant.
    return " ".join(_SLOT.sub("<parameter>", template.casefold()).split())

@lru_cache(maxsize=16)
def _project(profile, sha):
    data = command_packages._load(profile, sha)
    operations = {}
    reference = data.get("reference", {})
    annotated = {cid: (op["group"], op["key"], op["title"])
                 for op in reference.get("operations", []) for cid in op["command_ids"]}
    interfaces = {m["source_id"]: m["interface"] for m in reference.get("manuals", [])}
    grammar = {item["command_id"]: item for item in reference.get("cli_syntax", [])}
    for row in data["commands"]:
        unsupported = row.get("render_state", "template") == "unsupported"
        if (unsupported and row["id"] not in annotated
                or not _has_command_anchor(row["syntax"])):
            continue
        interface = " / ".join(sorted({interfaces[e["source_id"]] for e in row["evidence"]
                                      if e["source_id"] in interfaces}))
        if interface and "CLI" not in interface:
            continue  # Web/TL1 facts stay in the pack, never become console CLI.
        compiled = compile_cli(row, grammar.get(row["id"]))
        if not compiled:
            continue
        template = compiled["template"]
        # Prefer precise functional recipes to old broad keyword annotations
        # ("ont" must not match the substring in "control", for example).
        functional = template
        for parameter in compiled["parameters"]:
            functional = functional.replace(parameter["slot"], "<" + parameter["label"] + ">", 1)
        operation = operation_for(functional, row.get("summary", "")) or annotated.get(row["id"]) or _operation(row)
        if not operation:
            continue
        if operation[1].endswith("-review"):
            group, behavior, title = operation
            title = re.sub(r"^核对", "查看" if re.match(r"^(show|display|get)\s", template, re.IGNORECASE) else "配置", title)
            operation = group, behavior, title
        constraints = [{k: v for k, v in p.items() if k not in {"key", "slot", "label"}}
                       for p in compiled["parameters"]]
        key = _form_key(template) + json.dumps(constraints, ensure_ascii=False, sort_keys=True)
        # Explicit annotations retain model/view/protocol/risk boundaries;
        # identical strings need not be interchangeable across firmware.
        if reference:
            key += json.dumps([interface], ensure_ascii=False)
        forms = operations.setdefault(operation, {})
        forms.setdefault(key, {"key": key, "template": template, "rows": [],
                               "parameters": compiled["parameters"], "interface": interface})["rows"].append(row)
    categories = []
    for group, label, summary in GROUPS:
        scenarios = []
        for (category, behavior, title), forms in operations.items():
            if category != group:
                continue
            # Keep every complete documented form inside one behavior. The
            # earlier six-form cutoff discarded valid variants arbitrarily.
            ordered = sorted(forms.items(), key=lambda pair: (
                pair[1].get("documentation_only", False),
                len(_parameters(pair[1]["template"])), len(pair[1]["template"].split()), pair[0]))
            variants = [_variant(profile, behavior, item) for _, item in ordered]
            sid = hashlib.sha256((profile + ":" + group + ":" + behavior).encode()).hexdigest()[:20]
            first = variants[0]
            # Additive /1 contract: an older client safely sees the first form.
            scenarios.append({**first, "id": f"guide-{profile}-{sid}", "behavior_key": behavior,
                              "title": title, "summary": f"{len(variants)} 种命令形式" if len(variants) > 1 else first["template"],
                              "variants": variants, "fill_only": True})
        # The quick guide matches the switch feature: executable CLI only.
        # Browser/net-management procedures remain source data for the other
        # consumers, not extra explanation-only functions in this picker.
        if scenarios:
            scenarios.sort(key=lambda s: OPERATION_ORDER.get((group, s["behavior_key"]), len(OPERATION_ORDER)))
            categories.append({"id": f"guide-{profile}-{group}", "key": group,
                               "label": label, "summary": summary, "scenarios": scenarios})
    cli_available = not data.get("availability") or data["availability"]["cli"]
    available = bool(categories)
    message = ""
    if not cli_available:
        message = data["availability"]["reason"]
    elif not categories:
        message = "已有命令事实尚无适合常用速查的完整模板，不能直接下发原始手册语法。"
    elif not any(c["key"] not in {"maintenance", "configuration"} for c in categories):
        message = "现有资料暂无可下发的状态查询模板；目前仅展示已整理的视图与维护操作。"
    return {"schema": SCHEMA, "profile_id": profile, "label": data["profile"]["label"],
            "device_type": data["profile"]["device_type"], "version": data["profile"]["version"],
            "revision": sha, "available": available, "message": message, "categories": categories,
            "model_summary": reference.get("model_summary", ""),
            "manuals": reference.get("manuals", []),
            "fill_only": True}

def command_guide(profile):
    meta = command_packages.metadata(profile)
    if meta is None:
        raise ValueError("该设备未安装通用命令能力包")
    verify_integrity(profile, meta["sha256"])  # Even a warm projection must detect corruption.
    return _project(profile, meta["sha256"])


def command_guide_counts():
    """Small index for collapsed menus; count behaviors, not command variants.

    Derive counts from the very same verified projection used for the details.
    Never turn a missing/corrupt package into a misleading zero-count menu.
    """
    profiles, guides = [], {}
    for installed in command_packages.profiles():
        profile = installed["profile_id"]
        try:
            guide = command_guide(profile)
            guides[profile] = guide
            count = sum(len(category["scenarios"]) for category in guide["categories"])
            profiles.append({"profile_id": profile, "count": count, "revision": guide["revision"]})
        except (OSError, ValueError, KeyError, TypeError):
            profiles.append({"profile_id": profile, "count": None, "error": "该品牌操作数量暂不可用"})
    brands = []
    for vendor, label in (("fiberhome", "烽火"), ("huawei", "华为"), ("raisecom", "瑞斯康达"), ("zte", "中兴")):
        ids = [f"{vendor}_{device}" for device in ("olt", "onu") if any(r["profile_id"] == f"{vendor}_{device}" for r in profiles)]
        if not ids:
            continue
        failed = any(p not in guides for p in ids)
        operations = {(c["key"], s["behavior_key"]) for p in ids if p in guides
                      for c in guides[p]["categories"] for s in c["scenarios"]}
        models = list(dict.fromkeys(guides[p].get("model_summary", "") for p in ids if p in guides))
        brands.append({"id": f"pon-{vendor}", "label": label, "profile_ids": ids,
                       "model_summary": " · ".join(m for m in models if m),
                       "count": None if failed else len(operations)})
    return {"schema": "auditbox-command-guide-counts/1", "profiles": profiles, "brands": brands}
