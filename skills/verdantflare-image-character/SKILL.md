---
name: verdantflare-image-character
description: 生成人物五视图、人物半身照和人物视觉档案卡。人物五视图由一张参考人脸和指定服装生成，是供 H3 或 SD2.5 使用的单张五格设定图；人物半身照特指胸部以上的正脸照；人物视觉档案卡是面向 GPT Image 的 16:9 横版完整角色设定表（含信息栏、等高全身三视图与面部微距矩阵）。三类图像均通过 verdantflare-image 生成并核验。
---

# verdantflare-image-character

## 1. 范围与边界

人物五视图、人物半身照与人物视觉档案卡的取景、参考素材、版式约束和逐格验收归本技能管理。生成与返修统一通过 verdantflare-image 执行。

## 2. 按任务读取

### 2.1 核心能力

- [人物五视图](references/character-five-views.md)
- [人物半身照](references/character-portrait.md)
- [人物视觉档案卡](references/character-sheet.md)

### 2.2 参考资料

| 当前任务               | 读取资料                                     |
| :--------------------- | :------------------------------------------- |
| 判断是否适用、选择分支 | [分类与边界](references/01.base-category.md) |
| 执行操作与失败恢复     | [执行流程](references/01.base-workflow.md)   |
| 核对约束、默认值与例外 | [规则约束](references/01.base-rules.md)      |
| 验证、门禁与交付报告   | [验收标准](references/01.base-acceptance.md) |

先读当前任务对应专册，不要求通读全部专册。

## 3. 工具与命令

按[执行流程](references/01.base-workflow.md)准备参考和提示词，再通过[图像技能](../verdantflare-image/SKILL.md)调用现有 Image MCP。
