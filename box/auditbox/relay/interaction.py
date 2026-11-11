"""Recognize non-command prompts on the current rendered terminal row."""

import re

# Match the whole current row; text inside a command echo is not a prompt.
_AUTH = re.compile(r"^\s*(?:[\w.()-]+\s+)?(?:please\s+(?:enter|input)\s+)?(?:username|user\s*name|login|用户名|账号)\s*[:：]\s*$", re.I)
_SECRET = re.compile(r"^[^\r\n#>$]{0,160}(?:password|passwd|passphrase|口令|密码|验证码|token|secret)[^\r\n:：]{0,96}[:：]\s*$", re.I)
_PAGER = re.compile(r"^\s*(?:[-—─]{2,}\s*more\b[^\r\n]{0,120}|more:\s*(?:\([^\r\n]*\))?)\s*$", re.I)
_CONFIRM = re.compile(r"(?:\[(?:y/n|yes/no|confirm)\]|\((?:y/n|yes/no)\))\s*[:?：？]?\s*$", re.I)


def prompt_mode(line: str | None) -> str | None:
    if line is None:
        return None
    if _SECRET.fullmatch(line):
        return "secret"
    if _AUTH.fullmatch(line):
        return "auth"
    if _PAGER.fullmatch(line):
        return "pager"
    if _CONFIRM.search(line):
        return "interaction"
    return None
