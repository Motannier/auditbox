# 第三方来源与许可记录

本文件记录第三方组件的来源与许可信息。项目代码采用 [GNU GPL 第 3 版](LICENSE)（`GPL-3.0-only`），第三方组件适用各自条款；公开再分发权限须单独核实。

## Python 与前端依赖

依赖版本由 `box/uv.lock`、`agent/uv.lock` 和 `web/package-lock.json` 固定。安装或分发依赖时，保留各组件附带的版权、许可证和 NOTICE 文件。

引入或更新依赖时，同时检查 Python wheel 捆绑的本地库和 npm 包中的许可声明。Agent 的可选 `knowledge`、`office` 和 `dev` 依赖、系统软件及容器组件均遵循各自条款。

## 随源码提供的 Skill

| 材料 | 固定来源 | 当前许可记录 |
| --- | --- | --- |
| `agent/defaults/skills/skill-creator/` | ModelScope `@anthropics/skill-creator`，commit `068e9da79f1954f950f038c9e1b4104cd43abeac` | 原始 [Apache 2.0 LICENSE.txt](agent/defaults/skills/skill-creator/LICENSE.txt) 已保留 |
| `agent/defaults/skills/skill-vetter/` | ModelScope `@spclaudehome/skill-vetter`，commit `05e1e78ed66d6e59d923e758662aa207f859bd2b` | 当前副本未附独立许可证；公开分发前须确认授权 |

来源与兼容性适配见 [Skill 来源记录](agent/defaults/skills/SOURCES.md)。缺失的许可证和授权记录须单独补齐。

## 模型与运行时

| 材料 | 来源与处理 |
| --- | --- |
| MiniCPM5 2B GGUF | 官方 `openbmb/MiniCPM5-2B-GGUF`，来源许可证记录为 Apache 2.0；固定 revision、文件哈希和来源链接见 [模型清单](models/manifest.json)。权重独立保存，分发时须保留模型卡和适用声明 |
| Linux command-guard GGUF | Qwen3.5-0.8B 领域微调的 Q8_0 制品；采用 GPL-3.0-only，保留上游 Apache 2.0 许可和归属说明，见 [模型卡](models/model-cards/command-guard/README.md)。具体训练运行、数据授权及转换记录仍需补齐 |
| H3C h3c-risk GGUF | Qwen3.5-0.8B 领域微调的 Q8_0 制品；采用 GPL-3.0-only，保留上游 Apache 2.0 许可和归属说明，见 [模型卡](models/model-cards/h3c-risk/README.md)。具体训练运行、数据授权及转换记录仍需补齐 |
| llama.cpp CUDA 容器 | Compose 使用 `ghcr.io/ggml-org/llama.cpp` 的固定摘要；镜像单独获取，镜像内部组件及 GPU 运行时分发条款尚待核对 |

模型来源、文件校验及导入方式见 [模型说明](models/README.md)。

## 设备命令数据与界面素材

- `box/auditbox/reference/data/`、`box/auditbox/security/data/` 的压缩 JSON 及配套依赖包中的设备能力数据供运行时使用。部分数据由厂商手册或其他资料派生，构建工具位于 `tools/`；原始资料版本与公开再分发权限尚待逐项核对。
- 页面图片 `web/src/assets/login-illustration.png` 的来源及公开使用权记录尚未提供。
- 分发网页时应保留前端依赖和图片适用的第三方声明。

分发时，按实际组件与制品范围保留适用的版权、NOTICE 及许可证正文。
