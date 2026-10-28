"""Lossless manual behavior graph plus a conservative completion projection.

Every reviewed variant remains in ``variants`` and in exactly one behavior.
Bad OCR headings and parameter grammar remain evidence, never executable text.
Runtime selection policy belongs to switch_factory, not this graph.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
import gzip
import json
from pathlib import Path
import re
from types import MappingProxyType

from auditbox.device_profiles import REFERENCE_PROFILES
from auditbox.capability_packages import data_path, revision
from auditbox.switch_commands import behavior_for, normalize


RELATED_BEHAVIORS = {
    "system": ("logs", "config", "interface"),
    "interface": ("vlan", "neighbor", "aggregation", "logs"),
    "vlan": ("mac", "stp", "interface", "aggregation"),
    "mac": ("vlan", "arp", "interface"),
    "stp": ("interface", "vlan", "aggregation", "logs"),
    "neighbor": ("interface", "aggregation", "vlan"),
    "aggregation": ("interface", "neighbor", "stp"),
    "route": ("arp", "interface", "ospf", "bgp"),
    "arp": ("route", "mac", "interface"),
    "ipv6": ("interface", "logs"),
    "ospf": ("route", "interface", "logs"),
    "bgp": ("route", "interface", "logs"),
    "isis": ("route", "interface", "logs"),
    "rip": ("route", "interface", "logs"),
    "acl": ("interface", "logs", "config"),
    "qos": ("interface", "logs", "config"),
    "dhcp": ("arp", "interface", "logs"),
    "authentication": ("logs", "interface", "config"),
    "security": ("logs", "config", "interface"),
    "reliability": ("interface", "logs", "route"),
    "multicast": ("interface", "vlan", "route"),
    "mpls": ("route", "interface", "logs"),
    "vxlan": ("bgp", "mac", "route", "interface"),
    "config": ("system", "logs"), "logs": ("system", "interface"),
    "storage": ("system", "logs"), "management": ("system", "logs"),
    "wireless": ("interface", "logs"), "other": ("system", "logs"),
}


def completion_root(title: str) -> str:
    # Strip view annotations and grammar, not arbitrary command parameters.
    value = re.split(r"[（(\[<{]", normalize(title), maxsplit=1)[0].strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9 +_.:-]*(?:-[a-z0-9 +_.:-]*)*", value):
        return ""
    words = value.split()
    if len(words) > 9 or any(w in {"the", "commands", "will", "should", "please", "specified"} for w in words):
        return ""
    # Named metavariables accidentally retained in extracted command headings.
    for index, word in enumerate(words):
        if index and re.fullmatch(r"(?:\w+-)*(?:id|number|name|address|prefix|mask|length|value|range|seconds)", word):
            # Known literal keywords are not parameter names.
            if word not in {"router-id", "vlan-id", "system-id", "address", "name", "range"}:
                words = words[:index]
                break
    return " ".join(words)


@dataclass(frozen=True, slots=True)
class ManualVariant:
    id: str
    title: str
    syntax: str
    risk: bool
    reason: str
    models: str
    views: str
    sources: str
    behavior: str


@dataclass(frozen=True)
class SwitchCatalog:
    profile: str
    variants: tuple[ManualVariant, ...]
    behavior_graph: Mapping[str, tuple[ManualVariant, ...]]
    roots: Mapping[str, tuple[ManualVariant, ...]]


def catalog_for(profile: str) -> SwitchCatalog:
    return _catalog_version(profile, revision(profile))


@lru_cache(maxsize=3)
def _catalog_version(profile: str, version: str) -> SwitchCatalog:
    if profile not in REFERENCE_PROFILES:
        raise ValueError("Unsupported switch profile")
    path = Path(__file__).resolve().parents[1] / "reference/data" / f"{profile}.json.gz"
    path = data_path(profile, "reference", path)
    with gzip.open(path, "rt", encoding="utf-8") as source:
        data = json.load(source)
    if data["schema"] != 1 or data["profile"] != profile:
        raise ValueError("Invalid switch catalog")
    strings = data["strings"]
    variants, graph, roots = [], defaultdict(list), defaultdict(list)
    for row in data["entries"]:
        title = strings[row[1]]
        syntax = strings[row[2]]
        normalized_syntax = normalize(syntax)
        behavior = behavior_for(title)
        variant = ManualVariant(row[0], title, syntax, bool(row[3]),
                                *(strings[row[i]] for i in (4, 5, 6, 7)), behavior)
        variants.append(variant)
        graph[behavior].append(variant)
        root = completion_root(title)
        if root:
            roots[root].append(variant)
            # Negation is an independent completion, not an erased variant.
            for negative in ("undo", "no", "default"):
                if normalized_syntax.startswith(negative + " ") and not root.startswith(negative + " "):
                    roots[negative + " " + root].append(variant)
    return SwitchCatalog(profile, tuple(variants),
                         MappingProxyType({k: tuple(v) for k, v in graph.items()}),
                         MappingProxyType({k: tuple(v) for k, v in roots.items()}))


catalog_for.cache_clear = _catalog_version.cache_clear


def root_metadata(profile: str, root: str) -> dict:
    return _root_metadata_version(profile, root, revision(profile))


@lru_cache(maxsize=512)
def _root_metadata_version(profile: str, root: str, version: str) -> dict:
    rows = catalog_for(profile).roots.get(root, ())
    views, models = [], []
    for row in rows:
        view = row.views.split("型号限制：", 1)[0].replace("执行视图：", "").strip()
        if view and view not in views:
            views.append(view)
        for match in re.finditer(r"手册范围=([^；\n]+)(?:；版本=([^；\n]+))?", row.models):
            label = match[1] + (f" / {match[2]}" if match[2] else "")
            if label not in models:
                models.append(label)
    exact_direction = [r for r in rows if normalize(r.syntax) == root or normalize(r.syntax).startswith(root + " ")]
    return {"view": "、".join(views)[:240], "models": "；".join(models)[:320],
            "variant_count": len(rows),
            "risk_reason": next((r.reason for r in exact_direction if r.risk), "")[:240]}


root_metadata.cache_clear = _root_metadata_version.cache_clear
