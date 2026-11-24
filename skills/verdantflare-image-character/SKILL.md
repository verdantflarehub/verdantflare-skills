---
name: verdantflare-image-character
description: 生成人物五视图、人物半身照和人物视觉档案卡。人物五视图由一张参考人脸和指定服装生成，是供 H3 或 SD2.5 使用的单张五格设定图；人物半身照特指胸部以上的正脸照；人物视觉档案卡是面向 GPT Image 的 16:9 横版完整角色设定表（含信息栏、等高全身三视图与面部微距矩阵）。三类图像均通过 verdantflare-image 生成并核验。
---

# verdantflare-image-character

## 1. 范围与边界

本技能负责人物五视图、人物半身照与人物视觉档案卡（16:9 完整角色设定表）的取景、参考素材组织、版式约束与逐格验收。实际图像生成、编辑、任务恢复、内容分级与 Project 归档统一交由 `verdantflare-image` 执行；本技能不直接调用底层供应商接口，也不负责 MV 导演或视频生成。

## 2. 能力清单与按任务读取

按要执行的原子能力直达对应入口；跨能力参考准备与公共版式红线见 `00.common.rules.md`：

| 原子能力            | 入口文档                                                          |
| :------------------ | :---------------------------------------------------------------- |
| 人物五视图          | [01.five-views](references/01.five-views.entrypoint.md)           |
| 人物半身照          | [02.portrait](references/02.portrait.entrypoint.md)               |
| 16:9 人物视觉档案卡 | [03.character-sheet](references/03.character-sheet.entrypoint.md) |

先读目标能力的 `entrypoint.md`（输入与输出 → 分类 → 工作流 → 规范 → 验收），无需通读其他能力。

跨能力公共文档：[00.common.rules](references/00.common.rules.md)（由各能力入口引用）。

## 3. 工具与命令

按目标能力入口与[公共约束](references/00.common.rules.md)准备参考图和提示词，再通过[图像技能](../verdantflare-image/SKILL.md)调用现有 Image MCP（有参考图时调用 `image.edit`，纯文生图档案卡调用 `image.create`）。
