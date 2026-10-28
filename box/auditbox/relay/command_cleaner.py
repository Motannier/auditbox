"""Normalize terminal input into auditable command text."""

import re

# ANSI/VT100 转义序列：CSI 序列、OSC 序列、单字符转义
_ESC_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-9;?]*[A-Za-z]"
    r"|\][^\x07\x1b]*\x07"
    r"|[^[]"
    r")"
)


def clean_command(raw: str, *, strip_leading: bool = True) -> str:
    """
    把原始按键流还原成用户实际提交的命令文本。
    处理：退格/DEL、ANSI 转义序列、其余控制字符。
    """
    text = _ESC_RE.sub("", raw)

    result: list[str] = []
    for ch in text:
        if ch in ("\x7f", "\x08"):
            if result:
                result.pop()
        elif ch == "\x15":
            result.clear()
        elif ch < " " and ch != "\t":
            pass
        else:
            result.append(ch)

    cleaned = "".join(result)
    if strip_leading:
        return cleaned.strip()
    return cleaned.rstrip()
