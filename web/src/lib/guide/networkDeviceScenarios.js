import { Activity, Database, EthernetPort, Network, Server, Shield } from "lucide-react";
import { guideResult, invalidGuide } from "./helpers.js";

function fixedScenario({ id, categoryId, title, summary, commands, icon = Network, hints = [] }) {
  return {
    id,
    categoryId,
    title,
    summary,
    icon,
    fields: [],
    buildGuide: () =>
      guideResult({
        status: "good",
        title: `${title}指令已生成`,
        message: "以下均为只读查询指令，可直接下发到当前终端。不同型号或软件版本可能存在少量差异。",
        commands,
        hints: hints.length
          ? hints
          : ["先确认设备厂家和系统版本", "关注异常、Down、Drop 和错误计数", "修改配置前请单独核对影响范围"],
      }),
  };
}

export const firewallScenarios = [
  fixedScenario({
    id: "firewall-huawei-system",
    categoryId: "firewall-huawei",
    title: "系统与资源状态",
    summary: "查看版本、CPU 和内存",
    icon: Activity,
    commands: ["display version", "display cpu-usage", "display memory-usage"],
  }),
  fixedScenario({
    id: "firewall-huawei-forwarding",
    categoryId: "firewall-huawei",
    title: "接口与路由",
    summary: "查看接口、路由表和 ARP",
    icon: EthernetPort,
    commands: ["display interface brief", "display ip routing-table", "display arp"],
  }),
  fixedScenario({
    id: "firewall-huawei-policy",
    categoryId: "firewall-huawei",
    title: "安全策略",
    summary: "查看安全策略与命中情况",
    icon: Shield,
    commands: ["display security-policy rule all"],
    hints: ["关注规则状态和动作", "检查规则顺序", "结合命中次数判断流量是否匹配"],
  }),
  fixedScenario({
    id: "firewall-huawei-session-nat",
    categoryId: "firewall-huawei",
    title: "会话与 NAT",
    summary: "查看会话、NAT 策略和服务器映射",
    icon: Database,
    commands: ["display firewall session table", "display nat-policy rule all", "display nat server"],
  }),
  fixedScenario({
    id: "firewall-h3c-system",
    categoryId: "firewall-h3c",
    title: "系统与资源状态",
    summary: "查看版本、设备和资源使用",
    icon: Activity,
    commands: ["display version", "display device", "display cpu-usage", "display memory"],
  }),
  fixedScenario({
    id: "firewall-h3c-forwarding",
    categoryId: "firewall-h3c",
    title: "接口与路由",
    summary: "查看接口、路由表和 ARP",
    icon: EthernetPort,
    commands: ["display interface brief", "display ip routing-table", "display arp"],
  }),
  fixedScenario({
    id: "firewall-h3c-policy",
    categoryId: "firewall-h3c",
    title: "安全策略",
    summary: "查看 IPv4 策略和统计",
    icon: Shield,
    commands: ["display security-policy ip brief", "display security-policy statistics"],
  }),
  fixedScenario({
    id: "firewall-h3c-session-nat",
    categoryId: "firewall-h3c",
    title: "会话与 NAT",
    summary: "查看 IPv4 会话和 NAT 配置",
    icon: Database,
    commands: ["display session table ipv4", "display nat all"],
  }),
  fixedScenario({
    id: "firewall-fortinet-system",
    categoryId: "firewall-fortinet",
    title: "系统与资源状态",
    summary: "查看 FortiOS 版本和性能",
    icon: Activity,
    commands: ["get system status", "get system performance status"],
  }),
  fixedScenario({
    id: "firewall-fortinet-forwarding",
    categoryId: "firewall-fortinet",
    title: "接口与路由",
    summary: "查看物理接口和路由表",
    icon: EthernetPort,
    commands: ["get system interface physical", "get router info routing-table all"],
  }),
  fixedScenario({
    id: "firewall-fortinet-policy",
    categoryId: "firewall-fortinet",
    title: "策略与 NAT 对象",
    summary: "查看策略、VIP 和地址池",
    icon: Shield,
    commands: ["show firewall policy", "show firewall vip", "show firewall ippool"],
  }),
  fixedScenario({
    id: "firewall-fortinet-session",
    categoryId: "firewall-fortinet",
    title: "会话状态",
    summary: "查看会话统计和会话表",
    icon: Database,
    commands: ["diagnose sys session stat", "diagnose sys session list"],
  }),
];

