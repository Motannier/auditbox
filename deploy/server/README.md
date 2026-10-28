# AuditBox 服务器版部署

服务器版包含四个 systemd 应用服务、两组 Python 环境和两个使用 NVIDIA GPU 的 llama.cpp 容器。浏览器通过服务器 IP 的 18080 端口访问。完整拓扑、端口和执行链路见 [服务器架构](../../docs/ARCHITECTURE.md)。

## 前提

- Linux x86_64、systemd、CPython 3.10 或 3.11、uv。
- Node.js 20.19+ 或 22.12+，用于构建前端；也可在其他机器上构建。
- Docker Compose、NVIDIA GPU，以及容器所需的驱动和运行时。
- 问答和 Linux 审查 GGUF 文件；文件名及镜像摘要见本目录 Compose 与示例配置。
- 宿主机已配置好网络和 NTP。应用沿用这些设置，不配置 USB、热点、网口、NPU 或 Guardian。

两个模型共享 GPU 显存与算力；Compose 和应用 slice 的资源限制不提供硬件级 GPU 隔离。Python、Docker、GPU 驱动和前端构建工具须预先安装。

## 新服务器安装

在可写的源码目录构建前端，将两个模型文件放入同一目录，再执行安装脚本：

```bash
./scripts/quickstart.sh --build-only
sudo ./deploy/server/install.sh --address '<server-ip>' --models '/path/to/models' --uv "$(command -v uv)"
```

安装脚本校验模型大小和 SHA-256，创建以时间戳命名的 release，复制源码与构建结果，
准备专属 uv、模型目录和 Compose 配置，再调用 Python 安装器创建运行环境、账号和
systemd 单元。服务需在安装后手动启动。模型来源见 [模型说明](../../models/README.md)。

`--address` 使用服务器的 IPv4 地址；系统 `python3` 须为 3.10 或 3.11。
`--uv` 指定当前用户可用的 uv；`--release <version>` 可指定版本名。
脚本仅支持首次安装，检测到已有 `current` 时停止。已有实例先备份配置和数据库，再按下文升级。

| 路径 | 用途 |
| --- | --- |
| `/opt/auditbox-server/releases/<version>/` | 每个 release 的源码、网页及两组独立 Python 环境 |
| `/opt/auditbox-server/current` | 当前应用版本链接 |
| `/opt/auditbox-server/models/` | 两个已校验的 GGUF 文件 |
| `/opt/auditbox-server/models-runtime/` | Compose 与模型运行配置，新安装生成，已有文件保留 |
| `/etc/auditbox-server/` | 应用配置与初始凭据 |
| `/var/lib/auditbox-server/`、`/var/log/auditbox-server/` | 持久化状态与日志 |

应用以 `auditbox-server` 系统账号运行。Gateway 同时绑定 `127.0.0.1:18080` 和指定服务器
IP 的 18080；其他服务与模型仅监听回环地址。首次安装会生成独立密码与签名密钥。

启动前按 `models-runtime/.env` 中的固定镜像摘要准备 Docker 镜像。
模型启动器使用 `--pull never`；镜像和模型均需提前准备，安装脚本不负责下载。
离线安装时，用 `sudo env` 显式传入 `UV_OFFLINE=true`、`UV_NO_INDEX=true` 和
`UV_FIND_LINKS=/absolute/path/to/wheels` 后执行同一安装脚本；系统工具、镜像和驱动需预先准备。

## 启动和健康检查

```bash
sudo systemctl start auditbox-server-{proxy,gateway,models,inference,agent}

curl http://127.0.0.1:18080/health
curl http://127.0.0.1:18765/api/v1/health
curl http://127.0.0.1:18081/health
curl http://127.0.0.1:18082/health
```

模型 oneshot 服务先启动 Linux 审查模型，通过健康检查后再启动问答模型。两个模型就绪后，Inference 和 Agent 按 systemd 的启动顺序运行。

初始账号为 `opsadmin`、`superadmin`，随机密码保存在 `/etc/auditbox-server/initial-credentials.json`，仅 root 可读。环境配置位于同目录的 `auditbox.env`，升级时保留已有值。Agent 执行默认关闭，开启后每次终端写入仍须审批。知识库默认关闭，H3C 专属模型默认不启动。

浏览器访问 `http://<server-ip>:18080`，验证终端、问答、确认/阻断、Agent 审批、审计关联和重启后的数据保留情况。验证通过后，再为上述服务启用自启动。

## 升级、状态与回退

每次升级使用新的 release 目录，并保留旧目录及其两组虚拟环境。停止四个应用服务后，
备份 `/etc/auditbox-server/` 与 `/var/lib/auditbox-server/`，再运行新 release 的安装器。
安装器在新 release 中安装 Python 环境并切换 `current`，保留已有账号和状态。安装失败时，
先检查 `current` 的指向，再决定恢复旧版本或继续修复。保留原 release 目录，勿覆盖写入。

```bash
systemctl stop auditbox-server-{gateway,proxy,inference,agent}
# 完成备份后，按新安装步骤在新的 release 目录运行 install.py。
systemctl status auditbox-server-{gateway,proxy,inference,agent,models}
docker ps --filter label=com.docker.compose.project=auditbox
journalctl -u auditbox-server-agent -n 100 --no-pager
```

回退应用时，先停止四个应用服务，再将 `current` 指向之前验证过的 release。
服务会使用该 release 的源码和虚拟环境。确认旧版本兼容当前配置与数据库后，
启动应用并检查健康状态。从共享虚拟环境的旧部署升级前，须先为旧 release 安装独立环境，
以便回退时使用。回退模型时恢复旧镜像摘要和参数，仅重建本产品的两个容器。

配置、模型、业务数据库和日志独立于 release 保留。应用回退不会逆向迁移数据库。
清理范围仅限本产品，勿执行全局 Docker prune 或删除其他业务的容器、卷和网络。

