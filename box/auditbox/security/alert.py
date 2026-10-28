"""Classify interactive terminal commands before they are executed."""

from __future__ import annotations

import posixpath
import re
import shlex
from pathlib import PurePosixPath
from typing import Literal, TypedDict

from auditbox.device_profiles import H3C_COMWARE, LINUX, SWITCH_PROFILES, normalize_device_profile
from auditbox.security.switch_rules import confirmation_reason

ACTION_CONFIRM = "confirm"
ACTION_BLOCK = "block"


class CommandAlert(TypedDict):
    level: Literal["danger"]
    action: Literal["confirm", "block"]
    reason: str
    lock_terminal: bool


_COMMON_BLOCK_PATTERNS = [
    (re.compile(r"\berase\s+startup-config\b|\bwrite\s+erase\b", re.I),
     "擦除网络设备启动配置"),
    (re.compile(r"\bexecute\s+factoryreset\b", re.I),
     "恢复防火墙出厂设置"),
]

_LINUX_BLOCK_PATTERNS = [
    (re.compile(r"(?:>|\btee\b[^;&|]*)\s*/etc/(?:passwd|shadow|sudoers)\b", re.I),
     "覆盖关键认证或授权文件"),
    (re.compile(r":\(\)\s*\{.*:\|:.*\}", re.I),
     "Fork 炸弹将耗尽系统进程资源"),
]

_H3C_BLOCK_PATTERNS = [
    (re.compile(r"^\s*format\s+(?:flash|slot\d+#flash)", re.I),
     "格式化 Comware 设备 Flash 存储"),
    (re.compile(r"\breset\s+(?:saved-configuration|factory(?:-configuration)?)\b", re.I),
     "擦除 Comware 配置或恢复出厂设置"),
    (re.compile(r"^\s*delete\s+/unreserved\b", re.I),
     "不可恢复地删除 Comware 设备文件"),
]

_LINUX_CONFIRM_PATTERNS = [
    (re.compile(r"\b(shutdown|poweroff|halt|reboot)\b", re.I), "设备重启或关机"),
    (re.compile(r"\bsystemctl\s+(restart|stop|disable|mask)\b", re.I), "变更 systemd 服务运行状态"),
    (re.compile(r"\bkubectl\b[^;&|]*\bdelete(?:\s|$)", re.I), "删除 Kubernetes 资源"),
    (re.compile(r"\bkill\b[^;&|]*(?:-9\b|-KILL\b)", re.I), "强制结束进程"),
    (re.compile(r"\bchmod\b[^;&|]*(?:-R|--recursive)[^;&|]*\b777\b|\bchmod\b[^;&|]*\b777\b[^;&|]*(?:-R|--recursive)", re.I),
     "递归赋予所有用户写权限"),
    (re.compile(r"\bchown\b[^;&|]*(?:-R|--recursive)", re.I), "递归修改文件所有者"),
    (re.compile(r"\biptables\s+(?:-F\b|--flush\b)|\bnft\s+flush\s+ruleset\b", re.I),
     "清空防火墙规则"),
]

_H3C_CONFIRM_PATTERNS = [
    (re.compile(r"^\s*reboot(?:\s|$)", re.I), "重启 H3C 交换机"),
    (re.compile(r"^\s*save(?:\s|$)", re.I), "将当前 Comware 配置写入启动配置"),
    (re.compile(r"^\s*shutdown(?:\s|$)", re.I), "关闭当前接口或协议功能"),
    (re.compile(r"^\s*reset\s+counters\b", re.I), "清除 Comware 运行统计计数"),
    (
        re.compile(
            r"^\s*undo\s+(?:vlan|interface|ip\s+route-static|ospf|stp|link-aggregation)\b",
            re.I,
        ),
        "删除可能影响转发的 Comware 配置",
    ),
]

_SHELLS = {"bash", "dash", "ksh", "sh", "zsh"}
_RAW_DEVICE_PREFIXES = (
    "/dev/sd",
    "/dev/hd",
    "/dev/vd",
    "/dev/xvd",
    "/dev/nvme",
    "/dev/mmcblk",
    "/dev/mapper/",
    "/dev/dm-",
)


def check(command: str, *, device_profile: str = LINUX) -> CommandAlert | None:
    """Return a confirmation/blocking decision, or ``None`` for a normal command."""
    command = command.strip()
    if not command:
        return None

    profile = normalize_device_profile(device_profile) or LINUX
    for pattern, reason in _COMMON_BLOCK_PATTERNS:
        if pattern.search(command):
            return _alert(ACTION_BLOCK, reason)

    from auditbox.command_packages import registered, grade
    if registered(profile):
        result = grade(command, profile)
        return _alert(ACTION_CONFIRM, result['reason']) if result['action'] == 'confirm' else None
    if profile in SWITCH_PROFILES:
        return _check_switch(command, profile)
    if profile != LINUX:
        # A removed dynamic package is not a Linux adapter. Existing sessions
        # retain their selected profile and must not silently change policy.
        return _alert(ACTION_CONFIRM, "设备规则包未注册或已移除，需要二次确认；不会回退到 Linux 审核")
    return _check_linux(command)


