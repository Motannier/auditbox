# 模型选择与接入

AuditBox 将问答与命令风险审查分开配置。完整服务器部署默认运行 Qwen3.5 2B 和 Command Guard 两个本地模型；H3C Risk 按需启用。核心终端、命令规则和审计可独立运行，首次启动见 [QuickStart](../README.md#quickstart)。

| 模型 | 作用 | 默认运行配置 |
| --- | --- | --- |
| [Qwen3.5 2B](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF) | 标准／增强问答、Agent 计划与工具调用 | `Qwen3.5-2B-Q4_K_M.gguf`，别名 `qwen3.5-2b`，18081 端口，32K 上下文 |
| [Command Guard 0.8B](https://huggingface.co/Motanni/auditbox-command-guard-0.8b-gguf) | Linux 近期命令与上下文的旁路风险审查 | `command-guard-q8_0.gguf`，别名 `command-guard`，18082 端口，10,240 上下文 |
| [H3C Risk 0.8B](https://huggingface.co/Motanni/auditbox-h3c-risk-0.8b-gguf) | H3C Comware 命令的旁路风险审查 | 可选；`h3c-risk-q8_0.gguf`，别名 `h3c-risk`，单独启动服务 |

问答模型使用 [Qwen/Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B) 的 Unsloth Q4_K_M 量化制品，项目未对其微调。两个 0.8B 模型基于 Qwen3.5-0.8B 进行领域微调，以 GGUF Q8_0 提供。问答模型直接服务于 Agent，审查模型经 Inference 路由，链路见 [系统架构](../docs/ARCHITECTURE.md)。

模型风险结果用于提示与审计；命令下发前的确认和阻断由应用规则处理。模型卡说明了各自输入要求与适用边界：[Command Guard](model-cards/command-guard/README.md)、[H3C Risk](model-cards/h3c-risk/README.md)。

## 下载与校验

在仓库根目录执行以下命令，将 `model_dir` 替换为实际目录。下载使用固定 revision，与 [manifest.json](manifest.json) 中的文件大小和 SHA-256 对应：

```bash
model_dir=/path/to/models
mkdir -p "$model_dir"
curl --fail --location \
  'https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/f6d5376be1edb4d416d56da11e5397a961aca8ae/Qwen3.5-2B-Q4_K_M.gguf' \
  --output "$model_dir/Qwen3.5-2B-Q4_K_M.gguf"
curl --fail --location \
  'https://huggingface.co/Motanni/auditbox-command-guard-0.8b-gguf/resolve/28ddd3674df32b623d2cff6f9cb673ec40e1fa07/command-guard-q8_0.gguf' \
  --output "$model_dir/command-guard-q8_0.gguf"
python3 tools/check_models.py "$model_dir"
```

`check_models.py` 使用 Python 标准库检查两个默认文件是否存在、大小与哈希是否匹配，失败时以非零状态退出。也可执行 `make check-models MODEL_DIR=/path/to/models`。校验不会加载模型或启动容器，哈希一致不代表模型质量已经评测。

校验通过后，把同一目录传给 [服务器安装器](../deploy/server/README.md#首次安装) 的 `--models` 参数。默认文件名、容器参数及别名见 [Compose](../deploy/server/models.compose.yaml)。

## 启用 H3C 审查

H3C Risk 不在默认双模型 Compose 中。先下载[固定版本权重](https://huggingface.co/Motanni/auditbox-h3c-risk-0.8b-gguf/resolve/01e2fcf6850661ae4db6e82a19b3611594a89a11/h3c-risk-q8_0.gguf)，与模型卡中的文件大小、[SHA256SUMS](model-cards/h3c-risk/SHA256SUMS) 核对，再按 [模型卡](model-cards/h3c-risk/README.md#本地运行)启动独立服务。默认文件校验工具只检查 `manifest.json` 的 `models`，不检查 `optional_models`。

已有完整服务器部署时，在 `/etc/auditbox-server/auditbox.env` 设置：

```dotenv
AUDITBOX_INFERENCE_PROFILES=linux,h3c_comware
AUDITBOX_H3C_BACKEND_URL=http://127.0.0.1:8083
AUDITBOX_H3C_LLM_MODEL=h3c-risk
AUDITBOX_HISTORY_REVIEW_ENABLED=true
```

确认 H3C 服务健康后，重启读取路由配置的两个进程，并重新连接对应终端：

```bash
sudo systemctl restart auditbox-server-{proxy,inference}
```

H3C 模型的生命周期由部署者单独管理，不随默认 models 服务启动。设备连接选择 `h3c_comware` 平台后，近期历史审查才会使用对应路由；其他品牌仍使用各自规则或模型。

## 替换问答端点

Agent 可使用本地 llama.cpp、Ollama 或其他 OpenAI 兼容端点。需要同步核对模型名、端点、聊天模板、工具调用支持与上下文预算，配置入口见 [Agent 运行说明](../agent/README.md#运行与开发)。已有服务器保留旧模型配置，更换文件与服务别名时按 [部署说明](../deploy/server/README.md#更换模型)调整。

标准与增强模式共用问答模型，通过 `enable_thinking` 参数区分。外部端点会收到请求携带的对话、设备背景和文件摘录，使用前确认数据可发送范围。

## 来源、许可与已知限制

Qwen3.5 2B 沿用上游 Apache 2.0 许可。两个 0.8B 微调制品采用 GPL-3.0-only，并在各自模型卡目录保留基础模型的 Apache 2.0 许可和归属说明。详细来源见 [第三方记录](../THIRD_PARTY_NOTICES.md)。

训练运行、数据版本与使用权限、基础模型训练 revision 和转换工具版本尚未完整归档。当前没有与这些制品哈希绑定的独立评测结果，未提供准确率、召回率、误报率或吞吐指标。
