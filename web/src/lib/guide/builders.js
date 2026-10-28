import { guideResult, invalidGuide, cleanPath } from "./helpers.js";
import {
  HOST_PATTERN,
  SERVICE_PATTERN,
  KEYWORD_PATTERN,
  MOUNT_PATTERN,
  PATH_PATTERN,
  USER_PATTERN,
  PROTOCOL_PATTERN,
  IPV4_PATTERN,
  validatePath,
  validateDestructivePath,
  validateService,
  validatePid,
  validateKeyword,
  validateUser,
} from "./validators.js";

export function buildPortCheckGuide({ host, port }, target) {
  const cleanHost = (host || "").trim();
  const cleanPort = Number(port);

  if (!cleanHost || !IPV4_PATTERN.test(cleanHost)) {
    return {
      status: "error",
      title: "目标地址需要检查",
      message: "请输入完整 IPv4 地址，例如 192.168.10.1，每段范围为 0 到 255。",
      commands: [],
      hints: ["例子：192.168.10.1", "请直接输入完整地址"],
    };
  }

  if (!Number.isInteger(cleanPort) || cleanPort < 1 || cleanPort > 65535) {
    return {
      status: "error",
      title: "端口需要检查",
      message: "端口只能填写 1 到 65535 之间的数字。",
      commands: [],
      hints: ["SSH 常用 22", "HTTP 常用 80", "HTTPS 常用 443"],
    };
  }

  const commands = [
    `ping -c 4 ${cleanHost}`,
    `nc -zv ${cleanHost} ${cleanPort}`,
    `ss -tlnp | grep ":${cleanPort}"`,
    "iptables -L -n -v",
  ];
  const matchesCurrentTarget = cleanHost === target.host && cleanPort === Number(target.port);

  return {
    status: matchesCurrentTarget ? "good" : "warn",
    title: matchesCurrentTarget ? "端口可以访问" : "端口可能没有开放",
    message: matchesCurrentTarget
      ? `${cleanHost}:${cleanPort} 和当前设备一致，规则判断为可访问。`
      : "如果终端里 ping 成功但 nc 失败，优先检查服务是否启动，之后再看防火墙规则。",
    commands,
    hints: matchesCurrentTarget
      ? ["主机可达", "端口探测通过", "继续检查服务状态即可"]
      : ["看 ping 是否成功", "看 nc 是否 succeeded", "看 ss 是否有监听", "看 iptables 是否拦截"],
  };
}

export function buildTelnetConnectionGuide({ host, port }) {
  const cleanHost = (host || "").trim();
  const cleanPort = Number(port);
  if (!cleanHost || !IPV4_PATTERN.test(cleanHost)) {
    return invalidGuide(
      "目标地址需要检查",
      "请输入完整 IPv4 地址，例如 192.168.10.1，每段范围为 0 到 255。",
      ["例子：192.168.10.1", "请直接输入完整地址"],
    );
  }
  if (!Number.isInteger(cleanPort) || cleanPort < 1 || cleanPort > 65535) {
    return invalidGuide("端口需要检查", "端口只能填写 1 到 65535 之间的数字。", [
      "Telnet 默认端口为 23",
      "非标准端口请以现场配置为准",
    ]);
  }
  return guideResult({
    status: "warn",
    title: "Telnet 连接测试已生成",
    message: `将使用 Telnet 连接 ${cleanHost}:${cleanPort}。`,
    commands: [`telnet ${cleanHost} ${cleanPort}`],
    hints: [
      "出现 Connected 表示端口可以访问",
      "Connection refused 表示目标可达但服务未监听",
      "超时通常需要检查路由和防火墙",
      "退出交互会话时按 Ctrl+]，再输入 quit",
    ],
  });
}

export function buildTelnetServiceGuide({ port }) {
  const cleanPort = Number(port);
  if (!Number.isInteger(cleanPort) || cleanPort < 1 || cleanPort > 65535) {
    return invalidGuide("端口需要检查", "端口只能填写 1 到 65535 之间的数字。", [
      "Telnet 默认端口为 23",
    ]);
  }
  return guideResult({
    status: "warn",
    title: "Telnet 服务检查已生成",
    message: `将检查本机 ${cleanPort} 端口以及常见 Telnet 服务单元。`,
    commands: [
      `ss -tlnp | grep ":${cleanPort}"`,
      "systemctl status telnet.socket",
      "systemctl status xinetd",
    ],
    hints: [
      "ss 有 LISTEN 记录才表示服务正在监听",
      "不同发行版可能由 telnet.socket 或 xinetd 管理",
      "Telnet 为明文协议，只建议用于受控维护网络",
    ],
  });
}

