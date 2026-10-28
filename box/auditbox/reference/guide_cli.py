"""Compile documented CLI grammar for the fill-only quick guide.

This is a consumer projection, not a change to the shared command/risk graph.
Optional query filters are omitted, mandatory arguments stay unfilled, and
mutually exclusive values are operator inputs (never a guessed first branch).
Unparseable prose, examples with target values and multi-command fragments
remain in the source package, not in the executable quick guide.
"""
from __future__ import annotations

import re

from auditbox.security.cli_syntax import _parameter, _parse

_LITERAL = re.compile(r"[a-z][a-z0-9_.:/-]*\Z", re.IGNORECASE)
_NAME = re.compile(r"[a-z][a-z0-9_.:-]{0,79}\Z", re.IGNORECASE)
_SLOT = re.compile(r"<([^<>\r\n]{1,80})>|\{([a-zA-Z][a-zA-Z0-9_-]{0,79})\}")
_NUMBER = re.compile(r"(?:\d+-\d+|.*(?:-id|-number|-num|-count|-index|-size|-length)|"
                     r"(?:frame|slot|port|ont|onu|pon|vlan|shelf|rack)(?:id|no)|id|number|count)\Z")
_POSITION = re.compile(r"(?:frame|slot|port|ont|onu|pon|shelf|rack|olt)(?:id|no)?\d*\Z")
_EXAMPLE_COMMANDS = {"quit", "exit", "end", "return", "enable", "disable", "save", "commit",
                     "write", "config", "configure terminal"}
_LABELS = {
    "port-id": "端口编号", "portid": "端口编号", "portno": "端口编号", "port-name": "接口名称",
    "port-list": "端口列表", "interface": "接口名称", "interface1": "接口名称", "interface-name": "接口名称",
    "onu-id": "ONU 编号", "onuid": "ONU 编号", "ontid": "ONT 编号", "ont-id": "ONT 编号",
    "onu-list": "ONU 编号列表", "onulist": "ONU 编号列表", "pon-id": "PON 端口编号", "ponid": "PON 端口编号",
    "vlan-id": "VLAN ID", "vlanid": "VLAN ID", "vid": "VLAN ID", "cvid": "用户 VLAN ID",
    "svlan": "业务 VLAN ID", "slot-id": "槽位编号", "slotid": "槽位编号", "slotno": "槽位编号",
    "frameid": "机框编号", "shelfno": "机架编号", "rackno": "机柜编号", "profile-id": "模板编号",
    "profile-name": "模板名称", "name": "名称", "filename": "文件名", "string": "参数值",
    "ip-address": "IP 地址", "ipaddress": "IP 地址", "a.b.c.d": "IPv4 地址", "mask": "掩码",
    "password": "密码", "pwd": "密码", "username": "用户名", "time": "时间参数",
}


def _descriptor(name):
    name = name.casefold()
    kind = "integer" if _NUMBER.fullmatch(name) else "token"
    result = {"label": _LABELS.get(name, name), "validation": kind}
    if re.fullmatch(r"\d+-\d+", name):
        result.update(minimum=int(name.split("-")[0]), maximum=int(name.split("-")[1]))
    if name in {"a.b.c.d", "ip-address", "ipaddress", "ipv4-address"}:
        result["validation"] = "ipv4"
    result["secret"] = bool(re.search(r"password|secret|passwd|^pwd$", name))
    return result


def parameters_for(template):
    """Legacy compiled forms keep the same positional, separately filled slots."""
    result = []
    for i, match in enumerate(_SLOT.finditer(template)):
        name = match.group(1) or match.group(2)
        descriptor = _descriptor(name)
        # Old packs used informal slot labels; enforce documented numeric
        # ranges, not guessed type/ranges from a legacy label.
        if not re.fullmatch(r"\d+-\d+", name):
            descriptor["validation"] = "token"
        result.append({"key": f"parameter{i}", "slot": match.group(), **descriptor})
    counts = {}
    for parameter in result:
        label = parameter["label"]
        counts[label] = counts.get(label, 0) + 1
        if sum(p["label"] == label for p in result) > 1:
            parameter["label"] += f"（第 {counts[label]} 项）"
    return result


