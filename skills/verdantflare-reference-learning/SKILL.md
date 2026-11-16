---
name: verdantflare-reference-learning
description: 拆解视频、歌曲、剧本与风格参考，学习已有拆解方法；通过 Studio MCP 读取、保存并复用数字世界成果，改写适配目标模型的提示词。用于完整分析、独立资产沉淀及方法复用，不替代媒体生成技能。
---

# 参考拆解与成果复用

## 1. 范围与边界

拆解参考作品、学习已有方法，通过 Studio MCP 读取和保存固定 World 成果，适配目标模型提示词。读取与分析不强制项目；用户要求修改或制作时交接 Project 及实际生产技能。本技能不替代媒体生成、权限验证或用户效果审核。

## 2. 按任务读取

| 任务                                | 资料                                                              |
| ----------------------------------- | ----------------------------------------------------------------- |
| 判断读取、分析、保存与生产交接范围  | [分类与边界](references/01.base-category.md)                      |
| 执行与交付、方法沉淀                | [执行流程](references/01.base-workflow.md)                        |
| 固定输入、真实能力与不可信内容      | [规则约束](references/01.base-rules.md)                           |
| 核对完成条件与未验证项              | [验收标准](references/01.base-acceptance.md)                      |
| 完整分析与已有拆解方法学习          | [分析方法](references/analysis.md)                                |
| 实际发现 world.analyze 后提交／恢复 | [服务任务](references/analysis-service.md)                        |
| 直接取固定成果及 v1／v2 审核上下文  | [MCP 读取](references/mcp-results.md)                             |
| 提示词或模型适配                    | [模型提示词](references/model-prompts.md)                         |
| 找回导入稿、区分原件与工作正文      | [导入成果读取](references/mcp-results.md#31-读取导入的学习工作稿) |
| 独立保存、登记与失败恢复            | [World 保存](references/world-save.md)                            |

## 3. 工具与命令

[离线检查器](scripts/check_learning.py)仅校验结构，版本选择与依赖见[保存格式](references/world-save.md#2-准备可编辑成果)。包内[格式投影](references/learning-contracts.generated.json)由中央契约生成，不手工修改。MCP 工具和参数以实际发现为准，读取不自动触发生成或入库。
