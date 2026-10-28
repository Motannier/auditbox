# AuditBox Server

**面向 Linux 服务器和网络设备的本地智能运维工作台。**

AuditBox 在同一工作台提供浏览器终端、命令辅助、执行确认、AI 助手和关联审计。运维人员可以连接设备、查找命令、分析问题，并审批 Agent 的设备操作。人工输入和 Agent 终端操作复用同一条执行链路，记录命令决策、操作来源与执行结果。

服务器版部署在 Linux x86_64 主机上，通过宿主机现有网络连接目标设备。问答和 Linux 命令审查模型在本机 NVIDIA GPU 上运行，浏览器通过统一的 Gateway 访问终端、助手和审计功能。

[QuickStart](#quickstart) · [部署安装](deploy/server/README.md) · [本地开发](#本地开发) · [系统架构](docs/ARCHITECTURE.md) · [贡献](CONTRIBUTING.md) · [许可证](LICENSE)

## QuickStart

以下步骤启动浏览器终端、命令辅助、执行确认和审计。需要 Bash、uv、Python 3.11，
以及 Node.js 20.19+ 或 22.12+。在终端执行：

```bash
git clone https://github.com/Motannier/auditbox-opensource.git
cd auditbox-opensource
./scripts/quickstart.sh
```

脚本按锁文件安装依赖、构建网页、初始化配置，并在同一个终端启动 Proxy 和 Gateway。
浏览器访问 **http://127.0.0.1:18080**；在脚本所在终端按 Ctrl+C 停止这两个服务。

首次运行会生成 `opsadmin`、`superadmin` 的随机密码和独立的登录签名密钥，写入权限为
`0600` 的 `box/.env`。在该文件中查看 `AUDITBOX_ADMIN_PASSWORD` 和
`AUDITBOX_SUPER_ADMIN_PASSWORD`；脚本不会打印密码，已有配置保持原样。

使用 `opsadmin` 登录后，新建连接并填写可达目标的 IPv4 地址、SSH 端口、账号和密码，
即可使用终端及命令辅助。`superadmin` 可查看和导出审计。工作数据与日志保存在 `box/data/`。

后续启动使用 `./scripts/quickstart.sh --start-only`；仅安装依赖、构建和生成配置时使用
`--build-only`。默认选择 Python 3.11，需要 3.10 时执行
`PYTHON_VERSION=3.10 ./scripts/quickstart.sh`。`--start-only` 直接使用已安装的后端环境。

服务默认仅监听回环地址。在远程服务器运行时，可通过 SSH 本地端口转发访问：

```bash
ssh -L 18080:127.0.0.1:18080 user@server
```

### 启用 AI 或常驻部署

QuickStart 启动 Proxy 和 Gateway，无需模型、GPU、Docker 或系统服务。
首次初始化关闭历史旁路审查，确定性命令规则、人工确认与审计保持启用；问答与 Agent 显示未就绪。

启用完整 AI 功能需要两个本地模型及 Agent、Inference 服务。按 [模型说明](models/README.md)
准备文件，再按 [本地开发](#本地开发) 或 [服务器部署](deploy/server/README.md) 配置并启动。
要启用历史旁路审查，将 `box/.env` 的 `AUDITBOX_HISTORY_REVIEW_ENABLED` 改为 `true`，
并重启 Proxy。模型的固定版本下载地址及校验方式见 [模型说明](models/README.md)。

完整开发环境使用 `make setup`；单独构建网页使用 `make build`。
常驻服务器的账号、模型、systemd 安装和升级回退见
[部署文档](deploy/server/README.md)。

## 项目用途

AuditBox 面向日常排障、网络设备操作和需要保留执行依据的运维场景。

| 场景 | 使用方式 |
| --- | --- |
| Linux 日常排障 | 在终端查看资源、服务、进程和网络状态，配合命令速查及本地助手解释输出、整理下一步检查思路 |
| 交换机巡检与配置 | 选择设备平台，查看对应的命令补全、操作卡片与风险提示，按目标设备的 CLI 习惯执行操作 |
| 需要确认的变更 | 提交命令时展示规则命中的原因，对需要确认的操作明确批准或取消，对硬阻断项停止下发 |
| AI 辅助操作 | 先通过问答理解问题；开启 Agent 执行后，检查目标、工具与命令，对终端写入逐次审批 |
| 操作复盘 | 按登录、设备、终端会话和执行记录查找操作，关联 Agent 原问题、运行轮次、审批及结果摘要 |

日常排障时，操作员连接 Linux 服务器，用速查卡片检查资源，通过助手分析问题，再选择手动执行命令或批准 Agent 的终端调用。两种终端操作均经过终端代理和命令闸门，审计记录保留操作来源。

## 主要能力

| 能力 | 说明 |
| --- | --- |
| 浏览器终端 | 多标签会话，支持 SSH、Telnet 和宿主机可用串口，保留目标 CLI 的 Tab 补全、历史导航、行编辑与复制粘贴 |
| 设备与连接管理 | 保存设备信息和常用连接，记录最近连接，按明确选择的设备类型和平台加载对应能力 |
| 命令辅助 | 前缀补全、基于会话状态的下一步推荐、按用途组织的速查卡片和参数化命令生成 |
| 执行控制 | 在命令提交边界检查确定性规则，支持一次性确认、取消和硬阻断 |
| 旁路风险审查 | 后台分析近期命令上下文，将风险原因和关联命令显示到工作台并写入审计 |
| 本地 AI 助手 | 标准与增强问答、会话附件摘录、可配置知识库，以及开启执行后的终端、MCP、Skill 和本地工具能力 |
| 关联审计 | 关联登录、输入、执行和行为记录，提供结果摘要、审计导出及基于游标的增量读取 |

## 系统架构

默认服务器部署由四个应用进程、两个模型容器和本地持久化数据组成。

![AuditBox Server 架构](docs/images/architecture.svg)

Agent 的终端工具经 Gateway 复用 Proxy 已有连接；Proxy 通过 SSH、Telnet 或串口访问目标设备。

| 组件 | 主要职责 | 默认服务器地址 |
| --- | --- | --- |
| Gateway | 登录鉴权、网页与 API、WebSocket 会话、助手转发和内部终端工具桥 | `<server-ip>:18080` 与 `127.0.0.1:18080` |
| Proxy | 目标协议连接、终端输入恢复、命令确认与阻断、执行审计 | `127.0.0.1:19000`；同进程事件通道 `19001` |
| Agent | 模型对话、上下文管理、运行持久化、工具审批和扩展能力 | `127.0.0.1:18765` |
| Inference | 按设备平台路由模型请求，调度经过本服务的推理任务 | `127.0.0.1:18091` |
| 问答模型 | 提供标准与增强问答所用的 OpenAI 兼容接口 | `127.0.0.1:18081` |
| Linux 审查模型 | 分析 Linux 操作上下文 | `127.0.0.1:18082` |

Gateway 统一处理浏览器访问与登录鉴权，并向 Agent 传递已验证的操作员上下文。
Agent 自身没有独立登录鉴权；Agent、Proxy、Inference 和模型端点保持回环监听。
Proxy 的辅助事件通道与代理运行在同一进程中。

应用由 systemd 管理，后端和 Agent 使用各自的 Python 环境；两个模型使用 Docker Compose 和 llama.cpp CUDA 运行。详细端口、启动依赖和数据路径见 [架构说明](docs/ARCHITECTURE.md)。

## 关键设计

### 1. 在提交边界控制命令，保留原生终端交互

目标 CLI 处理普通输入、Tab、历史导航和行编辑。操作员按 Enter 时，Proxy 暂扣提交动作，恢复待执行命令并检查规则；远端编辑状态通过终端回显和重绘同步。

操作员可以沿用设备的原生交互，并在命令下发前查看确认原因。需要核对远端编辑状态时，Proxy 在恢复完整待执行命令后继续提交。设备适配器分别处理各平台的提示符、清行和分页语法。

### 2. 确定性规则与旁路审查各司其职

命令是否下发由命令闸门和确认状态决定。

| 决策 | 行为 |
| --- | --- |
| 允许 | 按当前规则继续下发 |
| 确认 | 保存待执行命令和一次性确认状态，等待操作员批准或取消 |
| 阻断 | 停止下发并显示原因，普通确认不能解除硬阻断 |

确认只对应当前待执行命令。命令变化后重新检查，需确认的命令重新申请批准。未命中风险规则的命令可能按未知项放行，其风险仍需评估。

旁路审查在后台分析近期命令及其上下文，生成风险提示和审计记录。Linux 可调用审查模型，其他平台使用已配置的规则或模型能力。旁路结果只用于提示和留存，命令下发仍由命令闸门及确认状态决定。

### 3. 问答与执行明确分开，Agent 复用已有连接

Agent 执行初始关闭，助手仅回答问题、解释命令和提供排查建议。开启执行后，主 Agent 才挂载终端、MCP、Skill 或本地执行工具。标准与增强模式使用同一个问答模型，通过思考参数区分；增强模式通常需要更长响应时间。

终端写入和 Ctrl+C 都需要逐次工具审批。系统保存暂停时的工具名称、参数和运行状态；继续请求只提交批准或拒绝决定，工具参数使用服务端保存值。

终端操作沿以下链路执行：

```text
操作员提出任务
  → Agent 生成工具调用
  → 操作员检查并批准
  → Gateway 定位当前活动终端
  → Proxy 执行命令规则与必要的二次确认
  → 目标设备执行并返回输出
  → 结果进入对话与关联审计
```

内置终端工具通过 Gateway 的本机内部接口读写已有会话，复用操作员建立的连接，无需取得目标 SSH 密码。人工输入可以中断正在等待结果的 Agent 操作。Agent 工具审批和危险命令确认分别生效，只读工具遵循各自的权限策略。

执行开关保存在 SQLite 中，之后以用户保存的状态为准。详见 [Agent 设计](agent/README.md) 和 [终端工具设计](agent/docs/TERMINAL_TOOL_DESIGN.md)。

### 4. 将上下文、审批和执行事实一起持久化

模型输入按稳定提示、已有历史和当前上下文组织。时间及变化后的目标／文件信息放在后部，相同快照不重复追加，便于复用前缀缓存。

接近输入预算时，Agent 压缩较早历史，保留任务约束、已执行证据、未完成步骤和审批状态；压缩后仍无法满足预算时返回错误。审批恢复使用暂停时保存的输入与检查点，保持原任务和批准内容一致。

服务端可为问答提供相关附件的有限摘录，启用知识库后还可提供检索结果。历史信息可能过时，问答模式不会自动取得完整的实时终端转录。文件检索、上下文预算和恢复规则见 [Agent 说明](agent/README.md)。

### 5. 审计关联操作来源与实际执行

审计采用登录、输入、执行和行为四类记录。登录身份随会话传递；每条命令的输入与执行记录相互关联，并保留设备、目标、终端会话及 `human`、`guide`、`agent` 来源。

Agent 终端操作还关联对话 ID、运行 ID、原问题摘要、操作目的和审批者。确认、取消、阻断、下发及风险复核都关联同一执行记录，可据此追溯命令对应的任务。

业务与审计记录保存在 SQLite，数据更新与递增变更日志在同一事务中提交，增量读取按序列游标推进。Agent 的会话、运行与审批使用独立 SQLite。终端结果保存经过脱敏且有长度限制的摘要；无法取得可靠退出状态时记为未知，包括仅观察到连接断开或输出静默的情况。

具体数据关系、历史格式转换和留存边界见 [关联审计设计](docs/linked-audit.md)。

## 设备适配与扩展

连接协议负责传输，设备平台决定命令语法、推荐与规则。运行时按操作员选择的 `device_type` 和 `device_profile` 加载平台能力。

| 设备大类 | 平台标识 | 适配对象 |
| --- | --- | --- |
| `server` | `linux` | Linux 服务器 |
| `switch` | `h3c_comware` | H3C Comware |
| `switch` | `huawei_vrp` | 华为 VRP |
| `switch` | `cisco_ios` | 思科 IOS／IOS XE |
| `switch` | `zte_zxr10` | 中兴 ZXR10 |

命令辅助包含前缀补全、行为图推荐和速查卡片，分别用于补齐输入、结合会话状态推荐下一步操作，以及按用途查找命令和参数。参考资料保留语法、型号、视图和来源信息；执行模板需单独整理，生成或推荐的命令统一经过终端控制链路。

设备能力包使用纯数据格式，包内容仅作为数据解析。内置平台加载已有规则和参考资料，通用命令包可注册新的平台命令及行为图，平台标识须避开内置平台。

导入仅限超级管理员，并要求当前没有活动终端。应用检查包的大小、结构、引用和 SHA-256，通过临时目录和原子替换保存。哈希用于核对内容完整性，包来源的可信性需另行确认。

设备适配范围取决于资料覆盖、命令规则和目标系统版本。当前服务器只启用 Linux 审查推理模型；使用 H3C 专用模型需另行准备模型、配置端点并启用对应路由。

## 模型与运行方式

| 用途 | 默认模型 | 服务别名 | 上下文配置 |
| --- | --- | --- | --- |
| 标准／增强问答 | `MiniCPM5-2B-Q4_K_M.gguf` | `minicpm5-2b` | 32,768 tokens |
| Linux 命令审查 | `command-guard-q8_0.gguf` | `command-guard` | 10,240 tokens |

默认模型运行在本机。Agent 直接调用问答端点，Proxy 的 Linux 旁路审查通过 Inference 调用审查端点。Inference 的排队、优先级和取消机制仅作用于经过它的请求，两个模型容器共享 GPU。

模型端点、服务别名和输入预算通过环境配置调整，模型文件、容器别名和上下文大小在 Compose 中配置，两处设置应保持一致。应用还支持 Ollama 和其他 OpenAI 兼容端点；外部模型、MCP 与 Skill 的访问范围由各自配置和能力决定。

模型来源、固定 revision、文件大小和 SHA-256 见 [模型说明](models/README.md) 与 [模型清单](models/manifest.json)。
两个自有 0.8B 微调制品分别见 [Linux 命令审查模型卡](models/model-cards/command-guard/README.md) 和
[H3C 命令审查模型卡](models/model-cards/h3c-risk/README.md)。H3C 模型需另行启用。
两个模型已发布到 Hugging Face：[Command Guard](https://huggingface.co/Motanni/auditbox-command-guard-0.8b-gguf) · [H3C Risk](https://huggingface.co/Motanni/auditbox-h3c-risk-0.8b-gguf)。下载默认配置所需的 MiniCPM5 和 Command Guard 后，按模型清单校验并导入。

## 构建与部署

### 环境要求

| 项目 | 要求 |
| --- | --- |
| 应用服务器 | Linux x86_64、systemd、Python 3.10 或 3.11、uv |
| 模型运行 | Docker Compose、NVIDIA GPU 及可供容器使用的驱动运行时 |
| 前端构建 | Node.js 20.19+ 或 22.12+ |
| 网络 | 服务器能够访问目标设备所需的 SSH／Telnet 端口；串口取决于宿主机设备及账号权限 |

应用使用宿主机现有网络与时间服务。服务器配置关闭浏览器网口修改和校时，不安装 USB OTG、热点、RK3588/NPU 或 Windows Guardian 服务。

### 构建前端

在仓库根目录执行：

```bash
make build
```

命令按锁文件安装前端依赖并构建网页，结果写入仓库根目录 `dist/`，同时执行生产资源和 Chromium 80 兼容检查。生产网页与 API 使用同源地址。

### 安装和启动

首次安装按 [服务器部署说明](deploy/server/README.md) 运行安装脚本，传入服务器 IP 和模型目录。
脚本准备 release、uv、模型文件和 Compose 配置，再由安装器创建专属账号、两组 Python 环境、
systemd 单元和当前版本链接；首次安装生成独立密码和签名密钥。已有实例升级按部署文档操作。

启动顺序为 Proxy、Gateway、两个模型、Inference 和 Agent。模型启动器先等待 Linux 审查模型健康，再启动问答模型。浏览器入口为 `http://<server-ip>:18080`，初始账号为 `opsadmin`、`superadmin`，随机密码在服务器的 `/etc/auditbox-server/initial-credentials.json` 中读取。

数据库、会话和文件区保存在 `/var/lib/auditbox-server/`，运行日志在 `/var/log/auditbox-server/`，模型在 `/opt/auditbox-server/models/`。部署、健康检查和回退步骤见 [部署文档](deploy/server/README.md)。

## 本地开发

先完成 QuickStart 的构建与配置；只准备环境可用 `./scripts/quickstart.sh --build-only`。
需要开发各模块时，安装 Make，并在仓库根目录安装完整开发依赖：

```bash
make setup
```

该命令安装两个 Python 环境、Agent 开发工具及前端依赖，不启动服务。
核心服务继续使用 `./scripts/quickstart.sh --start-only`。只需单独调试 Proxy 或 Gateway 时，
可使用 `make run-proxy` 或 `make run-gateway`，先停止组合启动以免端口冲突。

开发 AI 或前端时，按需从 `agent/.env.example`、`web/.env.example` 创建对应 `.env`，
保留已有配置。Agent 的模型地址与参数见 [模块说明](agent/README.md)；
前端开发示例连接 `127.0.0.1:18080`。额外服务按需启动：

| 工作目录 | 命令 |
| --- | --- |
| `box/` | `uv run --frozen --python 3.11 auditbox-inference` |
| `agent/` | `uv run --frozen --python 3.11 auditbox-agent` |
| `web/` | `npm run dev` |

后端、Agent 和前端依赖分别由各模块的 `uv.lock` 与 `web/package-lock.json` 固定。
开发环境与变更要求见 [贡献指南](CONTRIBUTING.md)。

## 代码结构

```text
auditbox-server/
├── box/
│   ├── auditbox/        # Gateway、协议代理、命令规则、推荐、审计与设备能力
│   └── test/            # 后端与部署工具回归
├── agent/
│   ├── src/             # Agent API、模型运行、上下文和能力管理
│   ├── defaults/        # 内置 Skill 及来源说明
│   └── tests/           # 问答、审批、文件区和上下文回归
├── web/
│   ├── src/             # React / xterm 工作台
│   └── tests/           # 前端逻辑、兼容性与浏览器回归
├── deploy/server/       # systemd 安装器与模型运行配置
├── scripts/             # 本地构建、配置与核心服务启动入口
├── tools/               # 模型文件校验与设备数据构建工具
├── models/              # 模型来源、版本和文件校验信息
└── docs/                # 架构与关联审计设计
```

终端控制从 [command_gate.py](box/auditbox/relay/command_gate.py) 开始阅读；Gateway 的接口与工具桥位于 [gateway/app.py](box/auditbox/gateway/app.py)；Agent 的模型和工具运行位于 [runtime.py](agent/src/auditbox_agent/runtime.py)，上下文组装与审批恢复位于 [context_manager.py](agent/src/auditbox_agent/context_manager.py)。

## 贡献

开发环境、依赖变更和第三方材料要求见 [贡献指南](CONTRIBUTING.md)。

报告问题时提供源码版本、复现步骤、相关平台和最少的脱敏日志；涉及终端时注明协议与设备平台，
涉及模型时注明模型文件和运行时。功能建议说明实际运维场景与期望行为。贡献变更时说明问题、
行为变化和实际验证结果。仓库提供对应的 Issue 和 Pull Request 模板。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [系统架构](docs/ARCHITECTURE.md) | 进程、端口、命令与 Agent 链路、启动依赖、持久化路径 |
| [服务器部署](deploy/server/README.md) | 环境配置、模型导入、systemd 安装、健康检查和回退 |
| [后端说明](box/README.md) | Python 服务入口和模块职责 |
| [Agent 说明](agent/README.md) | 问答模式、执行开关、上下文、文件区、MCP 与 Skill |
| [终端工具设计](agent/docs/TERMINAL_TOOL_DESIGN.md) | 共享终端、审批、超时和人工介入 |
| [关联审计](docs/linked-audit.md) | 来源关联、记录关系和历史日志转换 |
| [模型说明](models/README.md) | 模型文件、来源、版本与校验方法 |
| [贡献指南](CONTRIBUTING.md) | 本地开发、变更验证及来源记录 |
| [安全说明](SECURITY.md) | 访问边界、凭据管理与问题报告 |
| [第三方记录](THIRD_PARTY_NOTICES.md) | 依赖、Skill、模型和设备资料的许可信息 |
| [变更记录](CHANGELOG.md) | 项目版本与功能变化 |

## 许可证

除另有声明的第三方材料外，本项目代码采用 GNU General Public License 第 3 版
（`GPL-3.0-only`），完整条款见 [LICENSE](LICENSE)。本软件不提供任何担保，具体以许可证为准。

第三方依赖、Skill、模型与素材适用各自的许可，详见 [第三方来源与许可记录](THIRD_PARTY_NOTICES.md)。