export function buildRouteCheckGuide({ host }) {
  const cleanHost = (host || "").trim();
  if (!cleanHost || !IPV4_PATTERN.test(cleanHost)) {
    return {
      status: "error",
      title: "目标地址需要检查",
      message: "请输入完整 IPv4 地址，例如 192.168.10.1，每段范围为 0 到 255。",
      commands: [],
      hints: ["例子：192.168.10.1", "请直接输入完整地址"],
    };
  }

  return {
    status: "warn",
    title: "访问速度检查已生成",
    message: `${cleanHost} 将按 ping 延迟和路由跳点判断慢在哪里。`,
    commands: [`ping -c 4 ${cleanHost}`, `traceroute ${cleanHost}`],
    hints: ["ping 丢包说明链路不稳定", "traceroute 某一跳延迟很高时优先查该段网络", "目标不可达时先回到端口检查"],
  };
}

export function buildDnsCheckGuide({ domain }) {
  const cleanDomain = (domain || "").trim();
  if (!cleanDomain || !HOST_PATTERN.test(cleanDomain)) {
    return {
      status: "error",
      title: "域名需要检查",
      message: "只填写域名或主机名，不要带 http://、路径、空格和命令符号。",
      commands: [],
      hints: ["例子：example.com", "例子：server.local"],
    };
  }

  return {
    status: "warn",
    title: "域名解析检查已生成",
    message: `${cleanDomain} 将先看本机解析，再看 DNS 工具输出。`,
    commands: [`getent hosts ${cleanDomain}`, `nslookup ${cleanDomain}`],
    hints: ["getent 为空说明系统解析失败", "nslookup 有结果但业务失败时继续查端口", "解析到错误地址时检查 hosts 和 DNS 配置"],
  };
}

export function buildDiskCheckGuide({ mount }) {
  const cleanMount = (mount || "").trim() || "/";
  if (!MOUNT_PATTERN.test(cleanMount)) {
    return {
      status: "error",
      title: "挂载点需要检查",
      message: "挂载点必须以 / 开头，只填写本机路径，不要带空格或命令符号。",
      commands: [],
      hints: ["例子：/", "例子：/var", "例子：/data"],
    };
  }

  return {
    status: "good",
    title: "磁盘空间检查已生成",
    message: `${cleanMount} 将按本地 df 输出判断容量风险。`,
    commands: [`df -h ${cleanMount}`],
    hints: ["关注 Use% 是否超过 90%", "关注 Avail 是否明显偏低", "空间不足时优先清理日志和临时文件"],
  };
}

export function buildCpuCheckGuide({ keyword }) {
  const cleanKeyword = (keyword || "").trim();
  if (!KEYWORD_PATTERN.test(cleanKeyword)) {
    return {
      status: "error",
      title: "进程关键词需要检查",
      message: "关键词可以不填；如果填写，只能使用字母、数字、点、下划线、横线、冒号和斜杠。",
      commands: [],
      hints: ["例子：nginx", "例子：java", "不确定就留空"],
    };
  }

  const commands = ["uptime", "ps -eo pid,ppid,cmd,%cpu,%mem --sort=-%cpu | head -n 12"];
  if (cleanKeyword) {
    commands.push(`ps -eo pid,ppid,cmd,%cpu,%mem --sort=-%cpu | grep -i ${cleanKeyword}`);
  }

  return {
    status: "warn",
    title: "CPU 检查已生成",
    message: cleanKeyword ? `将重点查看包含 ${cleanKeyword} 的高 CPU 进程。` : "将列出 CPU 占用最高的进程。",
    commands,
    hints: ["load average 明显高于 CPU 核数时要关注", "%CPU 最高的进程优先确认", "不要直接 kill，先确认业务影响"],
  };
}

