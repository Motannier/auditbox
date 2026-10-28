# AuditBox

**面向 Linux 服务器和网络设备的本地智能运维与审计工作台。**

[![License: GPL-3.0-only](https://img.shields.io/badge/License-GPL--3.0--only-blue.svg)](LICENSE)
[![Python 3.10 / 3.11](https://img.shields.io/badge/Python-3.10%20%7C%203.11-3776AB.svg)](CONTRIBUTING.md)
[![Hugging Face Models](https://img.shields.io/badge/Hugging%20Face-Models-FFD21E.svg)](models/README.md)

AuditBox 将浏览器终端、命令辅助、AI 助手、执行确认和关联审计放在同一个工作台。运维人员可以连接设备、查找命令、理解输出，并在执行前检查变更，在执行后追溯操作依据。

人工输入与 Agent 终端操作复用已有连接，经过相同的命令控制与审计链路。问答和命令审查支持本地模型运行，业务数据与审计记录保存在自建环境中。

[主要能力](#主要能力) · [QuickStart](#quickstart) · [模型](#模型与运行方式) · [系统架构](#系统架构) · [部署](deploy/server/README.md) · [文档](#文档导航) · [贡献](CONTRIBUTING.md)

## 主要能力

- **浏览器终端**：多标签连接 SSH、Telnet 和宿主机可用串口，保留目标 CLI 的 Tab 补全、历史导航、行编辑与复制粘贴。
- **命令辅助**：结合设备平台提供前缀补全、下一步操作推荐、速查卡片和参数化命令生成。
- **执行确认**：在命令提交时检查确定性规则，对需要确认的操作展示依据，对硬阻断项停止下发。
- **本地 AI 助手**：支持标准与增强问答、附件摘录、可选知识库，以及按配置启用的终端、MCP、Skill 和本地工具。
- **旁路风险审查**：结合近期命令上下文生成风险建议，关联终端中的操作并保存审查结果。
- **关联审计**：记录登录身份、命令来源、确认决策和执行状态，关联 Agent 对话与审批过程，支持查询与导出。

## 项目用途

| 场景 | 使用方式 |
| --- | --- |
| Linux 日常排障 | 查看资源、服务、进程和网络状态，结合命令速查与助手整理检查思路 |
| 网络设备巡检与变更 | 按设备平台查看命令与操作卡片，在提交配置命令前核对风险和作用范围 |
| AI 辅助运维 | 让助手分析问题，开启执行后逐次审批终端写入，并跟踪工具的实际执行结果 |
| 操作复盘 | 按设备、会话和执行记录回溯操作，查看命令、审批者、来源与结果摘要 |

## QuickStart

需要 **Bash、uv、Python 3.11，以及 Node.js 20.19+ 或 22.12+**。在终端执行：

```bash
git clone https://github.com/Motannier/auditbox-opensource.git
cd auditbox-opensource
./scripts/quickstart.sh
```

脚本按锁文件安装依赖、构建网页、初始化配置，并在当前终端启动 Proxy 和 Gateway。浏览器访问 **http://127.0.0.1:18080**，按 **Ctrl+C** 停止本次启动的服务。

> [!NOTE]
> QuickStart 提供终端、命令辅助、执行确认与审计，无需 GPU 或模型。AI 问答、Agent 和模型旁路审查按下方的[模型说明](#模型与运行方式)与[部署文档](deploy/server/README.md)单独配置。

### 首次登录与连接

1. 在 `box/.env` 中查看 `AUDITBOX_ADMIN_PASSWORD` 和 `AUDITBOX_SUPER_ADMIN_PASSWORD`，分别对应 `opsadmin` 与 `superadmin`。
2. 使用 `opsadmin` 登录，新建连接，填写可达目标的 IPv4 地址、SSH 端口和登录凭据。
3. 打开终端，执行一条只读命令，查看命令辅助与操作记录。`superadmin` 可查看和导出审计。

首次运行生成独立的随机密码和登录签名密钥，保存在权限为 `0600` 的 `box/.env`。脚本保留已有配置，不打印密码；工作数据与日志保存在 `box/data/`。

| 后续操作 | 命令 |
| --- | --- |
| 使用现有环境启动 | `./scripts/quickstart.sh --start-only` |
| 仅安装依赖、构建与初始化 | `./scripts/quickstart.sh --build-only` |
| 使用 Python 3.10 构建 | `PYTHON_VERSION=3.10 ./scripts/quickstart.sh` |

服务默认仅监听回环地址。在远程主机运行时，可通过 SSH 本地端口转发访问：

```bash
ssh -L 18080:127.0.0.1:18080 user@server
```

## 模型与运行方式

默认 AI 部署通过本机 llama.cpp CUDA 容器运行问答模型和 Linux 命令审查模型。Agent 调用问答端点，Proxy 的 Linux 旁路审查经 Inference 调用审查端点。

| 用途 | 模型来源 | 启用方式 |
| --- | --- | --- |
| 标准／增强问答 | [MiniCPM5 2B GGUF](https://huggingface.co/openbmb/MiniCPM5-2B-GGUF) | 默认 AI 配置 |
| Linux 命令风险审查 | [Command Guard 0.8B Q8_0](https://huggingface.co/Motanni/auditbox-command-guard-0.8b-gguf) | 默认 AI 配置 |
| H3C Comware 命令风险审查 | [H3C Risk 0.8B Q8_0](https://huggingface.co/Motanni/auditbox-h3c-risk-0.8b-gguf) | 按需配置模型端点与路由 |

两个 0.8B 模型是基于 Qwen3.5-0.8B 的领域微调制品，采用 GPL-3.0-only，并保留基础模型的 Apache 2.0 许可与归属说明。MiniCPM5 使用上游官方制品。

- **下载与校验**：[模型说明](models/README.md)提供固定版本下载地址；[模型清单](models/manifest.json)记录文件名、revision、大小和 SHA-256。
- **用途与边界**：见 [Linux 模型卡](models/model-cards/command-guard/README.md)和 [H3C 模型卡](models/model-cards/h3c-risk/README.md)。
- **启用 AI**：准备模型文件，再按[服务器部署](deploy/server/README.md)或[本地开发](#本地开发)启动模型、Inference 和 Agent。QuickStart 初次配置关闭历史旁路审查，启用时将 `box/.env` 中的 `AUDITBOX_HISTORY_REVIEW_ENABLED` 改为 `true` 并重启 Proxy。

应用也支持 Ollama 和其他 OpenAI 兼容端点。模型地址、服务别名、上下文与输入预算需要相互匹配；外部模型、MCP 和 Skill 的访问范围由各自配置决定。

## 系统架构

浏览器通过 Gateway 进入工作台。Proxy 连接目标设备并处理命令控制；Agent 提供问答与工具运行；Inference 处理设备模型路由与经过它的推理任务。

![AuditBox 架构](docs/images/architecture.svg)

| 组件 | 职责 |
| --- | --- |
| Gateway | 登录鉴权、网页与 API、WebSocket 会话、助手转发和内部终端工具桥 |
| Proxy | SSH／Telnet／串口连接、命令恢复、确认与阻断、执行审计 |
| Agent | 模型对话、上下文管理、工具审批、运行持久化与扩展能力 |
| Inference | 按设备平台路由审查请求，对经过本服务的推理任务排队与调度 |

完整部署由 systemd 管理应用进程，以 Docker Compose 运行模型。Gateway 提供浏览器入口，其余应用与模型端点默认保持回环监听。Agent 没有独立登录鉴权，浏览器请求经 Gateway 认证后转发。

业务与审计记录保存在 SQLite；Agent 使用独立的会话、运行与审批存储。详细端口、启动依赖和数据路径见[系统架构](docs/ARCHITECTURE.md)。

## 关键设计

### 在命令提交边界检查，保留原生 CLI 交互

目标 CLI 继续处理普通输入、Tab、历史导航和行编辑。操作员按 Enter 时，Proxy 暂扣提交动作，恢复当前待执行命令，再执行已配置的规则。平台适配器分别处理提示符、清行和分页语法。

### 用规则控制下发，用上下文补充风险依据

命令闸门根据确定性规则和确认状态决定命令如何下发：

| 决策 | 行为 |
| --- | --- |
| 允许 | 按当前规则继续下发 |
| 确认 | 保存待执行命令与一次性确认状态，等待批准或取消 |
| 阻断 | 停止下发并显示原因，普通确认不能解除硬阻断 |

确认只对当前待执行命令生效；命令变化后重新检查。规则覆盖取决于平台与资料，未命中项可能按未知项放行。

旁路审查在后台分析近期命令及上下文，提供风险建议并留存审计依据。Linux 可调用审查模型，其他平台使用已配置的规则或模型能力。

### Agent 与人工操作复用终端和控制链路

Agent 执行初始关闭，助手可先用于问答和排查建议。启用执行后，终端写入与 Ctrl+C 需要逐次工具审批；命令命中危险规则时，继续经过命令闸门确认。

```text
操作员提出任务
  → Agent 生成工具调用，保存待审批参数
  → 操作员批准或拒绝
  → Gateway 定位当前活动终端
  → Proxy 检查命令规则与确认状态
  → 目标设备执行，结果进入对话与审计
```

Agent 终端工具通过 Gateway 的本机接口复用已建立的连接，无需持有目标 SSH 密码。人工输入可以中断正在等待结果的 Agent 操作，只读工具遵循各自的审批策略。详见[终端工具设计](agent/docs/TERMINAL_TOOL_DESIGN.md)。

### 保留上下文与审批，支持中断后恢复

模型输入按稳定提示、已有历史和当前上下文组织。接近输入预算时，Agent 压缩较早历史，保留任务约束、执行证据、未完成步骤与审批状态。

工具暂停时保存名称、参数与运行检查点。继续请求提交批准或拒绝决定，服务端使用保存的参数恢复执行。上下文、附件摘录与可选知识库的处理方式见 [Agent 说明](agent/README.md)。

### 审计关联任务来源与实际执行事实

审计按登录、输入、执行和行为组织记录，关联设备、会话及 `human`、`guide`、`agent` 来源。Agent 操作进一步关联原问题、对话、运行与审批者，保留确认、取消、阻断和下发状态。

终端结果保存经过脱敏且有长度限制的摘要；无法取得可靠退出状态时记为未知，包括仅观察到断连或输出静默的情况。记录关系、增量读取和留存边界见[关联审计设计](docs/linked-audit.md)。

## 设备适配与扩展

连接协议负责传输，设备平台决定命令语法、推荐与规则。运行时按操作员选择的 `device_type` 和 `device_profile` 加载平台能力。

| 设备大类 | 平台标识 | 适配对象 |
| --- | --- | --- |
| `server` | `linux` | Linux 服务器 |
| `switch` | `h3c_comware` | H3C Comware |
| `switch` | `huawei_vrp` | 华为 VRP |
| `switch` | `cisco_ios` | 思科 IOS／IOS XE |
| `switch` | `zte_zxr10` | 中兴 ZXR10 |

命令辅助由前缀补全、行为图推荐、速查卡片与参数化模板组成，保留语法、型号、视图和来源信息；生成或推荐的命令统一经过终端控制链路。

设备能力包采用纯数据格式，由超级管理员在无活动终端时导入。应用检查大小、结构、引用与 SHA-256，再通过临时目录和原子替换保存。适配范围取决于资料覆盖、目标型号与软件版本；当前默认模型路由仅启用 Linux 审查，H3C 模型按需配置。

## 构建与部署

完整 AI 部署面向 **Linux x86_64** 主机，需要 systemd、Python 3.10 / 3.11、uv、Docker Compose、NVIDIA GPU 及可供容器使用的驱动运行时。服务器使用宿主机现有网络与时间服务连接目标设备。

单独构建网页可在仓库根目录执行：

```bash
make build
```

该命令按锁文件安装前端依赖，构建网页，并执行生产资源与 Chromium 80 兼容检查。

常驻安装使用[服务器部署文档](deploy/server/README.md)中的脚本，传入服务器 IP 和模型目录。安装器创建专属账号、两组独立 Python 环境、systemd 服务与当前版本链接，为新环境生成随机凭据。

账号读取、模型导入、健康检查、升级和回退统一见部署文档。Agent、Proxy、Inference 与模型端点保持回环监听，外部访问经 Gateway 进入工作台。

## 本地开发

先按 QuickStart 准备构建与配置；仅准备环境可使用 `--build-only`。开发各模块时，在仓库根目录执行：

```bash
make setup
```

该命令安装后端、Agent 开发工具与前端依赖，不启动服务。核心工作台使用 `./scripts/quickstart.sh --start-only`；单独调试 Proxy 或 Gateway 可用 `make run-proxy`、`make run-gateway`，先停止组合启动以免端口冲突。

AI 与前端开发按需启动以下服务，并从 `agent/.env.example`、`web/.env.example` 创建所需配置，保留已有 `.env`：

| 工作目录 | 命令 |
| --- | --- |
| `box/` | `uv run --frozen --python 3.11 auditbox-inference` |
| `agent/` | `uv run --frozen --python 3.11 auditbox-agent` |
| `web/` | `npm run dev` |

模型进程按[模型说明](models/README.md)另行启动，前端开发示例连接 `127.0.0.1:18080`。后端与 Agent 分别使用各自的 Python 环境和 `uv.lock`，前端使用 `web/package-lock.json`。

依赖变更、验证入口与提交要求见[贡献指南](CONTRIBUTING.md)。

## 代码结构

```text
auditbox-opensource/
├── box/                 # Gateway、协议代理、命令规则、推荐与审计
├── agent/               # 模型对话、上下文、审批与扩展能力
├── web/                 # React / xterm 工作台
├── models/              # 模型来源、固定版本、模型卡与文件校验
├── deploy/server/       # systemd 安装器与模型运行配置
├── scripts/             # 构建、配置与核心服务启动入口
├── tools/               # 模型校验与设备数据构建工具
└── docs/                # 架构与关联审计设计
```

代码阅读入口：[命令闸门](box/auditbox/relay/command_gate.py)、[Gateway](box/auditbox/gateway/app.py)、[Agent 运行](agent/src/auditbox_agent/runtime.py)、[上下文管理](agent/src/auditbox_agent/context_manager.py)。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [系统架构](docs/ARCHITECTURE.md) | 组件、端口、执行链路、启动依赖与数据路径 |
| [服务器部署](deploy/server/README.md) | 环境配置、模型导入、安装、健康检查与回退 |
| [后端说明](box/README.md) | Python 服务入口与模块职责 |
| [Agent 说明](agent/README.md) | 问答、执行开关、上下文、文件区、MCP 与 Skill |
| [终端工具设计](agent/docs/TERMINAL_TOOL_DESIGN.md) | 共享终端、审批、超时与人工介入 |
| [关联审计](docs/linked-audit.md) | 来源关联、记录关系与历史日志转换 |
| [模型说明](models/README.md) | 模型下载、固定版本与校验方法 |
| [安全说明](SECURITY.md) | 访问边界、凭据管理与问题报告 |
| [第三方来源与许可](THIRD_PARTY_NOTICES.md) | 依赖、Skill、模型、设备资料与素材 |
| [变更记录](CHANGELOG.md) | 项目版本与功能变化 |

## 贡献

欢迎提交问题、功能建议与代码改进。报告问题时提供源码版本、设备平台、复现步骤和最少的脱敏日志；涉及模型时附上模型文件与运行时信息。

开发环境、变更验证和第三方材料要求见[贡献指南](CONTRIBUTING.md)。

## 许可证

除另有声明的第三方材料外，本项目代码采用 **GNU GPL 第 3 版，仅限该版本（`GPL-3.0-only`）**，完整条款见 [LICENSE](LICENSE)。本软件不提供任何担保，具体以许可证为准。

第三方依赖、Skill、模型与素材适用各自的许可，详见[第三方来源与许可记录](THIRD_PARTY_NOTICES.md)。
