"""Small executable query vocabulary, separate from the complete manual graph.

These fully matched forms prove a read-only query. Unknown commands may also
pass under the execution policy, but are not verified query recommendations.
Manual roots and arbitrary suffixes are not proof of safety. The base forms
are the existing common-operation guides; parameterized forms use each vendor's
own interface/VLAN syntax. Do not translate Cisco plurals into ZTE commands.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re


BEHAVIOR_LABELS = {
    "system": "设备与资源", "config": "配置核对", "logs": "日志与告警",
    "interface": "接口排查", "vlan": "VLAN 与交换", "mac": "MAC 学习",
    "stp": "生成树", "neighbor": "邻居发现", "aggregation": "链路聚合",
    "route": "路由与转发", "arp": "ARP 邻居", "ipv6": "IPv6",
    "ospf": "OSPF", "bgp": "BGP", "isis": "IS-IS", "rip": "RIP",
    "multicast": "组播", "acl": "访问控制", "qos": "QoS",
    "authentication": "认证与接入", "security": "安全防护", "management": "管理服务",
    "dhcp": "DHCP", "reliability": "可靠性", "mpls": "MPLS / VPN",
    "vxlan": "VXLAN / EVPN", "storage": "存储与文件", "wireless": "无线",
    "other": "其他设备行为",
}

# The graph is not restricted to this vocabulary. This is the executable policy.
QUERY_GROUPS = {
    "huawei_vrp": {
        "system": ("display version", "display device", "display cpu-usage", "display memory-usage"),
        "config": ("display current-configuration", "display saved-configuration"),
        "logs": ("display logbuffer",),
        "interface": ("display interface {interface}", "display interface brief", "display interface", "display counters error"),
        "vlan": ("display vlan {vlan}", "display vlan", "display port vlan"),
        "mac": ("display mac-address",),
        "stp": ("display stp brief",),
        "neighbor": ("display lldp neighbor brief",),
        "aggregation": ("display eth-trunk",),
        "route": ("display ip routing-table", "display ip interface brief"),
        "arp": ("display arp",),
        "ipv6": ("display ipv6 routing-table", "display ipv6 interface brief"),
        "ospf": ("display ospf peer", "display ospf routing"),
        "bgp": ("display bgp peer",),
        "acl": ("display acl all",),
    },
    "cisco_ios": {
        "system": ("show version", "show inventory", "show processes cpu sorted", "show processes memory sorted"),
        "config": ("show running-config", "show startup-config"),
        "logs": ("show logging",),
        "interface": ("show interfaces {interface}", "show interfaces status", "show interfaces counters errors", "show interfaces"),
        "vlan": ("show vlan id {vlan}", "show vlan brief", "show interfaces trunk"),
        "mac": ("show mac address-table",),
        "stp": ("show spanning-tree summary",),
        "neighbor": ("show cdp neighbors", "show lldp neighbors"),
        "aggregation": ("show etherchannel summary",),
        "route": ("show ip route", "show ip interface brief"),
        "arp": ("show ip arp",),
        "ipv6": ("show ipv6 route", "show ipv6 interface brief"),
        "ospf": ("show ip ospf neighbor",),
        "bgp": ("show ip bgp summary",),
        "acl": ("show access-lists",),
    },
    "zte_zxr10": {
        "system": ("show version", "show system-info", "show processor", "show processor details"),
        "config": ("show running-config", "show startup-config"),
        "logs": ("show logging alarm", "show logging configuration"),
        "interface": ("show interface {interface}", "show interface brief", "show interface"),
        "vlan": ("show vlan id {vlan}", "show vlan", "show vlan trunk"),
        "mac": ("show mac table",),
        "stp": ("show spantree mst-config", "show spantree inconsistentports"),
        "neighbor": ("show lldp neighbor",),
        "aggregation": ("show lacp sys-id", "show lacp neighbors"),
        "route": ("show ip forwarding route", "show ip interface brief"),
        "arp": ("show arp",),
    },
}

# Valid single-interface tokens, never arbitrary text or a pasted command.
INTERFACES = {
    "huawei_vrp": r"(?:GigabitEthernet|GE|XGigabitEthernet|XGE|(?:10|25|40|100)GE|Eth-Trunk|Vlanif|LoopBack|MEth)\s*\d+(?:/\d+){0,3}(?:\.\d+)?",
    "cisco_ios": r"(?:GigabitEthernet|Gi|FastEthernet|Fa|TenGigabitEthernet|Te|TwentyFiveGigE|FortyGigabitEthernet|HundredGigE|Ethernet|Eth|Port-channel|Po|Vlan|Loopback)\s*\d+(?:/\d+){0,3}(?:\.\d+)?",
    "zte_zxr10": r"(?:gei|xgei|xxgei|ce|smartgroup|vlan|loopback|fei)[-_]?\d+(?:/\d+){0,3}(?:\.\d+)?",
}


def normalize(command: str) -> str:
    return " ".join(command.casefold().split())


def single_command(command: str) -> bool:
    return bool(command.strip()) and not re.search(r"[\x00-\x1f\x7f;|&<>`$\\]", command)


@dataclass(frozen=True)
class Query:
    command: str
    behavior: str
    pattern: re.Pattern


@lru_cache(maxsize=3)
def queries(profile: str) -> tuple[Query, ...]:
    result = []
    for behavior, commands in QUERY_GROUPS.get(profile, {}).items():
        for command in commands:
            pattern = re.escape(command).replace(r"\{interface\}", "(?P<interface>" + INTERFACES[profile] + ")")
            pattern = pattern.replace(r"\{vlan\}", r"(?P<vlan>\d{1,4})")
            result.append(Query(command, behavior, re.compile(pattern, re.I)))
    return tuple(result)


def match_query(command: str, profile: str) -> tuple[Query, dict[str, str]] | None:
    if not single_command(command):
        return None
    value = normalize(command)
    # IOS exec show commands inside config mode must be prefixed with 'do'.
    if profile == "cisco_ios" and value.startswith("do "):
        value = value[3:]
    for query in queries(profile):
        match = query.pattern.fullmatch(value)
        if match:
            parameters = {k: v for k, v in match.groupdict().items() if v}
            if "vlan" in parameters and not 1 <= int(parameters["vlan"]) <= 4094:
                return None
            return query, parameters
    return None


@lru_cache(maxsize=32768)
def behavior_for(command: str) -> str:
    """Assign every manual command to a behavior without discarding risky nodes."""
    value = normalize(command)
    terms = set(re.split(r"[\s_-]+", value)) | set(value.split())
    for behavior, keywords in (
        ("vxlan", {"vxlan", "evpn", "vni", "nve"}),
        ("ospf", {"ospf", "ospfv3"}), ("bgp", {"bgp"}), ("isis", {"isis"}),
        ("rip", {"rip", "ripng"}), ("ipv6", {"ipv6", "nd"}),
        ("mpls", {"mpls", "ldp", "l2vpn", "l3vpn", "vpls", "vrf", "vpn"}),
        ("multicast", {"igmp", "mld", "pim", "msdp", "multicast"}),
        ("dhcp", {"dhcp", "dhcpv6"}),
        ("stp", {"stp", "rstp", "mstp", "spantree", "spanning"}),
        ("neighbor", {"lldp", "cdp"}), ("arp", {"arp"}),
        ("aggregation", {"lacp", "etherchannel", "smartgroup", "aggregation", "eth-trunk"}),
        ("mac", {"mac"}), ("vlan", {"vlan", "switchport", "trunk", "pvid"}),
        ("qos", {"qos", "traffic", "car", "queue", "dscp", "8021p", "police"}),
        ("acl", {"acl", "access-list", "access", "packet-filter"}),
        ("authentication", {"aaa", "radius", "tacacs", "hwtacacs", "dot1x", "authentication", "authorization"}),
        ("reliability", {"vrrp", "bfd", "track", "erps", "rrpp", "stack", "vpc"}),
        ("security", {"ssh", "ssl", "ipsec", "ike", "pki", "security", "attack"}),
        ("logs", {"logging", "logbuffer", "info-center", "alarm", "debug", "debugging"}),
        ("config", {"configuration", "config", "save", "write", "commit"}),
        ("storage", {"file", "flash", "dir", "delete", "format", "sftp", "ftp"}),
        ("wireless", {"wlan", "ssid", "radio", "ap"}),
        ("management", {"snmp", "ntp", "clock", "telnet", "terminal", "user"}),
        ("interface", {"interface", "interfaces", "shutdown", "description", "duplex", "speed", "mtu", "port"}),
        ("route", {"route", "routing", "ip", "ping", "traceroute", "tracert"}),
        ("system", {"version", "device", "cpu", "memory", "processor", "inventory", "reboot", "reload", "system"}),
    ):
        if terms & keywords:
            return behavior
    return "other"