export function buildMemoryCheckGuide({ threshold }) {
  const cleanThreshold = Number(threshold);
  if (!Number.isInteger(cleanThreshold) || cleanThreshold < 1 || cleanThreshold > 100) {
    return {
      status: "error",
      title: "告警阈值需要检查",
      message: "阈值只能填写 1 到 100 之间的数字。",
      commands: [],
      hints: ["常用阈值：80", "严重阈值：90"],
    };
  }

  return {
    status: cleanThreshold >= 90 ? "warn" : "good",
    title: "内存检查已生成",
    message: `将按 ${cleanThreshold}% 作为小白可理解的风险阈值。`,
    commands: ["free -h", "ps -eo pid,cmd,%mem,%cpu --sort=-%mem | head -n 12"],
    hints: ["available 太低说明可用内存紧张", "%MEM 最高的进程优先确认", "先看是否有缓存、再判断是否需要扩容或重启业务"],
  };
}

export function buildServiceStatusGuide({ service }) {
  const cleanService = (service || "").trim();
  if (!cleanService || !SERVICE_PATTERN.test(cleanService)) {
    return {
      status: "error",
      title: "服务名需要检查",
      message: "服务名只填写 systemd 服务名，不要带空格和命令符号。",
      commands: [],
      hints: ["例子：nginx", "例子：ssh", "例子：httpd"],
    };
  }

  return {
    status: "warn",
    title: "服务状态检查已生成",
    message: `${cleanService} 将按 status、is-active 和最近日志做本地规则判断。`,
    commands: [
      `systemctl status ${cleanService}`,
      `systemctl is-active ${cleanService}`,
      `journalctl -u ${cleanService} -n 50 --no-pager`,
    ],
    hints: ["active 表示服务正在运行", "failed 表示服务启动失败", "日志里优先看 error、denied、timeout"],
  };
}

export function buildLogSearchGuide({ service, keyword }) {
  const cleanService = (service || "").trim();
  const cleanKeyword = (keyword || "").trim() || "error";
  if (!cleanService || !SERVICE_PATTERN.test(cleanService)) {
    return {
      status: "error",
      title: "服务名需要检查",
      message: "服务名只填写 systemd 服务名，不要带空格和命令符号。",
      commands: [],
      hints: ["例子：nginx", "例子：ssh", "例子：httpd"],
    };
  }
  if (!KEYWORD_PATTERN.test(cleanKeyword)) {
    return {
      status: "error",
      title: "关键词需要检查",
      message: "关键词只能使用字母、数字、点、下划线、横线、冒号和斜杠。",
      commands: [],
      hints: ["例子：error", "例子：denied", "例子：timeout"],
    };
  }

  return {
    status: "warn",
    title: "日志检查已生成",
    message: `${cleanService} 的最近日志会按 ${cleanKeyword} 过滤。`,
    commands: [
      `journalctl -u ${cleanService} -n 100 --no-pager`,
      `journalctl -u ${cleanService} -n 200 --no-pager | grep -i ${cleanKeyword}`,
    ],
    hints: ["error 通常表示程序报错", "denied 通常和权限有关", "timeout 通常和网络或依赖服务有关"],
  };
}

export function buildRestartRiskGuide({ service }) {
  const cleanService = (service || "").trim();
  if (!cleanService || !SERVICE_PATTERN.test(cleanService)) {
    return {
      status: "error",
      title: "服务名需要检查",
      message: "服务名只填写 systemd 服务名，不要带空格和命令符号。",
      commands: [],
      hints: ["例子：nginx", "例子：ssh", "例子：httpd"],
    };
  }

  return {
    status: "warn",
    title: "重启前检查已生成",
    message: "这里不会自动重启，只列出重启前应该看的信息。",
    commands: [
      `systemctl status ${cleanService}`,
      `journalctl -u ${cleanService} -n 50 --no-pager`,
      `systemctl list-dependencies ${cleanService}`,
    ],
    hints: ["先确认服务不是关键链路", "看日志里有没有配置错误", "依赖服务异常时不要盲目重启"],
  };
}

