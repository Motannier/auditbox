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
  - linux
  - command-risk-analysis
  - quantized
---

# AuditBox Command Guard 0.8B — GGUF Q8_0

Command Guard 是面向 Linux 运维命令风险分析的 0.8B 级领域微调模型。
模型根据调用方提供的命令、工作目录、Shell 和近期操作上下文，生成潜在影响、风险原因及核对建议。
在 AuditBox 中，它用于 Linux 终端的旁路审查，为操作员和审计记录提供分析结果。

## 模型与文件

| 项目 | 内容 |
| --- | --- |
| 基础模型 | [Qwen/Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B) |
| 架构 | `qwen35`，24 层，隐藏维度 1024 |
| 微调标识 | GGUF 记录为 `Qwen3.5 0.8B Lora`，`general.finetune=lora` |
| 制品 | `command-guard-q8_0.gguf`，GGUF v3，Q8_0 |
| 文件大小 | 811,843,008 字节 |
| SHA-256 | `7497a4aece77fd790fb1b035260daf926d39196fdfac46b4771dcfe955d6954d` |
| 文本张量参数量 | 752,393,024；按 GGUF 张量维度统计 |
| 推荐接口 | 支持 `qwen35` 的 llama.cpp / OpenAI 兼容 Chat Completions |

文件哈希记录在 [SHA256SUMS](https://huggingface.co/Motanni/auditbox-command-guard-0.8b-gguf/blob/main/SHA256SUMS)。该制品提供文本推理权重，使用前核对文件大小和哈希。

## 适用场景

- 分析 Linux 命令及连续操作的潜在影响，辅助核对文件操作、服务变更、权限和网络配置。
- 将当前目录、变量、别名及历史操作作为判断上下文，解释命令的实际作用范围。
- 为人工审批、操作复盘和运维审计提供分析文本。

调用方负责提供当前设备状态。历史记录可能过时，命令已下发、已取消、已阻断和执行失败等状态须分别标注。
模型结果用于提示与审计；命令下发、权限控制和执行前确认由应用处理。

## 本地运行

使用支持 Qwen3.5 / `qwen35` 架构及 GGUF Q8_0 的 llama.cpp。
在模型文件所在目录启动仅监听本机的接口：

```bash
llama-server --model ./command-guard-q8_0.gguf --alias command-guard \
  --host 127.0.0.1 --port 18082 --ctx-size 10240 --parallel 1 \
  --jinja --reasoning off --reasoning-budget 0
```

在另一个终端请求分析一条只读命令：

```bash
curl --fail http://127.0.0.1:18082/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "command-guard",
    "messages": [
      {"role": "system", "content": "你是 Linux 运维命令风险分析助手。根据给定上下文说明命令的潜在影响和判断依据。"},
      {"role": "user", "content": "工作目录：/tmp/auditbox-demo；Shell：bash；当前命令：df -h。请分析风险与影响。"}
    ],
    "temperature": 0,
    "max_tokens": 256,
    "chat_template_kwargs": {"enable_thinking": false}
  }'
```

示例请求只生成分析文本。接入结构化审查时，须在系统提示中约定输出字段，检查 JSON 格式、风险枚举、
命令序号及证据是否有效；解析失败或缺少判断依据时，应用应记录为未判定。

## 微调、转换与评估记录

GGUF 的模型名称及微调字段记录了 LoRA 来源，文件当前为合并后的 Q8_0 推理制品。
本制品对应的基础模型固定 revision、训练数据版本、LoRA rank/alpha、学习率、训练轮数、
训练硬件和转换工具版本尚未完整归档，需与具体训练运行核对。

当前没有与该 SHA-256 绑定的独立评测报告，未提供准确率、召回率、误报率或吞吐指标。
GGUF 写入的上下文长度为 262,144；运行示例采用 10,240，本发布未提供长上下文效果和资源占用验证结果。

## 使用边界

模型可能误判风险、遗漏跨命令关联、混淆已下发与已生效状态，或返回格式不完整的结果。
风险分级需要结合确定性规则、实际设备输出和人工复核。当前未提供模型置信度的校准结果。
本模型面向 Linux 文本命令分析，其他平台和视觉输入需使用对应模型与运行链路。

## 许可证与来源

本微调制品及模型卡采用 GNU GPL 第 3 版，仅限该版本（`GPL-3.0-only`），完整条款见 [LICENSE](https://huggingface.co/Motanni/auditbox-command-guard-0.8b-gguf/blob/main/LICENSE)。
基础模型的 Apache 2.0 许可证及 Alibaba Cloud 版权声明保留在 [BASE_MODEL_LICENSE](https://huggingface.co/Motanni/auditbox-command-guard-0.8b-gguf/blob/main/BASE_MODEL_LICENSE)，
修改与归属说明见 [NOTICE](https://huggingface.co/Motanni/auditbox-command-guard-0.8b-gguf/blob/main/NOTICE)。训练数据及教师输出的来源与使用权限仍需随训练记录补齐。
