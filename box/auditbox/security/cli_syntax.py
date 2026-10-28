"""Match a bounded subset of vendor manual grammar, without executing it.

Unsupported/OCR-damaged grammar does not become a wildcard. This matcher is
only used to prove a read-only form (or choose a risk explanation). A failed
match is not proof of safety; the rule engine separately decides whether an
unknown command may pass or a known risk family still requires confirmation.
"""

from __future__ import annotations

from functools import lru_cache
import ipaddress
import re


_LEX = re.compile(r"&<\d+-\d+>|<[^<>]+>|[{}\[\]|*]|[^\s{}\[\]|*]+")
_INTERFACE_TYPE = re.compile(
    r"(?:gigabitethernet|tengigabitethernet|twenty-fivegige|fortygige|hundredgige|"
    r"fastethernet|ethernet|xgigabitethernet|(?:10|25|40|100|200|400)?ge|xge|gi|te|fa|eth|"
    r"gei|xgei|xxgei|fei|ce|vlan-interface|vlanif|vlan|bridge-aggregation|"
    r"route-aggregation|eth-trunk|port-channel|smartgroup|loopback|tunnel|meth|m-gigabitethernet)", re.I
)
_INTERFACE_NUMBER = re.compile(r"\d+(?:/\d+){0,4}(?:\.\d+)?")
_IDENTIFIER = re.compile(r"[\w./][\w./:#@-]{0,254}", re.ASCII)
_MAC = re.compile(r"(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}|[0-9a-f]{4}(?:[.-][0-9a-f]{4}){2}", re.I)


class UnsupportedSyntax(ValueError):
    pass


@lru_cache(maxsize=4096)
def _parse(syntax: str) -> tuple | None:
    if len(syntax) > 4096 or re.search(r"[^\x20-\x7e]", syntax):
        return None
    syntax = re.sub(r"(&<\d+-\d+>)", r" \1 ", syntax)
    tokens = _LEX.findall(syntax)
    if len(tokens) > 512:
        return None
    cursor = 0

    def group(end: str = "", depth: int = 0) -> tuple:
        nonlocal cursor
        if depth > 12:
            raise UnsupportedSyntax()
        choices, sequence = [], []
        while cursor < len(tokens):
            token = tokens[cursor]
            cursor += 1
            if token in ("]", "}"):
                if token != end:
                    raise UnsupportedSyntax()
                break
            if token == "|":
                if not sequence:
                    raise UnsupportedSyntax()
                choices.append(("seq", tuple(sequence)))
                sequence = []
                continue
            if token in ("[", "{"):
                child = group("]" if token == "[" else "}", depth + 1)
                sequence.append(("repeat", child, 0, 1) if token == "[" else child)
            elif token == "*":
                if not sequence or sequence[-1][0] not in {"repeat", "choice"}:
                    raise UnsupportedSyntax()
                child = sequence.pop()
                if child[0] == "repeat":
                    child = child[1]
                sequence.append(("repeat", child, 0, 16))
            elif re.fullmatch(r"&<\d+-\d+>", token):
                lo, hi = map(int, re.findall(r"\d+", token))
                if not sequence or not 0 <= lo <= hi <= 32:
                    raise UnsupportedSyntax()
                sequence.append(("repeat", sequence.pop(), lo, hi))
            elif any(c in token for c in "()&\\`") or token == "...":
                raise UnsupportedSyntax()
            else:
                sequence.append(("token", token.casefold()))
        else:
            if end:
                raise UnsupportedSyntax()
        if not sequence:
            raise UnsupportedSyntax()
        choices.append(("seq", tuple(sequence)))
        return choices[0] if len(choices) == 1 else ("choice", tuple(choices))

    try:
        result = group()
        return result if cursor == len(tokens) else None
    except (UnsupportedSyntax, ValueError, RecursionError):
        return None


def _words(command: str) -> tuple[str, ...]:
    # Comware accepts both GigabitEthernet1/0/1 and GigabitEthernet 1/0/1.
    result = []
    for word in command.casefold().split():
        parts = re.fullmatch(r"(.+?)(\d+(?:/\d+){0,4}(?:\.\d+)?)", word)
        if parts and _INTERFACE_TYPE.fullmatch(parts[1]):
            result.extend(parts.groups())
        else:
            result.append(word)
    return tuple(result)