export function buildFirewallCheckGuide({ port, protocol }) {
  const cleanPort = Number(port);
  const cleanProtocol = (protocol || "").trim().toLowerCase();
  if (!Number.isInteger(cleanPort) || cleanPort < 1 || cleanPort > 65535) {
    return {
      status: "error",
      title: "端口需要检查",
      message: "端口只能填写 1 到 65535 之间的数字。",
      commands: [],
      hints: ["SSH 常用 22", "HTTP 常用 80", "HTTPS 常用 443"],
    };
  }
  if (!PROTOCOL_PATTERN.test(cleanProtocol)) {
    return {
      status: "error",
      title: "协议需要检查",
      message: "协议只填写 tcp 或 udp。",
      commands: [],
      hints: ["多数 Web 和 SSH 服务使用 tcp", "DNS 可能使用 udp"],
    };
  }

  return {
    status: "warn",
    title: "防火墙检查已生成",
    message: `将只读检查 ${cleanProtocol}/${cleanPort} 的监听和防火墙规则。`,
    commands: [
      `ss -tulnp | grep ":${cleanPort}"`,
      "iptables -L -n -v",
      "ufw status numbered",
    ],
    hints: ["ss 无监听时先查服务", "iptables 或 ufw 拒绝时再考虑放行", "这里不会直接修改防火墙"],
  };
}

export function buildLoginFailuresGuide({ user }) {
  const cleanUser = (user || "").trim();
  if (!KEYWORD_PATTERN.test(cleanUser)) {
    return {
      status: "error",
      title: "用户名需要检查",
      message: "用户名可以不填；如果填写，只能使用字母、数字、点、下划线、横线、冒号和斜杠。",
      commands: [],
      hints: ["例子：admin", "例子：root", "不确定就留空"],
    };
  }

  const commands = ["lastb -n 20", "journalctl -n 200 --no-pager | grep -i \"Failed password\""];
  if (cleanUser) {
    commands.push(`journalctl -n 200 --no-pager | grep -i ${cleanUser}`);
  }

  return {
    status: "warn",
    title: "登录失败检查已生成",
    message: cleanUser ? `将重点查看 ${cleanUser} 的失败登录。` : "将查看最近失败登录和认证日志。",
    commands,
    hints: ["大量失败登录可能是密码错误或扫描", "来源 IP 集中时先确认是否可信", "不要在这里直接改账号策略"],
  };
}

export function buildTimeSyncGuide({ server }) {
  const cleanServer = (server || "").trim() || "ntp.aliyun.com";
  if (!HOST_PATTERN.test(cleanServer)) {
    return {
      status: "error",
      title: "NTP 服务器需要检查",
      message: "只填写域名、IP 或主机名，不要带空格和命令符号。",
      commands: [],
      hints: ["例子：ntp.aliyun.com", "例子：time.windows.com"],
    };
  }

  return {
    status: "good",
    title: "时间同步检查已生成",
    message: `${cleanServer} 将作为对照，确认日志时间是否可信。`,
    commands: ["timedatectl", "chronyc tracking", `chronyc sources -v | grep ${cleanServer}`],
    hints: ["System clock synchronized 应为 yes", "偏移过大会影响审计日志判断", "没有 chronyc 时再看系统使用的时间同步组件"],
  };
}

export function buildInterfaceListGuide() {
  return guideResult({
    status: "good",
    title: "网卡地址检查已生成",
    message: "将列出网卡、IP 和链路状态。",
    commands: ["ip -br addr", "ip -br link"],
    hints: ["UP 表示链路启用", "没有地址时检查 DHCP 或静态配置", "异常网卡优先看 link 状态"],
  });
}

export function buildRouteTableGuide() {
  return guideResult({
    title: "路由表检查已生成",
    message: "将查看默认路由和策略路由。",
    commands: ["ip route", "ip rule"],
    hints: ["default 路由决定默认出口", "多网卡环境关注 metric", "策略路由异常会导致访问绕路"],
  });
}

export function buildListeningPortsGuide() {
  return guideResult({
    title: "监听端口检查已生成",
    message: "将列出本机监听端口和进程。",
    commands: ["ss -tulnp"],
    hints: ["LISTEN 表示本机服务正在监听", "0.0.0.0 表示所有地址", "127.0.0.1 只允许本机访问"],
  });
}