def _check_h3c(command: str) -> CommandAlert | None:
    for pattern, reason in _H3C_BLOCK_PATTERNS:
        if pattern.search(command):
            return _alert(ACTION_BLOCK, reason)

    for pattern, reason in _H3C_CONFIRM_PATTERNS:
        if pattern.search(command):
            return _alert(ACTION_CONFIRM, reason)
    reason = confirmation_reason(command, H3C_COMWARE)
    return _alert(ACTION_CONFIRM, reason) if reason else None


def _check_switch(command: str, profile: str) -> CommandAlert | None:
    # Preserve hard blocks even when a switch receives several submitted lines.
    # Do not apply this switch-specific parsing to the existing Linux pathway.
    for part in re.split(r"[;\r\n]+", command):
        if profile == H3C_COMWARE:
            for pattern, reason in _H3C_BLOCK_PATTERNS:
                if pattern.search(part):
                    return _alert(ACTION_BLOCK, reason)
    if profile == H3C_COMWARE:
        return _check_h3c(command)
    reason = confirmation_reason(command, profile)
    return _alert(ACTION_CONFIRM, reason) if reason else None


def _check_linux(command: str) -> CommandAlert | None:
    for pattern, reason in _LINUX_BLOCK_PATTERNS:
        if pattern.search(command):
            return _alert(ACTION_BLOCK, reason)

    confirm_reason: str | None = None
    for words in _command_segments(command):
        nested = _nested_shell_command(words)
        if nested:
            nested_alert = check(nested, device_profile=LINUX)
            if nested_alert and nested_alert["action"] == ACTION_BLOCK:
                return nested_alert
            if nested_alert:
                confirm_reason = nested_alert["reason"]

        executable, args = _unwrap_command(words)
        if not executable:
            continue
        name = PurePosixPath(executable).name.lower()

        if name == "rm":
            recursive, force, targets = _rm_details(args)
            if recursive and force:
                if any(_is_root_delete_target(target) for target in targets):
                    return _alert(
                        ACTION_BLOCK,
                        "递归强制删除根目录",
                    )
                if "*" in targets:
                    return _alert(
                        ACTION_CONFIRM,
                        "递归强制删除当前目录全部内容",
                    )
                confirm_reason = "递归强制删除文件或目录"
        elif name == "find" and "-delete" in args:
            confirm_reason = "批量删除搜索结果"
        elif name == "shred":
            confirm_reason = "不可恢复地删除文件内容"
        elif name == "dd":
            output = next((arg[3:] for arg in args if arg.startswith("of=")), "")
            if output.startswith(_RAW_DEVICE_PREFIXES):
                return _alert(ACTION_BLOCK, "直接写入块设备")
        elif name.startswith("mkfs") or name in {"mke2fs", "blkdiscard"}:
            return _alert(ACTION_BLOCK, "格式化或丢弃块设备数据")
        elif name == "wipefs" and any(arg in {"-a", "--all"} for arg in args):
            return _alert(ACTION_BLOCK, "清除块设备文件系统签名")

    for pattern, reason in _LINUX_CONFIRM_PATTERNS:
        if pattern.search(command):
            confirm_reason = confirm_reason or reason

    return _alert(ACTION_CONFIRM, confirm_reason) if confirm_reason else None


def _alert(
    action: Literal["confirm", "block"],
    reason: str,
) -> CommandAlert:
    return {
        "level": "danger",
        "action": action,
        "reason": reason,
        "lock_terminal": False,  # Compatibility field; command blocks never lock the box.
    }


def _command_segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return [[command]]

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token and all(char in ";&|" for char in token):
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(token)
    return [segment for segment in segments if segment]


def _unwrap_command(words: list[str]) -> tuple[str, list[str]]:
    words = list(words)
    while words and "=" in words[0] and not words[0].startswith(("/", "./")):
        words.pop(0)
    while words:
        name = PurePosixPath(words[0]).name.lower()
        if name == "command":
            words.pop(0)
            continue
        if name == "env":
            words.pop(0)
            while words and (words[0].startswith("-") or "=" in words[0]):
                words.pop(0)
            continue
        if name == "sudo":
            words.pop(0)
            while words and words[0].startswith("-"):
                option = words.pop(0)
                if option in {"-u", "--user", "-g", "--group", "-h", "--host"} and words:
                    words.pop(0)
            continue
        break
    return (words[0], words[1:]) if words else ("", [])


def _nested_shell_command(words: list[str]) -> str:
    executable, args = _unwrap_command(words)
    if PurePosixPath(executable).name.lower() not in _SHELLS:
        return ""
    try:
        index = args.index("-c")
    except ValueError:
        return ""
    return args[index + 1] if index + 1 < len(args) else ""


def _rm_details(args: list[str]) -> tuple[bool, bool, list[str]]:
    recursive = False
    force = False
    targets: list[str] = []
    options_done = False
    for arg in args:
        if arg == "--":
            options_done = True
            continue
        if not options_done and arg.startswith("-"):
            recursive = recursive or arg == "--recursive" or (not arg.startswith("--") and "r" in arg.lower())
            force = force or arg == "--force" or (not arg.startswith("--") and "f" in arg.lower())
            continue
        targets.append(arg)
    return recursive, force, targets


def _is_root_delete_target(target: str) -> bool:
    if not target.startswith("/"):
        return False
    normalized = posixpath.normpath(target)
    return normalized == "/" or normalized.startswith("/*")