def compile_cli(row, metadata=None):
    """Return a concrete template + bounded input descriptors, or None.

    Names are taken from the original manual's extracted parameter table. No
    package-provided regex/code is evaluated. Composite vendor interface
    notation is handled by this engine, not by a string of sample identifiers.
    """
    metadata = metadata or {}
    raw = row["syntax"].strip()
    if metadata.get("kind") == "configuration_example" and raw.casefold() not in _EXAMPLE_COMMANDS:
        return None
    if (not raw or re.search(r"[\x00-\x1f\x7f;`$\\]", raw)
            or re.search(r"Copyright|Proprietary|Raisecom Tech|Command Line Reference|Command Mode|"
                         r"Description None|Supported Device|用户手册|命令参考|Optical Line Terminal|"
                         r"^(?:for|in) .*configuration mode:|^(?:for|in) .*interface mode:", raw, re.IGNORECASE)):
        return None
    # Preserve previously compiled, reviewed complete forms; do not regress
    # a special-case single-ONU recipe into a broader optional/all operation.
    if row.get("render_state", "template") != "unsupported" and not metadata:
        template = row["template"].strip()
        rest = _SLOT.sub("", template)
        if (not template or re.search(r"[<>{}\r\n;&|`$\\\[\]]", rest)
                or re.search(r"\b([a-z][a-z_-]+)\s+\1\b", rest, re.IGNORECASE)):
            return None
        return {"template": template, "parameters": parameters_for(template)}
    source = row["template"].strip() if row.get("render_state", "template") != "unsupported" else raw
    tree = _parse(source)
    if tree is None:
        return None
    names = {name.casefold() for name in metadata.get("parameters", [])}
    keywords = set(row.get("family", "").casefold().split()) - names
    query = bool(re.match(r"^(?:show|display|get)\s", raw, re.IGNORECASE))
    params = []

    def parameter(token):
        if token.startswith("<") and token.endswith(">"):
            return _descriptor(token[1:-1])
        if token in names:
            return _descriptor(token)
        if _parameter(token) and token not in keywords:
            return _descriptor(_parameter(token))
        # The separators here are literal CLI notation: not optional grammar.
        # Require every segment; never insert frame/slot/ONU example numbers.
        prefix = ""
        body = token
        found = re.fullmatch(r"((?:gpon|epon|xgpon|xgspon)-(?:olt|onu)_)(.+)", token)
        if found:
            prefix, body = found.groups()
        parts = re.split(r"([/:])", body)
        if len(parts) > 1 and all(_POSITION.fullmatch(p) for p in parts[::2]):
            return {"label": "ONU 接口" if "onu_" in prefix else "PON 接口" if prefix else
                    "/".join(_LABELS.get(p, p) for p in parts[::2]),
                    "validation": "position", "formats": [{"prefix": prefix, "separators": parts[1::2]}],
                    "secret": False}
        if _NAME.fullmatch(token) and re.fullmatch(r"(?:name|number|interface|index|value|port|string)\d+", token):
            return _descriptor(token)
        return None

    def slot(descriptor):
        index = len(params)
        if index >= 24:
            raise ValueError("too many parameters for a common operation")
        placeholder = f"<parameter{index}>"
        params.append({"key": f"parameter{index}", "slot": placeholder, **descriptor})
        return placeholder

    def simple_choice(node):
        # A choice must be answered, not silently flattened to its first form.
        tokens = []
        for child in node[1]:
            if child[0] != "seq" or len(child[1]) != 1 or child[1][0][0] != "token":
                raise ValueError("compound alternatives need an explicit functional recipe")
            tokens.append(child[1][0][1])
        descriptors = [parameter(token) for token in tokens]
        literals = [token for token, descriptor in zip(tokens, descriptors) if descriptor is None]
        if any(not re.fullmatch(r"[a-z0-9][a-z0-9_.:/-]*", literal) for literal in literals):
            raise ValueError("invalid enum")
        typed = [descriptor for descriptor in descriptors if descriptor is not None]
        if not typed:
            return slot({"label": "操作选项", "validation": "enum", "secret": False,
                         "options": [{"value": value, "label": value} for value in literals]})
        if all(d["validation"] == "position" for d in typed):
            descriptor = {**typed[0], "formats": [fmt for d in typed for fmt in d["formats"]]}
        elif len({d["validation"] for d in typed}) == 1:
            descriptor = typed[0]
        else:
            raise ValueError("incompatible parameter alternatives")
        if literals:
            descriptor = {**descriptor, "literals": literals,
                          "label": descriptor["label"] + "（或 " + " / ".join(literals) + "）"}
        return slot(descriptor)

    def visit(node):
        kind = node[0]
        if kind == "token":
            token = node[1]
            descriptor = parameter(token)
            if descriptor:
                return [slot(descriptor)]
            if not _LITERAL.fullmatch(token):
                raise ValueError("uncompiled argument or example value")
            return [token]
        if kind == "choice":
            return [simple_choice(node)]
        if kind == "repeat":
            if node[2] == 0 and query:
                return []  # Documented unfiltered query, not a guessed target.
            if node[2] > 1:
                raise ValueError("repeated argument list needs a functional recipe")
            return visit(node[1])
        result = []
        children = node[1]
        for i, child in enumerate(children):
            # Vendor tables write "rackno rackno": the first is the keyword,
            # the second the parameter. Do not turn both into argument slots.
            next_child = children[i + 1] if i + 1 < len(children) else None
            if (child[0] == "token" and _LITERAL.fullmatch(child[1]) and next_child and next_child[0] == "token"
                    and parameter(next_child[1]) and (next_child == child or child[1] in
                        {"port", "interface", "vlan", "username", "password", "name", "number", "index"})):
                result.append(child[1])
            else:
                result.extend(visit(child))
        return result

    try:
        words = visit(tree)
    except (ValueError, RecursionError):
        return None
    if (not words or words[0].startswith("<") or len(words) > 100
            or words[0] in {"no", "undo", "default"} and (len(words) < 2 or words[1].startswith("<"))
            or re.search(r"\b([a-z][a-z_-]+)\s+\1\b", " ".join(words))):
        return None
    template = " ".join(words)
    labels = [parameter["label"] for parameter in params]
    for index, descriptor in enumerate(params):
        if labels.count(descriptor["label"]) > 1:
            descriptor["label"] += f"（第 {labels[:index + 1].count(descriptor['label'])} 项）"
    # Metadata-less supported facts use their original reviewed template.
    # For the new metadata, all bare names are compiled from the source syntax.
    return {"template": template, "parameters": params}


