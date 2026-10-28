#!/usr/bin/env python3
"""Build runtime H3C command-view metadata from the existing workbook.

The workbook is the reviewed local command inventory.  Reading XLSX through
the Python standard library keeps this build step usable on Windows, Linux,
and macOS without adding an Excel runtime dependency.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"x": MAIN_NS, "r": REL_NS}


def normalize_match_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s*[（(][^（）()]*[）)]\s*$", "", value).strip()
    positions = [value.find(char) for char in "[{<" if char in value]
    if positions:
        value = value[: min(positions)].strip()
    return value.rstrip("- ")


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference)
    if not letters:
        raise ValueError(f"Invalid XLSX cell reference: {reference!r}")
    result = 0
    for character in letters.group(0):
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def _sheet_path(archive: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    sheet = next(
        (
            item
            for item in workbook.findall("x:sheets/x:sheet", NS)
            if item.get("name") == sheet_name
        ),
        None,
    )
    if sheet is None:
        raise ValueError(f"Workbook does not contain sheet {sheet_name!r}")
    relation_id = sheet.get(f"{{{REL_NS}}}id")
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relationship = next(
        (
            item
            for item in relationships.findall(f"{{{PACKAGE_REL_NS}}}Relationship")
            if item.get("Id") == relation_id
        ),
        None,
    )
    if relationship is None or not relationship.get("Target"):
        raise ValueError(f"Sheet relationship is missing for {sheet_name!r}")
    target = relationship.get("Target", "").lstrip("/")
    return posixpath.normpath(posixpath.join("xl", target.removeprefix("xl/")))


def _shared_strings(archive: zipfile.ZipFile) -> tuple[str, ...]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return ()
    return tuple(
        "".join(node.text or "" for node in item.findall(".//x:t", NS))
        for item in root.findall("x:si", NS)
    )


def _cell_value(cell: ET.Element, shared_strings: tuple[str, ...]) -> str:
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", NS))
    value = cell.findtext("x:v", default="", namespaces=NS)
    if cell_type == "s" and value:
        return shared_strings[int(value)]
    return value


def read_sheet_rows(workbook_path: Path, sheet_name: str) -> list[dict[str, str]]:
    with zipfile.ZipFile(workbook_path) as archive:
        shared_strings = _shared_strings(archive)
        root = ET.fromstring(archive.read(_sheet_path(archive, sheet_name)))

    raw_rows: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", NS):
        cells: dict[int, str] = {}
        for cell in row.findall("x:c", NS):
            reference = cell.get("r", "")
            cells[_column_index(reference)] = _cell_value(cell, shared_strings)
        if cells:
            values = [""] * (max(cells) + 1)
            for index, value in cells.items():
                values[index] = value
            raw_rows.append(values)

    if not raw_rows:
        return []
    headers = raw_rows[0]
    return [
        {
            header: values[index] if index < len(values) else ""
            for index, header in enumerate(headers)
            if header
        }
        for values in raw_rows[1:]
    ]


def build_module(workbook_path: Path) -> str:
    rows = read_sheet_rows(workbook_path, "命令总表")
    if len(rows) != 2815:
        raise ValueError(f"Expected 2815 command rows, found {len(rows)}")
    command_ids = [row.get("命令ID", "") for row in rows]
    if not all(command_ids) or len(set(command_ids)) != len(command_ids):
        raise ValueError("Workbook command IDs are missing or duplicated")

    views_by_command: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        match_key = normalize_match_key(
            row.get("归一化命令", "") or row.get("命令标题", "")
        )
        view = re.sub(r"[ \t]+", " ", row.get("视图", "")).strip()
        if match_key and view:
            views_by_command[match_key].add(view)

    if len(views_by_command) != 2570:
        raise ValueError(
            f"Expected 2570 unique command roots, found {len(views_by_command)}"
        )

    general_view_markers = ("任意视图", "用户视图", "系统视图")
    specialized = sorted(
        command
        for command, views in views_by_command.items()
        if not any(
            marker in view
            for view in views
            for marker in general_view_markers
        )
    )

    lines = [
        '"""Generated H3C command-view metadata from the reviewed workbook.',
        "",
        "Generated by tools/build_h3c_views.py. Do not edit by hand.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        f"COMMAND_VIEW_COUNT = {len(views_by_command)}",
        "",
        "COMMAND_VIEWS: dict[str, tuple[str, ...]] = {",
    ]
    for command in sorted(views_by_command):
        views = tuple(sorted(views_by_command[command]))
        lines.append(f"    {command!r}: {views!r},")
    lines.extend(["}", "", "SPECIALIZED_VIEW_MATCH_KEYS = frozenset({"])
    for command in specialized:
        lines.append(f"    {command!r},")
    lines.extend([
        "})",
        "",
        "__all__ = [",
        "    'COMMAND_VIEW_COUNT',",
        "    'COMMAND_VIEWS',",
        "    'SPECIALIZED_VIEW_MATCH_KEYS',",
        "]",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    parser.add_argument("output_module", type=Path)
    args = parser.parse_args()
    output = build_module(args.workbook)
    args.output_module.parent.mkdir(parents=True, exist_ok=True)
    args.output_module.write_text(output, encoding="utf-8", newline="\n")
    print(f"Generated {args.output_module} from the existing reviewed workbook")


if __name__ == "__main__":
    main()