def _parameter(token: str) -> str | None:
    if token.startswith("<") and token.endswith(">"):
        name = "-".join(token[1:-1].split())
        # Angle-bracket numeric ranges are not interchangeable with all tokens.
        return re.sub(r"(-(id|number|num|count|index|size|length|value|name|address|addr|prefix|mask))\d+$", r"\1", name)
    if token in {"filename", "directory", "file", "file-url", "regular-expression", "regexp", "regex"}:
        return token
    parameter = re.fullmatch(r"([a-z][\w-]*-(?:id|number|num|count|index|size|length|value|name|address|addr|prefix|mask|type|list|range))\d*", token)
    if parameter:
        return parameter[1]
    return None


def _consume(token: str, words: tuple[str, ...], pos: int) -> frozenset[int]:
    if pos >= len(words):
        return frozenset()
    # Parameters must satisfy their type; the placeholder's literal name must
    # not accidentally be accepted as a real numeric/interface argument.
    word = words[pos]
    name = _parameter(token)
    if name is None:
        return frozenset({pos + 1}) if token == word else frozenset()
    result: set[int] = set()
    if name == "interface-type":
        valid = bool(_INTERFACE_TYPE.fullmatch(word))
    elif name in {"interface-number", "interface-num"}:
        valid = bool(_INTERFACE_NUMBER.fullmatch(word))
    elif "interface" in name and name.endswith(("name", "list", "interface")):
        valid = bool(_INTERFACE_TYPE.fullmatch(word)) and pos + 1 < len(words) and bool(_INTERFACE_NUMBER.fullmatch(words[pos + 1]))
        if valid:
            result.add(pos + 2)
        return frozenset(result)
    elif "mac" in name and name.endswith(("address", "addr")):
        valid = bool(_MAC.fullmatch(word))
    elif name.endswith(("address", "addr", "prefix", "mask")):
        try:
            ipaddress.ip_network(word, strict=False)
            valid = True
        except ValueError:
            valid = name.endswith("mask") and word.isdecimal() and int(word) <= 128
    elif re.fullmatch(r"\d+-\d+", name):
        lo, hi = map(int, name.split("-"))
        valid = word.isdecimal() and lo <= int(word) <= hi
    elif name.endswith(("-id", "-number", "-num", "-count", "-index", "-size", "-length", "-value")) or name in {"count", "number"}:
        valid = word.isdecimal() and len(word) <= 10 and int(word) <= 4294967295
        if "vlan" in name:
            valid = valid and 1 <= int(word) <= 4094
    elif name.endswith("-type"):
        # An unspecified enum is not a general wildcard.
        valid = False
    elif name.endswith(("-name", "-list", "-range")) or name in {"filename", "directory", "file", "file-url", "word", "string", "name", "regular-expression", "regexp", "regex"}:
        valid = bool(_IDENTIFIER.fullmatch(word))
    else:
        valid = False
    if valid:
        result.add(pos + 1)
    return frozenset(result)


@lru_cache(maxsize=4096)
def matches(syntax: str, command: str) -> bool:
    node = _parse(syntax)
    words = _words(command)
    if node is None or not words or len(words) > 128:
        return False
    budget = 12000

    @lru_cache(maxsize=None)
    def visit(part: tuple, start: int) -> frozenset[int]:
        nonlocal budget
        budget -= 1
        if budget < 0:
            raise UnsupportedSyntax()
        kind = part[0]
        if kind == "token":
            return _consume(part[1], words, start)
        if kind == "choice":
            return frozenset(p for child in part[1] for p in visit(child, start))
        if kind == "seq":
            positions = frozenset({start})
            for child in part[1]:
                positions = frozenset(p for s in positions for p in visit(child, s))
                if not positions:
                    break
            return positions
        positions, result = frozenset({start}), set()
        if part[2] == 0:
            result.add(start)
        for iteration in range(1, part[3] + 1):
            positions = frozenset(p for s in positions for p in visit(part[1], s) if p > s)
            if iteration >= part[2]:
                result.update(positions)
            if not positions:
                break
        return frozenset(result)

    try:
        return len(words) in visit(node, 0)
    except (UnsupportedSyntax, RecursionError, ValueError):
        return False
