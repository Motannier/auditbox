"""设备类型检测：从 SSH 连接后的初始输出识别设备厂商/OS。

检测优先级：
  1. banner 关键词（最可靠，厂商通常在欢迎信息里明确标注）
  2. prompt 形态（fallback，用于只显示 prompt 不显示 banner 的设备）

返回的 device_type 同时作为预测转移表的命名空间 key，
确保 Cisco 命令和 Huawei 命令分开学习。
"""

import re

# (device_type, [正则列表])  —— 按精确度由高到低排列
_BANNER_RULES = [
    ("cisco_ios",   [r"Cisco IOS Software", r"IOS-XE Software",
                     r"Cisco Internetwork Operating System"]),
    ("cisco_nxos",  [r"Cisco Nexus Operating System", r"NX-OS Software"]),
    ("cisco_asa",   [r"Cisco Adaptive Security Appliance", r"Cisco ASA"]),
    ("huawei_vrp",  [r"Huawei Versatile Routing Platform", r"VRP \(R\) software",
                     r"HUAWEI\s+\S+\s+Versatile"]),
    ("h3c",         [r"H3C Comware", r"Comware Software, Version"]),
    ("zte_zxr10",   [r"\bZXR10\b", r"ZTE\s+.*(?:Switch|Router)"]),
    ("juniper",     [r"JUNOS Base OS", r"Juniper Networks"]),
    ("fortinet",    [r"FortiGate", r"FortiOS"]),
    ("linux",       [r"Ubuntu", r"CentOS", r"Debian", r"Red Hat", r"Arch Linux",
                     r"Welcome to Linux"]),
    ("windows",     [r"Microsoft Windows", r"Windows Server"]),
]

# (device_type, prompt 正则)  —— 匹配单行 stripped 文本
_PROMPT_RULES = [
    ("cisco_ios",   r"^[\w\-]+[#>]\s*$"),           # hostname# 或 hostname>
    ("huawei_vrp",  r"^[<\[][\w\-]+[>\]]$"),         # <hostname> 或 [hostname]
    ("linux",       r".+[@:].*[#\$]\s*$"),           # user@host:path$ 或 root#
]

DISPLAY_NAMES = {
    "cisco_ios":  "Cisco IOS",
    "cisco_nxos": "Cisco NX-OS",
    "cisco_asa":  "Cisco ASA",
    "huawei_vrp": "Huawei VRP",
    "h3c":        "H3C Comware",
    "zte_zxr10":  "ZTE ZXR10",
    "juniper":    "Juniper JUNOS",
    "fortinet":   "FortiGate",
    "linux":      "Linux",
    "windows":    "Windows",
    "unknown":    "未识别设备",
}

# 收集到此字节数后强制输出结果（即使未命中，也避免无限等待）
DETECT_BYTES = 4096


def detect(text: str) -> str:
    """从设备初始输出文本检测设备类型，返回 device_type key。"""
    for dtype, patterns in _BANNER_RULES:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return dtype

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for dtype, pat in _PROMPT_RULES:
            if re.match(pat, line):
                return dtype

    return "unknown"


def display_name(device_type: str) -> str:
    return DISPLAY_NAMES.get(device_type, device_type)
