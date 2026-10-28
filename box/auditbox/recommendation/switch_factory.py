"""Independent Huawei/Cisco/ZTE adapters on a shared behavior recommendation policy."""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from auditbox.switch_commands import (
    BEHAVIOR_LABELS, INTERFACES, behavior_for, match_query, normalize, queries, single_command,
)
from auditbox.recommendation.switch_catalog import RELATED_BEHAVIORS, catalog_for, root_metadata


@dataclass
class SwitchIntent:
    action: str
    parameters: dict[str, str] = field(default_factory=dict)


@dataclass
class SwitchSessionState:
    profile: str
    # Unknown initial privilege: never invent a config-mode transition.
    stack: list[SwitchIntent] = field(default_factory=list)
    current: SwitchIntent | None = None


def _interface(value: str, profile: str) -> str | None:
    return value if re.fullmatch(INTERFACES[profile], value, re.I) else None


def classify_switch_command(command: str, profile: str) -> SwitchIntent | None:
    if not single_command(command):
        return None
    value = normalize(command)
    query = match_query(value, profile)
    if query:
        return SwitchIntent(query[0].behavior, query[1])
    catalog = catalog_for(profile)
    roots = catalog.roots
    words = value.split()
    matched = next((" ".join(words[:n]) for n in range(len(words), 0, -1)
                    if " ".join(words[:n]) in roots), None)
    if matched is None:
        return None
    action = behavior_for(matched)
    parameters = {}
    interface = re.fullmatch(r"interface\s+(.+)", value)
    if interface and _interface(interface[1], profile):
        action, parameters = "interface", {"interface": interface[1]}
    vlan = re.fullmatch(r"vlan\s+(\d{1,4})", value)
    if vlan and 1 <= int(vlan[1]) <= 4094:
        action, parameters = "vlan", {"vlan": vlan[1]}
    return SwitchIntent(action, parameters)


def advance_switch_session_state(state: SwitchSessionState, command: str) -> SwitchIntent | None:
    value = normalize(command)
    if not single_command(command):
        state.current = None
        return None
    if value in {"return", "end"}:
        state.stack.clear()
        state.current = SwitchIntent("system")
        return state.current
    if value in {"quit", "exit"}:
        if state.stack:
            state.stack.pop()
        state.current = state.stack[-1] if state.stack else SwitchIntent("system")
        return state.current
    if value in {"system-view", "configure terminal", "conf t"}:
        state.stack = [SwitchIntent("config")]
        state.current = state.stack[-1]
        return state.current
    current = classify_switch_command(command, state.profile)
    if current is None:
        # Unknown submode entry cannot keep an earlier interface/VLAN target.
        if not value.startswith(("show ", "display ", "do show ")):
            state.stack.clear()
        state.current = None
        return None
    if re.match(r"^(?:interface|vlan|router\s+(?:ospf|bgp|isis|rip)|ospf|bgp|isis|acl)(?:\s|$)", value):
        if value.startswith(("interface ", "vlan ")):
            # Batch/range/nonstandard interfaces: do not reuse a previous target.
            if not current.parameters:
                current = SwitchIntent(current.action)
        base = state.stack[:1] if state.stack and state.stack[0].action == "config" else [SwitchIntent("config")]
        state.stack = base + [current]
    elif not match_query(command, state.profile) and state.stack:
        parent = state.stack[-1]
        # Only local interface edits inherit interface identity. Global queries
        # and independent tasks must not inherit VLAN/interface parameters.
        if re.match(r"^(?:(?:no|undo)\s+)?(?:shutdown|description|speed|duplex|mtu|switchport|port)(?:\s|$)", value):
            current = SwitchIntent(parent.action, dict(parent.parameters))
    state.current = current
    return current


def complete_switch_commands(prefix: str, profile: str, limit: int = 8) -> list[dict]:
    if not single_command(prefix) or limit <= 0:
        return []
    value = normalize(prefix) + (" " if prefix[-1:].isspace() else "")
    do_prefix = profile == "cisco_ios" and value.startswith("do ")
    if do_prefix:
        value = value[3:]
    roots = catalog_for(profile).roots
    common = {q.command for q in queries(profile) if "{" not in q.command}
    candidates = {root for root in roots if root.startswith(value)} | {q for q in common if q.startswith(value)}
    if do_prefix:
        candidates = {root for root in candidates if root.startswith("show ")}
    preferred = {"interface", "vlan", "shutdown", "no shutdown", "undo shutdown", "configure terminal", "system-view"}
    ordered = sorted(candidates, key=lambda root: (root != value, root not in common,
                     root not in preferred, len(root.split()), len(root), root))
    results = []
    for root in ordered[:min(limit, 50)]:
        rows = roots.get(root, ())
        metadata = root_metadata(profile, root) if rows else {"view": "查询视图（按型号核对）", "models": "通用操作；以设备型号/版本为准", "variant_count": 0, "risk_reason": ""}
        safe = match_query(root, profile) is not None
        risk = "normal" if safe else "confirm"
        behavior = behavior_for(root)
        results.append({"command": ("do " if do_prefix else "") + root, "source": "catalog", "usage_count": 0, "last_used_at": None,
                        "risk": risk, "behavior": behavior, "behavior_label": BEHAVIOR_LABELS[behavior],
                        **metadata})
    return results


def recommend_next_switch_commands(command: str, profile: str, *, limit: int = 5,
                                   history=(), state: SwitchSessionState | None = None) -> list[dict]:
    if limit <= 0 or not single_command(command):
        return []
    if state is None:
        state = SwitchSessionState(profile)
        for item in history:
            advance_switch_session_state(state, item)
        advance_switch_session_state(state, command)
    if state.profile != profile or state.current is None:
        return []
    current = state.current
    if profile == "zte_zxr10" and state.stack:
        return [{"command": "end", "source": "factory", "risk": "normal",
                 "behavior": current.action, "behavior_label": BEHAVIOR_LABELS[current.action],
                 "reason": "先返回特权视图再查询；不假设中兴支持 IOS 的 do show 语法",
                 "view": "配置视图 → 特权视图", "score": 1.0}]
    behaviors = (current.action, *RELATED_BEHAVIORS.get(current.action, ("system", "logs")))
    recent = {normalize(item) for item in history[-4:]} | {normalize(command)}
    results, seen = [], set()
    for behavior in behaviors:
        behavior_count = 0
        for query in queries(profile):
            if query.behavior != behavior:
                continue
            try:
                rendered = query.command.format(**current.parameters)
            except KeyError:
                continue  # Never invent VLAN IDs, interfaces or address values.
            if profile == "cisco_ios" and state.stack:
                rendered = "do " + rendered
            equivalent = normalize(rendered).removeprefix("do ")
            if equivalent in {r.removeprefix("do ") for r in recent} or rendered in seen:
                continue
            if not match_query(rendered, profile):
                continue
            seen.add(rendered)
            results.append({"command": rendered, "source": "factory", "risk": "normal",
                            "behavior": behavior, "behavior_label": BEHAVIOR_LABELS[behavior],
                            "reason": f"{BEHAVIOR_LABELS[current.action]}后，核对{BEHAVIOR_LABELS[behavior]}",
                            "view": "当前配置视图（do 查询）" if rendered.startswith("do ") else "查询视图；按型号核对",
                            "score": round(1 - len(results) * 0.08, 2)})
            if len(results) >= min(limit, 5):
                return results
            behavior_count += 1
            if behavior_count >= 2:
                break
    return results
