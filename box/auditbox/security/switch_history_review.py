"""Deterministic, non-blocking side-channel audit for Huawei/Cisco/ZTE.

Severity describes potential command impact, not proof of successful execution
or malicious intent. No model calls, confirmation prompts, or terminal locks.
"""

from __future__ import annotations

from collections import Counter
from auditbox.command_packages import registered, grade
from datetime import datetime, timezone
import re

from auditbox.device_profiles import REFERENCE_PROFILES, normalize_device_profile, profile_display_name
from auditbox.security.alert import check
from auditbox.security.switch_rules import command_policy, risk_catalog_match
from auditbox.switch_commands import normalize

RULE_REVIEW_PROFILES = REFERENCE_PROFILES


def uses_rule_review(profile: str) -> bool:
    return profile in RULE_REVIEW_PROFILES or registered(profile)

POLICY_VERSION = "2026-09-16.switch-side-review.2"
_RANK = {"low": 0, "unknown": 1, "medium": 2, "high": 3, "critical": 4}
_STATUS_LABELS = {"executed": "已下发，实际效果以设备输出为准", "blocked": "已阻断，未下发",
                  "cancelled": "已取消，未下发", "failed": "设备报错，未确认生效"}

# Grade known risk forms only, after consulting the existing command gate and
# reviewed catalog. Match operation tokens, never keywords inside descriptions.
_DISRUPTIVE = re.compile(
    r"^(?:(?:do\s+)?(?:reload|reboot|shutdown|poweroff)(?:\s|$)|"
    r"(?:undo|no)\s+shutdown(?:\s|$)|"
    r"(?:undo|no|default)\s+(?:interface|vlan|router|ospf|bgp|stp|spanning-tree|"
    r"aaa|username|local-user|acl|access-list|ip\s+(?:route|route-static|routing|address))(?:\s|$)|"
    r"(?:ip|ipv6)\s+(?:route|route-static)(?:\s|$)|"
    r"(?:reset|clear)\s+(?:saved-configuration|configuration|factory-configuration|interface|"
    r"bgp|ospf|ip\s+(?:bgp|ospf|route))(?:\s|$)|"
    r"(?:delete|erase)(?:\s|$)|"
    r"(?:switchport\s+(?:mode|access|trunk)|port\s+(?:link-type|default\s+vlan|trunk|hybrid))(?:\s|$)|"
    r"(?:debug|debugging)\s+all(?:\s|$))"
)


def _sequence(value) -> int | None:
    try:
        result = int(value)
        return result if result > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def grade_command(command: str, profile: str) -> dict:
    """Align with existing confirmation policy; keep uncertainty explicit."""
    profile = normalize_device_profile(profile)
    if registered(profile):
        decision = check(command, device_profile=profile)
        if decision and decision['action'] == 'block':
            return {'risk': 'critical', 'code': 'existing_hard_block', 'action': 'block', 'reason': decision['reason']}
        return grade(command, profile)
    if profile not in RULE_REVIEW_PROFILES:
        raise ValueError("rule side-channel review is only enabled for Huawei/Cisco/ZTE")
    decision = check(command, device_profile=profile)
    if decision is None:
        if command_policy(command, profile).state == "unknown":
            return {"risk": "unknown", "code": "unknown", "action": "allow", "match_type": "none",
                    "reason": "未命中已核验规则，按未知命令策略直接放行；不代表已核验无风险。"}
        return {"risk": "low", "code": "verified_read_only", "action": "allow",
                "reason": "匹配已核验的只读或导航规则，未识别到配置或运行状态变更。"}
    if decision["action"] == "block":
        return {"risk": "critical", "code": "existing_hard_block", "action": "block",
                "reason": decision["reason"]}
    match = risk_catalog_match(command, profile)
    result = {"risk": "unknown", "code": match.state, "action": "confirm",
              "reason": decision["reason"], "match_type": match.match_type}
    if match.rule:
        result.update(rule_id=match.rule.id, command_family=match.rule.title)
    if match.state != "risk":
        return result
    value = normalize(command)
    if profile == "cisco_ios" and value.startswith("do "):
        value = value[3:]
    # 'format bulkascii' configures an export format, not a storage format.
    destructive_format = value.startswith("format ") and value != "format bulkascii"
    high = bool(_DISRUPTIVE.match(value)) or destructive_format
    result.update(risk="high" if high else "medium",
                  code="disruptive_change" if high else "state_change")
    return result


def review_switch_history(history: list[dict], profile: str) -> dict:
    profile = normalize_device_profile(profile)
    if profile not in RULE_REVIEW_PROFILES and not registered(profile):
        raise ValueError("this device profile retains its model-based reviewer")
    findings, seen = [], set()
    for item in history:
        sequence = _sequence(item.get("seq"))
        command = str(item.get("command") or "").strip()
        if sequence is None or sequence in seen or not command:
            continue
        seen.add(sequence)
        finding = grade_command(command, profile)
        status = str(item.get("status") or "executed")
        authorized = item.get("authorized") is True
        finding.update(seq=sequence, command=command, status=status, authorized=authorized,
                       execution_note=_STATUS_LABELS.get(status, "执行状态待核对"))
        if authorized and status == "executed":
            finding["execution_note"] = "已确认本次下发；授权不改变潜在风险，旁路仅记录和展示"
        findings.append(finding)

    risk = max((item["risk"] for item in findings), key=_RANK.get, default="unknown")
    counts = Counter(item["risk"] for item in findings)
    unknown = counts["unknown"]
    unavailable = any(item["code"] == "unavailable" for item in findings)
    evidence = [item["seq"] for item in findings if item["risk"] != "low"]
    if findings and not evidence:
        evidence = [item["seq"] for item in findings]
    labels = {"low": "只读/导航", "medium": "普通变更", "high": "危险变更",
              "critical": "命令阻断项", "unknown": "待核验"}
    summary = "，".join(f"{counts[level]} 条{labels[level]}" for level in _RANK if counts[level])
    reason = (f"最近 {len(findings)} 条命令：{summary}。等级表示潜在影响，实际执行状态逐条标注。"
              if findings else "尚无可审计的终端命令。")
    if unknown:
        allowed_unknown = sum(item["risk"] == "unknown" and item["action"] == "allow" for item in findings)
        if allowed_unknown:
            reason += f"{allowed_unknown} 条未知命令按策略直接放行，不代表已核验无风险。"
        if unknown > allowed_unknown:
            reason += f"{unknown - allowed_unknown} 条待核验项执行前仍需确认。"
    latest = max(findings, key=lambda item: item["seq"], default={})
    return {
        "status": "error" if unavailable else "ready" if findings else "idle",
        "review_source": "rules", "device_profile": profile, "policy_version": POLICY_VERSION,
        "risk": risk, "intent": f"{profile_display_name(profile)}规则旁路审计",
        "reason": reason, "evidence": evidence, "findings": findings,
        "current": {key: latest[key] for key in ("seq", "risk", "reason")} if latest else {},
        "summary": dict(counts), "unverified_count": unknown,
        # This field is for legacy clients; no model confidence is fabricated.
        "confidence": 0.0, "lock": False,
        "lock_policy": {"allow_lock": False, "code": "rule_audit_only",
                        "reason": "旁路规则只审计；执行前确认与单条命令阻断独立生效。", "signals": []},
        "reviewed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reviewed_through_seq": max((_sequence(item.get("seq")) or 0 for item in history), default=0),
    }
