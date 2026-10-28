"""Lazy, paginated access to reviewed manual variants (never executable templates)."""

from __future__ import annotations

import gzip
import json
from functools import lru_cache
from pathlib import Path

from auditbox.device_profiles import REFERENCE_PROFILES
from auditbox.capability_packages import data_path, revision

DATA_DIR = Path(__file__).with_name("data")


def _catalog(profile: str) -> dict:
    return _catalog_version(profile, revision(profile))


@lru_cache(maxsize=1)
def _catalog_version(profile: str, version: str) -> dict:
    if profile not in REFERENCE_PROFILES:
        raise ValueError("该设备子类型尚无手册速查目录")
    with gzip.open(data_path(profile, "reference", DATA_DIR / f"{profile}.json.gz"), "rt", encoding="utf-8") as source:
        catalog = json.load(source)
    if catalog["schema"] != 1 or catalog["profile"] != profile:
        raise ValueError("手册速查目录版本不匹配")
    # Reuse interned strings rather than duplicating the source text for every
    # variant. Only one brand is retained; models do not load or consume this data.
    catalog["folded"] = [value.casefold() for value in catalog["strings"]]
    return catalog


_catalog.cache_clear = _catalog_version.cache_clear
_catalog.cache_info = _catalog_version.cache_info


def search_reference(
    profile: str, *, q: str = "", risk: str = "all", page: int = 1,
    limit: int = 20, command: str | None = None,
) -> dict:
    if risk not in {"all", "risk", "safe"} or page < 1 or not 1 <= limit <= 50:
        raise ValueError("无效的速查筛选或分页参数")
    catalog = _catalog(profile)
    strings, folded = catalog["strings"], catalog["folded"]
    terms = q.strip().casefold().split()
    matches = []
    for entry in catalog["entries"]:
        if risk != "all" and bool(entry[3]) != (risk == "risk"):
            continue
        if command is not None and strings[entry[1]] != command:
            continue
        # Keywords can match syntax, risk reason, model/version, view or source.
        if terms and not all(any(term in folded[entry[index]] for index in (1, 2, 4, 5, 6, 7)) for term in terms):
            continue
        matches.append(entry)
    start = (page - 1) * limit
    if command is None:
        groups: dict[str, dict] = {}
        for entry in matches:
            name = strings[entry[1]]
            group = groups.setdefault(name, {"command": name, "variants": 0, "risk": 0, "safe": 0})
            group["variants"] += 1
            group["risk" if entry[3] else "safe"] += 1
        ordered = sorted(groups, key=str.casefold)
        total = len(ordered)
        items = [groups[name] for name in ordered[start:start + limit]]
    else:
        total = len(matches)
        items = [{
            "id": entry[0], "command": strings[entry[1]], "syntax": strings[entry[2]],
            "risk": "risk" if entry[3] else "safe", "reason": strings[entry[4]] if entry[3] else "",
            "models": strings[entry[5]], "views": strings[entry[6]], "sources": strings[entry[7]],
        } for entry in matches[start:start + limit]]
    metadata = {key: catalog[key] for key in ("brand", "profile", "policy", "command_names", "variants", "risk", "safe")}
    return {"catalog": metadata, "items": items, "total": total, "matching_variants": len(matches),
            "page": page, "limit": limit, "mode": "variants" if command is not None else "commands"}
