# AuditBox 服务器后端

本模块负责目标连接、命令控制、设备辅助和关联审计，由三个独立进程组成。
服务器安装器将它们安装到同一个 Python 环境；[Agent](../agent/README.md) 使用独立环境。

| 进程 | 启动命令 | 服务器端口与职责 |
| --- | --- | --- |
| Gateway | `auditbox-gateway` | 18080，登录、HTTP API、静态站点、WebSocket 和 Agent 转发 |
| Proxy | `auditbox-proxy` | 回环 19000，SSH/Telnet/串口代理、命令闸门及审计；同进程启动 19001 事件通道 |
| Inference | `auditbox-inference` | 回环 18091，按设备平台转发模型请求并调度经过此服务的推理 |

命令入口及依赖声明见 [pyproject.toml](pyproject.toml)。完整拓扑和端口说明见
[服务器架构](../docs/ARCHITECTURE.md)。

## 模块

- [gateway/](auditbox/gateway/)：浏览器鉴权、终端会话和内部 Agent 工具桥。
- [relay/](auditbox/relay/)：终端输入恢复、命令确认/阻断及协议连接。
- [audit/](auditbox/audit/)、[db/](auditbox/db/)：业务状态、关联审计、JSONL 兼容记录和历史迁移。
- [prediction/](auditbox/prediction/)、[recommendation/](auditbox/recommendation/)、[reference/](auditbox/reference/)：辅助事件、命令推荐与速查，按会话指定的设备平台处理。
- [security/](auditbox/security/)：确定性风险规则和旁路风险评估；旁路结果仅用于记录和展示。
- [inference/](auditbox/inference/)：路由、请求排队与模型端点适配。
- [capability_packages.py](auditbox/capability_packages.py)、[command_packages.py](auditbox/command_packages.py)、[command_graph_packages.py](auditbox/command_graph_packages.py)：设备扩展数据加载和校验。

服务器配置关闭浏览器修改网口和宿主机时间的功能。应用使用宿主机现有网络，
设备平台、模型端点和监听范围均由配置指定。

## 开发

在仓库根目录使用统一入口构建、配置并运行核心终端：

```bash
./scripts/quickstart.sh
```

脚本默认使用 Python 3.11，在同一个终端中运行 Proxy 和 Gateway，按 Ctrl+C 停止两者。
已有环境通过 `./scripts/quickstart.sh --start-only` 启动；配置与访问方式见
[QuickStart](../README.md#quickstart)。单独调试进程时，可从仓库根目录使用
`make run-proxy` 或 `make run-gateway`。单独启动前先停止组合运行的服务，以免端口冲突。

开发配置将进程绑定到回环地址，Gateway 在 `127.0.0.1:18080` 提供构建后的网页。
前端开发时可另外启动 Vite。准备好模型后，按 [部署说明](../deploy/server/README.md)
启用 AI 服务并调整旁路审查配置；需要单独调试 Inference 时，在 `box/` 目录运行：

```bash
uv run --frozen --python 3.11 auditbox-inference
```

Agent 需另行启动。Inference 只调度经过自身的请求；服务器配置中的 Agent 直接访问问答端点。

正式安装通过 systemd 的 `EnvironmentFile` 读取 `/etc/auditbox-server/auditbox.env`，
通过 `PYTHONPATH` 加载源码。各环境独立生成配置文件中的凭据。

依赖声明见 `pyproject.toml`，具体版本由 `uv.lock` 固定。完整安装见
[部署说明](../deploy/server/README.md)；提交改动前按 [贡献指南](../CONTRIBUTING.md) 选择验证范围。