const switchCatalog = {
  huawei: {
    label: "华为",
    commands: {
      system: [
        ["设备概览", "查看版本、设备和补丁", ["display version", "display device", "display patch-information"]],
        ["资源状态", "查看 CPU 和内存", ["display cpu-usage", "display memory-usage"]],
        ["当前配置", "查看正在生效的配置", ["display current-configuration"]],
        ["启动配置", "查看已保存的启动配置", ["display saved-configuration"]],
        ["系统日志", "查看日志缓冲区中的异常", ["display logbuffer"]],
      ],
      interface: [
        ["端口状态", "查看接口 Up/Down 和速率", ["display interface brief"]],
        ["端口错误", "查看接口错误计数", ["display counters error"]],
      ],
      vlan: [
        ["VLAN 与 Trunk", "查看 VLAN 和端口成员", ["display vlan", "display port vlan"]],
        ["MAC 地址表", "查看 MAC 学习结果", ["display mac-address"]],
      ],
      topology: [
        ["STP 状态", "查看生成树和阻塞端口", ["display stp brief"]],
        ["邻居发现", "确认端口连接到哪台设备", ["display lldp neighbor brief"]],
        ["链路聚合", "检查 Eth-Trunk 成员与状态", ["display eth-trunk"]],
      ],
      routing: [
        ["三层接口", "查看 VLANIF 与 IP 状态", ["display ip interface brief"]],
        ["路由表", "检查默认路由与网段出口", ["display ip routing-table"]],
        ["ARP 邻居", "检查 IP 与 MAC 的对应关系", ["display arp"]],
      ],
    },
  },
  h3c: {
    label: "H3C",
    commands: {
      system: [
        ["设备概览", "查看版本、设备和补丁", ["display version", "display device", "display patch-information"]],
        ["资源状态", "查看 CPU 和内存", ["display cpu-usage", "display memory"]],
      ],
      interface: [
        ["端口状态", "查看接口 Up/Down 和速率", ["display interface brief"]],
        ["端口详情", "查看接口流量和错误", ["display interface"]],
      ],
      vlan: [
        ["VLAN 与端口", "查看 VLAN 和端口成员", ["display vlan brief", "display port"]],
        ["MAC 地址表", "查看 MAC 学习结果", ["display mac-address"]],
      ],
      topology: [
        ["STP 状态", "查看生成树和阻塞端口", ["display stp brief"]],
        ["邻居与聚合", "查看 LLDP 邻居和链路聚合", ["display lldp neighbor-information list", "display link-aggregation verbose"]],
      ],
      routing: [
        ["三层接口", "查看 VLAN 接口与 IP 状态", ["display ip interface brief"]],
        ["路由与 ARP", "查看路由表和邻居解析", ["display ip routing-table", "display arp"]],
      ],
    },
  },
  cisco: {
    label: "Cisco",
    commands: {
      system: [
        ["设备概览", "查看 IOS 版本和硬件", ["show version", "show inventory"]],
        ["资源状态", "查看 CPU 和内存", ["show processes cpu sorted", "show processes memory sorted"]],
        ["当前配置", "查看正在生效的配置", ["show running-config"]],
        ["启动配置", "查看已保存的启动配置", ["show startup-config"]],
        ["系统日志", "查看缓冲日志与告警", ["show logging"]],
      ],
      interface: [
        ["端口状态", "查看端口、VLAN 和速率", ["show interfaces status"]],
        ["端口错误", "查看接口错误计数", ["show interfaces counters errors"]],
      ],
      vlan: [
        ["VLAN 与 Trunk", "查看 VLAN 和 Trunk", ["show vlan brief", "show interfaces trunk"]],
        ["MAC 地址表", "查看 MAC 学习结果", ["show mac address-table"]],
      ],
      topology: [
        ["STP 状态", "查看生成树摘要", ["show spanning-tree summary"]],
        ["邻居发现", "确认端口连接到哪台设备", ["show cdp neighbors"]],
        ["链路聚合", "检查 EtherChannel 成员与状态", ["show etherchannel summary"]],
      ],
      routing: [
        ["三层接口", "查看 IP 接口状态", ["show ip interface brief"]],
        ["路由表", "检查默认路由与网段出口", ["show ip route"]],
        ["ARP 邻居", "检查 IP 与 MAC 的对应关系", ["show ip arp"]],
      ],
    },
  },
  zte: {
    label: "中兴 ZXR10 5960 / 5960-H",
    commands: {
      system: [
        ["设备概览", "查看版本和系统信息", ["show version", "show system-info"]],
        ["资源状态", "查看处理器资源及明细", ["show processor", "show processor details"]],
        ["当前配置", "查看正在生效的配置", ["show running-config"]],
        ["启动配置", "查看已保存的启动配置", ["show startup-config"]],
        ["系统告警", "查看运行告警及日志配置", ["show logging alarm", "show logging configuration"]],
      ],
      interface: [
        ["端口状态", "查看物理端口的 Up/Down 状态", ["show interface brief"]],
        ["端口流量与错误", "查看接口计数和错误情况", ["show interface"]],
      ],
      vlan: [
        ["VLAN 与 Trunk", "查看 VLAN 及 Trunk 端口成员", ["show vlan", "show vlan trunk"]],
        ["MAC 地址表", "查看 MAC 学习结果", ["show mac table"]],
      ],
      topology: [
        ["STP 状态", "检查 MST 配置及不一致端口", ["show spantree mst-config", "show spantree inconsistentports"]],
        ["邻居发现", "确认端口连接到哪台设备", ["show lldp neighbor"]],
        ["链路聚合", "查看 LACP 系统标识和邻居", ["show lacp sys-id", "show lacp neighbors"]],
      ],
      routing: [
        ["三层接口", "查看接口 IP 与协议状态", ["show ip interface brief"]],
        ["路由表", "检查当前 IPv4 转发表", ["show ip forwarding route"]],
        ["ARP 邻居", "检查 IP 与 MAC 的对应关系", ["show arp"]],
      ],
    },
  },
};

