# 服务器模型

默认部署通过两个本地 llama.cpp CUDA 容器运行问答模型和 Linux 审查模型，文件名和服务别名须与
[Compose](../deploy/server/models.compose.yaml) 一致。H3C 专用审查模型按需配置。

| 用途 | 文件 | 服务别名 | 来源 |
| --- | --- | --- | --- |
| 标准 / 增强问答 | `MiniCPM5-2B-Q4_K_M.gguf` | `minicpm5-2b` | 官方 Hugging Face 仓库，固定 revision |
| Linux 命令审查 | `command-guard-q8_0.gguf` | `command-guard` | 自有 0.8B 微调制品，GPL-3.0-only，[模型卡](model-cards/command-guard/README.md) |
| H3C Comware 命令审查（可选） | `h3c-risk-q8_0.gguf` | `h3c-risk` | 自有 0.8B 微调制品，GPL-3.0-only，[模型卡](model-cards/h3c-risk/README.md) |

[manifest.json](manifest.json) 的 `models` 记录默认部署所需文件，`optional_models` 记录可选 H3C 模型。
各条目包含来源、固定 revision、文件大小、SHA-256 和许可。
下载时使用固定 revision，导入前核对大小和哈希。

MiniCPM5 使用上游官方制品，可从 [固定版本的文件地址](https://huggingface.co/openbmb/MiniCPM5-2B-GGUF/blob/2079a22f3beaa4e306449978533478fe0522f4b3/MiniCPM5-2B-Q4_K_M.gguf) 获取。
两个 0.8B 模型均为 Qwen3.5-0.8B 领域微调的 GGUF Q8_0 制品，模型卡提供各自用途、
文件信息、运行示例和使用边界。模型卡与 GPL 许可、上游 Apache 许可及归属说明保存在
`model-cards/`。Hugging Face 固定版本下载地址：

- [Command Guard 0.8B Q8_0](https://huggingface.co/Motanni/auditbox-command-guard-0.8b-gguf/resolve/28ddd3674df32b623d2cff6f9cb673ec40e1fa07/command-guard-q8_0.gguf)（默认 Linux 命令审查）。
- [H3C Risk 0.8B Q8_0](https://huggingface.co/Motanni/auditbox-h3c-risk-0.8b-gguf/resolve/01e2fcf6850661ae4db6e82a19b3611594a89a11/h3c-risk-q8_0.gguf)（可选 H3C Comware 命令审查）。

具体训练运行、数据版本与使用权限、基础模型训练 revision 和转换工具版本尚未完整归档。
本次发布不提供独立评测结果，未声明准确率、召回率、误报率或吞吐指标。

## 本地校验与导入

将默认部署所需的 MiniCPM5 和 command-guard 文件放入同一目录，再从仓库根目录运行以下命令，替换为实际模型路径：

```bash
python3 tools/check_models.py /path/to/models
```

也可执行 `make check-models MODEL_DIR=/path/to/models`。工具使用 Python 标准库依次检查
文件是否存在、大小和 SHA-256，失败时以非零状态退出。校验过程不加载模型或启动容器。

校验通过后，按 [部署说明](../deploy/server/README.md) 导入 `/opt/auditbox-server/models/`。
哈希仅验证文件一致性；模型输出质量和分发授权须另行核验。

H3C 模型使用其下载目录中的 `SHA256SUMS` 单独校验，启动方式及需要启用的路由见
[H3C 模型卡](model-cards/h3c-risk/README.md)。
