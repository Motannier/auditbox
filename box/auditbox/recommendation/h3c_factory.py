"""Deterministic H3C Comware behavior graph and command assistance.

All official commands from the S5130-EI R35xx command-reference PDFs are
classified in :mod:`h3c_catalog` and attached to the complete behavior graph,
including query, configuration, recovery, specialized-view, and risky actions.
Terminal recommendations are a separately filtered projection of that graph.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType

from auditbox.recommendation.h3c_catalog import (
    BARE_DISPLAY_COMMANDS,
    BEHAVIOR_LABELS as CATALOG_BEHAVIOR_LABELS,
    COMMAND_CATALOG,
    COMMAND_COUNT,
    CONTEXT_REQUIRED_MATCH_KEYS,
    VIEW_ENTRY_BEHAVIORS,
)
from auditbox.recommendation.h3c_views import (
    COMMAND_VIEWS,
    SPECIALIZED_VIEW_MATCH_KEYS,
)
from auditbox.security.alert import check as alert_check


MAX_RECOMMENDATIONS = 5


_COMPLETION_PRIORITY = (
    "system-view",
    "quit",
    "return",
    "save",
    "interface",
    "interface range",
    "vlan",
    "ping",
    "tracert",
    "display version",
    "display device",
    "display cpu-usage",
    "display memory",
    "display interface brief",
    "display vlan brief",
    "display mac-address",
    "display stp",
    "display link-aggregation summary",
    "display lldp neighbor-information",
    "display ip interface brief",
    "display ip routing-table",
    "display arp",
    "display logbuffer",
)


_SUB_BEHAVIOR_LABELS: dict[str, str] = {
    "system.health.cpu": "CPU 与进程负载",
    "system.health.memory": "内存与进程占用",
    "ip.dhcp.server": "DHCP 服务器",
    "ip.dhcp.relay": "DHCP 中继",
    "ip.dhcp.snooping": "DHCP Snooping",
    "ip.dhcp.ipv6": "DHCPv6",
    "routing.ripng": "RIPng 动态路由",
    "multicast.igmp": "IGMP Snooping",
    "multicast.mld": "MLD Snooping",
    "multicast.pim": "PIM Snooping",
    "multicast.vlan": "组播 VLAN",
    "security.authentication.aaa": "AAA 与 RADIUS",
    "security.authentication.ldap": "LDAP 认证",
    "security.authentication.hwtacacs": "HWTACACS 认证",
    "security.access.dot1x": "802.1X 接入认证",
    "security.access.mac": "MAC 地址认证",
    "security.crypto.ipsec": "IPsec 与 IKE",
    "security.crypto.pki": "PKI 与证书",
    "security.crypto.ssh": "SSH 与公钥",
    "security.crypto.ssl": "SSL 策略与 TLS",
    "reliability.bfd": "BFD 快速检测",
    "reliability.vrrp": "VRRP 网关冗余",
    "reliability.track": "Track 联动",
    "reliability.ring": "ERPS、RRPP 与 Smart Link",
    "reliability.oam": "CFD 与链路 OAM",
    "observability.logging": "日志与信息中心",
    "observability.snmp": "SNMP 网络管理",
    "observability.nqa": "NQA 主动测量",
    "observability.time": "NTP 与时间同步",
    "observability.traffic": "流量采集与镜像",
    "automation.cwmp": "CWMP 远程管理",
    "automation.netconf": "NETCONF 自动化",
    "automation.eaa": "EAA 与脚本自动化",
}


_SECTION_SUB_BEHAVIOR_LABELS: dict[str, str] = {
    "system.access.cli": "CLI 与命令行",
    "system.access.login": "设备登录与用户线",
    "system.access.rbac": "RBAC 权限控制",
    "system.file_transfer.ftp_client": "FTP 客户端",
    "system.file_transfer.ftp_server": "FTP 服务器",
    "system.file_transfer.tftp": "TFTP 文件传输",
    "system.config.configuration": "配置文件与回滚",
    "system.config.filesystem": "设备文件系统",
    "system.config.emergency_shell": "应急 Shell",
    "system.config.software": "软件与启动文件升级",
    "automation.scripting": "Python 与 Tcl 脚本",
    "switching.interface.logical": "LoopBack 与逻辑接口",
    "switching.interface.layer2": "二层以太网接口",
    "switching.interface.range": "接口批量配置",
    "switching.interface.isolation": "端口隔离",
    "switching.mac.information": "MAC Information",
    "switching.spanning_tree.loop_detection": "环路检测",
    "switching.vlan.mvrp": "MVRP VLAN 注册",
    "switching.vlan.private": "Private VLAN",
    "switching.vlan.voice": "Voice VLAN",
    "switching.vlan.port": "基于端口的 VLAN",
    "switching.vlan.mac": "基于 MAC 的 VLAN",
    "switching.vlan.qinq": "QinQ",
    "switching.vlan.subnet": "基于 IP 子网的 VLAN",
    "switching.vlan.protocol": "基于协议的 VLAN",
    "switching.vlan.group": "VLAN 组",
    "switching.vlan.super": "Super VLAN",
    "switching.vlan.mapping": "VLAN 映射",
    "switching.discovery.l2pt": "L2PT 二层协议透传",
    "ip.arp.gratuitous": "免费 ARP",
    "ip.arp.proxy": "代理 ARP",
    "ip.arp.snooping": "ARP Snooping",
    "ip.addressing.optimization": "IP 性能优化",
    "ip.addressing.irdp": "IRDP 路由器发现",
    "ip.addressing.udp_helper": "UDP Helper",
    "ip.addressing.forwarding": "IP 转发基础",
    "ip.addressing.http_redirect": "HTTP 重定向",
    "ip.dhcp.client": "DHCP 客户端",
    "ip.dhcp.common": "DHCP 公共能力",
    "ip.dhcp.ipv6.server": "DHCPv6 服务器",
    "ip.dhcp.ipv6.relay": "DHCPv6 中继",
    "ip.dhcp.ipv6.snooping": "DHCPv6 Snooping",
    "ip.dhcp.ipv6.client": "DHCPv6 客户端",
    "ip.dhcp.ipv6.common": "DHCPv6 公共能力",
    "routing.policy": "路由策略",
    "routing.policy.ipv6": "IPv6 路由策略",
    "routing.pbr": "IPv4 策略路由",
    "routing.pbr.ipv6": "IPv6 策略路由",
    "routing.static": "IPv4 静态路由",
    "routing.static.ipv6": "IPv6 静态路由",
    "routing.ospfv3": "OSPFv3 动态路由",
    "multicast.pim.ipv6": "IPv6 PIM Snooping",
    "multicast.vlan.ipv6": "IPv6 组播 VLAN",
    "policy.acl.time_range": "ACL 时间段",
    "policy.qos.classifier": "QoS 流分类",
    "policy.qos.behavior": "QoS 流行为",
    "policy.qos.buffer": "数据缓冲区",
    "policy.qos.scheduler": "队列调度",
    "policy.qos.wfq": "加权公平队列",
    "policy.qos.wred": "WRED 拥塞避免",
    "policy.qos.wrr": "WRR 加权轮询",
    "policy.qos.car": "CAR 限速",
    "policy.qos.priority_mapping": "优先级映射",
    "policy.qos.trust": "端口优先级信任",
    "policy.qos.shaping": "流量整形",
    "policy.qos.rate_limit": "端口限速",
    "policy.qos.strict_priority": "严格优先级队列",
    "policy.qos.queue_statistics": "端口队列统计",
    "security.authentication.portal": "Portal 认证",
    "security.authentication.password": "Password Control",
    "security.authentication.web": "Web 认证",
    "security.authentication.local_user": "本地用户",
    "security.authentication.radius_server": "RADIUS 服务器",
    "security.authentication.user_profile": "User Profile",
    "security.access.port_security": "端口安全",
    "security.access.ra_guard": "RA Guard",
    "security.access.arp_detection": "ARP Detection",
    "security.access.ip_source_guard": "IP Source Guard",
    "security.access.arp_protection": "ARP 攻击防护",
    "security.access.mff": "MFF",
    "security.access.nd_detection": "ND Detection",
    "security.access.savi": "SAVI",
    "security.access.attack_defense": "攻击检测与防范",
    "security.crypto.ssh.client": "SSH 客户端",
    "security.crypto.ike": "IKE",
    "security.crypto.ikev2": "IKEv2",
    "security.crypto.public_key": "公钥管理",
    "security.crypto.fips": "FIPS 模式",
    "security.crypto.engine": "加密引擎",
    "reliability.cfd": "CFD 连接故障管理",
    "reliability.ethernet_oam": "以太网 OAM",
    "reliability.erps": "ERPS 环网保护",
    "reliability.rrpp": "RRPP 环网保护",
    "reliability.vrrp.ipv6": "IPv6 VRRP",
    "reliability.smart_link": "Smart Link",
    "reliability.process_placement": "进程分布优化",
    "reliability.dldp": "DLDP 单向链路检测",
    "reliability.monitor_link": "Monitor Link",
    "observability.process": "进程监控与维护",
    "observability.poe": "PoE 供电",
    "observability.rmon": "RMON 监控",
    "observability.sflow": "sFlow 流量采集",
    "observability.mirroring": "端口与流镜像",
    "observability.vcf": "VCF Fabric",
    "observability.nqa.server": "NQA 服务器",
    "observability.debug": "系统维护与调试",
    "observability.capture": "Packet Capture",
    "observability.telemetry.dial_out": "gRPC Dial-out",
}


_SECTION_BEHAVIORS: dict[str, str] = {
    "CLI命令": "system.access.cli",
    "登录设备命令": "system.access.login",
    "RBAC配置命令": "system.access.rbac",
    "FTP客户端配置命令": "system.file_transfer.ftp_client",
    "FTP服务器配置命令": "system.file_transfer.ftp_server",
    "TFTP配置命令": "system.file_transfer.tftp",
    "配置文件管理命令": "system.config.configuration",
    "文件系统管理命令": "system.config.filesystem",
    "应急Shell配置命令": "system.config.emergency_shell",
    "软件升级配置命令": "system.config.software",
    "EAA配置命令": "automation.eaa",
    "CWMP配置命令": "automation.cwmp",
    "NETCONF配置命令": "automation.netconf",
    "Tcl配置命令": "automation.scripting",
    "Python配置命令": "automation.scripting",
    "LoopBack接口、NULL接口和InLoopBack接口配置命令": "switching.interface.logical",
    "二层以太网接口的配置命令": "switching.interface.layer2",
    "接口批量配置命令": "switching.interface.range",
    "端口隔离配置命令": "switching.interface.isolation",
    "MAC Information配置命令": "switching.mac.information",
    "环路检测配置命令": "switching.spanning_tree.loop_detection",
    "MVRP配置命令": "switching.vlan.mvrp",
    "Private VLAN配置命令": "switching.vlan.private",
    "Voice VLAN配置命令": "switching.vlan.voice",
    "基于端口的VLAN配置命令": "switching.vlan.port",
    "基于MAC的VLAN配置命令": "switching.vlan.mac",
    "QinQ配置命令": "switching.vlan.qinq",
    "基于IP子网的VLAN配置命令": "switching.vlan.subnet",
    "基于协议的VLAN配置命令": "switching.vlan.protocol",
    "VLAN组配置命令": "switching.vlan.group",
    "Super VLAN配置命令": "switching.vlan.super",
    "VLAN映射配置命令": "switching.vlan.mapping",
    "L2PT配置命令": "switching.discovery.l2pt",
    "免费ARP配置命令": "ip.arp.gratuitous",
    "代理ARP配置命令": "ip.arp.proxy",
    "ARP Snooping配置命令": "ip.arp.snooping",
    "IP性能优化配置命令": "ip.addressing.optimization",
    "IRDP配置命令": "ip.addressing.irdp",
    "UDP Helper配置命令": "ip.addressing.udp_helper",
    "IP转发基础配置命令": "ip.addressing.forwarding",
    "HTTP重定向配置命令": "ip.addressing.http_redirect",
    "DHCP客户端配置命令": "ip.dhcp.client",
    "BOOTP客户端配置命令": "ip.dhcp.client",
    "DHCP公共命令": "ip.dhcp.common",
    "DHCP服务器配置命令": "ip.dhcp.server",
    "DHCP中继配置命令": "ip.dhcp.relay",
    "DHCP Snooping配置命令": "ip.dhcp.snooping",
    "DHCPv6服务器配置命令": "ip.dhcp.ipv6.server",
    "DHCPv6中继配置命令": "ip.dhcp.ipv6.relay",
    "DHCPv6 Snooping配置命令": "ip.dhcp.ipv6.snooping",
    "DHCPv6客户端配置命令": "ip.dhcp.ipv6.client",
    "DHCPv6公共命令": "ip.dhcp.ipv6.common",
    "路由策略公共配置命令": "routing.policy",
    "IPv4路由策略配置命令": "routing.policy",
    "IPv6路由策略配置命令": "routing.policy.ipv6",
    "策略路由配置命令": "routing.pbr",
    "IPv6策略路由配置命令": "routing.pbr.ipv6",
    "静态路由配置命令": "routing.static",
    "IPv6静态路由配置命令": "routing.static.ipv6",
    "OSPFv3配置命令": "routing.ospfv3",
    "IPv6 PIM Snooping命令": "multicast.pim.ipv6",
    "IPv6组播VLAN配置命令": "multicast.vlan.ipv6",
    "时间段配置命令": "policy.acl.time_range",
    "定义类的命令": "policy.qos.classifier",
    "定义流行为的命令": "policy.qos.behavior",
    "数据缓冲区配置命令": "policy.qos.buffer",
    "队列调度策略配置命令": "policy.qos.scheduler",
    "加权公平队列配置命令": "policy.qos.wfq",
    "WRED配置命令": "policy.qos.wred",
    "加权轮询队列配置命令": "policy.qos.wrr",
    "全局CAR配置命令": "policy.qos.car",
    "优先级映射表配置命令": "policy.qos.priority_mapping",
    "端口优先级信任模式配置命令": "policy.qos.trust",
    "流量整形配置命令": "policy.qos.shaping",
    "限速配置命令": "policy.qos.rate_limit",
    "严格优先级队列配置命令": "policy.qos.strict_priority",
    "端口队列统计配置命令": "policy.qos.queue_statistics",
    "Portal配置命令": "security.authentication.portal",
    "RADIUS配置命令": "security.authentication.aaa",
    "RADIUS服务器配置命令": "security.authentication.radius_server",
    "802.1X配置命令": "security.access.dot1x",
    "802.1X Client功能配置命令": "security.access.dot1x",
    "MAC地址认证配置命令": "security.access.mac",
    "ISP域中实现AAA配置命令": "security.authentication.aaa",
    "HWTACACS配置命令": "security.authentication.hwtacacs",
    "Password Control 配置命令": "security.authentication.password",
    "LDAP配置命令": "security.authentication.ldap",
    "Web认证配置命令": "security.authentication.web",
    "本地用户配置命令": "security.authentication.local_user",
    "User Profile配置命令": "security.authentication.user_profile",
    "端口安全配置命令": "security.access.port_security",
    "RA Guard配置命令": "security.access.ra_guard",
    "ARP Detection配置命令": "security.access.arp_detection",
    "IP Source Guard配置命令": "security.access.ip_source_guard",
    "ARP防止IP报文攻击配置命令": "security.access.arp_protection",
    "源MAC地址固定的ARP攻击检测配置命令": "security.access.arp_protection",
    "ARP报文限速配置命令": "security.access.arp_protection",
    "ARP自动扫描、固化配置命令": "security.access.arp_protection",
    "ARP报文源MAC地址一致性检查配置命令": "security.access.arp_protection",
    "ARP主动确认配置命令": "security.access.arp_protection",
    "授权ARP配置命令": "security.access.arp_protection",
    "ARP网关保护配置命令": "security.access.arp_protection",
    "ARP过滤保护配置命令": "security.access.arp_protection",
    "配置ARP报文源IP地址检查功能配置命令": "security.access.arp_protection",
    "MFF配置命令": "security.access.mff",
    "ND Detection配置命令": "security.access.nd_detection",
    "ND协议报文源MAC地址一致性检查命令": "security.access.nd_detection",
    "SAVI配置命令": "security.access.savi",
    "攻击检测与防范配置命令": "security.access.attack_defense",
    "SSH客户端配置命令": "security.crypto.ssh.client",
    "SSH服务器端配置命令": "security.crypto.ssh",
    "IKE配置命令": "security.crypto.ike",
    "IKEv2配置命令": "security.crypto.ikev2",
    "IPsec配置命令": "security.crypto.ipsec",
    "PKI配置命令": "security.crypto.pki",
    "SSL配置命令": "security.crypto.ssl",
    "公钥管理配置命令": "security.crypto.public_key",
    "FIPS配置命令": "security.crypto.fips",
    "加密引擎配置命令": "security.crypto.engine",
    "CFD配置命令": "reliability.cfd",
    "以太网OAM配置命令": "reliability.ethernet_oam",
    "ERPS配置命令": "reliability.erps",
    "RRPP配置命令": "reliability.rrpp",
    "IPv4 VRRP配置命令": "reliability.vrrp",
    "IPv6 VRRP配置命令": "reliability.vrrp.ipv6",
    "BFD配置命令": "reliability.bfd",
    "Smart Link配置命令": "reliability.smart_link",
    "进程分布优化配置命令": "reliability.process_placement",
    "DLDP配置命令": "reliability.dldp",
    "Track配置命令": "reliability.track",
    "Monitor Link配置命令": "reliability.monitor_link",
    "NQA客户端配置命令": "observability.nqa",
    "NQA服务器端命令": "observability.nqa.server",
    "信息中心配置命令": "observability.logging",
    "SNMP配置命令": "observability.snmp",
    "进程监控和维护命令": "observability.process",
    "NTP配置命令": "observability.time",
    "SNTP配置命令": "observability.time",
    "PoE配置命令": "observability.poe",
    "RMON配置命令": "observability.rmon",
    "sFlow配置命令": "observability.sflow",
    "端口镜像配置命令": "observability.mirroring",
    "流镜像配置命令": "observability.mirroring",
    "VCF Fabric配置命令": "observability.vcf",
    "系统维护与调试命令": "observability.debug",
    "Packet Capture配置命令": "observability.capture",
    "gRPC Dial-in模式配置命令": "observability.telemetry",
    "gRPC Dial-out模式配置命令": "observability.telemetry.dial_out",
}


def _section_behavior(behavior: str, section: str) -> str:
    # The same manual section label is reused for ping/tracert and for general
    # debugging.  Preserve the catalog's explicit diagnostics domain there.
    if behavior == "network.diagnostics":
        return behavior
    return _SECTION_BEHAVIORS.get(section, behavior)

BEHAVIOR_LABELS = MappingProxyType({
    **CATALOG_BEHAVIOR_LABELS,
    **_SUB_BEHAVIOR_LABELS,
    **_SECTION_SUB_BEHAVIOR_LABELS,
})


@dataclass(frozen=True)
class FlowEdge:
    target: str
    priority: int
    reason: str


@dataclass(frozen=True)
class CommandContext:
    action: str
    parameters: dict[str, str]


@dataclass(frozen=True)
class H3CViewFrame:
    """One confirmed Comware CLI view entered by the terminal session."""

    entry_command: str
    action: str | None
    parameters: dict[str, str]
    view_names: tuple[str, ...]


@dataclass
class H3CSessionState:
    """Incremental Comware view stack; independent from command history."""

    view_stack: list[H3CViewFrame]

    def __init__(self) -> None:
        self.view_stack = []

    def current_context(self) -> CommandContext | None:
        if not self.view_stack:
            return None
        frame = self.view_stack[-1]
        if frame.action is None:
            return None
        return CommandContext(frame.action, dict(frame.parameters))

    def current_view_names(self) -> tuple[str, ...]:
        if not self.view_stack:
            return ("用户视图",)
        return self.view_stack[-1].view_names


def _edges(*items: tuple[str, int, str]) -> tuple[FlowEdge, ...]:
    return tuple(FlowEdge(*item) for item in items)


_NODES: dict[str, str] = {
    "system.users": "display users",
    "system.user_interface": "display user-interface",
    "system.roles": "display role",
    "system.local_users": "display local-user",
    "system.device": "display device",
    "system.version": "display version",
    "system.cpu": "display cpu-usage",
    "system.cpu_configuration": "display cpu-usage configuration",
    "system.cpu_history": "display cpu-usage history",
    "system.process_cpu": "display process cpu",
    "system.memory": "display memory",
    "system.memory_threshold": "display memory-threshold",
    "system.process_memory": "display process memory",
    "system.environment": "display environment",
    "system.current_config": "display current-configuration",
    "system.saved_config": "display saved-configuration",
    "system.startup": "display startup",
    "system.ftp_server": "display ftp-server",
    "system.ftp_users": "display ftp-user",
    "system.boot_loader": "display boot-loader",
    "interface.current": "display interface {interface}",
    "interface.all": "display interface",
    "interface.range": "display interface range",
    "interface.transceiver_alarms": "display transceiver alarm interface",
    "vlan.current": "display vlan {vlan}",
    "vlan.brief": "display vlan brief",
    "vlan.all": "display vlan",
    "mac.table": "display mac-address",
    "mac.current": "display mac-address {mac_address}",
    "mac.statistics": "display mac-address statistics",
    "stp.status": "display stp",
    "stp.root": "display stp root",
    "aggregation.summary": "display link-aggregation summary",
    "aggregation.verbose": "display link-aggregation verbose",
    "aggregation.lacp": "display lacp system-id",
    "lldp.neighbors": "display lldp neighbor-information",
    "lldp.local": "display lldp local-information",
    "lldp.status": "display lldp status",
    "wan.pppoe_clients": "display pppoe-relay client-information format",
    "wan.pppoe_strategy": "display pppoe-relay client-information strategy",
    "wan.pppoe_statistics": "display pppoe-relay statistics",
    "irf.status": "display irf",
    "irf.configuration": "display irf configuration",
    "irf.links": "display irf link",
    "irf.topology": "display irf topology",
    "irf.mad": "display mad",
    "ip.interface": "display ip interface",
    "ip.interface_brief": "display ip interface brief",
    "ip.arp": "display arp",
    "ip.arp_target": "display arp {target}",
    "ip.arp_timer": "display arp timer aging",
    "ip.fib": "display fib",
    "ipv6.interface": "display ipv6 interface",
    "ipv6.neighbors": "display ipv6 neighbors all",
    "ipv6.routes": "display ipv6 routing-table",
    "dhcp.server_statistics": "display dhcp server statistics",
    "dhcp.server_conflict": "display dhcp server conflict",
    "dhcp.server_database": "display dhcp server database",
    "dhcp.server_expired": "display dhcp server expired",
    "dhcp.server_free": "display dhcp server free-ip",
    "dhcp.server_in_use": "display dhcp server ip-in-use",
    "dhcp.server_pool": "display dhcp server pool",
    "dhcp.server_pool_named": "display dhcp server pool {dhcp_pool}",
    "dhcp.relay_statistics": "display dhcp relay statistics",
    "dhcp.relay_check": "display dhcp relay check mac-address",
    "dhcp.relay_clients": "display dhcp relay client-information",
    "dhcp.relay_information": "display dhcp relay information",
    "dhcp.relay_servers": "display dhcp relay server-address",
    "dhcp.snooping_bindings": "display dhcp snooping binding",
    "dhcp.snooping_database": "display dhcp snooping binding database",
    "dhcp.snooping_packets": "display dhcp snooping packet statistics",
    "dhcp.snooping_trust": "display dhcp snooping trust",
    "dhcp.snooping_information": "display dhcp snooping information all",
    "dhcpv6.server": "display ipv6 dhcp server",
    "dhcpv6.server_database": "display ipv6 dhcp server database",
    "dhcpv6.server_in_use": "display ipv6 dhcp server ip-in-use",
    "dhcpv6.server_statistics": "display ipv6 dhcp server statistics",
    "dhcpv6.snooping_bindings": "display ipv6 dhcp snooping binding",
    "dhcpv6.snooping_database": "display ipv6 dhcp snooping binding database",
    "dhcpv6.snooping_packets": "display ipv6 dhcp snooping packet statistics",
    "dhcpv6.snooping_trust": "display ipv6 dhcp snooping trust",
    "dns.domains": "display dns domain",
    "dns.hosts": "display dns host",
    "dns.servers": "display dns server",
    "route.table": "display ip routing-table",
    "route.target": "display ip routing-table {target}",
    "route.ipv6_target": "display ipv6 routing-table {ipv6_target}",
    "route.ipv6": "display ipv6 routing-table",
    "route.ipv6_summary": "display ipv6 routing-table summary",
    "route.ipv6_statistics": "display ipv6 routing-table statistics",
    "route.rip": "display ip routing-table protocol rip",
    "route.summary": "display ip routing-table summary",
    "route.statistics": "display ip routing-table statistics",
    "ospf.peers": "display ospf peer",
    "ospf.peers_process": "display ospf {ospf_process} peer",
    "ospf.interfaces": "display ospf interface",
    "ospf.interfaces_process": "display ospf {ospf_process} interface",
    "ospf.routes": "display ospf routing",
    "ospf.routes_process": "display ospf {ospf_process} routing",
    "ospf.lsdb": "display ospf lsdb",
    "ospf.lsdb_process": "display ospf {ospf_process} lsdb",
    "ospfv3.peers_process": "display ospfv3 {ospf_process} peer",
    "ospfv3.interfaces_process": "display ospfv3 {ospf_process} interface",
    "ospfv3.routes_process": "display ospfv3 {ospf_process} routing",
    "ospfv3.lsdb_process": "display ospfv3 {ospf_process} lsdb",
    "rip.status": "display rip",
    "rip.neighbors": "display rip {rip_process} neighbor",
    "rip.routes": "display rip {rip_process} route",
    "rip.database": "display rip {rip_process} database",
    "rip.interfaces": "display rip {rip_process} interface",
    "ripng.status": "display ripng",
    "ripng.routes": "display ripng {rip_process} route",
    "ripng.database": "display ripng {rip_process} database",
    "ripng.interfaces": "display ripng {rip_process} interface",
    "ripng.route_table": "display ipv6 routing-table protocol ripng",
    "multicast.igmp": "display igmp-snooping",
    "multicast.igmp_groups": "display igmp-snooping group",
    "multicast.igmp_router_ports": "display igmp-snooping router-port",
    "multicast.igmp_static_groups": "display igmp-snooping static-group",
    "multicast.igmp_statistics": "display igmp-snooping statistics",
    "multicast.mld": "display mld-snooping",
    "multicast.mld_groups": "display mld-snooping group",
    "multicast.mld_router_ports": "display mld-snooping router-port",
    "multicast.mld_static_groups": "display mld-snooping static-group",
    "multicast.mld_statistics": "display mld-snooping statistics",
    "multicast.pim_neighbors": "display pim-snooping neighbor",
    "multicast.pim_router_ports": "display pim-snooping router-port",
    "multicast.pim_routes": "display pim-snooping routing-table",
    "multicast.pim_statistics": "display pim-snooping statistics",
    "multicast.vlans": "display multicast-vlan",
    "multicast.vlan_forwarding": "display multicast-vlan forwarding-table",
    "multicast.vlan_groups": "display multicast-vlan group",
    "acl.current": "display acl {acl_number}",
    "acl.rules": "display acl all",
    "acl.time_ranges": "display time-range all",
    "qos.policies": "display qos policy user-defined",
    "qos.policy_named": "display qos policy user-defined {qos_policy}",
    "qos.interfaces": "display qos policy interface",
    "qos.queues": "display qos queue interface",
    "qos.behaviors": "display traffic behavior user-defined",
    "qos.behavior_named": "display traffic behavior user-defined {qos_behavior}",
    "qos.classifiers": "display traffic classifier user-defined",
    "qos.classifier_named": "display traffic classifier user-defined {qos_classifier}",
    "auth.radius_schemes": "display radius scheme",
    "auth.radius_scheme_named": "display radius scheme {radius_scheme}",
    "auth.radius_statistics": "display radius statistics",
    "auth.radius_clients": "display radius-server active-client",
    "auth.radius_users": "display radius-server active-user",
    "auth.ldap": "display ldap scheme",
    "auth.ldap_named": "display ldap scheme {ldap_scheme}",
    "auth.hwtacacs": "display hwtacacs scheme",
    "auth.hwtacacs_named": "display hwtacacs scheme {hwtacacs_scheme}",
    "auth.domains": "display domain",
    "auth.dot1x": "display dot1x",
    "auth.dot1x_connections": "display dot1x connection",
    "auth.dot1x_supplicant": "display dot1x supplicant",
    "auth.mac": "display mac-authentication",
    "auth.mac_connections": "display mac-authentication connection",
    "security.port": "display port-security",
    "security.source_bindings": "display ip source binding",
    "security.arp_detection": "display arp detection",
    "crypto.ipsec_sa": "display ipsec sa",
    "crypto.ipsec_statistics": "display ipsec statistics",
    "crypto.ike_sa": "display ike sa",
    "crypto.ikev2_sa": "display ikev2 sa",
    "crypto.ipsec_transforms": "display ipsec transform-set",
    "crypto.pki_certificates": "display pki certificate request-status",
    "crypto.pki_domain": "display pki certificate request-status domain {pki_domain}",
    "crypto.pki_policy": "display pki certificate access-control-policy",
    "crypto.pki_attributes": "display pki certificate attribute-group",
    "crypto.public_keys": "display public-key peer",
    "crypto.ssl_server": "display ssl server-policy",
    "crypto.ssl_client": "display ssl client-policy",
    "crypto.ssh_server_status": "display ssh server status",
    "crypto.ssh_server_sessions": "display ssh server session",
    "crypto.ssh_users": "display ssh user-information",
    "crypto.ssh_source": "display ssh client source",
    "crypto.ssh_algorithms": "display ssh2 algorithm",
    "reliability.bfd": "display bfd session",
    "reliability.vrrp": "display vrrp",
    "reliability.track": "display track all",
    "reliability.cfd": "display cfd status",
    "reliability.cfd_md": "display cfd md",
    "reliability.cfd_meplist": "display cfd meplist",
    "reliability.cfd_mp": "display cfd mp",
    "reliability.cfd_services": "display cfd service-instance",
    "reliability.dldp": "display dldp",
    "reliability.dldp_statistics": "display dldp statistics",
    "reliability.erps": "display erps",
    "reliability.rrpp": "display rrpp brief",
    "reliability.rrpp_groups": "display rrpp ring-group",
    "reliability.smart_link": "display smart-link flush",
    "reliability.smart_link_groups": "display smart-link group all",
    "observe.logs": "display logbuffer",
    "observe.log_summary": "display logbuffer summary",
    "observe.diagnostic_logs": "display diagnostic-logfile summary",
    "observe.diagnostics": "display diagnostic-information",
    "observe.info_center": "display info-center",
    "observe.snmp": "display snmp-agent statistics",
    "observe.snmp_system": "display snmp-agent sys-info",
    "observe.snmp_community": "display snmp-agent community",
    "observe.snmp_groups": "display snmp-agent group",
    "observe.snmp_traps": "display snmp-agent trap-list",
    "observe.nqa": "display nqa result",
    "observe.nqa_entry": "display nqa result {nqa_admin} {nqa_operation}",
    "observe.nqa_history": "display nqa history",
    "observe.nqa_entry_history": "display nqa history {nqa_admin} {nqa_operation}",
    "observe.nqa_statistics": "display nqa statistics",
    "observe.nqa_reactions": "display nqa reaction counters",
    "observe.nqa_server": "display nqa server",
    "observe.ntp": "display ntp-service status",
    "observe.ntp_sessions": "display ntp-service sessions",
    "observe.ntp_trace": "display ntp-service trace",
    "observe.sntp_sessions": "display sntp sessions",
    "observe.clock": "display clock",
    "observe.sflow": "display sflow",
    "observe.mirroring": "display mirroring-group all",
    "automation.grpc": "display grpc",
    "automation.cwmp_status": "display cwmp status",
    "automation.cwmp_config": "display cwmp configuration",
    "openflow.status": "display openflow instance {openflow_instance} controller",
    "openflow.instances": "display openflow instance",
    "openflow.flows": "display openflow instance {openflow_instance} flow-table",
    "openflow.groups": "display openflow instance {openflow_instance} group",
    "openflow.meters": "display openflow instance {openflow_instance} meter",
    "openflow.summary": "display openflow summary",
}

H3C_RECOMMENDATION_NODES = MappingProxyType(_NODES)
# Keep the legacy public name above for compatibility.  Both public mappings
# expose the full registry; only recommend_next_h3c_commands applies terminal
# recommendation policy.
H3C_COMMAND_NODES = H3C_RECOMMENDATION_NODES


H3C_BEHAVIOR_GRAPH: dict[str, tuple[FlowEdge, ...]] = {
    "system.access": _edges(
        ("system.users", 100, "核对当前登录用户"),
        ("system.user_interface", 90, "查看登录线路配置与状态"),
        ("system.roles", 80, "核对角色权限"),
        ("system.local_users", 70, "查看本地用户配置"),
        ("system.version", 60, "确认设备软件版本"),
    ),
    "system.health": _edges(
        ("system.device", 100, "查看设备与板卡状态"),
        ("system.cpu", 90, "检查 CPU 使用率"),
        ("system.memory", 80, "检查内存使用情况"),
        ("system.environment", 70, "检查温度、电源和风扇环境"),
        ("system.version", 60, "确认软件与启动版本"),
    ),
    "system.config": _edges(
        ("system.current_config", 100, "核对当前生效配置"),
        ("system.saved_config", 90, "核对已保存配置"),
        ("system.startup", 80, "查看下次启动文件"),
        ("system.version", 70, "确认软件版本"),
        ("system.device", 60, "核对设备硬件状态"),
    ),
    "system.file_transfer": _edges(
        ("system.ftp_server", 100, "查看 FTP 服务状态"),
        ("system.ftp_users", 90, "查看 FTP 在线用户"),
        ("system.boot_loader", 80, "核对启动软件包"),
        ("system.current_config", 70, "核对相关传输配置"),
        ("system.device", 60, "确认设备运行状态"),
    ),
    "switching.interface": _edges(
        ("interface.current", 110, "查看当前接口的完整状态"),
        ("interface.all", 100, "查看接口状态和统计"),
        ("interface.range", 90, "核对接口批量配置范围"),
        ("interface.transceiver_alarms", 80, "检查光模块告警"),
        ("lldp.neighbors", 70, "核对相邻设备与对端端口"),
        ("vlan.brief", 60, "核对接口涉及的 VLAN"),
    ),
    "switching.vlan": _edges(
        ("vlan.current", 110, "查看当前 VLAN 的成员端口"),
        ("vlan.brief", 100, "查看 VLAN 摘要"),
        ("vlan.all", 90, "查看 VLAN 详细信息"),
        ("mac.table", 80, "核对二层转发表"),
        ("interface.all", 70, "检查相关接口状态"),
        ("stp.status", 60, "检查 VLAN 相关生成树状态"),
    ),
    "switching.mac": _edges(
        ("mac.table", 100, "查看 MAC 地址表"),
        ("mac.statistics", 90, "查看 MAC 表统计"),
        ("interface.all", 80, "核对地址学习接口"),
        ("vlan.brief", 70, "核对地址所属 VLAN"),
        ("lldp.neighbors", 60, "核对接口相邻设备"),
    ),
    "switching.spanning_tree": _edges(
        ("stp.status", 100, "查看生成树状态"),
        ("stp.root", 90, "核对根桥与根端口"),
        ("interface.all", 80, "检查端口链路状态"),
        ("aggregation.summary", 70, "核对聚合链路状态"),
        ("vlan.brief", 60, "核对参与计算的 VLAN"),
    ),
    "switching.aggregation": _edges(
        ("aggregation.summary", 100, "查看聚合组摘要"),
        ("aggregation.verbose", 90, "查看成员口选择状态"),
        ("aggregation.lacp", 80, "核对本端 LACP 系统标识"),
        ("interface.all", 70, "检查成员接口状态"),
        ("stp.status", 60, "检查聚合口生成树状态"),
    ),
    "switching.discovery": _edges(
        ("lldp.neighbors", 100, "查看 LLDP 邻居"),
        ("lldp.local", 90, "核对本地发布信息"),
        ("lldp.status", 80, "查看 LLDP 全局状态"),
        ("interface.all", 70, "检查邻居所在接口"),
        ("mac.table", 60, "交叉核对二层学习结果"),
    ),
    "switching.wan": _edges(
        ("wan.pppoe_clients", 100, "查看 PPPoE Relay 客户端"),
        ("wan.pppoe_strategy", 95, "核对 PPPoE Relay 客户端信息策略"),
        ("wan.pppoe_statistics", 90, "查看 PPPoE Relay 统计"),
        ("interface.all", 80, "检查接入接口状态"),
        ("ip.interface", 70, "核对三层接口配置"),
        ("route.table", 60, "核对接入后的路由"),
    ),
    "virtualization.irf": _edges(
        ("irf.status", 100, "查看 IRF 成员与主备状态"),
        ("irf.configuration", 90, "核对 IRF 配置"),
        ("irf.links", 80, "检查 IRF 链路"),
        ("irf.topology", 70, "查看 IRF 拓扑"),
        ("irf.mad", 60, "检查多 Active 检测状态"),
    ),
    "ip.addressing": _edges(
        ("ip.interface_brief", 100, "查看三层接口地址摘要"),
        ("route.table", 90, "核对 IP 路由表"),
        ("ip.arp", 80, "核对 ARP 邻居解析"),
        ("ip.fib", 70, "查看硬件转发表"),
        ("interface.all", 60, "检查底层接口状态"),
    ),
    "ip.arp": _edges(
        ("ip.arp_target", 110, "查看当前目标的 ARP 表项"),
        ("ip.arp", 100, "查看 ARP 表项"),
        ("ip.arp_timer", 90, "核对 ARP 老化时间"),
        ("ip.interface_brief", 80, "核对三层接口地址"),
        ("route.table", 70, "核对目标网段路由"),
        ("mac.table", 60, "交叉核对二层地址学习"),
    ),
    "ip.ipv6": _edges(
        ("ipv6.interface", 100, "查看 IPv6 接口状态"),
        ("ipv6.neighbors", 90, "查看 IPv6 邻居表"),
        ("ipv6.routes", 80, "查看 IPv6 路由表"),
        ("interface.all", 70, "检查底层接口状态"),
        ("dhcpv6.server_statistics", 60, "检查 DHCPv6 服务统计"),
    ),
    "ip.dhcp": _edges(
        ("dhcp.server_statistics", 100, "查看 DHCP 服务器统计"),
        ("dhcp.relay_statistics", 90, "查看 DHCP 中继统计"),
        ("dhcp.snooping_bindings", 80, "核对 DHCP Snooping 绑定"),
        ("dhcpv6.server_statistics", 70, "查看 DHCPv6 服务器统计"),
        ("dhcpv6.snooping_bindings", 60, "核对 DHCPv6 Snooping 绑定"),
    ),
    "ip.dns": _edges(
        ("dns.domains", 100, "查看域名后缀配置"),
        ("dns.hosts", 90, "查看静态与动态主机表"),
        ("dns.servers", 80, "查看 DNS 服务器配置"),
        ("route.table", 70, "核对到 DNS 服务器的路由"),
        ("ip.interface_brief", 60, "核对本地三层接口"),
    ),
    "network.diagnostics": _edges(
        ("route.target", 110, "核对到当前目标的精确选路"),
        ("route.table", 100, "核对到目标网段的选路"),
        ("ip.interface_brief", 90, "核对本地三层接口"),
        ("ip.arp", 80, "检查下一跳邻居解析"),
        ("dns.servers", 70, "检查域名解析服务器"),
        ("interface.all", 60, "检查底层链路状态"),
    ),
    "routing.base": _edges(
        ("acl.current", 110, "核对当前路由策略引用的 ACL"),
        ("route.table", 100, "查看生效路由表"),
        ("route.summary", 90, "查看路由协议摘要"),
        ("route.statistics", 80, "查看路由数量统计"),
        ("ip.fib", 70, "核对硬件转发表"),
        ("ip.interface_brief", 60, "核对出接口地址"),
    ),
    "routing.ospf": _edges(
        ("ospf.peers", 100, "检查 OSPF 邻居状态"),
        ("ospf.interfaces", 90, "检查 OSPF 接口状态"),
        ("ospf.routes", 80, "查看 OSPF 路由"),
        ("ospf.lsdb", 70, "查看链路状态数据库"),
        ("route.table", 60, "核对路由是否进入全局路由表"),
    ),
    "routing.rip": _edges(
        ("rip.neighbors", 110, "检查当前 RIP 进程邻居"),
        ("rip.routes", 105, "查看当前 RIP 进程路由"),
        ("rip.database", 100, "查看当前 RIP 进程数据库"),
        ("rip.interfaces", 95, "检查当前 RIP 进程接口"),
        ("rip.status", 90, "查看 RIP 进程状态"),
        ("route.rip", 80, "查看进入全局路由表的 RIP 路由"),
        ("route.table", 70, "核对全局路由表"),
        ("ip.interface_brief", 60, "核对参与 RIP 的三层接口"),
    ),
    "multicast": _edges(
        ("multicast.igmp", 100, "查看 IGMP Snooping 状态"),
        ("multicast.igmp_groups", 90, "查看 IPv4 组播成员"),
        ("multicast.mld", 80, "查看 IPv6 MLD Snooping 状态"),
        ("multicast.pim_neighbors", 70, "查看 PIM Snooping 邻居"),
        ("multicast.vlans", 60, "查看组播 VLAN"),
    ),
    "policy.acl": _edges(
        ("acl.current", 110, "查看当前 ACL 的规则与统计"),
        ("acl.rules", 100, "查看 ACL 规则与匹配统计"),
        ("acl.time_ranges", 90, "核对 ACL 引用的时间段"),
        ("qos.policies", 80, "查看引用 ACL 的 QoS 策略"),
        ("qos.classifiers", 70, "核对流分类条件"),
        ("qos.behaviors", 60, "核对匹配后的流行为"),
    ),
    "policy.qos": _edges(
        ("qos.policies", 100, "查看 QoS 策略"),
        ("qos.interfaces", 90, "查看接口应用的 QoS 策略"),
        ("qos.queues", 80, "查看接口队列状态"),
        ("qos.behaviors", 70, "查看流行为"),
        ("qos.classifiers", 60, "查看流分类器"),
    ),
    "security.authentication": _edges(
        ("auth.radius_schemes", 100, "查看 RADIUS 方案"),
        ("auth.radius_statistics", 90, "检查认证统计与失败情况"),
        ("auth.domains", 80, "核对认证域配置"),
        ("system.local_users", 70, "核对本地用户"),
        ("auth.dot1x", 60, "查看 802.1X 全局状态"),
    ),
    "security.access": _edges(
        ("security.port", 100, "查看端口安全状态"),
        ("security.source_bindings", 90, "核对 IP Source Guard 绑定"),
        ("security.arp_detection", 80, "查看 ARP Detection 状态"),
        ("auth.dot1x", 70, "查看 802.1X 接入状态"),
        ("auth.mac", 60, "查看 MAC 地址认证状态"),
    ),
    "security.crypto": _edges(
        ("crypto.ipsec_sa", 100, "查看 IPsec 安全联盟"),
        ("crypto.ipsec_statistics", 90, "查看 IPsec 报文统计"),
        ("crypto.ike_sa", 80, "查看 IKE 安全联盟"),
        ("crypto.ikev2_sa", 70, "查看 IKEv2 安全联盟"),
        ("crypto.pki_certificates", 60, "核对证书申请状态"),
    ),
    "reliability": _edges(
        ("reliability.bfd", 100, "查看 BFD 会话"),
        ("reliability.vrrp", 90, "查看 VRRP 状态"),
        ("reliability.track", 80, "查看 Track 项状态"),
        ("reliability.cfd", 70, "查看 CFD 状态"),
        ("reliability.erps", 60, "查看 ERPS 环状态"),
    ),
    "observability": _edges(
        ("observe.logs", 100, "查看日志缓冲区"),
        ("observe.info_center", 90, "核对信息中心配置"),
        ("observe.snmp", 80, "查看 SNMP 统计"),
        ("observe.nqa", 70, "查看 NQA 测试结果"),
        ("observe.ntp", 60, "核对设备时间同步状态"),
    ),
    "observability.telemetry": _edges(
        ("automation.grpc", 100, "查看 gRPC 运行状态"),
        ("observe.info_center", 90, "核对其他信息输出通道"),
        ("observe.snmp", 80, "交叉核对 SNMP 采集状态"),
        ("observe.sflow", 70, "交叉核对 sFlow 采集状态"),
        ("interface.all", 60, "核对采集对象接口状态"),
    ),
    "automation": _edges(
        ("automation.grpc", 100, "查看 gRPC 状态"),
        ("automation.cwmp_status", 90, "查看 CWMP 会话状态"),
        ("automation.cwmp_config", 80, "核对 CWMP 配置"),
        ("system.current_config", 70, "核对自动化后的生效配置"),
        ("system.users", 60, "查看当前管理会话"),
    ),
    "sdn.openflow": _edges(
        ("openflow.status", 110, "查看当前实例的控制器状态"),
        ("openflow.flows", 105, "查看当前实例流表"),
        ("openflow.groups", 100, "查看当前实例组表"),
        ("openflow.meters", 95, "查看当前实例 Meter 表"),
        ("openflow.instances", 90, "查看 OpenFlow 实例"),
        ("openflow.summary", 80, "查看 OpenFlow 摘要"),
        ("interface.all", 70, "核对 OpenFlow 端口状态"),
        ("observe.logs", 60, "检查控制器连接相关日志"),
    ),
}


H3C_BEHAVIOR_GRAPH.update({
    "system.health.cpu": _edges(
        ("system.cpu", 100, "查看当前 CPU 使用率"),
        ("system.cpu_history", 90, "查看 CPU 使用率历史"),
        ("system.cpu_configuration", 80, "核对 CPU 告警阈值配置"),
        ("system.process_cpu", 70, "定位高 CPU 进程"),
        ("system.device", 60, "核对高负载所在板卡状态"),
        ("system.environment", 50, "排除温度与供电异常"),
    ),
    "system.health.memory": _edges(
        ("system.memory", 100, "查看当前内存使用情况"),
        ("system.memory_threshold", 90, "核对内存告警阈值"),
        ("system.process_memory", 80, "定位高内存进程"),
        ("system.device", 70, "核对内存所在板卡状态"),
        ("system.current_config", 60, "核对相关资源配置"),
        ("system.environment", 50, "排除温度与供电异常"),
    ),
    "ip.dhcp.server": _edges(
        ("dhcp.server_statistics", 100, "查看 DHCP 地址分配统计"),
        ("dhcp.server_pool", 90, "核对地址池状态"),
        ("dhcp.server_in_use", 80, "查看已分配地址"),
        ("dhcp.server_free", 70, "查看可分配地址"),
        ("dhcp.server_conflict", 60, "检查冲突地址"),
        ("dhcp.server_expired", 50, "查看过期租约"),
        ("dhcp.server_database", 40, "核对租约数据库"),
    ),
    "ip.dhcp.relay": _edges(
        ("dhcp.relay_statistics", 100, "查看 DHCP 中继统计"),
        ("dhcp.relay_servers", 90, "核对中继服务器地址"),
        ("dhcp.relay_clients", 80, "查看中继客户端信息"),
        ("dhcp.relay_information", 70, "查看接口中继信息"),
        ("dhcp.relay_check", 60, "核对客户端 MAC 校验状态"),
        ("ip.interface_brief", 50, "检查中继三层接口"),
    ),
    "ip.dhcp.snooping": _edges(
        ("dhcp.snooping_bindings", 100, "查看 Snooping 绑定表"),
        ("dhcp.snooping_database", 90, "核对绑定数据库"),
        ("dhcp.snooping_trust", 80, "核对信任端口"),
        ("dhcp.snooping_packets", 70, "查看报文统计"),
        ("dhcp.snooping_information", 60, "查看所有接口 Snooping 信息"),
        ("security.source_bindings", 50, "交叉核对源地址绑定"),
    ),
    "ip.dhcp.ipv6": _edges(
        ("dhcpv6.server", 100, "查看 DHCPv6 服务状态"),
        ("dhcpv6.server_statistics", 90, "查看 DHCPv6 分配统计"),
        ("dhcpv6.server_in_use", 80, "查看已分配 IPv6 地址"),
        ("dhcpv6.server_database", 70, "核对 DHCPv6 数据库"),
        ("dhcpv6.snooping_bindings", 60, "查看 DHCPv6 Snooping 绑定"),
        ("dhcpv6.snooping_database", 50, "核对 DHCPv6 绑定数据库"),
        ("dhcpv6.snooping_packets", 40, "查看 DHCPv6 Snooping 报文统计"),
        ("dhcpv6.snooping_trust", 30, "核对 DHCPv6 信任端口"),
    ),
    "routing.ripng": _edges(
        ("ripng.routes", 110, "查看当前 RIPng 进程路由"),
        ("ripng.database", 105, "查看当前 RIPng 进程数据库"),
        ("ripng.interfaces", 100, "检查当前 RIPng 进程接口"),
        ("ripng.status", 90, "查看 RIPng 进程状态"),
        ("ripng.route_table", 80, "查看进入全局表的 RIPng 路由"),
        ("ipv6.routes", 70, "核对 IPv6 全局路由表"),
        ("ipv6.interface", 60, "核对参与 RIPng 的接口"),
    ),
    "multicast.igmp": _edges(
        ("multicast.igmp", 100, "查看 IGMP Snooping 状态"),
        ("multicast.igmp_groups", 90, "查看动态成员表"),
        ("multicast.igmp_router_ports", 80, "查看路由器端口"),
        ("multicast.igmp_static_groups", 70, "核对静态成员"),
        ("multicast.igmp_statistics", 60, "查看 IGMP Snooping 统计"),
        ("multicast.vlans", 50, "核对组播 VLAN"),
    ),
    "multicast.mld": _edges(
        ("multicast.mld", 100, "查看 MLD Snooping 状态"),
        ("multicast.mld_groups", 90, "查看 IPv6 组播成员表"),
        ("multicast.mld_router_ports", 80, "查看 IPv6 路由器端口"),
        ("multicast.mld_static_groups", 70, "核对静态 IPv6 成员"),
        ("multicast.mld_statistics", 60, "查看 MLD Snooping 统计"),
        ("ipv6.interface", 50, "核对 IPv6 接口状态"),
    ),
    "multicast.pim": _edges(
        ("multicast.pim_neighbors", 100, "查看 PIM Snooping 邻居"),
        ("multicast.pim_router_ports", 90, "查看 PIM 路由器端口"),
        ("multicast.pim_routes", 80, "查看 PIM Snooping 转发表"),
        ("multicast.pim_statistics", 70, "查看 PIM Snooping 统计"),
        ("multicast.igmp_groups", 60, "交叉核对 IPv4 组播成员"),
        ("multicast.vlans", 50, "核对承载组播的 VLAN"),
    ),
    "multicast.vlan": _edges(
        ("multicast.vlans", 100, "查看组播 VLAN 配置"),
        ("multicast.vlan_forwarding", 90, "查看组播 VLAN 转发表"),
        ("multicast.vlan_groups", 80, "查看组播 VLAN 成员"),
        ("multicast.igmp_groups", 70, "交叉核对 IGMP 成员"),
        ("vlan.brief", 60, "核对基础 VLAN 状态"),
        ("interface.all", 50, "检查组播 VLAN 接口状态"),
    ),
    "security.authentication.aaa": _edges(
        ("auth.radius_schemes", 100, "查看 RADIUS 方案"),
        ("auth.radius_statistics", 90, "检查 RADIUS 统计"),
        ("auth.radius_clients", 80, "查看活动 RADIUS 客户端"),
        ("auth.radius_users", 70, "查看活动认证用户"),
        ("auth.domains", 60, "核对认证域"),
        ("system.local_users", 50, "核对本地回退用户"),
    ),
    "security.authentication.ldap": _edges(
        ("auth.ldap", 100, "查看 LDAP 认证方案"),
        ("auth.domains", 90, "核对引用 LDAP 的认证域"),
        ("system.local_users", 80, "核对本地回退用户"),
        ("observe.logs", 70, "检查 LDAP 认证日志"),
        ("system.users", 60, "查看当前登录用户"),
        ("system.current_config", 50, "核对 LDAP 相关配置"),
    ),
    "security.authentication.hwtacacs": _edges(
        ("auth.hwtacacs", 100, "查看 HWTACACS 认证方案"),
        ("auth.domains", 90, "核对引用 HWTACACS 的认证域"),
        ("system.local_users", 80, "核对本地回退用户"),
        ("observe.logs", 70, "检查 HWTACACS 认证日志"),
        ("system.users", 60, "查看当前登录用户"),
        ("system.current_config", 50, "核对 HWTACACS 相关配置"),
    ),
    "security.access.dot1x": _edges(
        ("auth.dot1x", 100, "查看 802.1X 状态与统计"),
        ("auth.dot1x_connections", 90, "查看 802.1X 在线连接"),
        ("auth.dot1x_supplicant", 80, "查看 Supplicant 状态"),
        ("security.port", 70, "核对端口安全状态"),
        ("security.source_bindings", 60, "核对认证后的源绑定"),
        ("observe.logs", 50, "检查 802.1X 认证日志"),
    ),
    "security.access.mac": _edges(
        ("auth.mac", 100, "查看 MAC 地址认证状态"),
        ("auth.mac_connections", 90, "查看 MAC 地址认证连接"),
        ("security.port", 80, "核对端口安全状态"),
        ("security.source_bindings", 70, "核对认证后的源绑定"),
        ("security.arp_detection", 60, "检查 ARP 攻击检测"),
        ("observe.logs", 50, "检查 MAC 地址认证日志"),
    ),
    "security.crypto.ipsec": _edges(
        ("crypto.ipsec_sa", 100, "查看 IPsec 安全联盟"),
        ("crypto.ipsec_statistics", 90, "查看 IPsec 报文统计"),
        ("crypto.ipsec_transforms", 80, "核对 IPsec 转换集"),
        ("crypto.ike_sa", 70, "查看 IKEv1 安全联盟"),
        ("crypto.ikev2_sa", 60, "查看 IKEv2 安全联盟"),
        ("route.table", 50, "核对隧道对端选路"),
    ),
    "security.crypto.pki": _edges(
        ("crypto.pki_domain", 110, "查看当前 PKI 域证书申请状态"),
        ("crypto.pki_certificates", 100, "查看证书申请状态"),
        ("crypto.pki_policy", 90, "核对证书访问控制策略"),
        ("crypto.pki_attributes", 80, "核对证书属性组"),
        ("crypto.public_keys", 70, "查看对端公钥"),
        ("crypto.ssl_server", 60, "核对引用证书的 SSL 策略"),
    ),
    "security.crypto.ssh": _edges(
        ("crypto.ssh_server_status", 100, "查看 SSH 服务状态"),
        ("crypto.ssh_server_sessions", 90, "查看 SSH 服务会话"),
        ("crypto.ssh_users", 80, "查看 SSH 用户信息"),
        ("crypto.ssh_source", 70, "核对 SSH 客户端源地址"),
        ("crypto.ssh_algorithms", 60, "核对 SSH 算法集合"),
        ("observe.logs", 50, "检查 SSH 会话日志"),
    ),
    "security.crypto.ssl": _edges(
        ("crypto.ssl_server", 100, "查看 SSL 服务端策略"),
        ("crypto.ssl_client", 90, "查看 SSL 客户端策略"),
        ("crypto.pki_certificates", 80, "核对证书申请状态"),
        ("crypto.public_keys", 70, "核对相关公钥"),
        ("observe.logs", 60, "检查 TLS 协商日志"),
        ("system.current_config", 50, "核对 SSL 策略引用关系"),
    ),
    "reliability.bfd": _edges(
        ("reliability.bfd", 100, "查看 BFD 会话"),
        ("reliability.track", 90, "核对 BFD 关联的 Track 项"),
        ("interface.all", 80, "检查承载接口状态"),
        ("route.table", 70, "核对 BFD 保护的路由"),
        ("observe.logs", 60, "检查 BFD 状态切换日志"),
        ("ip.interface_brief", 50, "核对 BFD 源接口地址"),
    ),
    "reliability.vrrp": _edges(
        ("reliability.vrrp", 100, "查看 VRRP 状态"),
        ("reliability.track", 90, "核对 VRRP 关联的 Track 项"),
        ("ip.interface_brief", 80, "核对虚拟网关所在接口"),
        ("ip.arp", 70, "检查虚拟网关 ARP"),
        ("observe.logs", 60, "检查 VRRP 主备切换日志"),
        ("route.table", 50, "核对虚拟网关相关路由"),
    ),
    "reliability.track": _edges(
        ("reliability.track", 100, "查看所有 Track 项"),
        ("reliability.bfd", 90, "核对关联 BFD 会话"),
        ("reliability.vrrp", 80, "核对关联 VRRP 状态"),
        ("interface.all", 70, "检查关联接口"),
        ("route.table", 60, "检查关联路由"),
    ),
    "reliability.ring": _edges(
        ("reliability.erps", 100, "查看 ERPS 环状态"),
        ("reliability.rrpp", 90, "查看 RRPP 环摘要"),
        ("reliability.rrpp_groups", 80, "查看 RRPP 环组"),
        ("reliability.smart_link", 70, "查看 Smart Link Flush 状态"),
        ("reliability.smart_link_groups", 60, "查看所有 Smart Link 组"),
        ("interface.all", 50, "核对环网接口状态"),
    ),
    "reliability.oam": _edges(
        ("reliability.cfd", 100, "查看 CFD 全局状态"),
        ("reliability.cfd_md", 90, "查看维护域"),
        ("reliability.cfd_meplist", 80, "查看 MEP 列表"),
        ("reliability.cfd_mp", 70, "查看维护点"),
        ("reliability.cfd_services", 60, "查看 CFD 服务实例"),
        ("reliability.dldp", 50, "查看 DLDP 状态"),
        ("reliability.dldp_statistics", 40, "查看 DLDP 统计"),
    ),
    "observability.logging": _edges(
        ("observe.logs", 100, "查看日志缓冲区"),
        ("observe.log_summary", 90, "查看日志摘要"),
        ("observe.info_center", 80, "核对信息中心配置"),
        ("observe.diagnostic_logs", 70, "查看诊断日志文件摘要"),
        ("observe.diagnostics", 60, "收集只读诊断信息"),
        ("system.current_config", 50, "核对日志相关配置"),
    ),
    "observability.snmp": _edges(
        ("observe.snmp", 100, "查看 SNMP 统计"),
        ("observe.snmp_system", 90, "核对 SNMP 系统信息"),
        ("observe.snmp_community", 80, "核对团体名配置"),
        ("observe.snmp_groups", 70, "核对 SNMP 用户组"),
        ("observe.snmp_traps", 60, "查看 Trap 列表"),
        ("observe.logs", 50, "检查 SNMP 交互日志"),
    ),
    "observability.nqa": _edges(
        ("observe.nqa", 100, "查看 NQA 最近结果"),
        ("observe.nqa_history", 90, "查看 NQA 历史结果"),
        ("observe.nqa_statistics", 80, "查看 NQA 统计"),
        ("observe.nqa_reactions", 70, "查看 NQA 联动计数"),
        ("observe.nqa_server", 60, "查看 NQA 服务器状态"),
        ("route.table", 50, "核对到测量目标的路由"),
    ),
    "observability.time": _edges(
        ("observe.ntp", 100, "查看 NTP 同步状态"),
        ("observe.ntp_sessions", 90, "查看 NTP 会话"),
        ("observe.ntp_trace", 80, "跟踪 NTP 时钟源"),
        ("observe.sntp_sessions", 70, "查看 SNTP 会话"),
        ("observe.clock", 60, "核对设备当前时间"),
        ("observe.logs", 50, "检查时间同步日志"),
    ),
    "observability.traffic": _edges(
        ("observe.sflow", 100, "查看 sFlow 配置与状态"),
        ("observe.mirroring", 90, "查看所有镜像组"),
        ("interface.all", 80, "核对采集接口状态"),
        ("mac.statistics", 70, "查看二层转发统计"),
        ("observe.info_center", 60, "核对监控信息输出通道"),
        ("observe.logs", 50, "检查采集与镜像异常日志"),
    ),
    "automation.cwmp": _edges(
        ("automation.cwmp_status", 100, "查看 CWMP 会话状态"),
        ("automation.cwmp_config", 90, "核对 CWMP 配置"),
        ("system.current_config", 80, "核对远程下发后的配置"),
        ("system.users", 70, "查看当前管理会话"),
        ("observe.logs", 60, "检查 CWMP 交互日志"),
        ("system.device", 50, "确认设备运行状态"),
    ),
    "automation.netconf": _edges(
        ("system.current_config", 100, "核对 NETCONF 生效配置"),
        ("system.users", 90, "查看当前管理会话"),
        ("observe.logs", 80, "检查 NETCONF 操作日志"),
        ("observe.info_center", 70, "核对自动化事件输出"),
        ("system.device", 60, "确认设备运行状态"),
    ),
    "automation.eaa": _edges(
        ("system.current_config", 100, "核对 EAA 或脚本生效配置"),
        ("observe.logs", 90, "检查自动化执行日志"),
        ("observe.info_center", 80, "核对事件触发来源"),
        ("system.cpu", 70, "检查脚本执行负载"),
        ("system.users", 60, "查看当前管理会话"),
    ),
})


# Nodes whose executable form intentionally differs from the PDF command
# title.  The value names the exact catalog title whose syntax documents the
# extension or parameter placement.  Bare titles are validated separately by
# the generated BARE_DISPLAY_COMMANDS allow-list in tests.
H3C_RECOMMENDATION_SYNTAX_ROOTS = MappingProxyType({
    "interface.current": "display interface",
    "interface.transceiver_alarms": "display transceiver alarm",
    "vlan.current": "display vlan",
    "mac.current": "display mac-address",
    "wan.pppoe_clients": "display pppoe-relay client-information",
    "wan.pppoe_strategy": "display pppoe-relay client-information",
    "ip.arp_target": "display arp ip-address",
    "ipv6.neighbors": "display ipv6 neighbors",
    "dhcp.snooping_information": "display dhcp snooping information",
    "dhcp.server_pool_named": "display dhcp server pool",
    "route.target": "display ip routing-table ip-address",
    "route.ipv6_target": "display ipv6 routing-table ipv6-address",
    "route.rip": "display ip routing-table protocol",
    "rip.neighbors": "display rip neighbor",
    "rip.routes": "display rip route",
    "rip.database": "display rip database",
    "rip.interfaces": "display rip interface",
    "ripng.routes": "display ripng route",
    "ripng.database": "display ripng database",
    "ripng.interfaces": "display ripng interface",
    "ripng.route_table": "display ipv6 routing-table protocol",
    "ospf.peers_process": "display ospf peer",
    "ospf.interfaces_process": "display ospf interface",
    "ospf.routes_process": "display ospf routing",
    "ospf.lsdb_process": "display ospf lsdb",
    "ospfv3.peers_process": "display ospfv3 peer",
    "ospfv3.interfaces_process": "display ospfv3 interface",
    "ospfv3.routes_process": "display ospfv3 routing",
    "ospfv3.lsdb_process": "display ospfv3 lsdb",
    "acl.current": "display acl",
    "acl.rules": "display acl",
    "acl.time_ranges": "display time-range",
    "qos.policies": "display qos policy",
    "qos.policy_named": "display qos policy",
    "qos.behaviors": "display traffic behavior",
    "qos.behavior_named": "display traffic behavior",
    "qos.classifiers": "display traffic classifier",
    "qos.classifier_named": "display traffic classifier",
    "auth.radius_scheme_named": "display radius scheme",
    "auth.ldap_named": "display ldap scheme",
    "auth.hwtacacs_named": "display hwtacacs scheme",
    "observe.nqa_entry": "display nqa result",
    "observe.nqa_entry_history": "display nqa history",
    "crypto.pki_domain": "display pki certificate request-status",
    "crypto.ssh_server_status": "display ssh server",
    "crypto.ssh_server_sessions": "display ssh server",
    "reliability.track": "display track",
    "reliability.smart_link_groups": "display smart-link group",
    "observe.mirroring": "display mirroring-group",
    "openflow.status": "display openflow",
    "openflow.flows": "display openflow flow-table",
    "openflow.groups": "display openflow group",
    "openflow.meters": "display openflow meter",
})


_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), behavior)
    for pattern, behavior in (
        (r"\bopenflow\b", "sdn.openflow"),
        (r"\b(?:grpc|telemetry)\b", "observability.telemetry"),
        (r"\b(?:netconf|cwmp|eaa|python|tcl)\b", "automation"),
        (r"\b(?:irf|mad)\b", "virtualization.irf"),
        (r"\bpppoe-relay\b", "switching.wan"),
        (r"\b(?:igmp|mld|pim)-snooping\b|\bmulticast-vlan\b", "multicast"),
        (r"\b(?:ipsec|ikev2?|pki|ssl|ssh|public-key|fips)\b", "security.crypto"),
        (
            r"\b(?:radius|hwtacacs|ldap|dot1x|portal|mac-authentication|"
            r"domain|local-user|password-control)\b",
            "security.authentication",
        ),
        (r"\b(?:port-security|ip source binding|arp detection|ra guard|savi|mff)\b", "security.access"),
        (r"\b(?:bfd|vrrp|track|cfd|erps|rrpp|dldp|smart-link|monitor-link)\b", "reliability"),
        (r"\b(?:qos|traffic classifier|traffic behavior|car|wred|queue)\b", "policy.qos"),
        (r"\b(?:acl|time-range)\b", "policy.acl"),
        (r"\bospfv3\b", "routing.ospfv3"),
        (r"\bospf\b", "routing.ospf"),
        (r"\b(?:rip|ripng)\b", "routing.rip"),
        (r"\b(?:dhcp|bootp)\b", "ip.dhcp"),
        (r"\b(?:dns|ip host)\b", "ip.dns"),
        (r"\b(?:ping|tracert)(?:\s|$)", "network.diagnostics"),
        (r"\bipv6\b|\bndp?\b", "ip.ipv6"),
        (r"\barp\b", "ip.arp"),
        (r"\b(?:routing-table|route-policy|policy-based-route|static-route|fib)\b", "routing.base"),
        (r"\b(?:stp|spanning-tree|loopback-detection)\b", "switching.spanning_tree"),
        (r"\b(?:link-aggregation|bridge-aggregation|route-aggregation|lacp)\b", "switching.aggregation"),
        (r"\b(?:lldp|l2pt)\b", "switching.discovery"),
        (r"(?<![-\w])user-interface\b", "system.access"),
        (r"\bip\s+(?:address|interface)\b", "ip.addressing"),
        (r"(?<![-\w])(?:vlan|qinq|mvrp)\b", "switching.vlan"),
        (r"\bmac-address\b", "switching.mac"),
        (r"(?<![-\w])(?:interface|transceiver)\b", "switching.interface"),
        (
            r"\b(?:nqa|snmp|rmon|sflow|info-center|logbuffer|ntp-service|"
            r"sntp|mirroring-group|poe)\b",
            "observability",
        ),
        (
            r"\b(?:current-configuration|saved-configuration|startup|"
            r"boot-loader|save|configuration)\b",
            "system.config",
        ),
        (r"\b(?:ftp|tftp)\b", "system.file_transfer"),
        (
            r"\b(?:cpu-usage|memory|environment|device|version|"
            r"diagnostic-information|display clock)\b",
            "system.health",
        ),
        (r"\b(?:users|user-interface|role|system-view|quit|return|alias)\b", "system.access"),
    )
)


def _catalog_indexes() -> tuple[dict[str, str], dict[str, tuple[tuple[str, str], ...]]]:
    candidates: defaultdict[str, set[str]] = defaultdict(set)
    base_candidates: defaultdict[str, set[str]] = defaultdict(set)
    for _, _, match_key, behavior, _, section in COMMAND_CATALOG:
        if match_key:
            candidates[match_key].add(_section_behavior(behavior, section))
            base_candidates[match_key].add(behavior)

    # A title reused in multiple views cannot be classified from command text
    # alone.  Exact one-keyword titles are still safe when the complete catalog
    # assigns them to one behavior; prefix matching remains two-keyword minimum.
    exact = {
        match_key: (
            next(iter(behaviors))
            if len(behaviors) == 1
            else next(iter(base_candidates[match_key]))
        )
        for match_key, behaviors in candidates.items()
        if len(behaviors) == 1 or len(base_candidates[match_key]) == 1
    }
    grouped: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
    for match_key, behavior in exact.items():
        if len(match_key.split()) >= 2:
            grouped[match_key.split()[0]].append((match_key, behavior))
    ordered = {
        token: tuple(sorted(items, key=lambda item: len(item[0]), reverse=True))
        for token, items in grouped.items()
    }
    return exact, ordered


_EXACT_BEHAVIORS, _PREFIX_BEHAVIORS = _catalog_indexes()


def _ambiguous_catalog_match_keys() -> frozenset[str]:
    behaviors_by_key: defaultdict[str, set[str]] = defaultdict(set)
    for _, _, match_key, behavior, _, _ in COMMAND_CATALOG:
        if match_key:
            behaviors_by_key[match_key].add(behavior)
    return frozenset(
        match_key
        for match_key, behaviors in behaviors_by_key.items()
        if len(behaviors) > 1
    )


_AMBIGUOUS_CATALOG_MATCH_KEYS = _ambiguous_catalog_match_keys()


# Runtime disambiguation belongs to recommendation policy, not the complete
# behavior graph.  These official/common commands have ambiguous or missing
# workbook roots and must never inherit an unrelated active view.
_RUNTIME_COMMAND_BEHAVIORS: dict[str, str] = {
    "display interface": "switching.interface",
    "display mac-address": "switching.mac",
    "display patch-information": "system.health",
    "display version": "system.health",
}
_RUNTIME_COMPLETION_COMMANDS = frozenset({"display patch-information"})
_KNOWN_RULE_FIRST_TOKENS = frozenset({
    match_key.split(maxsplit=1)[0]
    for _, _, match_key, _, _, _ in COMMAND_CATALOG
    if match_key
}) | frozenset(
    command.split(maxsplit=1)[0]
    for command in _RUNTIME_COMMAND_BEHAVIORS
)


_VIEW_ANCHORS: tuple[tuple[re.Pattern[str], str | None], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), behavior)
    for pattern, behavior in (
        (r"^user-interface\b", "system.access"),
        (r"^interface\b", "switching.interface"),
        (r"^vlan\s+\d+\b", "switching.vlan"),
        (r"^ripng\s+\d+\b", "routing.ripng"),
        (r"^rip\s+\d+\b", "routing.rip"),
        (r"^ospfv3\s+\d+\b", "routing.ospfv3"),
        (r"^ospf\s+\d+\b", "routing.ospf"),
        (r"^route-policy\b", "routing.base"),
        (r"^acl(?:\s+(?:ipv6|mac))?\s+\d+\b", "policy.acl"),
        (r"^traffic\s+(?:classifier|behavior)\b", "policy.qos"),
        (r"^qos\s+policy\b", "policy.qos"),
        (r"^radius\s+scheme\b", "security.authentication.aaa"),
        (r"^ldap\s+scheme\b", "security.authentication.ldap"),
        (r"^hwtacacs\s+scheme\b", "security.authentication.hwtacacs"),
        (r"^pki\s+domain\b", "security.crypto.pki"),
        (r"^ssl\s+(?:client|server)-policy\b", "security.crypto.ssl"),
        (r"^openflow\s+instance\s+\d+\b", "sdn.openflow"),
        (r"^nqa\s+entry\b", "observability.nqa"),
        (r"^(?:rrpp\s+domain|erps\s+ring|smart-link\s+group|monitor-link\s+group)\b", "reliability.ring"),
        (r"^system-view$", None),
    )
)


_VIEW_LOCAL_COMMAND = re.compile(
    r"^(?:authentication-mode\b|silent-interface\b|if-match\b|"
    r"control-vlan\b|protected-vlan\b|ipv6(?:\s|$)|version\s+tls)",
    re.IGNORECASE,
)
_SPECIALIZED_VIEW_FIRST_TOKENS = frozenset(
    match_key.split(maxsplit=1)[0]
    for match_key in SPECIALIZED_VIEW_MATCH_KEYS
    if match_key
)


_VIEW_ENTRY_ITEMS = tuple(sorted(
    VIEW_ENTRY_BEHAVIORS.items(),
    key=lambda item: len(item[0]),
    reverse=True,
))


_STATIC_RESULT_VIEW_NAMES: dict[str, tuple[str, ...]] = {
    "acl": ("ACL 视图",),
    "address-family ipv4": ("RIB IPv4 地址族视图",),
    "address-family ipv6": ("RIB IPv6 地址族视图",),
    "area": ("OSPF 区域视图",),
    "dhcp server ip-pool": ("DHCP 地址池视图",),
    "erps ring": ("ERPS 实例视图",),
    "interface": ("以太网接口视图", "二层以太网接口视图"),
    "interface bridge-aggregation": ("二层聚合接口视图",),
    "interface loopback": ("LoopBack 接口视图",),
    "interface null": ("NULL 接口视图",),
    "interface range": ("接口批量配置视图",),
    "interface range name": ("接口批量配置视图",),
    "interface vlan-interface": ("VLAN 接口视图",),
    "ospf": ("OSPF 视图",),
    "ospfv3": ("OSPFv3 视图",),
    "qos policy": ("QoS 策略视图",),
    "rib": ("RIB 视图",),
    "rip": ("RIP 视图",),
    "ripng": ("RIPng 视图",),
    "traffic behavior": ("流行为视图",),
    "traffic classifier": ("类视图",),
    "vlan": ("VLAN 视图",),
}
_COMMAND_VIEW_MATCH_KEYS = tuple(sorted(COMMAND_VIEWS, key=len, reverse=True))


_IPV4_ROUTE_TARGETS = frozenset({
    "route.target", "route.table", "route.summary", "route.statistics",
})
_IPV6_ROUTE_TARGETS = frozenset({
    "route.ipv6_target", "route.ipv6", "route.ipv6_summary",
    "route.ipv6_statistics",
})


def _recommendation_edge_priority(
    edge: FlowEdge,
    parameters: dict[str, str],
) -> int:
    """Prefer checks for the address family present in the current command."""
    priority = edge.priority
    address_family = parameters.get("address_family")
    if "ipv6_target" in parameters or address_family == "ipv6":
        if edge.target in _IPV6_ROUTE_TARGETS:
            return priority + 1000
        if edge.target in _IPV4_ROUTE_TARGETS:
            return priority - 1000
    elif "target" in parameters or address_family == "ipv4":
        if edge.target in _IPV4_ROUTE_TARGETS:
            return priority + 1000
        if edge.target in _IPV6_ROUTE_TARGETS:
            return priority - 1000
    return priority


def recommend_next_h3c_commands(
    command: str,
    limit: int = MAX_RECOMMENDATIONS,
    *,
    history: Sequence[str] = (),
    state: H3CSessionState | None = None,
    context_parameters: dict[str, str] | None = None,
) -> list[dict]:
    """Return behavior-linked follow-ups from the graph and full catalog."""
    current = _normalize_command(command)
    if current in {"quit", "return"}:
        if state is None:
            state = H3CSessionState()
            for historical_command in history:
                advance_h3c_session_state(state, str(historical_command or ""))
            advance_h3c_session_state(state, current)
        context = state.current_context()
    else:
        context = classify_h3c_command(command, history=history, state=state)
    limit = min(max(limit, 1), MAX_RECOMMENDATIONS)
    if context is None:
        return []

    if context_parameters and current not in {"quit", "return"}:
        merged_parameters = dict(context_parameters)
        merged_parameters.update(context.parameters)
        context = CommandContext(context.action, merged_parameters)

    graph_suggestions: list[dict] = []
    seen: set[str] = set()
    for edge in sorted(
        H3C_BEHAVIOR_GRAPH.get(context.action, ()),
        key=lambda item: _recommendation_edge_priority(item, context.parameters),
        reverse=True,
    ):
        template = _NODES.get(edge.target)
        rendered = _render(template, context.parameters) if template else None
        if not rendered or _normalize_command(rendered) == current or rendered in seen:
            continue
        # The behavior graph intentionally contains every official query,
        # configuration, recovery, and dangerous command.  This first lane of
        # the recommendation policy selects only immediately executable checks;
        # safe configuration roots are admitted separately below with view and
        # risk filtering.
        if not rendered.lower().startswith("display "):
            continue
        if not _command_matches_address_family(
            rendered,
            context.parameters.get("address_family"),
        ):
            continue
        alert = alert_check(rendered, device_profile="h3c_comware")
        if alert and alert.get("action") == "block":
            continue
        effective_priority = _recommendation_edge_priority(edge, context.parameters)
        seen.add(rendered)
        graph_suggestions.append({
            "command": rendered,
            "source": "factory",
            "risk": "confirm" if alert else "normal",
            "reason": edge.reason,
            "priority": effective_priority,
            "behavior": context.action,
            "behavior_label": BEHAVIOR_LABELS[context.action],
        })
        if len(graph_suggestions) >= limit:
            break

    # The hand-authored operational graph deliberately starts with common
    # verification commands.  Add the closest official configuration/action
    # roots from the same behavior so smart recommendations are not restricted
    # to ``display``.  Two catalog neighbors are enough to expose the wider
    # Comware mesh without crowding out the immediately useful checks.
    catalog_quota = min(2, max(limit - 1, 0)) if graph_suggestions else min(2, limit)
    catalog_suggestions: list[dict] = []
    for candidate in _catalog_neighbors(
        context.action,
        current,
        state=state,
        parameters=context.parameters,
    ):
        if len(catalog_suggestions) >= catalog_quota:
            break
        if candidate in seen:
            continue
        alert = alert_check(candidate, device_profile="h3c_comware")
        if alert and alert.get("action") == "block":
            continue
        seen.add(candidate)
        catalog_suggestions.append({
            "command": candidate,
            "source": "factory",
            "risk": _command_risk(candidate, alert=alert),
            "reason": "同一行为域的 Comware 官方命令",
            "priority": 45,
            "behavior": context.action,
            "behavior_label": BEHAVIOR_LABELS[context.action],
            "view": _command_view_label(candidate),
        })
    if not catalog_suggestions:
        return graph_suggestions[:limit]

    graph_limit = max(limit - len(catalog_suggestions), 0)
    return [*graph_suggestions[:graph_limit], *catalog_suggestions][:limit]


def complete_h3c_command(prefix: str, limit: int = 8) -> list[dict]:
    """Complete against every official Comware catalog command root."""
    normalized_prefix = " ".join(str(prefix or "").lstrip().lower().split())
    limit = min(max(limit, 1), 20)
    if not normalized_prefix:
        return []

    priority = {
        command: index
        for index, command in enumerate(_COMPLETION_PRIORITY)
    }
    candidates = [
        command
        for command in (
            set(_catalog_completion_commands()) | set(_RUNTIME_COMPLETION_COMMANDS)
        )
        if command != normalized_prefix and command.startswith(normalized_prefix)
    ]
    candidates.sort(
        key=lambda command: (
            priority.get(command, len(priority)),
            len(command.split()),
            len(command),
            command,
        )
    )

    results = []
    for command in candidates[:limit]:
        context = classify_h3c_command(command)
        alert = alert_check(command, device_profile="h3c_comware")
        results.append({
            "command": command,
            "usage_count": 0,
            "last_used_at": None,
            "source": "catalog",
            "risk": _command_risk(command, alert=alert),
            "behavior": context.action if context else "",
            "behavior_label": BEHAVIOR_LABELS.get(context.action, "") if context else "",
            "view": _command_view_label(command),
        })
    return results


def classify_h3c_command(
    command: str,
    *,
    history: Sequence[str] = (),
    state: H3CSessionState | None = None,
) -> CommandContext | None:
    """Classify a Comware command using catalog, active view, and rules."""
    collapsed = _collapse_command(command)
    normalized = collapsed.lower()
    if not normalized:
        return None

    parameters = _extract_parameters(collapsed)
    view_context = state.current_context() if state is not None else _infer_view_context(history)

    behavior = _catalog_behavior(normalized)
    if behavior is None:
        behavior = _runtime_command_behavior(normalized)
    first_token = normalized.split(maxsplit=1)[0]
    needs_view = normalized in CONTEXT_REQUIRED_MATCH_KEYS or (
        behavior is None and first_token in CONTEXT_REQUIRED_MATCH_KEYS
    )
    if needs_view:
        behavior = None

    # A recognized global command or query is authoritative.  Session view is
    # consulted only for genuinely view-local syntax, never as a blanket
    # fallback for ambiguous ``display`` commands.
    if behavior is None and not needs_view and first_token in _KNOWN_RULE_FIRST_TOKENS:
        behavior = _rule_behavior(normalized)
    matched_catalog_key = _catalog_match_key(normalized)
    ambiguous_view_local = bool(
        view_context is not None
        and first_token != "display"
        and matched_catalog_key in _AMBIGUOUS_CATALOG_MATCH_KEYS
    )
    unresolved_specialized_view_command = bool(
        behavior is None
        and first_token != "display"
        and first_token in _SPECIALIZED_VIEW_FIRST_TOKENS
    )
    used_view_context = bool(view_context is not None and (
        needs_view
        or ambiguous_view_local
        or unresolved_specialized_view_command
        or _VIEW_LOCAL_COMMAND.search(normalized)
    ))
    if used_view_context:
        assert view_context is not None
        behavior = view_context.action

    if behavior is None and needs_view:
        return None

    if behavior is None:
        return None
    specialized = _specialize_behavior(behavior, normalized)
    if view_context is not None and (
        used_view_context
        or _behaviors_share_domain(specialized, view_context.action)
    ):
        for name, value in view_context.parameters.items():
            parameters.setdefault(name, value)
    return CommandContext(specialized, parameters)


def _behaviors_share_domain(left: str, right: str) -> bool:
    return (
        left == right
        or left.startswith(f"{right}.")
        or right.startswith(f"{left}.")
    )


def advance_h3c_session_state(
    state: H3CSessionState,
    command: str,
) -> CommandContext | None:
    """Apply one forwarded command and return its post-command intent."""
    normalized = _normalize_command(command)
    if not normalized:
        return None
    if normalized == "return":
        state.view_stack.clear()
        return None
    if normalized == "quit":
        if state.view_stack:
            state.view_stack.pop()
        return state.current_context()
    if normalized == "system-view":
        state.view_stack[:] = [H3CViewFrame(
            entry_command="system-view",
            action=None,
            parameters={},
            view_names=("系统视图",),
        )]
        return None

    entry = _matched_view_entry(normalized)
    if entry is not None:
        match_key, behavior = entry
        if _command_allowed_in_current_view(match_key, state):
            parameters = (
                dict(state.view_stack[-1].parameters)
                if state.view_stack
                else {}
            )
            parameters.update(_extract_parameters(normalized))
            view_names = _result_view_names(match_key, state)
            specialized = _specialize_behavior(behavior, normalized)
            state.view_stack.append(H3CViewFrame(
                entry_command=normalized,
                action=specialized,
                parameters=parameters,
                view_names=view_names,
            ))
            return CommandContext(specialized, dict(parameters))

    return classify_h3c_command(command, state=state)


def _catalog_behavior(command: str) -> str | None:
    behavior = _EXACT_BEHAVIORS.get(command)
    if behavior is not None:
        return behavior
    first_token = command.split(maxsplit=1)[0]
    for match_key, candidate in _PREFIX_BEHAVIORS.get(first_token, ()):
        if command.startswith(f"{match_key} "):
            return candidate
    # Some catalog roots (for example ``mac-address``) take free-form
    # parameters immediately after one keyword.  Reuse a unique one-keyword
    # catalog behavior only when that root is not view-local; ambiguous local
    # commands must continue through session-view inference instead.
    if first_token != "display" and first_token not in CONTEXT_REQUIRED_MATCH_KEYS:
        return _EXACT_BEHAVIORS.get(first_token)
    return None


def _infer_view_context(history: Sequence[str]) -> CommandContext | None:
    """Replay legacy history through the same incremental Comware adapter."""
    state = H3CSessionState()
    for raw_command in history:
        advance_h3c_session_state(state, str(raw_command or ""))
    return state.current_context()


def _runtime_command_behavior(command: str) -> str | None:
    for match_key, behavior in sorted(
        _RUNTIME_COMMAND_BEHAVIORS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if command == match_key or command.startswith(f"{match_key} "):
            return behavior
    return None


def _catalog_match_key(command: str) -> str | None:
    if command in COMMAND_VIEWS:
        return command
    first_token = command.split(maxsplit=1)[0]
    for match_key in _COMMAND_VIEW_MATCH_KEYS:
        if not match_key.startswith(first_token):
            continue
        if command.startswith(f"{match_key} "):
            return match_key
    return None


def _rule_behavior(command: str) -> str | None:
    for pattern, candidate in _RULES:
        if pattern.search(command):
            return candidate
    return None


def _matched_view_entry(command: str) -> tuple[str, str] | None:
    for match_key, behavior in _VIEW_ENTRY_ITEMS:
        if command == match_key or command.startswith(f"{match_key} "):
            return match_key, behavior
    for pattern, behavior in _VIEW_ANCHORS:
        if behavior is None or not pattern.search(command):
            continue
        for match_key in _COMMAND_VIEW_MATCH_KEYS:
            if command == match_key or command.startswith(f"{match_key} "):
                return match_key, behavior
    return None


def _view_entry_behavior(command: str) -> str | None:
    entry = _matched_view_entry(command)
    if entry is None:
        return None
    return _specialize_behavior(entry[1], command)


def _result_view_names(
    match_key: str,
    state: H3CSessionState,
) -> tuple[str, ...]:
    if match_key == "area" and state.current_view_names() == ("OSPFv3 视图",):
        return ("OSPFv3 区域视图",)
    return _STATIC_RESULT_VIEW_NAMES.get(match_key, ())


def _command_allowed_in_current_view(
    command: str,
    state: H3CSessionState,
) -> bool:
    allowed_views = _command_allowed_views(command)
    if not allowed_views:
        return False
    allowed_text = "\n".join(allowed_views)
    current_views = state.current_view_names()
    in_user_view = current_views == ("用户视图",)

    if "除用户视图外的任意视图" in allowed_text:
        return not in_user_view
    if "任意视图" in allowed_text:
        return True
    if in_user_view:
        return any(view == "用户视图" for view in allowed_views)
    return any(
        current_view in allowed_text
        or (
            current_view == "ACL 视图"
            and "ACL 视图" in allowed_text
        )
        for current_view in current_views
    )


def _command_allowed_views(command: str) -> tuple[str, ...]:
    normalized = _normalize_command(command)
    exact = COMMAND_VIEWS.get(normalized)
    if exact:
        return exact
    for match_key in _COMMAND_VIEW_MATCH_KEYS:
        if normalized.startswith(f"{match_key} "):
            return COMMAND_VIEWS[match_key]
    return ()


def _specialize_behavior(behavior: str, command: str) -> str:
    """Refine broad PDF chapters into operationally useful sub-behaviors."""
    if behavior == "system.health":
        if re.search(r"\b(?:cpu-usage|process\s+cpu)\b", command):
            return "system.health.cpu"
        if re.search(r"\b(?:memory(?:-threshold)?|process\s+memory)\b", command):
            return "system.health.memory"

    if behavior == "ip.dhcp":
        if re.search(r"\bipv6\s+dhcp\b|\bdhcpv6\b", command):
            return "ip.dhcp.ipv6"
        if re.search(r"\bdhcp\s+snooping\b", command):
            return "ip.dhcp.snooping"
        if re.search(r"\bdhcp\s+relay\b", command):
            return "ip.dhcp.relay"
        if re.search(r"\bdhcp\s+server\b", command):
            return "ip.dhcp.server"

    if behavior == "routing.rip" and re.search(r"\bripng\b", command):
        return "routing.ripng"

    if behavior == "multicast":
        if re.search(r"\bigmp-snooping\b", command):
            return "multicast.igmp"
        if re.search(r"\bmld-snooping\b", command):
            return "multicast.mld"
        if re.search(r"\bpim-snooping\b", command):
            return "multicast.pim"
        if re.search(r"\bmulticast-vlan\b", command):
            return "multicast.vlan"

    if behavior in {"security.authentication", "security.access"}:
        if re.search(r"\bdot1x\b", command):
            return "security.access.dot1x"
        if re.search(r"\bmac-authentication\b", command):
            return "security.access.mac"
        if re.search(r"\bldap\b", command):
            return "security.authentication.ldap"
        if re.search(r"\bhwtacacs\b", command):
            return "security.authentication.hwtacacs"
        if re.search(r"\bradius\b", command):
            return "security.authentication.aaa"

    if behavior == "security.crypto":
        if re.search(r"\b(?:ipsec|ikev2?|isakmp)\b", command):
            return "security.crypto.ipsec"
        if re.search(r"\bpki\b", command):
            return "security.crypto.pki"
        if re.search(r"\b(?:ssh|public-key)\b", command):
            return "security.crypto.ssh"
        if re.search(r"\bssl\b|\btls(?:1(?:\.\d)?)?\b", command):
            return "security.crypto.ssl"

    if behavior == "reliability":
        if re.search(r"\bbfd\b", command):
            return "reliability.bfd"
        if re.search(r"\bvrrp\b", command):
            return "reliability.vrrp"
        if re.search(r"\btrack\b", command):
            return "reliability.track"
        if re.search(r"\b(?:erps|rrpp|smart-link|monitor-link)\b", command):
            return "reliability.ring"
        if re.search(r"\b(?:cfd|dldp|oam)\b", command):
            return "reliability.oam"

    if behavior == "observability":
        if re.search(r"\b(?:logbuffer|info-center|diagnostic-logfile)\b", command):
            return "observability.logging"
        if re.search(r"\b(?:snmp|rmon)\b", command):
            return "observability.snmp"
        if re.search(r"\bnqa\b", command):
            return "observability.nqa"
        if re.search(r"\b(?:ntp-service|sntp)\b", command):
            return "observability.time"
        if re.search(r"\b(?:sflow|mirroring-group)\b", command):
            return "observability.traffic"

    if behavior == "automation":
        if re.search(r"\bcwmp\b", command):
            return "automation.cwmp"
        if re.search(r"\bnetconf\b", command):
            return "automation.netconf"
        if re.search(r"\b(?:eaa|python|tcl)\b", command):
            return "automation.eaa"

    return behavior


_CATALOG_TERM_NOISE = frozenset({
    "all",
    "brief",
    "configuration",
    "current",
    "display",
    "statistics",
    "status",
    "undo",
    "verbose",
})
_NEVER_RECOMMEND_PREFIXES = (
    "boot-loader",
    "delete",
    "erase",
    "format",
    "reboot",
    "reset",
    "schedule reboot",
    "shutdown",
)


@lru_cache(maxsize=1)
def _catalog_completion_commands() -> tuple[str, ...]:
    """Return all unique, user-fillable roots from the 2815-row catalog."""
    commands = {
        match_key
        for _, _, match_key, _, _, _ in COMMAND_CATALOG
        if match_key and re.search(r"[a-z0-9]", match_key, re.IGNORECASE)
    }
    return tuple(sorted(commands))


@lru_cache(maxsize=1)
def _catalog_commands_by_behavior() -> dict[str, tuple[str, ...]]:
    commands: defaultdict[str, set[str]] = defaultdict(set)
    for _, _, match_key, behavior, _, section in COMMAND_CATALOG:
        if not match_key or not re.search(r"[a-z0-9]", match_key, re.IGNORECASE):
            continue
        section_behavior = _section_behavior(behavior, section)
        commands[_specialize_behavior(section_behavior, match_key)].add(match_key)
    return {
        behavior: tuple(sorted(values))
        for behavior, values in commands.items()
    }


def _catalog_neighbors(
    behavior: str,
    current: str,
    *,
    state: H3CSessionState | None = None,
    parameters: dict[str, str] | None = None,
) -> tuple[str, ...]:
    """Rank official non-display roots nearest to the current behavior node."""
    current_terms = _catalog_terms(current)
    address_family = (parameters or {}).get("address_family")
    diagnostic_ipv6 = (
        _uses_ipv6_syntax(current)
        if behavior == "network.diagnostics"
        else None
    )
    ranked: list[tuple[int, int, int, str]] = []
    commands_by_behavior = _catalog_commands_by_behavior()
    parent_overrides = {
        "routing.ospfv3": ("routing.ospf", "routing.base"),
        "routing.policy": ("routing.base",),
        "routing.policy.ipv6": ("routing.policy", "routing.base"),
        "routing.pbr": ("routing.base",),
        "routing.pbr.ipv6": ("routing.pbr", "routing.base"),
        "routing.static": ("routing.base",),
        "routing.static.ipv6": ("routing.static", "routing.base"),
    }
    inherited = [
        candidate_behavior
        for candidate_behavior in commands_by_behavior
        if behavior.startswith(f"{candidate_behavior}.")
    ]
    behavior_chain = tuple(dict.fromkeys((
        behavior,
        *parent_overrides.get(behavior, ()),
        *sorted(inherited, key=len, reverse=True),
    )))
    candidate_sources: dict[str, int] = {}
    for distance, source_behavior in enumerate(behavior_chain):
        for candidate in commands_by_behavior.get(source_behavior, ()):
            candidate_sources.setdefault(candidate, distance)

    for candidate, distance in candidate_sources.items():
        if (
            (candidate == current or current.startswith(f"{candidate} "))
            or candidate.startswith("display ")
            or candidate.startswith(_NEVER_RECOMMEND_PREFIXES)
            or (alert_check(candidate, device_profile="h3c_comware") or {}).get("action") == "block"
            or (
                diagnostic_ipv6 is not None
                and _uses_ipv6_syntax(candidate) != diagnostic_ipv6
            )
            or not _command_matches_address_family(candidate, address_family)
        ):
            continue
        if state is None:
            if (
                candidate in CONTEXT_REQUIRED_MATCH_KEYS
                or candidate in SPECIALIZED_VIEW_MATCH_KEYS
                or not _is_general_recommendation_command(candidate)
            ):
                continue
        elif not _command_allowed_in_current_view(candidate, state):
            continue
        candidate_terms = _catalog_terms(candidate)
        if (
            not current.startswith("display ")
            and candidate_terms
            and candidate_terms <= current_terms
        ):
            continue
        overlap = len(current_terms & candidate_terms)
        if overlap == 0:
            continue
        # Exact sub-behavior membership is itself a deterministic semantic edge;
        # inherited parent commands still require shared feature terms.
        score = overlap * 100 + (40 if distance == 0 else max(0, 20 - distance * 5))
        ranked.append((-score, len(candidate.split()), len(candidate), candidate))
    ranked.sort()
    return tuple(item[3] for item in ranked)


def _is_general_recommendation_command(command: str) -> bool:
    general_views = {"用户视图", "系统视图", "任意视图"}
    return any(view in general_views for view in COMMAND_VIEWS.get(command, ()))


def _catalog_terms(command: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9.-]*", command.lower())
        if token not in _CATALOG_TERM_NOISE
    }


def _uses_ipv6_syntax(command: str) -> bool:
    return bool(
        re.search(r"(?:^|\s)ipv6(?:\s|$)|[0-9a-f]*:[0-9a-f:]", command, re.IGNORECASE)
    )


def _command_matches_address_family(command: str, address_family: str | None) -> bool:
    if address_family == "ipv4":
        return not _uses_ipv6_syntax(command)
    if address_family != "ipv6":
        return True
    normalized = _normalize_command(command)
    if _uses_ipv6_syntax(normalized):
        return True
    return not normalized.startswith((
        "display arp",
        "display fib",
        "display ip ",
    ))


def _command_risk(command: str, *, alert: dict | None = None) -> str:
    del command
    # Keep confirmation-required commands discoverable and align their badge
    # with the execution gate; only hard blocks are filtered above.
    return "confirm" if alert is not None else "normal"


def _command_view_label(command: str) -> str:
    return " / ".join(
        view.replace("\n", "、")
        for view in COMMAND_VIEWS.get(command, ())
    )


def _extract_parameters(command: str) -> dict[str, str]:
    parameters: dict[str, str] = {}
    lowered = command.lower()
    if re.search(r"(?:^|\s)(?:ipv6|ospfv3|ripng)(?:\s|$)", lowered):
        parameters["address_family"] = "ipv6"
    elif re.search(
        r"(?:^|\s)address-family\s+ipv4(?:\s|$)|^display\s+ip\b",
        lowered,
    ):
        parameters["address_family"] = "ipv4"
    interface = re.search(
        r"(?<![-\w])((?:ten-gigabitethernet|twenty-fivegige|fortygige|"
        r"hundredgige|gigabitethernet|xgige|ethernet|bridge-aggregation|"
        r"route-aggregation|vlan-interface|loopback|tunnel|serial|dialer|"
        r"null)\s*\d[\w./:-]*)\b",
        command,
        re.IGNORECASE,
    )
    if interface:
        canonical_interface = re.sub(r"\s+", " ", interface.group(1)).strip()
        canonical_interface = re.sub(
            r"(?<=[A-Za-z])(?=\d)",
            " ",
            canonical_interface,
            count=1,
        )
        parameters["interface"] = canonical_interface
    vlan = re.search(r"(?<![-\w])vlan\s+(\d{1,4})\b", command, re.IGNORECASE)
    if vlan and 1 <= int(vlan.group(1)) <= 4094:
        parameters["vlan"] = vlan.group(1)

    rip_process = re.search(r"(?<![-\w])rip(?:ng)?\s+(\d{1,5})\b", command, re.IGNORECASE)
    if rip_process and 1 <= int(rip_process.group(1)) <= 65535:
        parameters["rip_process"] = rip_process.group(1)

    openflow_instance = re.search(
        r"\bopenflow\s+instance\s+(\d{1,5})\b",
        command,
        re.IGNORECASE,
    )
    if openflow_instance and 1 <= int(openflow_instance.group(1)) <= 65535:
        parameters["openflow_instance"] = openflow_instance.group(1)

    acl_number = re.search(
        r"(?<![-\w])acl(?:\s+(?:ipv6|mac))?(?:\s+number)?\s+(\d{1,5})\b",
        command,
        re.IGNORECASE,
    )
    if acl_number and 1 <= int(acl_number.group(1)) <= 65535:
        parameters["acl_number"] = acl_number.group(1)

    pki_domain = re.search(
        r"\bpki\s+domain\s+([A-Za-z0-9_.:-]{1,64})\b",
        command,
        re.IGNORECASE,
    )
    if pki_domain:
        parameters["pki_domain"] = pki_domain.group(1)

    nqa_entry = re.search(
        r"\bnqa\s+entry\s+([A-Za-z0-9_.:-]{1,32})\s+([A-Za-z0-9_.:-]{1,32})\b",
        command,
        re.IGNORECASE,
    )
    if nqa_entry:
        parameters["nqa_admin"] = nqa_entry.group(1)
        parameters["nqa_operation"] = nqa_entry.group(2)

    ospf_process = re.search(
        r"(?<![-\w])ospf(?:v3)?\s+(\d{1,5})\b",
        command,
        re.IGNORECASE,
    )
    if ospf_process and 1 <= int(ospf_process.group(1)) <= 65535:
        parameters["ospf_process"] = ospf_process.group(1)

    named_patterns = {
        "dhcp_pool": r"\bdhcp\s+server\s+ip-pool\s+([A-Za-z0-9_.:-]{1,63})\b",
        "qos_policy": r"^qos\s+policy\s+([A-Za-z0-9_.:-]{1,31})\b",
        "qos_classifier": r"^traffic\s+classifier\s+([A-Za-z0-9_.:-]{1,31})\b",
        "qos_behavior": r"^traffic\s+behavior\s+([A-Za-z0-9_.:-]{1,31})\b",
        "radius_scheme": r"^radius\s+scheme\s+([A-Za-z0-9_.:-]{1,32})\b",
        "ldap_scheme": r"^ldap\s+scheme\s+([A-Za-z0-9_.:-]{1,32})\b",
        "hwtacacs_scheme": r"^hwtacacs\s+scheme\s+([A-Za-z0-9_.:-]{1,32})\b",
    }
    for name, pattern in named_patterns.items():
        match = re.search(pattern, command, re.IGNORECASE)
        if match:
            parameters[name] = match.group(1)

    mac_address = re.search(
        r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{4}-){2}[0-9A-Fa-f]{4}(?![0-9A-Fa-f])|"
        r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])",
        command,
    )
    if mac_address:
        compact_mac = re.sub(r"[-:]", "", mac_address.group(0)).lower()
        parameters["mac_address"] = "-".join(
            compact_mac[index:index + 4]
            for index in range(0, 12, 4)
        )

    for candidate in re.findall(
        r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f]{0,4}(?:/\d{1,3})?(?![\w:])",
        command,
    ):
        try:
            interface_address = ipaddress.ip_interface(candidate)
        except ValueError:
            continue
        if isinstance(interface_address.ip, ipaddress.IPv6Address):
            parameters["ipv6_target"] = candidate.lower()
            parameters["address_family"] = "ipv6"
            break

    for candidate in re.findall(r"(?<![\w:])(?:\d{1,3}\.){3}\d{1,3}(?![\w:])", command):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if isinstance(address, ipaddress.IPv4Address):
            parameters["target"] = str(address)
            parameters.setdefault("address_family", "ipv4")
            break
    return parameters


def _render(template: str, parameters: dict[str, str]) -> str | None:
    try:
        return template.format(**parameters)
    except KeyError:
        return None


def _normalize_command(command: str) -> str:
    return _collapse_command(command).lower()


def _collapse_command(command: str) -> str:
    normalized = unicodedata.normalize("NFKC", command)
    return re.sub(r"\s+", " ", normalized).strip()


def _catalog_display_rank(command: str) -> tuple[int, int, int, str]:
    preferred = (
        " brief", " summary", " status", " statistics", " configuration",
        " interface", " session", " peer", " neighbor", " verbose",
    )
    marker_rank = next(
        (index for index, marker in enumerate(preferred) if marker in command),
        len(preferred),
    )
    return marker_rank, len(command.split()), len(command), command


def _augment_behavior_graphs_from_catalog() -> None:
    """Attach every official command root to its runtime behavior.

    Hand-written display checks retain high graph priority for useful traversal,
    while configuration, recovery, specialized-view, and risky roots are all
    preserved as graph nodes.  Recommendation filtering happens elsewhere.
    """
    official_titles = {_normalize_command(title) for _, title, _, _, _, _ in COMMAND_CATALOG}
    catalog_commands = sorted({
        _normalize_command(match_key)
        for _, _, match_key, _, _, _ in COMMAND_CATALOG
        if match_key and re.search(r"[a-z0-9]", match_key, re.IGNORECASE)
    })
    node_by_command = {
        _normalize_command(template): node
        for node, template in _NODES.items()
        if "{" not in template
    }
    for index, command in enumerate(catalog_commands, start=1):
        if command in node_by_command:
            continue
        node = f"catalog.command.{index:04d}"
        _NODES[node] = command
        node_by_command[command] = node

    commands_by_behavior: defaultdict[str, set[str]] = defaultdict(set)
    display_commands_by_behavior: defaultdict[str, set[str]] = defaultdict(set)
    for _, title, match_key, behavior, _, section in COMMAND_CATALOG:
        normalized_title = _normalize_command(title)
        normalized_key = _normalize_command(match_key)
        if not normalized_key or not re.search(r"[a-z0-9]", normalized_key, re.IGNORECASE):
            continue
        runtime_behavior = _specialize_behavior(
            _section_behavior(behavior, section),
            normalized_key,
        )
        commands_by_behavior[runtime_behavior].add(normalized_key)
        if normalized_key in BARE_DISPLAY_COMMANDS and normalized_title == normalized_key:
            display_commands_by_behavior[runtime_behavior].add(normalized_key)

    # Enrich the established graph without disturbing its first five choices.
    for behavior, existing in tuple(H3C_BEHAVIOR_GRAPH.items()):
        edges = list(existing)
        seen = {edge.target for edge in edges}
        for offset, command in enumerate(
            sorted(display_commands_by_behavior.get(behavior, ()), key=_catalog_display_rank)[:12]
        ):
            node = node_by_command.get(command)
            if not node or node in seen:
                continue
            edges.append(FlowEdge(
                node,
                max(20, 55 - offset * 3),
                f"补充核查：{command}",
            ))
            seen.add(node)
        for command in sorted(commands_by_behavior.get(behavior, ())):
            node = node_by_command.get(command)
            if not node or node in seen:
                continue
            edges.append(FlowEdge(
                node,
                15,
                f"官方行为节点：{command}",
            ))
            seen.add(node)
        H3C_BEHAVIOR_GRAPH[behavior] = tuple(edges)

    established_behaviors = set(H3C_BEHAVIOR_GRAPH)
    parent_overrides = {
        "routing.ospfv3": "routing.ospf",
        "routing.policy": "routing.base",
        "routing.policy.ipv6": "routing.base",
        "routing.pbr": "routing.base",
        "routing.pbr.ipv6": "routing.base",
        "routing.static": "routing.base",
        "routing.static.ipv6": "routing.base",
    }
    for behavior in sorted(set(BEHAVIOR_LABELS) - established_behaviors, key=lambda item: (item.count("."), item)):
        parent_candidates = [
            candidate
            for candidate in established_behaviors
            if behavior.startswith(f"{candidate}.")
        ]
        if behavior in parent_overrides:
            parent = parent_overrides[behavior]
        elif not parent_candidates:
            raise AssertionError(f"H3C behavior has no parent graph: {behavior}")
        else:
            parent = max(parent_candidates, key=len)
        edges: list[FlowEdge] = []
        seen: set[str] = set()
        for offset, command in enumerate(
            sorted(display_commands_by_behavior.get(behavior, ()), key=_catalog_display_rank)[:12]
        ):
            node = node_by_command.get(command)
            if not node or node in seen:
                continue
            edges.append(FlowEdge(
                node,
                max(75, 130 - offset * 5),
                f"核查{BEHAVIOR_LABELS[behavior]}：{command}",
            ))
            seen.add(node)
        for edge in H3C_BEHAVIOR_GRAPH[parent]:
            if edge.target in seen:
                continue
            edges.append(FlowEdge(
                edge.target,
                min(edge.priority, 70),
                f"{BEHAVIOR_LABELS[parent]}基础核查：{edge.reason}",
            ))
            seen.add(edge.target)
            if len(edges) >= 14:
                break
        for command in sorted(commands_by_behavior.get(behavior, ())):
            node = node_by_command.get(command)
            if not node or node in seen:
                continue
            edges.append(FlowEdge(
                node,
                15,
                f"{BEHAVIOR_LABELS[behavior]}官方命令：{command}",
            ))
            seen.add(node)
        if len(edges) < 5:
            raise AssertionError(f"H3C behavior graph is too small: {behavior}")
        H3C_BEHAVIOR_GRAPH[behavior] = tuple(edges)
        established_behaviors.add(behavior)


_augment_behavior_graphs_from_catalog()


_PARAMETERIZED_BEHAVIOR_TARGETS: dict[str, tuple[str, ...]] = {
    "switching.mac": ("mac.current",),
    "ip.dhcp.server": ("dhcp.server_pool_named",),
    "network.diagnostics": (
        "route.target", "route.ipv6_target", "route.table", "route.ipv6",
        "route.summary", "route.ipv6_summary",
    ),
    "ip.ipv6": ("route.ipv6_target", "route.ipv6", "route.ipv6_summary", "route.ipv6_statistics"),
    "routing.base": ("route.ipv6_target", "route.ipv6", "route.ipv6_summary", "route.ipv6_statistics"),
    "routing.static.ipv6": ("route.ipv6_target", "route.ipv6", "route.ipv6_summary", "route.ipv6_statistics"),
    "routing.ospf": (
        "ospf.peers_process", "ospf.interfaces_process",
        "ospf.routes_process", "ospf.lsdb_process",
    ),
    "routing.ospfv3": (
        "ospfv3.peers_process", "ospfv3.interfaces_process",
        "ospfv3.routes_process", "ospfv3.lsdb_process",
    ),
    "policy.qos": ("qos.policy_named", "qos.classifier_named", "qos.behavior_named"),
    "policy.qos.classifier": ("qos.classifier_named", "qos.policy_named"),
    "policy.qos.behavior": ("qos.behavior_named", "qos.policy_named"),
    "security.authentication.aaa": ("auth.radius_scheme_named",),
    "security.authentication.ldap": ("auth.ldap_named",),
    "security.authentication.hwtacacs": ("auth.hwtacacs_named",),
    "observability.nqa": ("observe.nqa_entry", "observe.nqa_entry_history"),
}
for _behavior, _targets in _PARAMETERIZED_BEHAVIOR_TARGETS.items():
    _existing_edges = H3C_BEHAVIOR_GRAPH[_behavior]
    _parameter_edges = tuple(
        FlowEdge(target, 170 - index * 5, "沿用当前命令参数进行精确核查")
        for index, target in enumerate(_targets)
    )
    _remaining_edges = tuple(
        edge for edge in _existing_edges if edge.target not in _targets
    )
    H3C_BEHAVIOR_GRAPH[_behavior] = (*_parameter_edges, *_remaining_edges)


def catalog_command_titles() -> tuple[str, ...]:
    """Expose the complete official title list for verification and tooling."""
    return tuple(entry[1] for entry in COMMAND_CATALOG)


assert COMMAND_COUNT == len(COMMAND_CATALOG)
assert set(H3C_BEHAVIOR_GRAPH) == set(BEHAVIOR_LABELS)
assert all(
    edge.target in H3C_COMMAND_NODES
    for edges in H3C_BEHAVIOR_GRAPH.values()
    for edge in edges
)
assert set(H3C_COMMAND_NODES) == {
    edge.target
    for edges in H3C_BEHAVIOR_GRAPH.values()
    for edge in edges
}
assert set(_catalog_completion_commands()).issubset({
    _normalize_command(_NODES[edge.target])
    for edges in H3C_BEHAVIOR_GRAPH.values()
    for edge in edges
    if "{" not in _NODES[edge.target]
})


__all__ = [
    "BEHAVIOR_LABELS",
    "COMMAND_COUNT",
    "H3C_COMMAND_NODES",
    "H3C_BEHAVIOR_GRAPH",
    "H3C_RECOMMENDATION_NODES",
    "H3C_RECOMMENDATION_SYNTAX_ROOTS",
    "H3CSessionState",
    "advance_h3c_session_state",
    "complete_h3c_command",
    "MAX_RECOMMENDATIONS",
    "catalog_command_titles",
    "classify_h3c_command",
    "recommend_next_h3c_commands",
]