const switchIcons = {
  system: Server,
  interface: EthernetPort,
  vlan: Database,
  topology: Network,
  routing: Network,
};

const fixedSwitchScenarios = Object.entries(switchCatalog).flatMap(([vendorId, vendor]) =>
  Object.entries(vendor.commands).flatMap(([functionId, scenarios]) =>
    scenarios.map(([title, summary, commands], index) =>
      fixedScenario({
        id: `switch-${vendorId}-${functionId}-${index + 1}`,
        categoryId: `switch-${vendorId}-${functionId}`,
        title,
        summary,
        commands,
        icon: switchIcons[functionId],
        hints: [
          `当前模板适用于常见${vendor.label}交换机版本`,
          "先查看摘要，再按异常端口继续定位",
          "不同产品线的命令参数可能略有差异",
        ],
      }),
    ),
  ),
);

// Common operations use the existing GuideForm, never raw manual grammar. No
// example port/VLAN is silently supplied as an execution target.
const targetedQueries = {
  huawei: { interface: "display interface", vlan: "display vlan" },
  cisco: { interface: "show interfaces", vlan: "show vlan id" },
  zte: { interface: "show interface", vlan: "show vlan id" },
};

const targetedSwitchScenarios = Object.entries(targetedQueries).flatMap(([vendorId, commands]) =>
  ["interface", "vlan"].map((kind) => {
    const isInterface = kind === "interface";
    const field = isInterface
      ? { key: "interfaceName", label: "接口名称" }
      : { key: "vlanId", label: "VLAN ID", inputMode: "numeric" };
    return {
      id: `switch-${vendorId}-${kind}-detail`, categoryId: `switch-${vendorId}-${kind}`,
      title: isInterface ? "指定端口详情" : "指定 VLAN 详情",
      summary: isInterface ? "输入接口名称，定位端口流量与错误" : "输入 VLAN ID，检查该 VLAN 的端口成员",
      icon: switchIcons[kind], fields: [field],
      buildGuide(form = {}) {
        const value = String(form[field.key] || "").trim();
        const valid = isInterface
          ? /^[a-z][a-z0-9_./:-]*(?: +[0-9][a-z0-9_./:-]*)?$/i.test(value) && /\d/.test(value)
          : /^\d{1,4}$/.test(value) && Number(value) >= 1 && Number(value) <= 4094;
        if (!valid) return invalidGuide(
          isInterface ? "请填写完整接口名称" : "请填写 1–4094 的 VLAN ID",
          "仅接受单个目标，不接受多行或附加命令", [],
        );
        return guideResult({
          status: "good", title: "查询指令已生成", message: "下发只会填入终端，按回车后执行查询。",
          commands: [`${commands[kind]} ${isInterface ? value : Number(value)}`],
          hints: ["请使用设备上显示的实际接口名称", "不同型号或软件版本的接口命名可能不同"],
        });
      },
    };
  }),
);

export const switchScenarios = [...fixedSwitchScenarios, ...targetedSwitchScenarios];
