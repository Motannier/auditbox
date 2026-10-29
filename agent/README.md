# AuditBox Agent

Agent 为工作台提供 AI 问答、对话文件和经审批的工具操作。它以独立进程运行，经 Gateway 接收浏览器请求；模型、会话数据库和工具配置可分别管理。整体用途见 [项目 README](../README.md)。

初次使用先接通问答模型。标准与增强模式默认共用 Qwen3.5 2B Q4_K_M 和 32K 上下文，分别关闭与开启思考；增强模式通常耗时更长，思考内容不显示在聊天正文中。需要助手实际操作时，再开启 Agent 执行并选择可用工具。

Agent API 没有独立登录鉴权，保持 `127.0.0.1:18765` 监听，浏览器请求经 Gateway 认证后转发。当前 Agent 使用固定本地用户保存会话，不按浏览器登录账号隔离对话和文件；操作员账号用于关联终端审计。

[运行](#运行与开发) · [执行开关](#模型问答与执行开关) · [对话文件](#对话文件区) · [工具与扩展](#工具mcpskill-与审批) · [共享终端](#共享终端) · [知识库](#知识库) · [上下文实现](#问答上下文)

## 运行与开发

先按 [QuickStart](../README.md#quickstart) 启动核心工作台，再按 [模型说明](../models/README.md) 准备问答端点。以下命令在仓库的 `agent/` 目录执行，使用 Python 3.11 和独立虚拟环境；也支持 Python 3.10：

```bash
test -e .env || cp .env.example .env
uv sync --frozen --python 3.11
uv run --frozen --python 3.11 auditbox-agent
```

启动前核对 `.env` 中的模型端点和 Gateway 地址。示例配置使用
`AUDITBOX_AGENT_MODEL_BACKEND=openai`、`http://127.0.0.1:18081/v1` 和
`http://127.0.0.1:18080`，Agent 监听 `127.0.0.1:18765`。
模型对话需要可用的模型服务；终端工具还需要已启动的 Gateway、Proxy 和活动终端。

健康检查：

```bash
curl --fail http://127.0.0.1:18765/api/v1/health
```

服务器部署通过安装器和 `auditbox-server-agent.service` 管理，步骤见
[服务器部署说明](../deploy/server/README.md)。Agent 与 Gateway 使用各自的 Python 环境。本地运行按 Ctrl+C 停止 Agent，之后可重复上述 `uv run` 命令启动。

## 模型问答与执行开关

默认提供问题解答、命令解释和排查建议。问答模式不会自动读取实时终端，判断设备现状时需要提供当前输出；历史输出与命令建议不构成执行授权。
助手“插件”设置中的 **Agent 执行** 总开关初始关闭，保存在 SQLite 的
`control:agent-execution` 能力记录中。`AUDITBOX_AGENT_EXECUTION_ENABLED=false`
决定首次初始化的状态；后续以用户保存的设置为准，重启后保留。

关闭时，标准与增强模式只回答问题，不挂载终端、MCP、Skill、文件或知识库工具；服务端仍可提供与问题相关的对话文件摘录，以及已启用知识库的检索结果。
各能力的启用设置会保留，在总开关开启后生效。状态接口显示
`execution_enabled=false`、`interaction_mode=qa` 和能力 `active=false`。
继续待审批运行、执行终端命令和发送 Ctrl+C 的接口均拒绝执行。
开启后，Agent 按启用的能力加载工具；切换前须等待正在生成的任务结束或停止。

## 对话文件区

Markdown、TXT、DOCX 经 Gateway 上传到当前对话的独立文件区，由 Agent 保存原文件并提取规范化文本。
模型上下文包含文件清单（`file_id`、文件名、字符数和标题目录）。问答模式提供与当前问题相关的有限摘录；
开启执行后通过工具按需读取正文。Agent 可通过只读的 `list_conversation_files`、
`get_conversation_file_outline`、`search_conversation_files` 和 `read_conversation_file` 工具
检索和分段读取；访问范围限定为当前 `session_id` 的文件，返回内容不暴露宿主机路径。

只读文件工具无需逐次审批，文件删除由用户在前端显式操作；删除会话时同时清理文件区。
仅在用户提到附件或任务需要文件时读取正文，普通问答、MCP/Skill 查询和终端操作可直接进行。
空文件区无需每轮加入提示词。默认每个对话 10 个文件、单文件 5MB、解析后 100 万字符，读取
工具单次最多返回 4000 字符。相关接口：

```text
GET    /api/v1/conversations/{id}/files
POST   /api/v1/conversations/{id}/files
DELETE /api/v1/conversations/{id}/files/{file_id}
```

## 工具、MCP、Skill 与审批

提供 Agno `CodingTools`、`DockerTools`、`FileGenerationTools` 和 `SQLTools` 四类工具集，默认均关闭。
开启 Agent 执行总开关并启用相应能力后，模型才会看到对应工具。
这四类工具与 MCP 调用均在执行前暂停，等待 Agno HITL 用户确认。
本地开发默认将 CodingTools 的目录范围设为 `data/workspace`，生成文件写入 `data/generated`，SQLTools 连接独立的 `data/tool-workspace.sqlite3`。systemd 部署使用 `/var/lib/auditbox-server/` 下的对应目录。工具运行仍受服务账号权限与宿主机条件限制；默认服务器部署不授予应用 Docker socket 访问权，Docker 工具需要另外配置运行条件。

MCP 自动发现服务提供的工具，工具白名单可限制挂载范围，已挂载的工具均需逐次审批。
连接配置必填名称、HTTP 地址和传输协议，工具白名单与可选鉴权请求头位于“更多设置”。
新连接默认保存为启用状态，在执行总开关开启后向模型提供，用户可在列表中关闭连接。
总开关关闭时不挂载或连接 MCP。切换能力后重建 Agent，同步模型工具列表与界面状态。

本地 Skill 从 `AUDITBOX_AGENT_SKILL_DIRECTORY`（默认 `data/skills`）加载。默认安装并启用
ModelScope 的 `@spclaudehome/skill-vetter` 与 `@anthropics/skill-creator`。上传的 `.zip`/`.skill` 包
须包含单个顶层目录，且该目录内含 `SKILL.md`；每个 Skill 可独立启停。

```json
[
  {
    "name": "operations",
    "url": "http://127.0.0.1:8770/mcp",
    "transport": "streamable-http",
    "tools": ["query_device", "get_interface_status"]
  }
]
```

能力配置保存在 SQLite。相关接口：

```text
GET   /api/v1/capabilities
PATCH /api/v1/capabilities/{capability_id}
POST  /api/v1/capabilities/mcp
POST  /api/v1/capabilities/skills
DELETE /api/v1/capabilities/{capability_id}
```

删除接口只接受用户导入的 Skill 和用户创建的 MCP；内置工具、内置 Skill 与环境预置能力受到保护。

执行开启后，已启用的工具、MCP 与 Skill 清单写入稳定系统上下文，模型可直接调用，
无需额外枚举能力。

## 共享终端

开启 Agent 执行后可调用 `list_terminal_sessions`、`read_terminal`、`send_terminal_command` 和
`interrupt_terminal`。Gateway 持有浏览器正在使用的 Proxy 终端会话；Agent 通过回环内部接口
读取同一终端的人工命令与设备输出。写命令和发送 Ctrl+C 均进入 Agno 暂停/审批流程，
逐次批准后才写入同一条 Gateway→Proxy 连接，再将实际输出返回给 Agent。
人工介入时，正在等待的 Agent 命令返回 `interrupted_by_user`，
后续步骤需依据实际工具结果判断。
Agent 不持有 SSH 密码，也不建立第二条 SSH/Telnet 连接。工具批准后，命令仍受命令闸门约束：需要确认时另行确认，硬阻断时拒绝下发。完整流程与状态说明见 [共享终端工具设计](docs/TERMINAL_TOOL_DESIGN.md)。

## 知识库

知识库用于跨对话检索导入资料，默认关闭；当前对话附件通过上面的文件区使用。需要知识库时，在 `agent/` 目录安装可选依赖，配置 Embedding 后重启 Agent：

```bash
uv sync --frozen --python 3.11 --extra knowledge
```

```text
AUDITBOX_AGENT_KNOWLEDGE_ENABLED=true
AUDITBOX_AGENT_EMBEDDING_MODEL=<model-id>
AUDITBOX_AGENT_EMBEDDING_BASE_URL=http://127.0.0.1:<port>/v1
```

知识库的 ChromaDB 和通用文档读取器通过可选依赖安装。`knowledge` extra 支持 TXT、Markdown、
HTML 和 PDF；`office` extra 提供 DOCX/XLSX/PPTX 读取依赖。知识库 API 的默认上传扩展名列表不接受 DOCX、XLSX、PPTX；仅安装 `office` extra 不会更改该列表。对话文件区的 DOCX 解析使用基础依赖中的 `python-docx`，与知识库上传分开处理。

## 单用户 API

API 使用 `AUDITBOX_AGENT_LOCAL_USER_ID` 对应的固定本地用户关联会话，创建对话和发送消息时省略 `user_id`。
以下示例用于服务器本机调试；浏览器请求经 Gateway 认证后转发：

```bash
curl -X POST http://127.0.0.1:18765/api/v1/conversations \
  -H 'Content-Type: application/json' \
  -d '{"title":"网络故障排查"}'

curl -N -X POST 'http://127.0.0.1:18765/api/v1/conversations/<session_id>/messages' \
  -H 'Content-Type: application/json' \
  -d '{"message":"帮我分析交换机接口不通的原因","stream":true}'
```

将第二个请求中的 `<session_id>` 替换为创建对话响应中的 `session_id`。

## 问答上下文

上下文按照“稳定提示 → 历史 → 最新快照 → 当前问题 → 当前时间”组织，使用 32K 窗口、
85% 压缩触发与 90% 输入上限；时间和变化快照置于尾部以支持前缀缓存。

- 问答模式不发送工具 schema 或能力目录。历史工具结果转为普通参考文本，并移除工具调用协议。
  压缩摘要围绕“问题与背景 / 已确认事实及来源 / 未解疑问与缺少资料”，区分命令建议与已执行操作。
- Agent 根据 `device_type/device_profile` 记录设备快照，并提示 Linux/H3C 命令语法。
  其他类型需先确认品牌和系统；SSH/Telnet/Console 仅表示连接方式。
- 问题提到附件或文件名时，服务端在当前会话内按关键词检索，提供最多
  `AUDITBOX_AGENT_QA_FILE_CONTEXT_CHARACTERS`（默认 6000）字符的摘录。普通问题不加载正文。
  摘录只覆盖命中的内容；资料不足时需指定章节或补充资料。
  知识库默认关闭；启用后由服务端提供检索结果，不增加模型工具循环。
- 上下文组装失败或超出预算时，`InputCheckError` 会在请求模型前终止处理。

连接信息只作为回答背景，历史命令输出可能过时。上下文 token 数采用离线保守估计，缓存复用、回答质量和首字延迟需在实际服务器上验证。

## 多步任务与上下文缓存（开启执行后）

- 审批恢复使用暂停时保存的 `RunInput`，沿用当时的历史、检查点、任务约束和时间，再追加本次工具结果。
  Agent 服务重启后仍可从 SQLite 恢复。
- 近期历史保留配对的工具调用与真实结果，失败、取消和待审批运行保留原目标与状态。
  缺少返回结果的调用不记为已执行；继续任务时，后续操作仍需逐次审批。
- 终端和文件快照在变化时追加，未变化时复用已有快照，以最后一份为准。
  历史快照保留；目标或文件清空时追加空状态，避免沿用旧设备信息。
- 默认使用 32K 上下文窗口、85% 压缩触发与 90% 输入上限，工具参数也计入预算。
  仅压缩最旧的连续片段，保留目标、约束、已完成证据、未完成步骤和阻塞，待审批运行作为压缩边界。
  摘要失败或过大时保留原历史；新轮次或审批恢复超过输入预算时明确报错，保持原窗口限制。
- 多步核对在同一个 Agent 的模型与工具循环中进行，操作结论需结合实际工具结果核实。
- 本地兼容接口提供缓存计数时标记 `provider_reported`；未提供时标记 `unavailable`，
  命中率为 `null`。稳定前缀为 KV 复用提供条件，服务换槽、重启、模式切换和检查点压缩
  都可能导致缓存未命中；设备状态仍需另行核实。

标准与增强模式使用 `chat_template_kwargs.enable_thinking` 参数区分。兼容旧请求时，`fast` 映射为 `enhanced`，`FAST_MODEL*` 配置不参与问答路由。Agent 默认直接访问问答端点，不经过 Inference 队列。

实现入口：[API 路由](src/auditbox_agent/api.py)、[模型与工具运行](src/auditbox_agent/runtime.py)、[上下文管理](src/auditbox_agent/context_manager.py)。设计分工见 [Agent 架构](docs/ARCHITECTURE.md)，开发检查见 [贡献指南](../CONTRIBUTING.md)。
