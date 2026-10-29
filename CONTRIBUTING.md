# 开发与贡献

先阅读 [项目 README](README.md)，了解项目用途与命令控制链路，再按改动范围查看 [后端](box/README.md)、[Agent](agent/README.md) 或 [架构说明](docs/ARCHITECTURE.md)。

## 开始开发

- 服务器运行目标：Linux x86_64、Python 3.10 / 3.11；完整 AI 部署另需 systemd、Docker Compose 和 NVIDIA GPU。
- 前端构建：Node.js 20.19+ 或 22.12+，使用 `web/package-lock.json`。
- 后端和 Agent 分别使用各自的 `pyproject.toml`、`uv.lock` 和虚拟环境。保持两个环境独立，更新依赖时以声明和锁文件为准，避免只依赖当前机器的 `pip freeze`。

执行 `./scripts/quickstart.sh` 可构建、配置并启动核心工作台；后续使用 `--start-only` 直接启动已准备好的环境，Ctrl+C 停止本次启动的服务。
核心工作台运行 Proxy 和 Gateway，详见 [QuickStart](README.md#quickstart)。

开发各模块时，在仓库根目录查看命令并安装开发依赖：

```bash
make help
make setup
```

`make setup` 分别安装后端、Agent（含 `dev` extra）和前端依赖，不启动服务。
Make 的 Python 默认版本为 3.11，可用 `make setup PYTHON_VERSION=3.10` 改为 3.10。
QuickStart 自动初始化后端配置，也可单独执行 `make configure`；仅在 `box/.env` 不存在时生成独立密码与签名密钥。核心配置关闭历史旁路审查，保留确定性命令规则与审计。
Agent 和 Vite 开发配置按需从各自示例复制，保留已有 `.env`，并核对模型地址。
Agent、模型、Proxy 和内部接口保持回环监听。

`web/.env.production` 是公开构建配置，API 地址留空以使用页面同源。请保留此文件，勿添加凭据或实际环境地址。

## 修改与验证

改动前检查调用方、配置项和对应测试。一次改动围绕一个具体行为；删除文件前确认入口、导入、打包和测试均无引用。

从仓库根目录选择检查：

| 改动范围 | 检查入口 |
| --- | --- |
| 文档与配置注释 | 核对链接、章节锚点、命令与实际配置，无需启动模型或安装服务 |
| Gateway、Proxy、命令闸门、设备规则或审计 | `make test-backend` |
| Agent、审批、文件区或会话上下文 | `make test-agent`；必要时运行模块内的 Ruff 和 mypy |
| 网页功能、资源或样式 | `make build`、`make test-web`；兼容性改动另运行 `make test-browser-compat` |
| 跨模块行为 | `make test`，并补跑受影响的构建与兼容检查 |

后端与 Agent 单元测试使用临时目录和模拟依赖。浏览器测试需要本机 Chrome / Chromium，可通过 `CHROME_PATH` 指定。默认 UI 测试启动本地 Vite 并模拟 API；保持 `WEBUI_TEST_URL` 未设置，避免连接实际业务环境。
保留能验证实际行为的测试和合成样例，勿加入真实账号、终端记录或客户配置。

依赖变更须同时更新声明和锁文件，说明用途，核对许可证并保留适用声明；随源码引入第三方材料时，同步更新 [第三方说明](THIRD_PARTY_NOTICES.md)。

修复缺陷时补充复现测试；文档或纯样式改动按影响范围检查。
提交评审时说明触发场景、行为变化、验证命令与结果，并注明未验证的环境。
安装器会修改目标环境。日常回归使用临时目录和模拟调用验证安装行为，不执行真实安装。

## 来源与边界

提交前，请确认有权按本项目的 [GPL-3.0-only 许可证](LICENSE) 提供贡献。第三方材料须保留原许可，并确认其与项目的使用和分发方式兼容。

引入代码、图片、Skill、模型或设备资料派生数据时，记录原始来源、固定版本和许可证，保留原作者声明。记录保存在 [第三方说明](THIRD_PARTY_NOTICES.md)；授权不明的材料须明确标注待核实，第三方许可须单独遵守。

提交前检查新增文件与 diff，排除实际 `.env`、密码、签名密钥、私钥、客户配置和业务数据。测试凭据使用明确的合成值，日志与截图按需脱敏。漏洞报告方式见 [安全说明](SECURITY.md)。
