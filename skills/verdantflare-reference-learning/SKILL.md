---
name: verdantflare-reference-learning
description: 拆解视频、歌曲、剧本与风格参考，学习已有拆解方法；通过 Studio MCP 读取、保存并复用数字世界成果，改写适配目标模型的提示词。用于完整分析、独立资产沉淀及方法复用，不替代媒体生成技能。
---

# 参考拆解与成果复用（verdantflare-reference-learning）

## 1. 范围与边界

本技能负责拆解视频、歌曲、剧本与风格参考作品，学习已有拆解方法，通过 Studio MCP 读取和保存固定 World 成果，并将共同创作意图改写适配为目标模型提示词。读取与分析不强制绑定 Project；用户明确要求修改提示词、改图或制作时，先交接 `verdantflare-project` 新建真实项目，再由对应生产技能（`x-verdantflare-short-drama`、`verdantflare-video`、`verdantflare-image`、`verdantflare-music`、`verdantflare-speech` 或工程技能）经 Studio MCP 执行。本技能不替代媒体生成、权限验证或用户效果审核。

## 2. 能力清单与按任务读取

按要执行的原子能力直达对应入口；跨能力 MCP 读取边界、固定输入与方法复用规则见 `00.common.rules.md`：

| 原子能力（能干什么活） | 入口文档                                                                        |
| :--------------------- | :------------------------------------------------------------------------------ |
| 媒体拆解               | [01.media-breakdown.entrypoint.md](references/01.media-breakdown.entrypoint.md) |
| 世界归档               | [02.world-asset.entrypoint.md](references/02.world-asset.entrypoint.md)         |
| 提示词改写             | [03.prompt-adapt.entrypoint.md](references/03.prompt-adapt.entrypoint.md)       |

先读目标能力的 `entrypoint.md`；同一请求可组合拆解、读取、适配与保存，但“读取／复制”不自动触发生成，“入库”不等于安装技能。

跨能力公共文档：[00.common.rules.md](references/00.common.rules.md)（由各能力入口引用）。

## 3. 工具与命令

包内[离线检查器](scripts/check_learning.py)仅校验本地 JSON 结构（拒绝重复键与非有限数字），[中央格式投影](references/learning-contracts.generated.json)由中央契约生成、不手工修改。在具备 Python 3.10+、`jsonschema` 4、`referencing` 的环境中从技能根目录执行：

```text
python3 scripts/check_learning.py --input path/to/learning-v1.json
python3 scripts/check_learning.py --schema reference-learning-v2 --input path/to/learning-v2.json
python3 scripts/check_learning.py --schema world-draft-manifest --input path/to/manifest.json
```

MCP 工具与参数以运行时 `tools/list` 实际发现为准，读取不自动触发生成或入库；完整校验与服务提交流程见各能力入口。
