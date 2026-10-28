"""Deterministic switch execution rules. No model or recommendation dependency.

Reviewed risk and pending forms require confirmation. Unknown commands pass,
without claiming they are verified read-only. Known-risk family guards,
compound/write syntax and unavailable rule data still require confirmation.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
import gzip
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Literal
import zlib

from auditbox.device_profiles import H3C_COMWARE, SWITCH_PROFILES
from auditbox.capability_packages import data_path, revision, rules_metadata
from auditbox.security.cli_syntax import matches
from auditbox.switch_commands import INTERFACES, match_query, normalize

log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).with_name("data")
_H3C_INTERFACE = r"(?:GigabitEthernet|Ten-GigabitEthernet|TenGigabitEthernet|FortyGigE|HundredGigE|Ethernet|GE|XGE|Bridge-Aggregation|Route-Aggregation|Vlan-interface|LoopBack|M-GigabitEthernet)\s*\d+(?:/\d+){0,3}(?:\.\d+)?"


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    title: str
    syntax: str
    status: int  # 0=reviewed read-only, 1=risk, 2=pending review
    reason: str
    models: str
    views: str


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    state: Literal["read_only", "unknown", "confirm", "unavailable"]
    reason: str | None = None


def _root(title: str) -> str:
    value = re.split(r"[（(\[<{]", normalize(title), maxsplit=1)[0].strip()
    return value if re.fullmatch(r"[a-z0-9][a-z0-9 +_.:/-]{0,180}", value) else ""


def _catalog(profile: str) -> dict[str, tuple[Rule, ...]] | None:
    try:
        return _catalog_version(profile, revision(profile))
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=4)
def _catalog_version(profile: str, version: str) -> dict[str, tuple[Rule, ...]] | None:
    if profile not in SWITCH_PROFILES:
        return None
    try:
        metadata = rules_metadata(profile, DATA_DIR / "manifest.json")
        packed = data_path(profile, "rules", DATA_DIR / f"{profile}.json.gz").read_bytes()
        if hashlib.sha256(packed).hexdigest() != metadata["sha256"]:
            raise ValueError("switch rule checksum/schema mismatch")
        data = json.loads(gzip.decompress(packed))
        if data["schema"] != 1 or data["profile"] != profile or len(data["entries"]) != metadata["variants"]:
            raise ValueError("switch rule profile/count mismatch")
        strings, groups = data["strings"], {}
        for entry in data["entries"]:
            if entry[3] not in (0, 1, 2):
                raise ValueError("unknown risk status")
            rule = Rule(entry[0], strings[entry[1]], strings[entry[2]], entry[3], strings[entry[4]], strings[entry[5]], strings[entry[6]])
            keys = {_root(rule.title)}
            if rule.status:
                # Negated forms can be shorter than the manual heading, e.g.
                # "accept-lifetime utc" also documents "undo accept-lifetime".
                # These are catalogued risks, not unknown commands. Index the
                # documented forms too; never infer a safe wildcard from them.
                keys.update((normalize(rule.syntax), _root(rule.syntax)))
            for key in sorted(keys - {""}):
                groups.setdefault(key, []).append(rule)
        return {key: tuple(value) for key, value in groups.items()}
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, EOFError, zlib.error) as exc:
        log.warning("Switch rules unavailable for %s; requiring confirmation: %s", profile, exc)
        return None


_catalog.cache_clear = _catalog_version.cache_clear


def _group_candidates(catalog: dict, command: str) -> Iterator[tuple[Rule, ...]]:
    candidates = [command]
    if command.startswith(("undo ", "no ", "default ")):
        candidates.append(command.split(" ", 1)[1])
    seen = set()
    for value in candidates:
        exact = catalog.get(value)
        if exact and value not in seen:
            seen.add(value)
            yield exact
        words = value.split()
        for n in range(min(len(words), 14), 0, -1):
            key = " ".join(words[:n])
            result = catalog.get(key)
            if result and key not in seen:
                seen.add(key)
                yield result


def _explain(rules: tuple[Rule, ...], command: str) -> str:
    risky = [rule for rule in rules if rule.status]
    if not risky:
        return "命令所属规则需复核，请核对实际型号、执行视图和影响。"
    exact = [rule for rule in risky if normalize(rule.syntax) == command]
    matched = exact or [rule for rule in risky if matches(rule.syntax, command)]
    rule = next((r for r in matched if r.status == 2), None) or (matched or risky)[0]
    if rule.status == 2:
        return "待核对命令，按默认策略需要二次确认：" + (rule.reason or "原资料不足以确定操作影响。")[:320]
    return (rule.reason or "修改交换机配置或运行状态。")[:360]


def _split_filter(command: str) -> tuple[str, str | None]:
    # The pipe in a manual's {a | b} is grammar; here the input is actual CLI.
    parts = command.split("|")
    if len(parts) == 1:
        return command, None
    if len(parts) > 5:
        return "", "输出管道过长，无法核验处理过程，需要二次确认。"
    for part in parts[1:]:
        if not re.fullmatch(r"\s*(?:include|exclude|begin|section)\s+[^|;\r\n<>`$&\\]+\s*|\s*count\s*", part, re.I):
            return "", "输出管道包含写文件、未知处理或未核验参数，需要二次确认。"
    return parts[0].strip(), None


def confirmation_reason(command: str, profile: str) -> str | None:
    """None permits execution, including unknown commands; it is not a risk grade."""
    return command_policy(command, profile).reason


def command_policy(command: str, profile: str) -> CommandPolicy:
    """Keep permissive unknown handling distinct from verified read-only forms."""
    if profile not in SWITCH_PROFILES:
        return CommandPolicy("unavailable", "交换机设备类型未识别，无法加载规则，需要二次确认。")
    catalog = _catalog(profile)
    if catalog is None:
        return CommandPolicy("unavailable", "交换机规则库未就绪，无法核验该命令，默认需要二次确认。")
    if len(command) > 8192 or re.search(r"[\x00-\x1f\x7f;&<>`$\\]", command):
        return CommandPolicy("confirm", "包含组合命令、输出写入或未核验语法，需要二次确认。")
    value, problem = _split_filter(normalize(command))
    if problem:
        return CommandPolicy("confirm", problem)
    if profile == "cisco_ios" and value.startswith("do "):
        value = value[3:]
    if not value:
        return CommandPolicy("unknown")
    candidates = tuple(_group_candidates(catalog, value))
    rules = candidates[0] if candidates else ()
    # A more specific read-only family must not hide a pending variant from a
    # broader family. Pending also takes precedence over navigation/exceptions.
    pending = tuple(r for group in candidates for r in group if r.status == 2 and (normalize(r.syntax) == value or matches(r.syntax, value)))
    if pending:
        return CommandPolicy("confirm", _explain(pending, value))

    # These operations were already covered by execution regression tests but
    # lack a matching family in the imported manuals. Keep their confirmation
    # explicit instead of relying on the now-permissive unknown fallback.
    if profile == "cisco_ios" and re.match(r"(?:no\s+)?hostname(?:\s|$)", value):
        return CommandPolicy("confirm", "修改或恢复设备名称，改变管理配置，需要二次确认。")
    if profile == "cisco_ios" and re.match(r"clear counters(?:\s|$)", value):
        return CommandPolicy("confirm", "清除接口统计，丢失历史观测数据，需要二次确认。")
    if profile == "zte_zxr10" and re.match(r"reboot(?:\s|$)", value):
        return CommandPolicy("confirm", "重启设备，可能中断业务并丢失未保存配置，需要二次确认。")

    # Branch rules precede general query rules; a display/show prefix is never
    # itself permission to execute. These are the explicit reviewed exceptions.
    if value.startswith("display diagnostic-information"):
        return CommandPolicy("confirm", "集中诊断可能保存文件或改变采集状态，需要二次确认。")
    if re.match(r"(?:ping|tracert|traceroute|debug|debugging|test|capture|packet-capture)(?:\s|$)", value):
        return CommandPolicy("confirm", "启动主动测试、调试或采集，可能产生流量、占用资源或改变运行状态，需要二次确认。")
    if profile == "zte_zxr10" and re.match(r"show cos-queue(?:\s|$)", value):
        interface = INTERFACES[profile]
        if re.fullmatch(r"show cos-queue\s+" + interface, value, re.I):
            return CommandPolicy("read_only")
        if re.fullmatch(r"show cos-queue\s+" + interface + r"\s+clear", value, re.I):
            return CommandPolicy("confirm", "清除队列统计，丢失历史观测数据，需要二次确认。")
        return CommandPolicy("confirm", "队列命令包含清除统计的风险分支，当前参数无法区分，需要二次确认。")
    if profile == H3C_COMWARE and re.match(r"monitor (?:process|thread)(?:\s|$)", value):
        if re.fullmatch(r"monitor (?:process|thread) dumbtty(?: iteration \d+)?(?: (?:chassis \d+ )?slot \d+(?: cpu \d+)?)?", value):
            return CommandPolicy("read_only")
        return CommandPolicy("confirm", "交互监视模式可终止进程或线程，需要二次确认；固定 dumbtty 统计形式可只读。")
    if profile == H3C_COMWARE and value.startswith("public-key local export "):
        forms = r"public-key local export (?:dsa|ecdsa|rsa)(?: name [\w.-]+)? (?:openssh|ssh1|ssh2)"
        if re.fullmatch(forms, value):
            # Only RSA has the ssh1 branch in the reviewed source.
            if value.endswith(" ssh1") and not value.startswith("public-key local export rsa"):
                return CommandPolicy("confirm", "公钥导出命令的编码参数未核验，需要二次确认。")
            return CommandPolicy("read_only")
        return CommandPolicy("confirm", "导出公钥可能创建或覆盖文件，或参数尚未核验，需要二次确认。")

    navigation = {"?", "help", "quit", "exit", "end", "return"}
    if profile in (H3C_COMWARE, "huawei_vrp"):
        navigation |= {"system-view"}
    else:
        navigation |= {"configure terminal"}
    if value in navigation:
        changed = tuple(r for group in candidates for r in group if r.status and (normalize(r.syntax) == value or matches(r.syntax, value)))
        if changed:
            return CommandPolicy("confirm", _explain(changed, value))
        return CommandPolicy("read_only")
    if match_query(value, profile):
        return CommandPolicy("read_only")
    # Known Comware interface spelling may omit the space between type/number.
    if profile == H3C_COMWARE and re.fullmatch(r"display interface\s+" + _H3C_INTERFACE + r"(?: brief)?", value, re.I):
        return CommandPolicy("read_only")

    if not rules:
        return CommandPolicy("unknown")
    # A mixed family may have view-dependent or optional side effects. Only the
    # reviewed explicit branch exceptions above may bypass that ambiguity.
    if any(rule.status for rule in rules):
        if profile == "huawei_vrp" and value == "format bulkascii":
            return CommandPolicy("confirm", "修改 bulk-file 导出格式配置，需要二次确认；该形式不是存储格式化。")
        return CommandPolicy("confirm", _explain(rules, value))
    if any(matches(rule.syntax, value) for rule in rules):
        return CommandPolicy("read_only")
    return CommandPolicy("unknown")


@dataclass(frozen=True, slots=True)
class CatalogMatch:
    state: str  # risk, pending, unknown, unavailable
    rule: Rule | None = None
    match_type: str = "none"  # syntax or family; not a device compatibility claim


def risk_catalog_match(command: str, profile: str) -> CatalogMatch:
    """Read-only provenance for side-channel grading, never execution permission.

    The command gate remains the authority. A family-only match does not claim
    that the precise syntax/model/view has been verified on the device.
    """
    catalog = _catalog(profile)
    if catalog is None:
        return CatalogMatch("unavailable")
    if len(command) > 8192 or re.search(r"[\x00-\x1f\x7f;&<>`$\\]", command):
        return CatalogMatch("unknown")
    value, problem = _split_filter(normalize(command))
    if problem:
        return CatalogMatch("unknown")
    if profile == "cisco_ios" and value.startswith("do "):
        value = value[3:]
    candidates = tuple(_group_candidates(catalog, value))
    matched = [r for group in candidates for r in group
               if r.status and (normalize(r.syntax) == value or matches(r.syntax, value))]
    pending = next((r for r in matched if r.status == 2), None)
    if pending:
        return CatalogMatch("pending", pending, "syntax")
    if matched:
        return CatalogMatch("risk", matched[0], "syntax")
    nearest = candidates[0] if candidates else ()
    pending = next((r for r in nearest if r.status == 2), None)
    if pending:
        return CatalogMatch("pending", pending, "family")
    if nearest and all(r.status == 1 for r in nearest):
        return CatalogMatch("risk", nearest[0], "family")
    return CatalogMatch("unknown")
