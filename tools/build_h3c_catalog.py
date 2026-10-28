#!/usr/bin/env python3
"""Build the packaged H3C command-to-behavior catalog.

The input is the marker-verified JSON extracted from the S5130-EI R35xx PDF
set.  The generated module is plain Python so PyInstaller can bundle it on
Windows, Linux, and macOS without platform-specific data-file paths.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BEHAVIOR_LABELS: dict[str, str] = {
    "system.access": "系统访问与命令行",
    "system.health": "设备状态与资源",
    "system.config": "配置、文件与软件",
    "system.file_transfer": "文件传输服务",
    "switching.interface": "接口与端口",
    "switching.vlan": "VLAN 与二层隔离",
    "switching.mac": "MAC 地址转发",
    "switching.spanning_tree": "生成树与环路",
    "switching.aggregation": "链路聚合",
    "switching.discovery": "邻居发现与二层透传",
    "switching.wan": "广域网接入",
    "virtualization.irf": "IRF 虚拟化",
    "ip.addressing": "IP 地址与转发",
    "ip.arp": "ARP 与邻居解析",
    "ip.ipv6": "IPv6 基础",
    "ip.dhcp": "DHCP 地址分配",
    "ip.dns": "域名解析",
    "network.diagnostics": "网络连通性诊断",
    "routing.base": "路由表、静态路由与策略",
    "routing.ospf": "OSPF 动态路由",
    "routing.rip": "RIP 动态路由",
    "multicast": "IP 组播",
    "policy.acl": "ACL 与时间段",
    "policy.qos": "QoS 与流量策略",
    "security.authentication": "认证、授权与计费",
    "security.access": "接入安全与攻击防护",
    "security.crypto": "加密、证书与安全隧道",
    "reliability": "高可用与链路可靠性",
    "observability": "网络管理、监控与测量",
    "observability.telemetry": "Telemetry 与流式采集",
    "automation": "自动化与远程管理",
    "sdn.openflow": "OpenFlow 与 SDN",
}


def _contains(text: str, *needles: str) -> bool:
    return any(needle.lower() in text.lower() for needle in needles)


def classify_behavior(command: dict[str, Any]) -> str:
    """Assign every official command to exactly one operations behavior."""
    manual = str(command.get("manual_category") or "")
    section = str(command.get("section_title") or "")
    title = str(command.get("normalized_title") or command.get("command_title") or "")
    context = f"{section} {title}"

    if re.match(r"^(?:ping|tracert)(?:\s|$)", title, re.IGNORECASE):
        return "network.diagnostics"

    if manual == "基础配置":
        if _contains(section, "Tcl", "Python"):
            return "automation"
        if _contains(section, "FTP", "TFTP"):
            return "system.file_transfer"
        if _contains(section, "CLI", "登录", "RBAC"):
            return "system.access"
        if _contains(section, "设备管理"):
            return "system.health"
        return "system.config"

    if manual == "IRF":
        return "virtualization.irf"

    if manual == "二层技术-以太网交换":
        if _contains(section, "链路聚合"):
            return "switching.aggregation"
        if _contains(section, "LLDP", "L2PT"):
            return "switching.discovery"
        if _contains(section, "生成树", "环路检测"):
            return "switching.spanning_tree"
        if _contains(section, "MAC") and not _contains(section, "基于MAC的VLAN"):
            return "switching.mac"
        if _contains(section, "VLAN", "MVRP", "QinQ"):
            return "switching.vlan"
        return "switching.interface"

    if manual == "二层技术-广域网接入":
        return "switching.wan"

    if manual == "三层技术-IP业务":
        if _contains(section, "DHCP", "BOOTP"):
            return "ip.dhcp"
        if _contains(section, "ARP"):
            return "ip.arp"
        if _contains(section, "域名解析"):
            return "ip.dns"
        if _contains(section, "IPv6"):
            return "ip.ipv6"
        return "ip.addressing"

    if manual == "三层技术-IP路由":
        if _contains(section, "OSPF"):
            return "routing.ospf"
        if _contains(section, "RIP"):
            return "routing.rip"
        return "routing.base"

    if manual == "IP组播":
        return "multicast"

    if manual == "ACL和QoS":
        if _contains(section, "ACL", "时间段"):
            return "policy.acl"
        return "policy.qos"

    if manual == "安全":
        if _contains(
            context,
            "IPsec",
            "IKE",
            "PKI",
            "SSL",
            "SSH",
            "公钥",
            "FIPS",
            "加密引擎",
        ):
            return "security.crypto"
        if _contains(
            context,
            "802.1X",
            "MAC地址认证",
            "Portal",
            "Web认证",
            "RADIUS",
            "HWTACACS",
            "LDAP",
            "AAA",
            "本地用户",
            "Password Control",
            "User Profile",
        ):
            return "security.authentication"
        return "security.access"

    if manual == "可靠性":
        return "reliability"

    if manual == "网络管理和监控":
        if _contains(section, "EAA", "CWMP", "NETCONF"):
            return "automation"
        return "observability"

    if manual == "Telemetry":
        return "observability.telemetry"

    if manual == "OpenFlow":
        return "sdn.openflow"

    raise ValueError(f"Unsupported H3C manual category: {manual!r}")


def normalize_match_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s*[（(][^（）()]*[）)]\s*$", "", value).strip()
    positions = [value.find(char) for char in "[{<" if char in value]
    if positions:
        value = value[: min(positions)].strip()
    return value.rstrip("- ")


def _normalize_syntax(value: str) -> str:
    """Normalize PDF line wrapping without changing Comware grammar."""
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"(?<=\w)\s*-\s+(?=\w)", "-", value)
    value = re.sub(r"(?<=\w)\s+-\s*(?=\w)", "-", value)
    return re.sub(r"\s+", " ", value).strip().lower()


def _remove_optional_groups(value: str) -> str:
    """Remove balanced ``[...]`` grammar groups from one syntax form."""
    output: list[str] = []
    depth = 0
    for character in value:
        if character == "[":
            depth += 1
        elif character == "]":
            depth = max(depth - 1, 0)
        elif depth == 0:
            output.append(character)
    return "".join(output)


def supports_bare_display(command: dict[str, Any]) -> bool:
    """Return whether the documented title is itself an executable command.

    PDF command titles are not always runnable forms.  For example, ``display
    acl`` is a title, while its grammar requires ``all``, a number, or a name.
    The generated allow-list lets recommendation tests distinguish such titles
    from syntax forms whose remaining arguments are all optional.
    """
    title = normalize_match_key(
        str(command.get("normalized_title") or command.get("command_title") or "")
    )
    if not title.startswith("display "):
        return False

    syntax = _normalize_syntax(str(command.get("syntax") or ""))
    # A few PDF titles end in the metavariable itself.  The typography that
    # distinguishes it from a literal keyword is lost during text extraction.
    if title.endswith((" ip-address", " ipv6-address")):
        return False

    # Some PDF cells contain two command forms separated only by line wrapping.
    forms = re.split(r"(?=\b(?:display|undo)\s)", syntax)
    for form in forms:
        minimum = _remove_optional_groups(form)
        minimum = re.sub(r"&<[^>]+>|\*", "", minimum)
        minimum = re.sub(r"\s+", " ", minimum).strip()
        if minimum == title:
            return True
    return False


def is_context_free_view(value: str) -> bool:
    """Return whether a command can be interpreted outside a feature view."""
    collapsed = re.sub(r"\s+", "", str(value or ""))
    return any(marker in collapsed for marker in ("任意视图", "用户视图", "系统视图"))


def _py_string(value: str) -> str:
    return repr(value)


def build_module(data: dict[str, Any]) -> str:
    entries: list[tuple[str, str, str, str, str, str]] = []
    for document in data.get("documents", []):
        for command in document.get("commands", []):
            title = str(command.get("normalized_title") or command.get("command_title") or "").strip()
            entries.append((
                str(command.get("command_id") or ""),
                title,
                normalize_match_key(title),
                classify_behavior(command),
                str(command.get("manual_category") or ""),
                str(command.get("section_title") or ""),
            ))

    parsed_count = int(data.get("parsed_command_count") or 0)
    if len(entries) != parsed_count:
        raise ValueError(f"Catalog count mismatch: {len(entries)} != {parsed_count}")
    if any(not command_id or not title or not behavior for command_id, title, _, behavior, _, _ in entries):
        raise ValueError("Catalog contains an incomplete entry")
    unknown = sorted({entry[3] for entry in entries} - BEHAVIOR_LABELS.keys())
    if unknown:
        raise ValueError(f"Catalog contains unknown behaviors: {unknown}")

    behavior_counts = Counter(entry[3] for entry in entries)
    source_documents = [
        (
            str(document.get("source_file") or ""),
            int(document.get("page_count") or 0),
            str(document.get("document_version") or ""),
            str(document.get("product_version") or ""),
            int(document.get("parsed_command_count") or 0),
        )
        for document in data.get("documents", [])
    ]
    bare_display_commands = sorted({
        normalize_match_key(
            str(command.get("normalized_title") or command.get("command_title") or "")
        )
        for document in data.get("documents", [])
        for command in document.get("commands", [])
        if supports_bare_display(command)
    })
    views_by_match_key: defaultdict[str, list[str]] = defaultdict(list)
    for document in data.get("documents", []):
        for command in document.get("commands", []):
            title = str(
                command.get("normalized_title") or command.get("command_title") or ""
            )
            views_by_match_key[normalize_match_key(title)].append(
                str(command.get("view") or "")
            )
    context_required_match_keys = sorted(
        match_key
        for match_key, views in views_by_match_key.items()
        if len(match_key.split()) == 1
        and views
        and not any(is_context_free_view(view) for view in views)
    )
    view_entry_candidates: defaultdict[str, set[str]] = defaultdict(set)
    for document in data.get("documents", []):
        for command in document.get("commands", []):
            purpose = str(command.get("purpose") or "")
            if not re.search(r"进入.{0,40}视图", purpose):
                continue
            title = str(
                command.get("normalized_title") or command.get("command_title") or ""
            )
            match_key = normalize_match_key(title)
            if match_key:
                view_entry_candidates[match_key].add(classify_behavior(command))
    view_entry_behaviors = {
        match_key: next(iter(behaviors))
        for match_key, behaviors in view_entry_candidates.items()
        if len(behaviors) == 1
    }

    lines = [
        '"""Generated H3C S5130-EI R35xx command behavior catalog.',
        "",
        "Generated by tools/build_h3c_catalog.py. Do not edit by hand.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        f"DOCUMENT_COUNT = {int(data.get('document_count') or 0)}",
        f"PAGE_COUNT = {int(data.get('page_count') or 0)}",
        f"COMMAND_MARKER_COUNT = {int(data.get('command_marker_count') or 0)}",
        f"COMMAND_COUNT = {parsed_count}",
        "CATALOG_FIELDS = ('command_id', 'title', 'match_key', 'behavior', 'manual', 'section')",
        "",
        "BEHAVIOR_LABELS: dict[str, str] = {",
    ]
    for behavior, label in BEHAVIOR_LABELS.items():
        lines.append(f"    {_py_string(behavior)}: {_py_string(label)},")
    lines.extend(["}", "", "BARE_DISPLAY_COMMANDS = frozenset({"])
    for command in bare_display_commands:
        lines.append(f"    {_py_string(command)},")
    lines.extend(["})", "", "CONTEXT_REQUIRED_MATCH_KEYS = frozenset({"])
    for match_key in context_required_match_keys:
        lines.append(f"    {_py_string(match_key)},")
    lines.extend(["})", "", "VIEW_ENTRY_BEHAVIORS: dict[str, str] = {"])
    for match_key in sorted(view_entry_behaviors):
        lines.append(
            f"    {_py_string(match_key)}: {_py_string(view_entry_behaviors[match_key])},"
        )
    lines.extend(["}", "", "BEHAVIOR_COUNTS: dict[str, int] = {"])
    for behavior in BEHAVIOR_LABELS:
        lines.append(f"    {_py_string(behavior)}: {behavior_counts.get(behavior, 0)},")
    lines.extend(["}", "", "SOURCE_DOCUMENTS = ("])
    for document in source_documents:
        rendered = ", ".join(
            _py_string(value) if isinstance(value, str) else str(value)
            for value in document
        )
        lines.append(f"    ({rendered}),")
    lines.extend([
        ")",
        "",
        "COMMAND_CATALOG = (",
    ])
    for entry in entries:
        rendered = ", ".join(_py_string(value) for value in entry)
        lines.append(f"    ({rendered}),")
    lines.extend([
        ")",
        "",
        "__all__ = [",
        "    'BARE_DISPLAY_COMMANDS',",
        "    'BEHAVIOR_COUNTS',",
        "    'BEHAVIOR_LABELS',",
        "    'CATALOG_FIELDS',",
        "    'COMMAND_CATALOG',",
        "    'COMMAND_COUNT',",
        "    'COMMAND_MARKER_COUNT',",
        "    'CONTEXT_REQUIRED_MATCH_KEYS',",
        "    'DOCUMENT_COUNT',",
        "    'PAGE_COUNT',",
        "    'SOURCE_DOCUMENTS',",
        "    'VIEW_ENTRY_BEHAVIORS',",
        "]",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", type=Path)
    parser.add_argument("output_module", type=Path)
    args = parser.parse_args()

    data = json.loads(args.input_json.read_text(encoding="utf-8"))
    output = build_module(data)
    args.output_module.parent.mkdir(parents=True, exist_ok=True)
    args.output_module.write_text(output, encoding="utf-8", newline="\n")
    print(
        f"Generated {args.output_module} with "
        f"{data['parsed_command_count']} commands and {len(BEHAVIOR_LABELS)} behaviors"
    )


if __name__ == "__main__":
    main()