export function buildNetworkConnectionsGuide() {
  return guideResult({
    title: "连接状态检查已生成",
    message: "将统计 TCP 连接状态。",
    commands: ["ss -tan state established", "ss -s"],
    hints: ["ESTAB 很多时关注业务连接", "TIME-WAIT 很多通常和短连接有关", "SYN-SENT 多可能是外部不可达"],
  });
}

export function buildDnsConfigGuide() {
  return guideResult({
    title: "DNS 配置检查已生成",
    message: "将查看 resolver、hosts 和系统解析配置。",
    commands: ["cat /etc/resolv.conf", "cat /etc/hosts", "resolvectl status"],
    hints: ["优先看 nameserver", "hosts 会覆盖 DNS", "systemd-resolved 环境看 resolvectl"],
  });
}

export function buildArpCacheGuide() {
  return guideResult({
    title: "邻居缓存检查已生成",
    message: "将查看 ARP/neighbor 缓存。",
    commands: ["ip neigh"],
    hints: ["FAILED 表示邻居不可达", "MAC 地址异常时检查同网段冲突", "缓存为空不一定异常"],
  });
}

export function buildSystemOverviewGuide() {
  return guideResult({
    status: "good",
    title: "系统概览检查已生成",
    message: "将查看系统基本身份和运行时间。",
    commands: ["hostnamectl", "cat /etc/os-release", "uptime"],
    hints: ["确认主机名是否符合资产记录", "发行版决定包管理命令", "uptime 过短可能刚重启过"],
  });
}

export function buildKernelVersionGuide() {
  return guideResult({
    title: "内核版本检查已生成",
    message: "将查看内核、架构和启动参数。",
    commands: ["uname -a", "cat /proc/cmdline"],
    hints: ["内核版本影响驱动和安全补丁", "架构决定软件包类型", "启动参数异常可能影响网络或存储"],
  });
}

export function buildHardwareInfoGuide() {
  return guideResult({
    title: "硬件信息检查已生成",
    message: "将查看 CPU、内存和块设备概况。",
    commands: ["lscpu", "free -h", "lsblk -f"],
    hints: ["确认 CPU 核数", "确认内存容量", "确认磁盘挂载关系"],
  });
}

export function buildProcessListGuide() {
  return guideResult({
    title: "进程列表检查已生成",
    message: "将列出资源占用最高的进程。",
    commands: ["ps -eo pid,ppid,cmd,%cpu,%mem --sort=-%cpu | head -n 15"],
    hints: ["先确认高占用进程是否为业务进程", "不要直接 kill", "需要结束进程时走高危操作确认"],
  });
}

export function buildProcessTreeGuide() {
  return guideResult({
    title: "进程树检查已生成",
    message: "将按父子关系查看进程。",
    commands: ["pstree -ap | head -n 60", "ps -ef --forest | head -n 40"],
    hints: ["父进程能解释谁拉起了子进程", "重复进程过多时关注启动脚本", "缺少 pstree 时看 ps --forest"],
  });
}

export function buildPidDetailGuide({ pid }) {
  const { value, error } = validatePid(pid);
  if (error) return error;
  return guideResult({
    title: "PID 详情检查已生成",
    message: `将查看 PID ${value} 的状态和打开文件。`,
    commands: [`ps -p ${value} -o pid,ppid,cmd,%cpu,%mem`, `ls -l /proc/${value}/fd | head`],
    hints: ["确认进程命令行", "打开文件多时关注日志和网络连接", "无法访问 /proc 可能是权限不足"],
  });
}

export function buildProcessSearchGuide({ keyword }) {
  const { value, error } = validateKeyword(keyword, "nginx");
  if (error) return error;
  return guideResult({
    title: "进程查找已生成",
    message: `将查找包含 ${value} 的进程。`,
    commands: [`ps -ef | grep -i ${value}`],
    hints: ["grep 自身可能也会出现", "确认 PID 后再查详情", "结束进程必须走高危操作"],
  });
}

export function buildDirectorySizeGuide({ path }) {
  const { value, error } = validatePath(path, "目录路径");
  if (error) return error;
  return guideResult({
    title: "目录大小检查已生成",
    message: `将查看 ${value} 的空间占用。`,
    commands: [`du -sh ${value}`, `du -h --max-depth=1 ${value} | sort -h | tail -n 10`],
    hints: ["先看总量，再看一级目录", "日志目录过大时先确认保留策略", "删除路径必须走高危操作"],
  });
}

