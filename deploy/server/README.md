# 在服务器上部署 AuditBox

本目录提供 systemd 安装器和本地模型运行配置，适合需要常驻运行终端、AI 助手与审计的 Linux 服务器。首次试用核心工作台可从 [QuickStart](../../README.md#quickstart) 开始，无需先准备 GPU 和模型。

完整部署运行 Gateway、Proxy、Inference、Agent 四个应用进程，以及两个 llama.cpp CUDA 模型容器。浏览器通过服务器的 18080 端口访问，其余端口仅监听本机。服务分工与数据路径见 [系统架构](../../docs/ARCHITECTURE.md)。

## 安装前准备

- Linux x86_64、systemd、CPython 3.10 或 3.11、uv；系统 `python3` 需为上述版本并支持 `venv`。
- Git、Bash、Node.js 20.19+ 或 22.12+，用于从源码构建网页。
- Docker Compose、NVIDIA GPU，以及容器所需的驱动和运行时。
- [模型说明](../../models/README.md#下载与校验)中的两个默认 GGUF 文件，放入同一目录。
- 可供浏览器和目标设备访问的宿主机网络。时间同步由宿主机 NTP 管理。

工具、驱动和模型需提前准备。两个模型共享 GPU 显存与算力，Compose 的资源限制不提供 GPU 硬件隔离。串口连接还需要宿主机设备和服务账号具有相应访问权限。

## 首次安装

在仓库根目录执行，替换实际服务器 IPv4 地址和模型目录：

```bash
./scripts/quickstart.sh --build-only
sudo ./deploy/server/install.sh --address '<server-ip>' --models '/path/to/models' --uv "$(command -v uv)"
```

脚本校验模型大小与 SHA-256，复制源码、构建结果和模型，创建两组独立 Python 环境、系统账号及 systemd 单元。首次配置生成随机登录密码与签名密钥；安装完成后再手动启动服务。

`--uv` 指定当前用户可用的 Linux uv；`--release <version>` 可指定版本名，默认使用时间戳。脚本检测到已有 `current` 时会停止，已有实例按下文升级。

| 路径 | 用途 |
| --- | --- |
| `/opt/auditbox-server/releases/<version>/` | 每个版本的源码、网页及后端与 Agent 虚拟环境 |
| `/opt/auditbox-server/current` | 当前应用版本链接 |
| `/opt/auditbox-server/bin/uv` | 安装器使用的独立 uv |
| `/opt/auditbox-server/models/` | 两个默认 GGUF 文件 |
| `/opt/auditbox-server/models-runtime/` | 模型 Compose 与镜像配置 |
| `/etc/auditbox-server/` | 应用配置与初始凭据 |
| `/var/lib/auditbox-server/`、`/var/log/auditbox-server/` | 持久化状态与日志 |

应用以 `auditbox-server` 系统账号运行。Gateway 监听 `127.0.0.1:18080` 和指定服务器 IP 的 18080，其他服务与模型保持回环监听。

### 准备固定版本镜像

安装器将 [models.env.example](models.env.example) 复制到 `models-runtime/.env`。核对其中的镜像摘要后，执行：

```bash
sudo docker compose --env-file /opt/auditbox-server/models-runtime/.env \
  -f /opt/auditbox-server/models-runtime/models.compose.yaml pull
```

模型启动器使用 `--pull never`，启动时不会下载镜像。离线环境需提前导入相同摘要的镜像，并备齐系统工具、GPU 驱动及依赖包。离线安装可用 `sudo env` 显式传入 `UV_OFFLINE=true`、`UV_NO_INDEX=true` 和 `UV_FIND_LINKS=/absolute/path/to/wheels` 后执行安装脚本。

## 启动、登录与检查

```bash
sudo systemctl start auditbox-server-{proxy,gateway,models,inference,agent}

curl --fail http://127.0.0.1:18080/health
curl --fail http://127.0.0.1:18765/api/v1/health
curl --fail http://127.0.0.1:18091/health
curl --fail http://127.0.0.1:18081/health
curl --fail http://127.0.0.1:18082/health
```

模型启动器先等待 Linux 审查模型健康，再启动问答模型。systemd 按依赖关系安排进程启动，仍需核对健康响应与日志，确认模型实际可用。

浏览器访问 `http://<server-ip>:18080`。账号 `opsadmin` 用于设备操作，`superadmin` 用于管理和审计；初始随机密码保存在仅 root 可读的 `/etc/auditbox-server/initial-credentials.json`。

连接一台测试设备，核对终端、问答、命令确认、Agent 审批和审计关联，再验证重启后的数据保留。确认运行正常后启用自启动：

```bash
sudo systemctl enable auditbox-server-{proxy,gateway,models,inference,agent}
```

## 配置与可选能力

环境配置位于 `/etc/auditbox-server/auditbox.env`。修改后重启受影响的应用服务；Agent 执行总开关首次初始化后以界面保存的设置为准。

- 服务器安装默认启用 Linux 历史旁路审查；QuickStart 生成的本地配置将其关闭。两者都保留确定性命令检查与审计。
- Agent 执行初始关闭，终端写入和 Ctrl+C 逐次审批。Agent 当前不按浏览器账号隔离对话和文件，详见 [Agent 说明](../../agent/README.md)。
- 知识库默认关闭，H3C 专属模型默认不启动。[H3C 接入配置](../../models/README.md#启用-h3c-审查)需另行准备。
- 应用沿用宿主机网络和时间，服务器配置关闭浏览器校时、网口修改与 Guardian 准入，不安装板端网络助手。

### 更换模型

已有的 `auditbox.env` 和 `models-runtime/` 配置会保留。更换问答模型时，先准备并校验新 GGUF，再同步修改：

| 配置位置 | 需核对的值 |
| --- | --- |
| `models-runtime/models.compose.yaml` 的 assistant 服务 | `--model` 文件路径、`--alias`、上下文长度与运行参数 |
| `auditbox.env` | `AUDITBOX_ASSISTANT_LLM_MODEL`、`AUDITBOX_AGENT_STANDARD_MODEL` 与模型别名一致；端点变化时同步修改相应 URL |
| Agent 上下文配置 | 输入预算与模型服务实际的上下文长度一致 |

本版本默认文件为 `Qwen3.5-2B-Q4_K_M.gguf`，别名为 `qwen3.5-2b`。准备完成后重启 models、Inference 和 Agent，并重新核对问答。H3C 服务单独管理。

## 升级与回退

升级使用新的版本目录，保留旧源码和各自的虚拟环境。以下命令在包含新版本提交、工作区干净的源码仓库根目录执行，将 `HEAD` 对应的源码和本地构建结果放入同一版本目录。

先构建新版本，再停止四个应用服务，备份 `/etc/auditbox-server/`、`/var/lib/auditbox-server/` 与 `/var/log/auditbox-server/`。备份应覆盖整个目录，包含 SQLite 可能存在的 WAL 文件；按原权限保存凭据。另行保留 `models-runtime/` 配置，以及当前版本链接的目标。

```bash
./scripts/quickstart.sh --build-only
readlink -f /opt/auditbox-server/current
sudo systemctl stop auditbox-server-{gateway,proxy,inference,agent}
# 完成上述备份后继续；版本目录必须是新目录。
release_dir="/opt/auditbox-server/releases/$(date -u +%Y%m%dT%H%M%SZ)"
sudo mkdir "$release_dir"
git archive HEAD | sudo tar -x -C "$release_dir"
sudo cp -a dist "$release_dir/dist"
sudo python3 "$release_dir/deploy/server/install.py" --release "$release_dir" --address '<server-ip>'
sudo systemctl start auditbox-server-{proxy,gateway,inference,agent}
```

安装器在新版本目录安装依赖、更新服务单元并切换 `current`，保留已有配置和业务状态。`--address` 不会覆盖已有环境文件中的地址；服务器 IP 变化时需同步调整 Gateway 监听、公开 URL 与 CORS 配置。

日常状态与日志检查：

```bash
sudo systemctl status auditbox-server-{gateway,proxy,inference,agent,models}
sudo docker ps --filter label=com.docker.compose.project=auditbox
sudo journalctl -u auditbox-server-agent -n 100 --no-pager
```

升级失败时先检查 `current` 指向，再决定修复或回退。回退前停止四个应用服务，确认旧版本兼容当前配置与数据库，用旧版本的安装器重新生成服务配置并切换链接，再启动和检查健康。应用回退不会逆向迁移数据库；不兼容时需结合停机备份恢复数据。

从共享虚拟环境的旧部署升级前，先为旧版本准备独立环境。回退模型时恢复原镜像摘要和参数，仅重建本产品容器。清理限于本产品的旧版本，保留所需备份与回退文件。
