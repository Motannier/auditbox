---
language:
  - zh
  - en
license: gpl-3.0
base_model: Qwen/Qwen3.5-0.8B
base_model_relation: finetune
pipeline_tag: text-generation
tags:
  - gguf
  - qwen3.5
  - auditbox
  - h3c
  - comware
  - command-risk-analysis
  - quantized
---

# AuditBox H3C Risk 0.8B — GGUF Q8_0

H3C Risk 是面向 H3C Comware 命令风险分析的 0.8B 级领域微调模型。
模型根据调用方提供的设备平台、命令历史和当前操作，生成潜在影响、风险原因及核对建议。
在 AuditBox 中，它可作为 H3C 终端的专用旁路审查模型，辅助操作员核对命令语义与变更范围。

## 模型与文件

| 项目 | 内容 |
| --- | --- |
| 基础模型 | [Qwen/Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B) |
| 架构 | `qwen35`，24 层，隐藏维度 1024 |
| 微调标识 | GGUF 记录为 `Qwen3.5 0.8B H3Crisk`，`general.finetune=h3crisk` |
| 制品 | `h3c-risk-q8_0.gguf`，GGUF v3，Q8_0 |
| 文件大小 | 811,843,040 字节 |
| SHA-256 | `0d277487d36f7c68c6c7cdd166f9c117bf25f5d0b01976af45b5d2d29b2ed059` |
| 文本张量参数量 | 752,393,024；按 GGUF 张量维度统计 |
| 推荐接口 | 支持 `qwen35` 的 llama.cpp / OpenAI 兼容 Chat Completions |

文件哈希记录在 [SHA256SUMS](SHA256SUMS)。该制品提供文本推理权重，使用前核对文件大小和哈希。

## 适用场景

- 分析 H3C Comware 查询、配置和连续操作的潜在影响，辅助核对接口、VLAN、路由及配置管理操作。
- 结合设备型号、系统版本和当前视图，解释命令适用范围与待核对事项。
- 为变更审批、巡检复盘和关联审计提供分析文本。

调用方须明确设备平台，并提供当前视图及必要输出。具体命令语法和效果以目标设备及对应版本手册为准。
模型结果用于提示与审计；命令下发、权限控制和执行前确认由应用处理。

## 本地运行

使用支持 Qwen3.5 / `qwen35` 架构及 GGUF Q8_0 的 llama.cpp。
在模型文件所在目录启动仅监听本机的接口：

```bash
llama-server --model ./h3c-risk-q8_0.gguf --alias h3c-risk \
  --host 127.0.0.1 --port 8083 --ctx-size 2048 --parallel 1 \
  --jinja --reasoning off --reasoning-budget 0
```

在另一个终端请求分析一条只读命令：

```bash
curl --fail http://127.0.0.1:8083/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "h3c-risk",
    "messages": [
      {"role": "system", "content": "你是 H3C Comware 命令风险分析助手。使用 H3C 语义分析给定命令，说明潜在影响和需要核对的信息。"},
      {"role": "user", "content": "设备平台：H3C Comware；当前视图：用户视图；当前命令：display interface brief。请分析风险与影响。"}
    ],
    "temperature": 0,
    "max_tokens": 256,
    "chat_template_kwargs": {"enable_thinking": false}
  }'
```

示例请求只生成分析文本。接入结构化审查时，须在系统提示中约定输出字段，检查 JSON 格式、风险枚举、
命令序号及证据是否有效；解析失败或缺少判断依据时，应用应记录为未判定。

AuditBox 服务器默认只启用 Linux 模型路由。另行启动 H3C 服务后，在 Proxy 和 Inference 使用的环境配置中设置：

```dotenv
AUDITBOX_INFERENCE_PROFILES=linux,h3c_comware
AUDITBOX_H3C_BACKEND_URL=http://127.0.0.1:8083
AUDITBOX_H3C_LLM_MODEL=h3c-risk
AUDITBOX_HISTORY_REVIEW_ENABLED=true
```

重启 Proxy、Inference 并重新连接 H3C 终端。模型服务需要单独管理，不随默认双模型服务启动。本模型适用于 H3C Comware；其他品牌使用各自的规则或模型。

## 微调、转换与评估记录

GGUF 的模型名称及微调字段标识了 H3C 领域版本，当前文件为 Q8_0 推理制品。
本制品对应的基础模型固定 revision、训练数据版本、具体微调方法与超参数、训练硬件及转换工具版本
尚未完整归档，需与具体训练运行核对。

当前没有与该 SHA-256 绑定的独立评测报告，未提供准确率、召回率、误报率或吞吐指标。
GGUF 写入的上下文长度为 262,144；运行示例采用 2,048，本发布未提供长上下文效果和资源占用验证结果。

## 使用边界

模型可能混淆设备型号、命令视图、版本差异或其他品牌的相似语法，风险结果需结合实际设备输出复核。
命令下发、执行成功、已取消和已阻断等状态须分别提供。单次批准只对当前操作生效，后续命令仍需检查。
当前未提供模型置信度的校准结果，本发布未提供设备覆盖范围和风险识别能力的独立评测结果。

## 许可证与来源

本微调制品及模型卡采用 GNU GPL 第 3 版，仅限该版本（`GPL-3.0-only`），完整条款见 [LICENSE](LICENSE)。
基础模型的 Apache 2.0 许可证及 Alibaba Cloud 版权声明保留在 [BASE_MODEL_LICENSE](BASE_MODEL_LICENSE)，
修改与归属说明见 [NOTICE](NOTICE)。训练资料的来源、使用权限及其与本权重的对应关系仍需补齐。