export function buildInodeGuide({ mount }) {
  const cleanMount = (mount || "").trim() || "/";
  if (!MOUNT_PATTERN.test(cleanMount)) {
    return invalidGuide("挂载点需要检查", "挂载点必须以 / 开头，不要带空格或命令符号。", [
      "例子：/",
      "例子：/var",
    ]);
  }
  return guideResult({
    title: "inode 检查已生成",
    message: `将查看 ${cleanMount} 的 inode 使用率。`,
    commands: [`df -ih ${cleanMount}`],
    hints: ["IUse% 很高说明小文件过多", "空间未满但不能写入时常见 inode 耗尽", "优先查缓存和小日志文件"],
  });
}

export function buildMountCheckGuide() {
  return guideResult({
    title: "挂载情况检查已生成",
    message: "将查看文件系统、挂载点和块设备。",
    commands: ["findmnt", "lsblk -f"],
    hints: ["确认关键目录挂载正确", "只读挂载会导致写入失败", "磁盘 UUID 错误会影响开机挂载"],
  });
}

export function buildFilePermissionGuide({ path }) {
  const { value, error } = validatePath(path, "文件路径");
  if (error) return error;
  return guideResult({
    title: "文件权限检查已生成",
    message: `将查看 ${value} 的权限和 ACL。`,
    commands: [`ls -ld ${value}`, `getfacl -p ${value}`],
    hints: ["先看属主和权限位", "ACL 可能覆盖普通权限", "改权限属于变更操作，先确认影响"],
  });
}

export function buildTailFileGuide({ path }) {
  const { value, error } = validatePath(path, "文件路径");
  if (error) return error;
  return guideResult({
    title: "文件尾部检查已生成",
    message: `将查看 ${value} 的最后内容。`,
    commands: [`tail -n 80 ${value}`],
    hints: ["适合看日志最新错误", "权限不足时需要更高权限", "不要在这里清空日志"],
  });
}

export function buildServiceListGuide() {
  return guideResult({
    title: "服务列表检查已生成",
    message: "将查看失败服务和正在运行的服务。",
    commands: ["systemctl --failed", "systemctl list-units --type=service --state=running"],
    hints: ["failed 服务优先处理", "running 不代表业务完全正常", "重启服务走高危操作"],
  });
}

export function buildBootServicesGuide() {
  return guideResult({
    title: "开机自启检查已生成",
    message: "将查看启用服务和 timer。",
    commands: ["systemctl list-unit-files --type=service --state=enabled", "systemctl list-timers --all"],
    hints: ["enabled 表示开机启用", "timer 可能替代 cron", "禁用服务属于变更操作"],
  });
}

export function buildUserListGuide() {
  return guideResult({
    title: "用户列表检查已生成",
    message: "将查看本地用户和登录会话。",
    commands: ["cut -d: -f1,3,7 /etc/passwd", "who", "last -n 10"],
    hints: ["UID 0 是 root 权限", "异常 shell 需要关注", "近期登录来源可辅助审计"],
  });
}

export function buildUserGroupsGuide({ user }) {
  const { value, error } = validateUser(user, "operator");
  if (error) return error;
  return guideResult({
    title: "用户组检查已生成",
    message: `将查看 ${value} 的用户组。`,
    commands: [`id ${value}`, `groups ${value}`],
    hints: ["sudo/docker 等组通常有较高权限", "组权限异常需要核对授权", "改组属于变更操作"],
  });
}

export function buildSudoersGuide() {
  return guideResult({
    title: "sudo 权限检查已生成",
    message: "将只读查看 sudo 配置入口。",
    commands: ["ls -l /etc/sudoers /etc/sudoers.d", "grep -R \"^[^#].*ALL\" /etc/sudoers /etc/sudoers.d 2>/dev/null"],
    hints: ["关注 NOPASSWD", "sudoers.d 下可能有额外授权", "不要直接编辑 sudoers"],
  });
}

