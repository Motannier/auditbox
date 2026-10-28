"""Factory Linux completion catalog and deterministic operations graph.

The graph deliberately does not learn from audit history.  It models the complete
factory command/behavior mesh, including observation, change, recovery, and risky
actions.  The terminal recommender applies its own read-only policy when projecting
that graph into at most five follow-up commands.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit

from auditbox.security.alert import check as alert_check

MAX_RECOMMENDATIONS = 5


_PACKAGE_EXECUTABLE_FAMILIES = {
    "apt": "debian",
    "apt-get": "debian",
    "apt-cache": "debian",
    "aptitude": "debian",
    "dpkg": "debian",
    "dpkg-query": "debian",
    "dnf": "rpm",
    "yum": "rpm",
    "rpm": "rpm",
    "apk": "apk",
    "pacman": "pacman",
    "zypper": "zypper",
    "snap": "snap",
    "flatpak": "flatpak",
}
_PACKAGE_TARGET_FAMILIES = {
    "package.debian": "debian",
    "package.debian_info": "debian",
    "package.dpkg_info": "debian",
    "package.rpm": "rpm",
    "package.rpm_info": "rpm",
    "package.dnf_info": "rpm",
    "package.apk": "apk",
    "package.pacman": "pacman",
    "package.zypper": "zypper",
    "package.snap": "snap",
    "package.flatpak": "flatpak",
}


@dataclass(frozen=True)
class FlowEdge:
    target: str
    priority: int
    reason: str


@dataclass(frozen=True)
class CommandContext:
    action: str
    parameters: dict[str, str]


_NODES: dict[str, str] = {
    "directory.list.details": "ls -lah",
    "directory.location": "pwd",
    "directory.size": "du -sh .",
    "directory.mount": "findmnt -T .",
    "directory.metadata": "stat .",
    "storage.capacity.types": "df -hT",
    "storage.inodes": "df -ih",
    "storage.blocks": "lsblk -f",
    "storage.mounts": "findmnt",
    "storage.var_usage": "du -xhd1 /var | sort -h",
    "storage.capacity": "df -h",
    "storage.root_usage": "du -xhd1 / | sort -h",
    "system.load": "uptime",
    "system.memory": "free -h",
    "system.vmstat": "vmstat 1 5",
    "system.cpu_processes": "ps aux --sort=-%cpu | head -n 15",
    "system.memory_processes": "ps aux --sort=-%mem | head -n 15",
    "system.failed_services": "systemctl --failed --no-pager",
    "system.kernel_errors": "dmesg --level=err,warn",
    "system.identity": "hostnamectl",
    "system.kernel": "uname -a",
    "system.boot_errors": "journalctl -p err -b --no-pager",
    "process.snapshot": "top -b -n 1",
    "process.tree": "ps -ef --forest",
    "service.logs": "journalctl -u {service} -n 100 --no-pager",
    "service.logs.hour": "journalctl -u {service} --since '1 hour ago' --no-pager",
    "service.logs.errors": "journalctl -u {service} -p err -b --no-pager",
    "service.status": "systemctl status {service} --no-pager",
    "service.properties": "systemctl show {service} --no-pager",
    "service.active": "systemctl is-active {service}",
    "service.enabled": "systemctl is-enabled {service}",
    "service.unit": "systemctl cat {service} --no-pager",
    "service.overview": "systemctl --failed --no-pager",
    "service.running": "systemctl list-units --type=service --state=running --no-pager",
    "service.files": "systemctl list-unit-files --type=service --no-pager",
    "network.address.brief": "ip -brief addr",
    "network.address": "ip addr",
    "network.link": "ip -brief link",
    "network.route": "ip route",
    "network.rules": "ip rule",
    "network.neighbors": "ip neigh",
    "network.sockets": "ss -lntp",
    "network.socket_summary": "ss -s",
    "network.established": "ss -tan state established",
    "network.route_target": "ip route get {target}",
    "network.ping_target": "ping -c 4 {target}",
    "network.resolve_target": "getent ahosts {target}",
    "network.dns_config": "cat /etc/resolv.conf",
    "network.resolved_status": "resolvectl status",
    "users.current": "who",
    "users.sessions": "w",
    "users.recent": "last -n 10",
    "users.accounts": "getent passwd",
    "users.groups": "getent group",
    "schedule.cron": "crontab -l",
    "schedule.timers": "systemctl list-timers --all --no-pager",
    "package.debian": "dpkg -l",
    "package.rpm": "rpm -qa",
    "firewall.nft": "nft list ruleset",
    "firewall.iptables": "iptables -L -n -v",
}


# The original graph covered the first-line checks used in the terminal demo.
# These hand-authored nodes extend its high-quality observational lane.  The full
# catalog is attached later as complete behavior nodes, including change and risky
# actions; recommendation policy is intentionally applied only at projection time.
_NODES.update({
    # Files, directories, permissions, and archives.
    "file.type": "file -- {path}",
    "file.path": "readlink -f -- {path}",
    "file.metadata": "stat -- {path}",
    "file.acl": "getfacl -p -- {path}",
    "file.open_handles": "lsof -- {path}",
    "file.checksum": "sha256sum -- {path}",
    "file.deleted_open": "lsof +L1",
    "file.large_here": "find . -xdev -type f -size +100M -printf '%s %p\\n' | sort -nr | head -n 50",
    "file.recent_here": "find . -xdev -type f -printf '%T@ %p\\n' | sort -nr | head -n 50",
    "file.permissions_here": "find . -maxdepth 2 -printf '%M %u:%g %p\\n' | head -n 100",
    "file.umask": "umask -S",
    "archive.list": "tar -tf {path}",
    # Filesystems, block devices, LVM, RAID, SMART, and IO.
    "storage.blkid": "blkid",
    "storage.block_details": "lsblk -o NAME,PATH,SIZE,TYPE,FSTYPE,FSVER,MOUNTPOINTS,MODEL,SERIAL",
    "storage.mount_options": "findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS",
    "storage.filesystems": "cat /proc/filesystems",
    "storage.swaps": "swapon --show",
    "storage.lvm_pvs": "pvs -o+pv_used",
    "storage.lvm_vgs": "vgs",
    "storage.lvm_lvs": "lvs -a -o+devices",
    "storage.raid_status": "cat /proc/mdstat",
    "storage.raid_scan": "mdadm --detail --scan",
    "storage.smart": "smartctl -a {device}",
    "storage.io": "iostat -xz 1 3",
    "storage.kernel_errors": "dmesg --level=err,warn | tail -n 100",
    # OS, boot, kernel, hardware, and time.
    "system.os_release": "cat /etc/os-release",
    "system.machine": "uname -m",
    "system.boot_time": "uptime -s",
    "system.boot_previous_errors": "journalctl -b -1 -p err --no-pager",
    "system.boot_blame": "systemd-analyze blame --no-pager",
    "system.boot_chain": "systemd-analyze critical-chain --no-pager",
    "system.time": "timedatectl status",
    "system.time_sources": "chronyc sources -v",
    "system.time_tracking": "chronyc tracking",
    "system.cpu": "lscpu",
    "system.hardware_pci": "lspci -nn",
    "system.hardware_usb": "lsusb",
    "system.hardware_dmi": "dmidecode -t system",
    "system.temperatures": "sensors",
    "system.modules": "lsmod",
    "system.cmdline": "cat /proc/cmdline",
    "system.sysctl_changed": "sysctl -a",
    "system.entropy": "cat /proc/sys/kernel/random/entropy_avail",
    # Processes and performance.
    "process.pid": "ps -p {pid} -o pid,ppid,user,stat,lstart,etime,%cpu,%mem,args",
    "process.pid_status": "cat /proc/{pid}/status",
    "process.pid_limits": "cat /proc/{pid}/limits",
    "process.pid_files": "lsof -p {pid}",
    "process.pid_tree": "pstree -aps {pid}",
    "process.io": "pidstat -d 1 5",
    "process.cpu": "pidstat -u 1 5",
    "process.memory": "pidstat -r 1 5",
    "process.scheduler": "ps -eo pid,ppid,ni,cls,rtprio,pri,stat,comm --sort=-pri | head -n 30",
    "performance.cpu": "mpstat -P ALL 1 3",
    "performance.disk": "iostat -xz 1 3",
    "performance.history": "sar -A 1 3",
    "performance.pressure": "cat /proc/pressure/cpu; cat /proc/pressure/memory; cat /proc/pressure/io",
    # systemd services and logs.
    "service.dependencies": "systemctl list-dependencies {service} --no-pager",
    "service.reverse_dependencies": "systemctl list-dependencies --reverse {service} --no-pager",
    "service.processes": "systemctl status {service} --no-pager",
    "service.journal_disk": "journalctl --disk-usage",
    "service.journal_errors": "journalctl -p err -b --no-pager",
    # Network links, routing, reachability, DNS, sockets, and packet capture.
    "network.address_stats": "ip -s address",
    "network.link_details": "ip -details -statistics link",
    "network.link_device": "ip -details -statistics link show dev {interface}",
    "network.ethtool": "ethtool {interface}",
    "network.ethtool_stats": "ethtool -S {interface}",
    "network.bridge_links": "bridge link show",
    "network.bridge_vlans": "bridge vlan show",
    "network.nmcli": "nmcli device status",
    "network.networkctl": "networkctl list --no-pager",
    "network.routes_all": "ip -details route show table all",
    "network.route_cache": "ip route show cache",
    "network.trace_target": "tracepath {target}",
    "network.mtr_target": "mtr -rwzc 10 {target}",
    "network.dig_target": "dig +time=2 +tries=1 {target}",
    "network.resolver_query": "resolvectl query {target}",
    "network.port_target": "nc -vz -w 3 {target} {port}",
    "network.http_head": "curl --fail --silent --show-error --head --max-time 10 {url}",
    "network.tls_target": "openssl s_client -brief -connect {target}:{port} -servername {target}",
    "network.packet_sample": "tcpdump -ni any -c 50",
    "network.conntrack": "conntrack -L",
    "network.socket_processes": "lsof -nP -i",
    "network.firewall_firewalld": "firewall-cmd --list-all",
    "network.firewall_ufw": "ufw status verbose",
    # Accounts, authentication, SSH, and host security.
    "users.account": "getent passwd {user}",
    "users.identity": "id {user}",
    "users.lastlog": "lastlog -u {user}",
    "users.password_status": "passwd -S {user}",
    "users.password_age": "chage -l {user}",
    "users.sudo": "sudo -l -U {user}",
    "security.sshd_config": "sshd -T",
    "security.ssh_log": "journalctl -u ssh -u sshd -n 100 --no-pager",
    "security.selinux": "sestatus",
    "security.apparmor": "aa-status",
    "security.audit": "auditctl -s",
    "security.failures": "lastb -n 20",
    # Schedulers and packages across common distributions.
    "schedule.at": "atq",
    "schedule.system": "ls -lah /etc/cron.d /etc/cron.daily /etc/cron.hourly /etc/cron.weekly /etc/cron.monthly",
    "package.debian_info": "apt-cache policy {package}",
    "package.dpkg_info": "dpkg-query -s {package}",
    "package.rpm_info": "rpm -qi {package}",
    "package.dnf_info": "dnf info {package}",
    "package.apk": "apk info -vv",
    "package.pacman": "pacman -Q",
    "package.zypper": "zypper packages --installed-only",
    "package.snap": "snap list",
    "package.flatpak": "flatpak list",
    # Containers, Kubernetes, and virtual machines.
    "container.list": "{engine} ps -a",
    "container.info": "{engine} info",
    "container.inspect": "{engine} inspect {container}",
    "container.logs": "{engine} logs --tail 100 {container}",
    "container.stats": "{engine} stats --no-stream {container}",
    "container.networks": "{engine} network ls",
    "container.volumes": "{engine} volume ls",
    "container.images": "{engine} images",
    "kubernetes.context": "kubectl config current-context",
    "kubernetes.cluster": "kubectl cluster-info",
    "kubernetes.nodes": "kubectl get nodes -o wide",
    "kubernetes.pods": "kubectl get pods -A -o wide",
    "kubernetes.events": "kubectl get events -A --sort-by=.lastTimestamp",
    "kubernetes.workloads": "kubectl get deploy,statefulset,daemonset -A",
    "kubernetes.services": "kubectl get service,ingress -A",
    "kubernetes.helm": "helm list -A",
    "virtualization.list": "virsh list --all",
    "virtualization.info": "virsh dominfo {domain}",
    "virtualization.networks": "virsh net-list --all",
    "virtualization.storage": "virsh pool-list --all",
    # Common web and data services.
    "web.nginx_config": "nginx -T",
    "web.nginx_version": "nginx -V",
    "web.apache_config": "apachectl -S",
    "database.mysql_ping": "mysqladmin ping",
    "database.mysql_processes": "mysqladmin processlist",
    "database.postgres_ready": "pg_isready",
    "database.redis_info": "redis-cli INFO",
    "database.redis_clients": "redis-cli CLIENT LIST",
})


LINUX_RECOMMENDATION_NODES = MappingProxyType(_NODES)


def _edges(*items: tuple[str, int, str]) -> tuple[FlowEdge, ...]:
    return tuple(FlowEdge(*item) for item in items)


_GRAPH: dict[str, tuple[FlowEdge, ...]] = {
    "directory.change": _edges(
        ("directory.list.details", 100, "查看新目录内容"),
        ("directory.location", 90, "确认当前路径"),
        ("directory.size", 80, "查看当前目录占用"),
        ("directory.mount", 70, "确认所在挂载点"),
        ("directory.metadata", 60, "查看目录属性"),
    ),
    "directory.list": _edges(
        ("directory.location", 100, "确认当前路径"),
        ("directory.list.details", 90, "显示隐藏文件和详细信息"),
        ("directory.size", 80, "查看当前目录占用"),
        ("directory.mount", 70, "确认所在挂载点"),
        ("directory.metadata", 60, "查看目录属性"),
    ),
    "directory.location": _edges(
        ("directory.list.details", 100, "查看当前目录内容"),
        ("directory.size", 90, "查看当前目录占用"),
        ("directory.mount", 80, "确认所在挂载点"),
        ("directory.metadata", 70, "查看目录属性"),
        ("storage.capacity", 60, "检查文件系统空间"),
    ),
    "storage.capacity": _edges(
        ("storage.capacity.types", 100, "查看文件系统类型和空间"),
        ("storage.inodes", 90, "检查 inode 使用情况"),
        ("storage.blocks", 80, "查看块设备和文件系统"),
        ("storage.mounts", 70, "查看挂载关系"),
        ("storage.var_usage", 60, "定位 /var 下的大目录"),
    ),
    "storage.inodes": _edges(
        ("storage.capacity", 100, "检查磁盘空间"),
        ("storage.capacity.types", 90, "查看文件系统类型"),
        ("storage.mounts", 80, "查看挂载关系"),
        ("storage.blocks", 70, "查看块设备"),
        ("storage.var_usage", 60, "定位常见占用目录"),
    ),
    "storage.blocks": _edges(
        ("storage.mounts", 100, "查看块设备挂载关系"),
        ("storage.capacity.types", 90, "检查文件系统空间"),
        ("storage.inodes", 80, "检查 inode"),
        ("storage.capacity", 70, "查看磁盘使用率"),
        ("directory.mount", 60, "确认当前目录所在设备"),
    ),
    "storage.mounts": _edges(
        ("storage.blocks", 100, "查看块设备和文件系统"),
        ("storage.capacity.types", 90, "检查各挂载点空间"),
        ("storage.inodes", 80, "检查各挂载点 inode"),
        ("directory.mount", 70, "确认当前目录所在挂载点"),
        ("storage.capacity", 60, "查看磁盘使用率"),
    ),
    "storage.usage": _edges(
        ("storage.capacity", 100, "检查文件系统剩余空间"),
        ("storage.inodes", 90, "检查 inode 使用情况"),
        ("storage.mounts", 80, "确认目录所在挂载点"),
        ("storage.blocks", 70, "查看底层块设备"),
        ("directory.list.details", 60, "查看目录内容"),
    ),
    "system.load": _edges(
        ("system.memory", 100, "检查内存和交换空间"),
        ("system.vmstat", 90, "观察 CPU、内存和 IO"),
        ("system.cpu_processes", 80, "查看高 CPU 进程"),
        ("system.failed_services", 70, "查看失败的服务"),
        ("system.kernel_errors", 60, "查看内核错误和告警"),
    ),
    "system.memory": _edges(
        ("system.memory_processes", 100, "查看高内存进程"),
        ("system.vmstat", 90, "观察内存和 IO 变化"),
        ("system.load", 80, "检查系统负载"),
        ("process.snapshot", 70, "查看进程资源快照"),
        ("system.kernel_errors", 60, "检查内核内存告警"),
    ),
    "process.list": _edges(
        ("system.cpu_processes", 100, "查看高 CPU 进程"),
        ("system.memory_processes", 90, "查看高内存进程"),
        ("process.snapshot", 80, "查看实时资源快照"),
        ("process.tree", 70, "查看进程父子关系"),
        ("system.load", 60, "检查系统整体负载"),
    ),
    "process.activity": _edges(
        ("system.cpu_processes", 100, "列出高 CPU 进程"),
        ("system.memory_processes", 90, "列出高内存进程"),
        ("system.memory", 80, "检查系统内存"),
        ("system.load", 70, "检查系统负载"),
        ("system.failed_services", 60, "查看失败服务"),
    ),
    "service.status": _edges(
        ("service.logs", 100, "查看服务最近日志"),
        ("service.properties", 90, "查看服务运行属性"),
        ("service.active", 80, "确认服务是否运行"),
        ("service.enabled", 70, "确认服务是否开机启动"),
        ("service.unit", 60, "查看服务单元配置"),
    ),
    "service.logs": _edges(
        ("service.status", 100, "返回检查服务状态"),
        ("service.logs.hour", 90, "查看最近一小时日志"),
        ("service.logs.errors", 80, "筛选本次启动的错误日志"),
        ("service.properties", 70, "查看服务运行属性"),
        ("service.unit", 60, "查看服务单元配置"),
    ),
    "service.overview": _edges(
        ("service.running", 100, "查看正在运行的服务"),
        ("service.files", 90, "查看服务启用状态"),
        ("system.boot_errors", 80, "查看本次启动错误"),
        ("system.load", 70, "检查系统负载"),
        ("system.kernel_errors", 60, "查看内核错误"),
    ),
    "network.address": _edges(
        ("network.address.brief", 100, "查看精简地址信息"),
        ("network.route", 90, "查看路由表"),
        ("network.link", 80, "查看链路状态"),
        ("network.sockets", 70, "查看监听端口"),
        ("network.neighbors", 60, "查看邻居缓存"),
    ),
    "network.link": _edges(
        ("network.address.brief", 100, "查看接口地址"),
        ("network.route", 90, "查看路由表"),
        ("network.neighbors", 80, "查看邻居缓存"),
        ("network.sockets", 70, "查看监听端口"),
        ("network.socket_summary", 60, "查看套接字摘要"),
    ),
    "network.route": _edges(
        ("network.address.brief", 100, "核对接口地址"),
        ("network.rules", 90, "查看策略路由规则"),
        ("network.neighbors", 80, "查看邻居缓存"),
        ("network.sockets", 70, "查看监听端口"),
        ("network.socket_summary", 60, "查看连接摘要"),
    ),
    "network.sockets": _edges(
        ("network.socket_summary", 100, "查看套接字统计"),
        ("network.established", 90, "查看已建立连接"),
        ("network.address.brief", 80, "核对接口地址"),
        ("network.route", 70, "核对路由表"),
        ("network.neighbors", 60, "查看邻居缓存"),
    ),
    "network.ping": _edges(
        ("network.route_target", 100, "查看到目标的选路"),
        ("network.resolve_target", 90, "检查目标名称解析"),
        ("network.route", 80, "查看完整路由表"),
        ("network.address.brief", 70, "核对本机地址"),
        ("network.socket_summary", 60, "查看网络连接摘要"),
    ),
    "network.dns": _edges(
        ("network.resolve_target", 100, "使用系统解析器复查"),
        ("network.dns_config", 90, "查看 DNS 配置"),
        ("network.resolved_status", 80, "查看解析服务状态"),
        ("network.ping_target", 70, "测试目标连通性"),
        ("network.route", 60, "核对路由表"),
    ),
    "system.identity": _edges(
        ("system.kernel", 100, "查看内核版本"),
        ("system.load", 90, "查看运行时长和负载"),
        ("system.memory", 80, "查看内存"),
        ("storage.capacity", 70, "查看磁盘空间"),
        ("system.failed_services", 60, "查看失败服务"),
    ),
    "kernel.logs": _edges(
        ("system.kernel_errors", 100, "筛选内核错误和告警"),
        ("system.boot_errors", 90, "查看本次启动错误"),
        ("system.kernel", 80, "确认内核版本"),
        ("system.failed_services", 70, "查看失败服务"),
        ("system.load", 60, "检查系统负载"),
    ),
    "users.sessions": _edges(
        ("users.current", 100, "查看当前登录用户"),
        ("users.sessions", 90, "查看登录来源和负载"),
        ("users.recent", 80, "查看最近登录记录"),
        ("users.accounts", 70, "查看系统账号"),
        ("users.groups", 60, "查看系统用户组"),
    ),
    "schedule": _edges(
        ("schedule.timers", 100, "查看 systemd 定时器"),
        ("service.overview", 90, "查看失败服务"),
        ("system.boot_errors", 80, "查看本次启动错误"),
        ("system.load", 70, "检查系统负载"),
        ("users.current", 60, "确认当前登录用户"),
    ),
    "package": _edges(
        ("package.debian", 100, "查看 Debian 系已安装软件包"),
        ("package.rpm", 90, "查看 RPM 系已安装软件包"),
        ("storage.capacity", 80, "检查安装前磁盘空间"),
        ("network.dns_config", 70, "检查软件源所需的 DNS"),
        ("system.load", 60, "检查系统负载"),
    ),
    "firewall": _edges(
        ("firewall.nft", 100, "查看 nftables 规则"),
        ("firewall.iptables", 90, "查看 iptables 规则和计数"),
        ("network.sockets", 80, "查看监听端口"),
        ("network.established", 70, "查看已建立连接"),
        ("network.route", 60, "核对路由表"),
    ),
}


BEHAVIOR_LABELS: dict[str, str] = {
    "directory.change": "目录切换",
    "directory.list": "目录浏览",
    "directory.location": "路径定位",
    "file.read": "文件读取",
    "file.inspect": "文件属性",
    "file.search": "内容检索",
    "file.find": "文件查找",
    "file.create": "文件与目录创建",
    "file.copy": "文件复制与同步",
    "file.move": "文件移动与改名",
    "file.remove": "文件删除后核查",
    "file.permissions": "文件权限",
    "file.ownership": "文件属主",
    "file.links": "符号链接与硬链接",
    "archive": "归档与压缩",
    "storage.capacity": "文件系统容量",
    "storage.inodes": "inode 使用",
    "storage.blocks": "块设备",
    "storage.mounts": "挂载关系",
    "storage.usage": "目录占用",
    "storage.filesystem": "文件系统维护",
    "storage.partition": "磁盘分区",
    "storage.lvm": "LVM 存储",
    "storage.raid": "软件 RAID",
    "storage.smart": "磁盘健康",
    "storage.io": "磁盘 IO",
    "storage.swap": "交换空间",
    "system.load": "系统负载",
    "system.memory": "系统内存",
    "system.identity": "系统身份",
    "system.os": "操作系统版本",
    "system.boot": "启动过程",
    "system.time": "时间同步",
    "system.hardware": "硬件信息",
    "system.power": "关机重启后核查",
    "kernel.logs": "内核日志",
    "kernel.modules": "内核模块",
    "kernel.tuning": "内核参数",
    "process.list": "进程列表",
    "process.activity": "进程活动",
    "process.control": "进程控制后核查",
    "process.open_files": "文件与端口占用",
    "process.performance": "性能分析",
    "service.status": "服务状态",
    "service.logs": "服务日志",
    "service.overview": "服务总览",
    "service.change": "服务变更后核查",
    "network.address": "网络地址",
    "network.link": "链路状态",
    "network.route": "路由与策略路由",
    "network.sockets": "套接字与端口",
    "network.ping": "网络连通性",
    "network.trace": "路径追踪",
    "network.dns": "DNS 解析",
    "network.http": "HTTP 服务",
    "network.port": "TCP/UDP 端口",
    "network.tls": "TLS 连接",
    "network.packet": "抓包与流量",
    "network.interface": "网卡参数",
    "network.bridge": "Linux 网桥与 VLAN",
    "network.manager": "网络管理服务",
    "users.sessions": "登录会话",
    "users.account": "用户账号",
    "users.change": "用户变更后核查",
    "users.password": "密码与有效期",
    "users.sudo": "sudo 权限",
    "security.ssh": "SSH 服务",
    "security.mac": "SELinux 与 AppArmor",
    "security.audit": "安全审计",
    "schedule": "计划任务",
    "schedule.at": "一次性任务",
    "package": "软件包",
    "package.change": "软件包变更后核查",
    "firewall": "主机防火墙",
    "container.list": "容器总览",
    "container.inspect": "容器详情",
    "container.logs": "容器日志",
    "container.change": "容器变更后核查",
    "container.images": "容器镜像",
    "container.network": "容器网络与存储",
    "container.compose": "Compose 应用",
    "kubernetes.inspect": "Kubernetes 集群",
    "kubernetes.change": "Kubernetes 变更后核查",
    "kubernetes.helm": "Helm 发布",
    "virtualization.inspect": "虚拟机与 libvirt",
    "virtualization.change": "虚拟机变更后核查",
    "web.server": "Web 服务",
    "database.mysql": "MySQL/MariaDB",
    "database.postgresql": "PostgreSQL",
    "database.redis": "Redis",
}


_DOMAIN_DEFAULTS: dict[str, tuple[str, ...]] = {
    "files": (
        "directory.list.details", "directory.location", "directory.size",
        "directory.mount", "file.large_here", "file.recent_here",
        "storage.capacity",
    ),
    "storage": (
        "storage.capacity.types", "storage.inodes", "storage.block_details",
        "storage.mount_options", "storage.io", "storage.kernel_errors",
        "system.kernel_errors", "storage.filesystems", "storage.root_usage",
    ),
    "system": (
        "system.load", "system.memory", "system.failed_services",
        "system.kernel_errors", "system.boot_errors", "system.os_release",
        "storage.capacity",
    ),
    "process": (
        "system.cpu_processes", "system.memory_processes", "process.snapshot",
        "process.tree", "process.cpu", "process.io", "system.load",
    ),
    "service": (
        "service.overview", "service.running", "service.files",
        "system.boot_errors", "service.journal_errors", "system.load",
        "network.sockets",
    ),
    "network": (
        "network.address.brief", "network.link", "network.route",
        "network.neighbors", "network.sockets", "network.socket_summary",
        "network.dns_config", "network.route_cache", "network.dig_target",
        "network.resolver_query",
    ),
    "users": (
        "users.current", "users.sessions", "users.recent", "users.accounts",
        "users.groups", "security.failures", "security.ssh_log",
    ),
    "security": (
        "security.sshd_config", "security.ssh_log", "security.selinux",
        "security.apparmor", "security.audit", "security.failures",
        "users.recent",
    ),
    "schedule": (
        "schedule.cron", "schedule.timers", "schedule.at", "schedule.system",
        "system.failed_services", "system.boot_errors", "users.current",
    ),
    "package": (
        "package.debian", "package.rpm", "package.apk", "package.pacman",
        "package.zypper", "storage.capacity", "network.dns_config",
    ),
    "container": (
        "container.list", "container.info", "container.images",
        "container.networks", "container.volumes", "network.sockets",
        "storage.capacity",
    ),
    "kubernetes": (
        "kubernetes.context", "kubernetes.cluster", "kubernetes.nodes",
        "kubernetes.pods", "kubernetes.events", "kubernetes.workloads",
        "kubernetes.services",
    ),
    "virtualization": (
        "virtualization.list", "virtualization.networks",
        "virtualization.storage", "system.load", "system.memory",
        "network.bridge_links", "storage.capacity",
    ),
    "web": (
        "network.sockets", "network.http_head", "network.tls_target",
        "system.failed_services", "service.journal_errors", "system.load",
        "network.firewall_ufw",
    ),
    "database": (
        "network.sockets", "system.memory_processes", "system.cpu_processes",
        "storage.capacity", "storage.io", "system.failed_services",
        "system.kernel_errors",
    ),
}


# behavior -> (domain fallback, behavior-specific checks).  Specific checks are
# ranked first; generic domain checks keep the graph useful when a parameter is
# unavailable or the optional diagnostic package is not installed.
_EXTRA_BEHAVIOR_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "file.read": ("files", ("file.metadata", "file.type", "file.path", "file.checksum", "file.open_handles")),
    "file.inspect": ("files", ("file.metadata", "file.type", "file.path", "file.acl", "file.open_handles")),
    "file.search": ("files", ("file.recent_here", "file.large_here", "directory.location")),
    "file.find": ("files", ("file.recent_here", "file.large_here", "directory.mount")),
    "file.create": ("files", ("file.metadata", "directory.list.details", "file.permissions_here", "file.umask", "storage.capacity")),
    "file.copy": ("files", ("file.metadata", "file.checksum", "directory.list.details", "storage.capacity", "directory.mount")),
    "file.move": ("files", ("file.metadata", "file.path", "directory.list.details", "directory.mount")),
    "file.remove": ("files", ("directory.list.details", "file.deleted_open", "storage.capacity", "storage.inodes")),
    "file.permissions": ("files", ("file.metadata", "file.acl", "file.permissions_here", "file.umask")),
    "file.ownership": ("files", ("file.metadata", "file.acl", "file.permissions_here", "users.accounts")),
    "file.links": ("files", ("file.path", "file.metadata", "file.open_handles", "directory.list.details")),
    "archive": ("files", ("archive.list", "file.type", "file.checksum", "file.metadata", "storage.capacity")),
    "storage.filesystem": ("storage", ("storage.mount_options", "storage.capacity.types", "storage.inodes", "storage.blkid", "storage.kernel_errors")),
    "storage.partition": ("storage", ("storage.block_details", "storage.blkid", "storage.mount_options", "storage.capacity.types")),
    "storage.lvm": ("storage", ("storage.lvm_pvs", "storage.lvm_vgs", "storage.lvm_lvs", "storage.block_details", "storage.mount_options")),
    "storage.raid": ("storage", ("storage.raid_status", "storage.raid_scan", "storage.block_details", "storage.smart", "storage.io")),
    "storage.smart": ("storage", ("storage.smart", "storage.block_details", "storage.io", "storage.kernel_errors")),
    "storage.io": ("storage", ("storage.io", "performance.pressure", "system.cpu_processes", "system.memory", "storage.smart")),
    "storage.swap": ("storage", ("storage.swaps", "system.memory", "system.vmstat", "process.memory", "storage.capacity")),
    "system.os": ("system", ("system.os_release", "system.kernel", "system.machine", "system.identity", "system.boot_time")),
    "system.boot": ("system", ("system.boot_errors", "system.boot_previous_errors", "system.boot_chain", "system.boot_blame", "system.cmdline")),
    "system.time": ("system", ("system.time", "system.time_tracking", "system.time_sources", "system.failed_services", "system.boot_errors")),
    "system.hardware": ("system", ("system.cpu", "system.hardware_pci", "system.hardware_usb", "system.hardware_dmi", "system.temperatures")),
    "system.power": ("system", ("system.boot_time", "system.boot_errors", "system.failed_services", "system.kernel_errors", "system.boot_chain")),
    "kernel.modules": ("system", ("system.modules", "system.kernel", "system.kernel_errors", "system.cmdline", "system.os_release")),
    "kernel.tuning": ("system", ("system.sysctl_changed", "system.cmdline", "system.kernel", "system.kernel_errors", "system.entropy")),
    "process.control": ("process", ("process.pid", "process.pid_status", "process.pid_limits", "process.pid_tree", "process.pid_files", "process.scheduler")),
    "process.open_files": ("process", ("process.pid_files", "file.deleted_open", "network.socket_processes", "network.sockets", "process.tree")),
    "process.performance": ("process", ("process.cpu", "process.memory", "process.io", "performance.cpu", "performance.disk", "performance.pressure")),
    "service.change": ("service", ("service.status", "service.processes", "service.logs", "service.active", "service.enabled", "service.dependencies", "service.reverse_dependencies")),
    "network.trace": ("network", ("network.trace_target", "network.mtr_target", "network.route_target", "network.ping_target", "network.resolve_target")),
    "network.http": ("network", ("network.http_head", "network.resolve_target", "network.route_target", "network.tls_target", "network.sockets")),
    "network.port": ("network", ("network.port_target", "network.route_target", "network.resolve_target", "network.sockets", "network.packet_sample")),
    "network.tls": ("network", ("network.tls_target", "network.resolve_target", "network.route_target", "network.http_head", "system.time")),
    "network.packet": ("network", ("network.packet_sample", "network.link_details", "network.address_stats", "network.routes_all", "network.conntrack")),
    "network.interface": ("network", ("network.link_device", "network.ethtool", "network.ethtool_stats", "network.address_stats", "network.neighbors")),
    "network.bridge": ("network", ("network.bridge_links", "network.bridge_vlans", "network.link_details", "network.address.brief", "network.routes_all")),
    "network.manager": ("network", ("network.nmcli", "network.networkctl", "network.address.brief", "network.route", "system.failed_services")),
    "users.account": ("users", ("users.account", "users.identity", "users.lastlog", "users.password_status", "users.password_age")),
    "users.change": ("users", ("users.account", "users.identity", "users.groups", "users.lastlog", "security.ssh_log")),
    "users.password": ("users", ("users.password_status", "users.password_age", "users.lastlog", "security.failures", "security.ssh_log")),
    "users.sudo": ("users", ("users.sudo", "users.identity", "users.account", "security.audit", "security.failures")),
    "security.ssh": ("security", ("security.sshd_config", "security.ssh_log", "network.sockets", "network.firewall_ufw", "users.recent")),
    "security.mac": ("security", ("security.selinux", "security.apparmor", "security.audit", "system.kernel_errors", "system.failed_services")),
    "security.audit": ("security", ("security.audit", "security.failures", "security.ssh_log", "users.recent", "system.time")),
    "schedule.at": ("schedule", ("schedule.at", "schedule.cron", "schedule.timers", "schedule.system", "users.current")),
    "package.change": ("package", ("package.debian_info", "package.dpkg_info", "package.rpm_info", "package.dnf_info", "storage.capacity", "network.dns_config", "system.failed_services")),
    "container.list": ("container", ("container.list", "container.info", "container.stats", "container.networks", "container.volumes")),
    "container.inspect": ("container", ("container.inspect", "container.logs", "container.stats", "container.networks", "network.sockets")),
    "container.logs": ("container", ("container.logs", "container.inspect", "container.stats", "system.kernel_errors", "storage.capacity")),
    "container.change": ("container", ("container.list", "container.inspect", "container.logs", "container.stats", "network.sockets")),
    "container.images": ("container", ("container.images", "container.list", "container.info", "container.volumes", "storage.capacity")),
    "container.network": ("container", ("container.networks", "container.volumes", "container.list", "network.bridge_links", "network.sockets")),
    "container.compose": ("container", ("container.list", "container.logs", "container.stats", "container.networks", "network.sockets")),
    "kubernetes.inspect": ("kubernetes", ("kubernetes.pods", "kubernetes.events", "kubernetes.nodes", "kubernetes.workloads", "kubernetes.services")),
    "kubernetes.change": ("kubernetes", ("kubernetes.pods", "kubernetes.events", "kubernetes.workloads", "kubernetes.services", "kubernetes.nodes")),
    "kubernetes.helm": ("kubernetes", ("kubernetes.helm", "kubernetes.pods", "kubernetes.events", "kubernetes.workloads", "kubernetes.services")),
    "virtualization.inspect": ("virtualization", ("virtualization.list", "virtualization.info", "virtualization.networks", "virtualization.storage", "system.load")),
    "virtualization.change": ("virtualization", ("virtualization.list", "virtualization.info", "virtualization.networks", "virtualization.storage", "system.kernel_errors")),
    "web.server": ("web", ("web.nginx_config", "web.nginx_version", "web.apache_config", "network.sockets", "service.journal_errors")),
    "database.mysql": ("database", ("database.mysql_ping", "database.mysql_processes", "network.sockets", "storage.io", "system.memory_processes")),
    "database.postgresql": ("database", ("database.postgres_ready", "network.sockets", "storage.io", "system.memory_processes", "service.journal_errors")),
    "database.redis": ("database", ("database.redis_info", "database.redis_clients", "network.sockets", "system.memory_processes", "storage.capacity")),
}


def _register_extra_behaviors() -> None:
    for behavior, (domain, primary_targets) in _EXTRA_BEHAVIOR_SPECS.items():
        targets = tuple(dict.fromkeys((*primary_targets, *_DOMAIN_DEFAULTS[domain])))
        _GRAPH[behavior] = _edges(*(
            (
                target,
                max(20, 100 - index * 10),
                f"进一步核查：{_NODES[target]}",
            )
            for index, target in enumerate(targets[:9])
        ))


_register_extra_behaviors()

# Add deeper fallback branches to the original common behaviors without
# changing their first five, battle-tested recommendations.
_ORIGINAL_BEHAVIOR_DOMAINS = {
    "directory.change": "files", "directory.list": "files", "directory.location": "files",
    "storage.capacity": "storage", "storage.inodes": "storage", "storage.blocks": "storage",
    "storage.mounts": "storage", "storage.usage": "storage",
    "system.load": "system", "system.memory": "system", "system.identity": "system",
    "kernel.logs": "system", "process.list": "process", "process.activity": "process",
    "service.status": "service", "service.logs": "service", "service.overview": "service",
    "network.address": "network", "network.link": "network", "network.route": "network",
    "network.sockets": "network", "network.ping": "network", "network.dns": "network",
    "users.sessions": "users", "schedule": "schedule", "package": "package", "firewall": "network",
}
for _behavior, _domain in _ORIGINAL_BEHAVIOR_DOMAINS.items():
    _current_edges = list(_GRAPH[_behavior])
    _seen_targets = {edge.target for edge in _current_edges}
    for _target in _DOMAIN_DEFAULTS[_domain]:
        if _target in _seen_targets:
            continue
        _current_edges.append(FlowEdge(_target, 20, f"补充核查：{_NODES[_target]}"))
        _seen_targets.add(_target)
    _GRAPH[_behavior] = tuple(_current_edges)


LINUX_BEHAVIOR_GRAPH = MappingProxyType(_GRAPH)


# Ordered by usefulness.  Matching is intentionally case-sensitive because Linux
# executable names are case-sensitive.  Trailing spaces mark commands that need
# an argument and are useful completions without pretending to be executable yet.
_COMPLETION_CATALOG = (
    "ls",
    "ls -lah",
    "pwd",
    "cd ",
    "cat ",
    "less ",
    "head -n 20 ",
    "tail -n 100 ",
    "stat ",
    "find ",
    "grep -R --line-number ",
    "df -h",
    "df -hT",
    "df -ih",
    "du -sh ",
    "du -xhd1 ",
    "lsblk -f",
    "findmnt",
    "findmnt -T ",
    "free -h",
    "uptime",
    "vmstat 1 5",
    "top",
    "top -b -n 1",
    "ps aux",
    "ps -ef --forest",
    "ps aux --sort=-%cpu",
    "ps aux --sort=-%mem",
    "pgrep -a ",
    "systemctl status ",
    "systemctl show ",
    "systemctl is-active ",
    "systemctl is-enabled ",
    "systemctl cat ",
    "systemctl --failed --no-pager",
    "systemctl list-units --type=service --state=running --no-pager",
    "systemctl list-timers --all --no-pager",
    "journalctl -u ",
    "journalctl -p err -b --no-pager",
    "journalctl -k -b --no-pager",
    "ip addr",
    "ip -brief addr",
    "ip -brief link",
    "ip route",
    "ip rule",
    "ip neigh",
    "ss -lntp",
    "ss -s",
    "ss -tan state established",
    "ping -c 4 ",
    "getent ahosts ",
    "resolvectl status",
    "hostnamectl",
    "uname -a",
    "dmesg --level=err,warn",
    "who",
    "w",
    "last -n 10",
    "id",
    "getent passwd",
    "getent group",
    "crontab -l",
    "nft list ruleset",
    "iptables -L -n -v",
)


_EXTRA_COMPLETION_CATALOG = (
    # Files and text processing.
    "tree -a",
    "mkdir -p ",
    "touch ",
    "cp -a ",
    "mv ",
    "rm -i ",
    "rm -rf ",
    "ln -s ",
    "readlink -f ",
    "realpath ",
    "file ",
    "namei -l ",
    "getfacl -p ",
    "setfacl -m ",
    "chmod ",
    "chown ",
    "locate ",
    "rg --line-number ",
    "awk ",
    "sed -n ",
    "sort ",
    "uniq -c",
    "cut -d ",
    "tr ",
    "wc -l ",
    "tee ",
    "diff -u ",
    "cmp ",
    "sha256sum ",
    "md5sum ",
    "xargs -r ",
    "tar -tf ",
    "tar -czf ",
    "tar -xzf ",
    "gzip ",
    "gunzip ",
    "zip -r ",
    "unzip -l ",
    "rsync -aHAX --info=progress2 ",
    # Storage and filesystems.
    "blkid",
    "lsblk -o NAME,PATH,SIZE,TYPE,FSTYPE,FSVER,MOUNTPOINTS,MODEL,SERIAL",
    "findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS",
    "mount",
    "mount ",
    "umount ",
    "swapon --show",
    "pvs -o+pv_used",
    "vgs",
    "lvs -a -o+devices",
    "pvdisplay",
    "vgdisplay",
    "lvdisplay",
    "fdisk -l",
    "parted -l",
    "cat /proc/mdstat",
    "mdadm --detail --scan",
    "mdadm --detail ",
    "smartctl -a ",
    "iostat -xz 1 3",
    "fsck -N ",
    "xfs_repair -n ",
    "tune2fs -l ",
    "xfs_info ",
    # System, boot, hardware, time, and kernel.
    "cat /etc/os-release",
    "cat /proc/cmdline",
    "cat /proc/pressure/cpu",
    "cat /proc/pressure/memory",
    "cat /proc/pressure/io",
    "lscpu",
    "lspci -nn",
    "lsusb",
    "dmidecode -t system",
    "sensors",
    "timedatectl status",
    "chronyc tracking",
    "chronyc sources -v",
    "systemd-analyze blame --no-pager",
    "systemd-analyze critical-chain --no-pager",
    "loginctl list-sessions",
    "localectl status",
    "sysctl -a",
    "sysctl ",
    "lsmod",
    "modinfo ",
    "modprobe ",
    "uptime -s",
    "journalctl -b -1 -p err --no-pager",
    "reboot",
    "shutdown -r now",
    "shutdown -h now",
    # Processes and performance.
    "pstree -ap",
    "pidstat -u 1 5",
    "pidstat -r 1 5",
    "pidstat -d 1 5",
    "mpstat -P ALL 1 3",
    "sar -A 1 3",
    "lsof -nP -i",
    "lsof +L1",
    "lsof -p ",
    "fuser -v ",
    "kill ",
    "pkill ",
    "killall ",
    "nice -n ",
    "renice ",
    "strace -p ",
    "perf top",
    # systemd and logs.
    "systemctl start ",
    "systemctl stop ",
    "systemctl restart ",
    "systemctl reload ",
    "systemctl enable ",
    "systemctl disable ",
    "systemctl mask ",
    "systemctl unmask ",
    "systemctl list-dependencies ",
    "systemctl list-dependencies --reverse ",
    "systemctl daemon-reload",
    "journalctl -b --no-pager",
    "journalctl -b -1 --no-pager",
    "journalctl -f",
    "journalctl --disk-usage",
    "journalctl --since '1 hour ago' --no-pager",
    # Network and DNS.
    "ip -s address",
    "ip -details -statistics link",
    "ip -details route show table all",
    "ip route get ",
    "ip link show dev ",
    "ip addr show dev ",
    "ethtool ",
    "ethtool -S ",
    "bridge link show",
    "bridge vlan show",
    "networkctl list --no-pager",
    "networkctl status ",
    "nmcli device status",
    "nmcli connection show",
    "traceroute ",
    "tracepath ",
    "mtr -rwzc 10 ",
    "dig +time=2 +tries=1 ",
    "dig +short ",
    "host ",
    "nslookup ",
    "resolvectl query ",
    "curl --fail --silent --show-error --head --max-time 10 ",
    "curl -v ",
    "wget --spider ",
    "nc -vz -w 3 ",
    "openssl s_client -brief -connect ",
    "tcpdump -ni any -c 50",
    "tcpdump -ni any host ",
    "tshark -i any -c 50",
    "conntrack -L",
    # Firewall and host security.
    "nft list tables",
    "nft list ruleset",
    "iptables-save",
    "ip6tables -L -n -v",
    "firewall-cmd --state",
    "firewall-cmd --list-all",
    "firewall-cmd --list-all-zones",
    "ufw status verbose",
    "sshd -T",
    "sestatus",
    "getenforce",
    "aa-status",
    "auditctl -s",
    "ausearch -m USER_LOGIN -ts recent",
    # Users and scheduled work.
    "whoami",
    "groups ",
    "getent passwd ",
    "getent group ",
    "lastlog -u ",
    "passwd -S ",
    "chage -l ",
    "sudo -l",
    "sudo -l -U ",
    "visudo -c",
    "useradd ",
    "usermod ",
    "userdel ",
    "ssh -v ",
    "ssh-keygen -lf ",
    "atq",
    "systemctl list-timers --all --no-pager",
    # Packages across distributions.
    "apt update",
    "apt install ",
    "apt remove ",
    "apt-cache policy ",
    "dpkg-query -s ",
    "dpkg -S ",
    "dnf check-update",
    "dnf info ",
    "dnf install ",
    "dnf remove ",
    "yum info ",
    "rpm -qi ",
    "rpm -qf ",
    "apk info -vv",
    "apk add ",
    "pacman -Q",
    "pacman -S ",
    "zypper packages --installed-only",
    "zypper install ",
    "snap list",
    "flatpak list",
    # Docker, Podman, Kubernetes, Helm, and libvirt.
    "docker ps -a",
    "docker info",
    "docker inspect ",
    "docker logs --tail 100 ",
    "docker stats --no-stream",
    "docker images",
    "docker network ls",
    "docker volume ls",
    "docker compose ps",
    "docker compose logs --tail 100",
    "podman ps -a",
    "podman info",
    "podman inspect ",
    "podman logs --tail 100 ",
    "podman stats --no-stream",
    "podman images",
    "podman network ls",
    "podman volume ls",
    "kubectl config current-context",
    "kubectl cluster-info",
    "kubectl get nodes -o wide",
    "kubectl get pods -A -o wide",
    "kubectl get events -A --sort-by=.lastTimestamp",
    "kubectl get deploy,statefulset,daemonset -A",
    "kubectl get service,ingress -A",
    "kubectl describe ",
    "kubectl logs ",
    "kubectl apply -f ",
    "kubectl delete ",
    "helm list -A",
    "helm status ",
    "helm upgrade --install ",
    "virsh list --all",
    "virsh dominfo ",
    "virsh net-list --all",
    "virsh pool-list --all",
    # Common application services.
    "nginx -t",
    "nginx -T",
    "nginx -V",
    "apachectl -S",
    "apachectl configtest",
    "mysqladmin ping",
    "mysqladmin processlist",
    "pg_isready",
    "redis-cli INFO",
    "redis-cli CLIENT LIST",
)

_COMPLETION_CATALOG = tuple(dict.fromkeys((*_COMPLETION_CATALOG, *_EXTRA_COMPLETION_CATALOG)))

# Recommendation policy is a projection over the complete graph.  Capture the
# curated observational targets before catalog change/recovery/risky nodes are
# attached; graph construction itself remains unrestricted.
_RECOMMENDABLE_GRAPH_TARGETS = frozenset(_NODES)


def complete_linux_command(prefix: str, limit: int = 8) -> list[dict]:
    """Return factory completions without consulting command audit history."""
    prefix = prefix.lstrip()
    limit = min(max(limit, 1), 20)
    if not prefix:
        return []

    matches = []
    for command in _COMPLETION_CATALOG:
        if command == prefix or not command.startswith(prefix):
            continue
        alert = alert_check(command.strip(), device_profile="linux")
        context = _classify(command)
        matches.append({
            "command": command,
            "usage_count": 0,
            "last_used_at": None,
            "source": "catalog",
            "risk": "confirm" if alert else "normal",
            "behavior": context.action if context else "",
            "behavior_label": BEHAVIOR_LABELS.get(context.action, "") if context else "",
        })
        if len(matches) >= limit:
            break
    return matches


def recommend_next_linux_commands(
    command: str,
    limit: int = MAX_RECOMMENDATIONS,
    *,
    context_parameters: dict[str, str] | None = None,
) -> list[dict]:
    """Return the highest-priority static follow-ups for a Linux command."""
    context = _classify(command)
    limit = min(max(limit, 1), MAX_RECOMMENDATIONS)
    if context is None:
        return []
    if context_parameters:
        merged_parameters = dict(context_parameters)
        merged_parameters.update(context.parameters)
        context = CommandContext(context.action, merged_parameters)

    current = _normalized_command(command)
    suggestions: list[dict] = []
    seen: set[str] = set()
    for edge in sorted(_GRAPH.get(context.action, ()), key=lambda item: item.priority, reverse=True):
        template = _NODES.get(edge.target)
        rendered = _render(template, context.parameters) if template else None
        if not rendered or rendered == current or rendered in seen:
            continue
        # Full-graph catalog nodes include mutating and dangerous actions for
        # behavior reasoning.  This terminal-only projection admits the curated
        # observational lane and then applies the live danger gate below.
        if edge.target not in _RECOMMENDABLE_GRAPH_TARGETS:
            continue
        package_family = context.parameters.get("package_family")
        target_family = _PACKAGE_TARGET_FAMILIES.get(edge.target)
        if package_family and target_family and package_family != target_family:
            continue
        alert = alert_check(rendered, device_profile="linux")
        if alert:
            # Factory recommendations are intentionally read-only.  Commands that
            # need confirmation remain available through completion/quick guides.
            continue
        seen.add(rendered)
        suggestions.append({
            "command": rendered,
            "source": "factory",
            "risk": "normal",
            "reason": edge.reason,
            "priority": edge.priority,
            "behavior": context.action,
            "behavior_label": BEHAVIOR_LABELS[context.action],
        })
        if len(suggestions) >= limit:
            break
    return suggestions


def classify_linux_command(command: str) -> CommandContext | None:
    """Expose deterministic intent classification to the session dispatcher."""
    return _classify(command)


def _classify(command: str) -> CommandContext | None:
    tokens = _command_tokens(command)
    if not tokens:
        return None
    executable = tokens[0].rsplit("/", 1)[-1]
    args = tokens[1:]

    if executable in {"cd", "pushd", "popd"}:
        return CommandContext("directory.change", {})
    if executable in {"ls", "tree", "dir"}:
        return CommandContext("directory.list", {})
    if executable == "pwd":
        return CommandContext("directory.location", {})
    if executable in {"cat", "less", "more", "head", "tail", "tac", "nl"}:
        return CommandContext("file.read", _path_parameters(args))
    if executable in {"stat", "file", "readlink", "realpath", "namei", "getfacl"}:
        return CommandContext("file.inspect", _path_parameters(args))
    if executable in {
        "grep", "egrep", "fgrep", "rg", "ag", "ack", "awk", "sed", "sort",
        "uniq", "cut", "tr", "wc", "tee", "diff", "cmp", "sha256sum",
        "md5sum", "xargs",
    }:
        return CommandContext("file.search", {})
    if executable in {"find", "locate", "updatedb"}:
        return CommandContext("file.find", {})
    if executable in {"mkdir", "touch", "mkfifo", "mknod"}:
        return CommandContext("file.create", _path_parameters(args))
    if executable in {"cp", "install", "rsync", "scp"}:
        return CommandContext("file.copy", _path_parameters(args))
    if executable in {"mv", "rename"}:
        return CommandContext("file.move", _path_parameters(args))
    if executable in {"rm", "unlink", "shred", "rmdir"}:
        return CommandContext("file.remove", _path_parameters(args))
    if executable in {"chmod", "setfacl"}:
        return CommandContext("file.permissions", _path_parameters(args))
    if executable in {"chown", "chgrp"}:
        return CommandContext("file.ownership", _path_parameters(args))
    if executable == "ln":
        return CommandContext("file.links", _path_parameters(args))
    if executable in {"tar", "zip", "unzip", "gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz", "7z"}:
        return CommandContext("archive", _path_parameters(args))
    if executable == "df":
        return CommandContext("storage.inodes" if _has_option(args, "-i", "--inodes") else "storage.capacity", {})
    if executable == "du":
        return CommandContext("storage.usage", {})
    if executable == "lsblk":
        return CommandContext("storage.blocks", {})
    if executable in {"findmnt", "mount", "umount"}:
        return CommandContext("storage.mounts", {})
    if executable in {"blkid", "wipefs", "fsck", "e2fsck", "xfs_repair", "tune2fs", "xfs_info", "resize2fs", "xfs_growfs", "mkfs"} or executable.startswith("mkfs."):
        return CommandContext("storage.filesystem", _device_parameters(args))
    if executable in {"fdisk", "sfdisk", "cfdisk", "parted", "partprobe"}:
        return CommandContext("storage.partition", _device_parameters(args))
    if executable in {"pvs", "vgs", "lvs", "pvdisplay", "vgdisplay", "lvdisplay", "pvcreate", "vgcreate", "lvcreate", "lvextend", "lvreduce", "lvremove", "vgremove", "pvremove"}:
        return CommandContext("storage.lvm", _device_parameters(args))
    if executable == "mdadm":
        return CommandContext("storage.raid", _device_parameters(args))
    if executable == "smartctl":
        return CommandContext("storage.smart", _device_parameters(args))
    if executable in {"iostat", "iotop"}:
        return CommandContext("storage.io", {})
    if executable in {"swapon", "swapoff", "mkswap"}:
        return CommandContext("storage.swap", _device_parameters(args))
    if executable == "uptime":
        return CommandContext("system.load", {})
    if executable == "free":
        return CommandContext("system.memory", {})
    if executable in {"ps", "pgrep", "pidof", "pstree"}:
        parameters = _pid_parameters(args)
        return CommandContext("process.list", parameters)
    if executable in {"top", "htop", "vmstat"}:
        return CommandContext("process.activity", {})
    if executable in {"pidstat", "mpstat", "sar", "perf", "strace"}:
        return CommandContext("process.performance", _pid_parameters(args))
    if executable in {"kill", "pkill", "killall", "renice", "nice", "chrt", "ionice"}:
        return CommandContext("process.control", _pid_parameters(args))
    if executable in {"lsof", "fuser"}:
        if executable == "lsof" and any(arg == "-i" or arg.startswith("-i") for arg in args):
            return CommandContext("network.sockets", {})
        return CommandContext("process.open_files", _pid_parameters(args))
    if executable == "systemctl":
        return _classify_systemctl(args)
    if executable == "service":
        service = _first_positional(args)
        operation = _next_positional(args, 1) if service else None
        action = "service.change" if operation in {"start", "stop", "restart", "reload"} else "service.status"
        return CommandContext(action, {"service": _quote_parameter(service)} if service else {})
    if executable == "journalctl":
        service = _option_value(args, "-u", "--unit")
        if service:
            return CommandContext("service.logs", {"service": _quote_parameter(service)})
        return CommandContext("kernel.logs", {})
    if executable in {"reboot", "shutdown", "poweroff", "halt"}:
        return CommandContext("system.power", {})
    if executable == "ip":
        operation = _first_positional(args)
        if operation in {"addr", "address", "a"}:
            return CommandContext("network.address", _interface_parameters(args))
        if operation in {"link", "l"}:
            return CommandContext("network.link", _interface_parameters(args))
        if operation in {"route", "r", "rule"}:
            return CommandContext("network.route", _network_target_parameters(args))
        if operation in {"neigh", "neighbor", "n"}:
            return CommandContext("network.link", {})
        if operation in {"tunnel", "tuntap", "netns", "maddress"}:
            return CommandContext("network.interface", _interface_parameters(args))
    if executable in {"ifconfig", "route", "arp"}:
        return CommandContext("network.address" if executable == "ifconfig" else "network.route", _interface_parameters(args))
    if executable == "bridge":
        return CommandContext("network.bridge", _interface_parameters(args))
    if executable in {"ethtool", "mii-tool"}:
        return CommandContext("network.interface", _interface_parameters(args))
    if executable in {"nmcli", "networkctl", "nmtui"}:
        return CommandContext("network.manager", _interface_parameters(args))
    if executable in {"ss", "netstat"}:
        return CommandContext("network.sockets", {})
    if executable in {"ping", "ping4", "ping6"}:
        target = _last_positional(args)
        if target:
            return CommandContext("network.ping", {"target": _quote_parameter(target)})
        return CommandContext("network.ping", {})
    if executable in {"traceroute", "traceroute6", "tracepath", "tracepath6", "mtr"}:
        return CommandContext("network.trace", _network_target_parameters(args))
    if executable in {"dig", "host", "nslookup"}:
        target = _last_positional(args)
        if target:
            return CommandContext("network.dns", {"target": _quote_parameter(target)})
        return CommandContext("network.dns", {})
    if executable == "resolvectl" and args and args[0] == "query":
        return CommandContext("network.dns", _network_target_parameters(args[1:]))
    if executable == "resolvectl":
        return CommandContext("network.dns", {})
    if executable in {"curl", "wget", "http", "httpie"}:
        return CommandContext("network.http", _url_parameters(args))
    if executable in {"nc", "ncat", "netcat", "telnet"}:
        return CommandContext("network.port", _host_port_parameters(args))
    if executable == "openssl" and "s_client" in args:
        return CommandContext("network.tls", _openssl_parameters(args))
    if executable in {"tcpdump", "tshark", "dumpcap", "conntrack"}:
        return CommandContext("network.packet", _network_target_parameters(args))
    if executable in {"hostname", "hostnamectl", "uname", "lscpu", "localectl", "loginctl"}:
        return CommandContext("system.identity" if executable in {"hostname", "hostnamectl"} else "system.os", {})
    if executable in {"lsb_release", "os-release"}:
        return CommandContext("system.os", {})
    if executable in {"systemd-analyze", "last-reboot"}:
        return CommandContext("system.boot", {})
    if executable in {"date", "timedatectl", "chronyc", "ntpq", "hwclock"}:
        return CommandContext("system.time", {})
    if executable in {"lspci", "lsusb", "dmidecode", "sensors", "inxi", "lshw"}:
        return CommandContext("system.hardware", {})
    if executable == "dmesg":
        return CommandContext("kernel.logs", {})
    if executable in {"lsmod", "modinfo", "modprobe", "rmmod", "insmod", "depmod"}:
        return CommandContext("kernel.modules", {})
    if executable == "sysctl":
        return CommandContext("kernel.tuning", {})
    if executable in {"who", "w", "last", "lastb", "users", "whoami"}:
        return CommandContext("users.sessions", {})
    if executable in {"id", "groups", "getent", "lastlog"}:
        return CommandContext("users.account", _user_parameters(args))
    if executable in {"useradd", "adduser", "usermod", "userdel", "deluser", "groupadd", "groupmod", "groupdel"}:
        return CommandContext("users.change", _user_parameters(args))
    if executable in {"passwd", "chage", "chpasswd"}:
        return CommandContext("users.password", _user_parameters(args))
    if executable in {"sudo", "visudo"}:
        return CommandContext("users.sudo", _user_parameters(args))
    if executable in {"ssh", "sshd", "ssh-keygen", "ssh-copy-id", "sftp"}:
        return CommandContext("security.ssh", _ssh_parameters(args))
    if executable in {"sestatus", "getenforce", "setenforce", "semanage", "restorecon", "aa-status", "apparmor_status"}:
        return CommandContext("security.mac", {})
    if executable in {"auditctl", "ausearch", "aureport"}:
        return CommandContext("security.audit", {})
    if executable == "crontab":
        return CommandContext("schedule", {})
    if executable in {"at", "atq", "atrm", "batch"}:
        return CommandContext("schedule.at", {})
    if executable in {"apt", "apt-get", "apt-cache", "aptitude", "dpkg", "dpkg-query", "dnf", "yum", "rpm", "apk", "pacman", "zypper", "snap", "flatpak"}:
        operation = _first_positional(args) or ""
        change_operations = {
            "install", "remove", "purge", "upgrade", "dist-upgrade", "full-upgrade",
            "add", "del", "delete", "erase", "-i", "-U", "-S", "-R",
        }
        action = "package.change" if operation in change_operations else "package"
        parameters = _package_parameters(args)
        parameters["package_family"] = _PACKAGE_EXECUTABLE_FAMILIES[executable]
        return CommandContext(action, parameters)
    if executable in {"nft", "iptables", "ip6tables", "ebtables", "iptables-save", "ip6tables-save", "firewall-cmd", "ufw"}:
        return CommandContext("firewall", {})
    if executable in {"docker", "podman", "nerdctl"}:
        return _classify_container(executable, args)
    if executable in {"docker-compose", "podman-compose"}:
        return CommandContext("container.compose", {"engine": _quote_parameter(executable.split("-", 1)[0])})
    if executable == "kubectl":
        operation = _first_positional(args) or ""
        action = "kubernetes.inspect" if operation in {"get", "describe", "logs", "top", "config", "cluster-info", "api-resources", "version", "explain"} else "kubernetes.change"
        return CommandContext(action, {})
    if executable == "helm":
        return CommandContext("kubernetes.helm", {})
    if executable in {"virsh", "virt-install"}:
        operation = _first_positional(args) or ""
        read_operations = {"list", "dominfo", "domstate", "dumpxml", "net-list", "net-info", "pool-list", "pool-info", "vol-list"}
        action = "virtualization.inspect" if operation in read_operations else "virtualization.change"
        return CommandContext(action, _domain_parameters(args))
    if executable in {"nginx", "apachectl", "apache2ctl", "httpd"}:
        return CommandContext("web.server", {})
    if executable in {"mysql", "mysqladmin", "mariadb", "mariadb-admin"}:
        return CommandContext("database.mysql", {})
    if executable in {"psql", "pg_isready", "pg_ctl"}:
        return CommandContext("database.postgresql", {})
    if executable in {"redis-cli", "redis-server"}:
        return CommandContext("database.redis", {})
    return None


_COMMON_VALUE_OPTIONS = frozenset({
    "-c", "--count", "--directory", "-f", "--file", "-g", "--group",
    "-h", "--host", "--interface",
    "-I", "-n", "--lines", "-o", "--output", "-p", "--port", "--pid",
    "-s", "--source", "-t", "--timeout", "-u", "--user", "-w", "--wait",
    "-W", "--password", "--namespace", "--context", "--tail", "--since",
})


def _operands(args: list[str], *, value_options: frozenset[str] = _COMMON_VALUE_OPTIONS) -> list[str]:
    operands: list[str] = []
    skip_next = False
    literal = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if literal:
            operands.append(arg)
            continue
        if arg == "--":
            literal = True
            continue
        if arg in value_options:
            skip_next = True
            continue
        if any(arg.startswith(f"{option}=") for option in value_options if option.startswith("--")):
            continue
        if not arg.startswith("-"):
            operands.append(arg)
    return operands


def _path_parameters(args: list[str]) -> dict[str, str]:
    operands = _operands(args)
    if not operands:
        return {}
    return {"path": _quote_parameter(operands[-1])}


def _device_parameters(args: list[str]) -> dict[str, str]:
    operands = _operands(args)
    device = next((item for item in reversed(operands) if item.startswith("/dev/")), None)
    if device is None and operands:
        device = operands[-1]
    return {"device": _quote_parameter(device)} if device else {}


def _pid_parameters(args: list[str]) -> dict[str, str]:
    explicit = _option_value(args, "-p", "--pid")
    if explicit and explicit.isdigit():
        return {"pid": explicit}
    for item in _operands(args):
        if item.isdigit():
            return {"pid": item}
    return {}


def _interface_parameters(args: list[str]) -> dict[str, str]:
    for marker in ("dev", "device", "interface"):
        try:
            value = args[args.index(marker) + 1]
        except (ValueError, IndexError):
            continue
        if value and not value.startswith("-"):
            return {"interface": _quote_parameter(value)}
    operands = _operands(args)
    excluded = {
        "addr", "address", "link", "show", "set", "list", "status", "route",
        "rule", "neigh", "neighbor", "tunnel", "netns", "maddress",
    }
    for value in reversed(operands):
        if value not in excluded and not value.isdigit():
            return {"interface": _quote_parameter(value)}
    return {}


def _network_target_parameters(args: list[str]) -> dict[str, str]:
    excluded = {
        "add", "append", "change", "delete", "del", "flush", "get", "list",
        "replace", "show", "table", "all", "cache", "default", "route", "rule",
        "query", "monitor", "help",
    }
    for value in reversed(_operands(args)):
        candidate = value.split("/", 1)[0]
        if candidate.lower() in excluded or candidate.isdigit():
            continue
        return {"target": _quote_parameter(candidate)}
    return {}


def _url_parameters(args: list[str]) -> dict[str, str]:
    operands = _operands(args)
    if not operands:
        return {}
    raw_url = operands[-1]
    if raw_url in {"head", "get", "post", "put", "delete"}:
        return {}
    parsed = urlsplit(raw_url if "://" in raw_url else f"//{raw_url}")
    host = parsed.hostname
    parameters = {"url": _quote_parameter(raw_url)}
    if host:
        parameters["target"] = _quote_parameter(host)
        try:
            parsed_port = parsed.port
        except ValueError:
            parsed_port = None
        parameters["port"] = str(parsed_port or (443 if parsed.scheme == "https" else 80))
    return parameters


def _host_port_parameters(args: list[str]) -> dict[str, str]:
    operands = _operands(args)
    if not operands:
        return {}
    port = operands[-1] if operands[-1].isdigit() else None
    target_index = -2 if port and len(operands) >= 2 else -1
    target = operands[target_index].rsplit("@", 1)[-1]
    parameters = {"target": _quote_parameter(target)} if target else {}
    if port:
        parameters["port"] = port
    return parameters


def _openssl_parameters(args: list[str]) -> dict[str, str]:
    endpoint = _option_value(args, "-connect")
    if not endpoint:
        return {}
    host = endpoint
    port = "443"
    if endpoint.startswith("[") and "]:" in endpoint:
        host, port = endpoint[1:].rsplit("]:", 1)
    elif ":" in endpoint:
        host, port = endpoint.rsplit(":", 1)
    if not port.isdigit():
        return {}
    return {"target": _quote_parameter(host), "port": port}


def _user_parameters(args: list[str]) -> dict[str, str]:
    excluded = {"passwd", "group", "shadow", "hosts", "services", "status"}
    for value in reversed(_operands(args)):
        if value not in excluded and not value.isdigit():
            return {"user": _quote_parameter(value)}
    return {}


def _ssh_parameters(args: list[str]) -> dict[str, str]:
    operands = _operands(args)
    if not operands:
        return {}
    target = operands[-1].rsplit("@", 1)[-1]
    parameters = {"target": _quote_parameter(target)} if target else {}
    port = _option_value(args, "-p", "--port")
    if port and port.isdigit():
        parameters["port"] = port
    return parameters


def _package_parameters(args: list[str]) -> dict[str, str]:
    operations = {
        "install", "remove", "purge", "upgrade", "update", "add", "del",
        "delete", "erase", "info", "search", "show", "policy", "list",
    }
    for value in reversed(_operands(args)):
        if value.lower() not in operations and not value.startswith("/"):
            return {"package": _quote_parameter(value)}
    return {}


def _classify_container(executable: str, args: list[str]) -> CommandContext:
    operation = _first_positional(args) or ""
    engine = _quote_parameter(executable)
    parameters = {"engine": engine}
    container_operations = {
        "inspect", "logs", "stats", "start", "stop", "restart", "kill", "rm",
        "exec", "attach", "pause", "unpause", "wait", "top", "port", "rename",
    }
    if operation in container_operations:
        operation_index = _first_positional_index(args)
        remaining = args[operation_index + 1:] if operation_index is not None else []
        operands = _operands(remaining)
        if operands:
            parameters["container"] = _quote_parameter(operands[-1])
    if operation in {"ps", "info", "version", "events", "system"}:
        action = "container.list"
    elif operation in {"inspect", "top", "port"}:
        action = "container.inspect"
    elif operation == "logs":
        action = "container.logs"
    elif operation in {"images", "image", "pull", "push", "build", "tag", "rmi", "save", "load"}:
        action = "container.images"
    elif operation in {"network", "volume"}:
        action = "container.network"
    elif operation == "compose":
        action = "container.compose"
    else:
        action = "container.change"
    return CommandContext(action, parameters)


def _domain_parameters(args: list[str]) -> dict[str, str]:
    operands = _operands(args)
    if len(operands) < 2:
        return {}
    return {"domain": _quote_parameter(operands[1])}


def _classify_systemctl(args: list[str]) -> CommandContext:
    operation_index = _first_positional_index(args)
    if operation_index is None:
        return CommandContext("service.overview", {})
    operation = args[operation_index]
    if operation == "list-timers":
        return CommandContext("schedule", {})
    if operation in {"status", "show", "is-active", "is-enabled", "cat"}:
        service = _next_positional(args, operation_index + 1)
        if service:
            return CommandContext("service.status", {"service": _quote_parameter(service)})
    if operation in {
        "start", "stop", "restart", "try-restart", "reload", "reload-or-restart",
        "enable", "disable", "reenable", "mask", "unmask", "daemon-reload",
        "reset-failed", "set-property",
    }:
        service = _next_positional(args, operation_index + 1)
        return CommandContext(
            "service.change",
            {"service": _quote_parameter(service)} if service else {},
        )
    return CommandContext("service.overview", {})


def _command_tokens(command: str) -> list[str]:
    if not command or any(ord(char) < 32 and char not in "\t" for char in command):
        return []
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return []
    if not tokens:
        return []

    # For compound commands, the final completed command is the best description
    # of what the operator just did.  Pipelines stay intact and are classified by
    # their first executable because their primary diagnostic intent starts there.
    for index in range(len(tokens) - 1, -1, -1):
        if tokens[index] in {"&&", "||", ";"}:
            tokens = tokens[index + 1:]
            break
    tokens = _strip_execution_prefix(tokens)
    return tokens if tokens and "\t" not in "".join(tokens) else []


def _strip_execution_prefix(tokens: list[str]) -> list[str]:
    tokens = list(tokens)
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        tokens.pop(0)
    if not tokens or tokens[0] != "sudo":
        return tokens

    sudo_invocation = list(tokens)
    tokens.pop(0)
    options_with_values = {"-u", "--user", "-U", "--other-user", "-g", "--group", "-h", "--host", "-p", "--prompt"}
    while tokens and tokens[0].startswith("-"):
        option = tokens.pop(0)
        if option in options_with_values and tokens:
            tokens.pop(0)
    return tokens or sudo_invocation


def _render(template: str, parameters: dict[str, str]) -> str | None:
    try:
        rendered = template.format_map(parameters)
    except KeyError:
        return None
    return rendered.strip()


def _normalized_command(command: str) -> str:
    return " ".join(command.strip().split())


def _quote_parameter(value: str) -> str:
    return shlex.quote(value)


def _has_option(args: list[str], *options: str) -> bool:
    return any(arg in options or (arg.startswith("-") and any(option[1:] in arg[1:] for option in options if option.startswith("-") and len(option) == 2)) for arg in args)


def _option_value(args: list[str], *options: str) -> str | None:
    for index, arg in enumerate(args):
        if arg in options and index + 1 < len(args):
            return args[index + 1]
        for option in options:
            if option.startswith("--") and arg.startswith(f"{option}="):
                return arg.split("=", 1)[1]
    return None


def _first_positional(args: list[str]) -> str | None:
    index = _first_positional_index(args)
    return args[index] if index is not None else None


def _first_positional_index(args: list[str]) -> int | None:
    for index, arg in enumerate(args):
        if not arg.startswith("-"):
            return index
    return None


def _next_positional(args: list[str], start: int) -> str | None:
    for arg in args[start:]:
        if not arg.startswith("-"):
            return arg
    return None


def _last_positional(args: list[str]) -> str | None:
    options_with_values = {"-c", "-i", "-I", "-s", "-t", "-W", "-w", "-q"}
    skip_next = False
    positionals = []
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in options_with_values:
            skip_next = True
            continue
        if not arg.startswith("-"):
            positionals.append(arg)
    return positionals[-1] if positionals else None


def _augment_linux_graph_from_catalog() -> None:
    node_by_command = {
        _normalized_command(template): node
        for node, template in _NODES.items()
        if "{" not in template
    }
    for index, raw_command in enumerate(_COMPLETION_CATALOG, start=1):
        command = _normalized_command(raw_command)
        context = _classify(command)
        if not command or context is None:
            continue
        node = node_by_command.get(command)
        if node is None:
            node = f"catalog.command.{index:04d}"
            _NODES[node] = command
            node_by_command[command] = node
        edges = list(_GRAPH[context.action])
        if all(edge.target != node for edge in edges):
            edges.append(FlowEdge(node, 10, f"完整行为节点：{command}"))
            _GRAPH[context.action] = tuple(edges)


_augment_linux_graph_from_catalog()
LINUX_COMMAND_NODES = LINUX_RECOMMENDATION_NODES


assert set(BEHAVIOR_LABELS) == set(LINUX_BEHAVIOR_GRAPH)
assert all(
    edge.target in LINUX_COMMAND_NODES
    for edges in LINUX_BEHAVIOR_GRAPH.values()
    for edge in edges
)
assert all(len(edges) >= 5 for edges in LINUX_BEHAVIOR_GRAPH.values())
assert set(LINUX_COMMAND_NODES).issubset({
    edge.target
    for edges in LINUX_BEHAVIOR_GRAPH.values()
    for edge in edges
})


__all__ = [
    "BEHAVIOR_LABELS",
    "LINUX_BEHAVIOR_GRAPH",
    "LINUX_COMMAND_NODES",
    "LINUX_RECOMMENDATION_NODES",
    "MAX_RECOMMENDATIONS",
    "classify_linux_command",
    "complete_linux_command",
    "recommend_next_linux_commands",
]