def matches_compiled(command, compiled):
    """Prove an exact compiled form for conservative risk confirmation only.

    Never use this to authorize a previously unsupported read-only command.
    Unknown commands still fall through to the existing unknown policy.
    """
    words = command.split()
    template = compiled["template"].split()
    if len(words) != len(template):
        return False
    parameters = {p["slot"]: p for p in compiled["parameters"]}
    for expected, value in zip(template, words):
        parameter = parameters.get(expected)
        if parameter is None:
            if expected.casefold() != value.casefold(): return False
            continue
        if not re.fullmatch(r"[a-zA-Z0-9_./:@,+%=-]{1,256}", value): return False
        if value in parameter.get("literals", []): continue
        kind = parameter["validation"]
        if kind == "enum" and value not in {option["value"] for option in parameter["options"]}: return False
        if kind == "integer" and (not re.fullmatch(r"\d{1,10}", value)
                or int(value) < parameter.get("minimum", 0) or int(value) > parameter.get("maximum", 9999999999)): return False
        if kind == "ipv4" and (not re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", value)
                or any(int(part) > 255 for part in value.split('.'))): return False
        if kind == "position":
            def position(fmt, value=value):
                if not value.startswith(fmt["prefix"]): return False
                parts = re.split(r"([/:])", value[len(fmt["prefix"]):])
                return parts[1::2] == fmt["separators"] and all(re.fullmatch(r"\d{1,10}", p) for p in parts[::2])
            if not any(position(fmt) for fmt in parameter["formats"]): return False
    return True