export function buildSshConfigGuide() {
  return guideResult({
    title: "SSH 配置检查已生成",
    message: "将查看 SSH 登录相关配置。",
    commands: ["grep -E \"^(PermitRootLogin|PasswordAuthentication|PubkeyAuthentication|Port)\" /etc/ssh/sshd_config"],
    hints: ["PermitRootLogin yes 风险较高", "PasswordAuthentication 决定密码登录", "改配置后需要评估重启 sshd 风险"],
  });
}

export function buildPackageListGuide({ keyword }) {
  const { value, error } = validateKeyword(keyword);
  if (error) return error;
  const filter = value ? ` | grep -i ${value}` : "";
  return guideResult({
    title: "软件包列表检查已生成",
    message: value ? `将筛选包含 ${value} 的软件包。` : "将查看已安装软件包列表。",
    commands: [`dpkg -l${filter}`, `rpm -qa${filter}`],
    hints: ["Debian/Ubuntu 看 dpkg", "RHEL/CentOS 看 rpm", "安装和卸载属于变更操作"],
  });
}

export function buildPackageSourcesGuide() {
  return guideResult({
    title: "软件源检查已生成",
    message: "将查看 apt/yum 软件源配置。",
    commands: ["ls -l /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null", "yum repolist all 2>/dev/null"],
    hints: ["软件源不可达会导致安装失败", "内网环境通常使用私有源", "修改源属于变更操作"],
  });
}

export function buildEnvVarsGuide() {
  return guideResult({
    title: "环境变量检查已生成",
    message: "将查看系统环境变量和 PATH。",
    commands: ["printenv | sort", "echo $PATH"],
    hints: ["PATH 顺序会影响命令命中", "敏感变量不要外传", "服务进程环境可能和登录 shell 不同"],
  });
}

export function buildRebootGuide() {
  return guideResult({
    status: "warn",
    title: "重启系统待确认",
    message: "这是高危操作，确认后才会写入终端。",
    commands: ["sudo reboot"],
    hints: ["会中断当前设备上的业务", "确认无人正在操作", "确认已保存日志和配置"],
    danger: true,
  });
}

export function buildShutdownGuide() {
  return guideResult({
    status: "warn",
    title: "关闭系统待确认",
    message: "这是高危操作，确认后才会写入终端。",
    commands: ["sudo shutdown -h now"],
    hints: ["会让设备离线", "小盒子可能需要人工重新上电", "确认远程链路不会丢失"],
    danger: true,
  });
}

export function buildRestartServiceDangerGuide({ service }) {
  const { value, error } = validateService(service);
  if (error) return error;
  return guideResult({
    status: "warn",
    title: "重启服务待确认",
    message: `${value} 将被重启，确认后才会写入终端。`,
    commands: [`sudo systemctl restart ${value}`],
    hints: ["可能导致业务短暂中断", "先查看服务状态和日志", "确认有回滚方案"],
    danger: true,
  });
}

export function buildStopServiceGuide({ service }) {
  const { value, error } = validateService(service);
  if (error) return error;
  return guideResult({
    status: "warn",
    title: "停止服务待确认",
    message: `${value} 将被停止，确认后才会写入终端。`,
    commands: [`sudo systemctl stop ${value}`],
    hints: ["停止后业务可能不可用", "确认依赖关系", "需要恢复时手动 start"],
    danger: true,
  });
}

export function buildKillProcessGuide({ pid }) {
  const { value, error } = validatePid(pid);
  if (error) return error;
  return guideResult({
    status: "warn",
    title: "结束进程待确认",
    message: `PID ${value} 将被强制结束，确认后才会写入终端。`,
    commands: [`sudo kill -9 ${value}`],
    hints: ["可能导致数据未写完", "先确认进程详情", "优先用服务管理命令停止服务"],
    danger: true,
  });
}

export function buildDeletePathGuide({ path }) {
  const { value, error } = validateDestructivePath(path);
  if (error) return error;
  return guideResult({
    status: "warn",
    title: "删除路径待确认",
    message: `${value} 将被递归删除，确认后才会写入终端。`,
    commands: [`sudo rm -rf ${value}`],
    hints: ["删除不可恢复", "先确认路径没有写错", "建议先执行目录大小和权限检查"],
    danger: true,
  });
}
