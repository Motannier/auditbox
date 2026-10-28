"""Validate optional, data-only quick-reference annotations on command packs.

Annotations select facts by ID; they cannot provide an executable template or
override the command's risk, model, view or execution profile. Web instructions
are explicitly browse-only, never terminal commands.
"""
import re

GROUPS = {"system", "access", "interface", "service", "routing", "security",
          "fault", "configuration", "maintenance", "wireless", "voice"}


def validate_reference(reference, commands, sources):
    def text(value, limit=4096):
        return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and "\0" not in value

    def identifier(value):
        return text(value, 120) and re.fullmatch(r"[a-z][a-z0-9_-]*", value)

    def evidence(items):
        return isinstance(items, list) and 1 <= len(items) <= 100 and all(
            isinstance(item, dict) and set(item) == {"source_id", "pages", "note"}
            and item["source_id"] in sources and text(item["note"])
            and isinstance(item["pages"], list) and 1 <= len(item["pages"]) <= 100
            and all(type(p) is int and 1 <= p <= sources[item["source_id"]]["pages"] for p in item["pages"])
            for item in items)

    required = {"schema", "model_summary", "manuals", "operations", "procedures"}
    if (not isinstance(reference, dict) or not required <= set(reference)
            or set(reference) - required - {"cli_syntax"}
            or reference["schema"] != "auditbox-command-reference/1"
            or not text(reference["model_summary"], 500)):
        raise ValueError("操作速查说明无效")
    manuals = reference["manuals"]
    if not isinstance(manuals, list) or len(manuals) > 200:
        raise ValueError("操作速查手册目录无效")
    manual_ids = set()
    for item in manuals:
        if (not isinstance(item, dict) or set(item) != {"source_id", "title", "models", "interface"}
                or item["source_id"] not in sources or item["source_id"] in manual_ids
                or not text(item["title"], 500) or not text(item["interface"], 120)
                or not isinstance(item["models"], list) or len(item["models"]) > 100
                or any(not text(model, 500) for model in item["models"])):
            raise ValueError("操作速查手册引用无效")
        manual_ids.add(item["source_id"])
    if manual_ids != set(sources):
        raise ValueError("操作速查不能遗漏包内手册")
    operations, selected, keys = reference["operations"], set(), set()
    if not isinstance(operations, list) or len(operations) > 2000:
        raise ValueError("操作速查分类无效")
    command_ids = {c["id"] for c in commands}
    for item in operations:
        if (not isinstance(item, dict) or set(item) != {"key", "group", "title", "command_ids"}
                or not identifier(item["key"]) or item["group"] not in GROUPS
                or not text(item["title"], 120) or (item["group"], item["key"]) in keys
                or not isinstance(item["command_ids"], list) or not item["command_ids"]
                or len(item["command_ids"]) > 50000
                or any(c not in command_ids or c in selected for c in item["command_ids"])
                or len(set(item["command_ids"])) != len(item["command_ids"])):
            raise ValueError("操作速查命令引用无效")
        selected.update(item["command_ids"])
        keys.add((item["group"], item["key"]))
    # Source parameter declarations, not executable templates or risk policy.
    # The built-in compiler interprets only the original command's syntax.
    syntax_ids = set()
    syntax_rows = reference.get("cli_syntax", [])
    if not isinstance(syntax_rows, list) or len(syntax_rows) > len(commands):
        raise ValueError("CLI 参数资料无效")
    for item in syntax_rows:
        if (not isinstance(item, dict) or set(item) != {"command_id", "kind", "parameters"}
                or item["command_id"] not in command_ids or item["command_id"] in syntax_ids
                or item["kind"] not in {"command_reference", "configuration_table", "configuration_example", "context_reference_only"}
                or not isinstance(item["parameters"], list) or len(item["parameters"]) > 512
                or len(set(item["parameters"])) != len(item["parameters"])
                or any(not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_.:-]{0,79}", name)
                       for name in item["parameters"])):
            raise ValueError("CLI 参数声明或命令引用无效")
        syntax_ids.add(item["command_id"])
    procedures = reference["procedures"]
    if not isinstance(procedures, list) or len(procedures) > 500:
        raise ValueError("手册操作步骤无效")
    ids = set()
    for item in procedures:
        if (not isinstance(item, dict) or set(item) != {
                "id", "key", "group", "title", "summary", "steps", "evidence", "models", "interface", "risk", "reason"}
                or not identifier(item["id"]) or item["id"] in ids or not identifier(item["key"])
                or item["group"] not in GROUPS or not text(item["title"], 120)
                or not text(item["summary"]) or item["interface"] not in {"Web", "EMS/TL1", "安装/硬件"}
                or item["risk"] not in {"read_only", "ordinary_change", "dangerous_change", "pending"}
                or not isinstance(item["reason"], str) or len(item["reason"]) > 4096
                or item["risk"] != "read_only" and not item["reason"].strip()
                or not isinstance(item["steps"], list) or not 1 <= len(item["steps"]) <= 30
                or any(not text(step) for step in item["steps"])
                or not isinstance(item["models"], list) or len(item["models"]) > 100
                or any(not text(model, 500) for model in item["models"])
                or not evidence(item["evidence"])):
            raise ValueError("手册操作步骤或出处无效")
        ids.add(item["id"])
    return reference
