import { Database, EthernetPort, HardDrive, Server, Shield, UserRound } from "lucide-react";
import { DEVICE_TYPES, deviceProfileConfig } from "../deviceProfiles.js";

function switchFunctionGroups(vendorId) {
  return [
    {
      id: `switch-${vendorId}-system`,
      label: "系统状态",
      summary: "版本、资源与运行状态",
      icon: Server,
    },
    {
      id: `switch-${vendorId}-interface`,
      label: "接口检查",
      summary: "端口状态、流量与错误计数",
      icon: EthernetPort,
    },
    {
      id: `switch-${vendorId}-vlan`,
      label: "VLAN 与 MAC",
      summary: "VLAN、Trunk 与 MAC 地址表",
      icon: Database,
    },
    {
      id: `switch-${vendorId}-topology`,
      label: "二层拓扑",
      summary: "STP、邻居发现与链路聚合",
      icon: EthernetPort,
    },
    {
      id: `switch-${vendorId}-routing`,
      label: "三层转发",
      summary: "三层接口、路由与 ARP",
      icon: Database,
    },
  ];
}

export const offlineSkillTree = [
  {
    id: "linux-ops",
    label: "Linux 运维",
    summary: "主机系统与服务排查",
    icon: Server,
    branches: [
      { id: "network", label: "网络检查", summary: "连通性、端口、DNS 和路由", icon: EthernetPort },
      { id: "system", label: "系统检查", summary: "磁盘、CPU、内存和基础状态", icon: HardDrive },
      { id: "process", label: "进程资源", summary: "进程、PID、资源占用和查找", icon: Database },
      { id: "file", label: "文件磁盘", summary: "目录、挂载、inode 和权限", icon: HardDrive },
      { id: "service", label: "服务检查", summary: "服务状态、日志和重启前确认", icon: Server },
      { id: "user", label: "用户权限", summary: "用户、用户组、sudo 和 SSH", icon: UserRound },
      { id: "software", label: "软件计划", summary: "软件包、软件源和环境变量", icon: Database },
      { id: "security", label: "安全检查", summary: "防火墙、登录失败和时间同步", icon: Shield },
      { id: "danger", label: "高危操作", summary: "重启、停止、删除等需要二次确认", icon: Shield },
    ],
  },
  {
    id: "firewall-ops",
    label: "防火墙运维",
    summary: "按厂家查看策略、会话和 NAT",
    icon: Shield,
    branches: [
      {
        id: "firewall-huawei",
        label: "华为防火墙",
        summary: "USG 与 HiSecEngine 常用只读指令",
        icon: Shield,
      },
      {
        id: "firewall-h3c",
        label: "H3C 防火墙",
        summary: "SecPath 系列常用只读指令",
        icon: Shield,
      },
      {
        id: "firewall-fortinet",
        label: "Fortinet",
        summary: "FortiGate 常用只读指令",
        icon: Shield,
      },
    ],
  },
  {
    id: "switch-ops",
    label: "交换机运维",
    summary: "按厂家和功能快速检查",
    icon: EthernetPort,
    branches: [
      {
        id: "switch-huawei",
        label: "华为交换机",
        summary: "CloudEngine 与 S 系列",
        icon: EthernetPort,
        groups: switchFunctionGroups("huawei"),
      },
      {
        id: "switch-h3c",
        label: "H3C 交换机",
        summary: "Comware 系列",
        icon: EthernetPort,
        groups: switchFunctionGroups("h3c"),
      },
      {
        id: "switch-cisco",
        label: "Cisco 交换机",
        summary: "Catalyst IOS 与 IOS XE",
        icon: EthernetPort,
        groups: switchFunctionGroups("cisco"),
      },
      {
        id: "switch-zte",
        label: "中兴交换机",
        summary: "ZXR10 5960 / 5960-H 常用操作",
        icon: EthernetPort,
        groups: switchFunctionGroups("zte"),
      },
    ],
  },
];

export function skillTreeForDeviceProfile(deviceProfile) {
  const selected = deviceProfileConfig(deviceProfile);
  const profiles = selected
    ? [selected]
    : DEVICE_TYPES.flatMap((type) => type.profiles);

  return offlineSkillTree.flatMap((domain) => {
    const domainProfiles = profiles.filter((profile) => profile.guideRootIds.includes(domain.id));
    if (!domainProfiles.length) return [];

    const showsEveryBranch = domainProfiles.some((profile) => !profile.guideBranchIds.length);
    const allowedBranches = new Set(domainProfiles.flatMap((profile) => profile.guideBranchIds));
    return [{
      ...domain,
      branches: showsEveryBranch
        ? domain.branches
        : domain.branches.filter((branch) => allowedBranches.has(branch.id)),
    }];
  });
}
