---
name: verdantflare-crew-character
description: 剧组角色设计专员（Crew），负责角色全链路视觉设计与定妆资产交付：人物五视图、人物半身照、16:9 人物视觉档案卡、视频抽帧锁脸多角度参考库以及回头/高低机位补图与表情肤质局部修复。遵循三层信息隔离契约，零底层模型权重渗透，统一委托 verdantflare-image / verdantflare-video / verdantflare-project 执行生产与归档。
---

# 角色设计专员（verdantflare-crew-character）

## 1. 范围与边界

本技能是剧组专职**角色视觉设计与定妆资产交付**的业务专员（`crew`），覆盖从定妆母版到多角度锁脸参考库的完整工种闭环：人物五视图、人物半身照、16:9 人物视觉档案卡、视频取姿态抽帧与正脸纠正的多角度参考库（含“半身照＋无头三视图”服装设定页），以及回头/高低机位补图、局部表情或肤质修复与 Studio 项目归档。不用于精确三维重建、一般单图美化或 MV/短剧总导演统筹。

遵循三层信息隔离契约：人类只提角色意图、风格与首轮定妆母版审定意见；本专员负责版式约束、参考职责分离、逻辑模型选型（默认委托 `verdantflare-image` 的 GPT Image 路线，视频取姿态按需委托 Studio 版 `verdantflare-video`）、失败兜底与逐格验收；底模权重、LoRA 配比、节点编号与渠道网关严格封死在底层原子技能内。角色首轮定妆母版必须先经人类审定通过后方可批量衍生。

## 2. 能力清单与按任务读取

按对外独立交付的角色资产类型直达对应入口：

| 原子能力   | 入口文档                                                                        |
| :--------- | :------------------------------------------------------------------------------ |
| 人物五视图 | [01.five-views.entrypoint.md](references/01.five-views.entrypoint.md)           |
| 人物半身照 | [02.portrait.entrypoint.md](references/02.portrait.entrypoint.md)               |
| 人物档案卡 | [03.character-sheet.entrypoint.md](references/03.character-sheet.entrypoint.md) |
| 多角参考库 | [04.multi-angle-lib.entrypoint.md](references/04.multi-angle-lib.entrypoint.md) |
| 补图精修   | [05.local-repair.entrypoint.md](references/05.local-repair.entrypoint.md)       |

先读目标能力的 `entrypoint.md`（输入与输出 → 分类 → 工作流 → 规范 → 验收），无需通读其他能力；跨能力参考组织、坐标与锁脸红线、首轮母版检查点及 `audit_images.py` 技术审计规则见 [00.common.rules.md](references/00.common.rules.md)（由各能力入口引用）。

## 3. 工具与命令

出图、视频取姿态与项目归档分别委托[图像技能](../verdantflare-image/SKILL.md)、[视频技能](../verdantflare-video/SKILL.md)、[资产技能](../verdantflare-artifact/SKILL.md)与[项目技能](../verdantflare-project/SKILL.md)。包内[图片取证脚本](scripts/audit_images.py)（Python 3.10+ 与 Pillow）用于对清单实测解码尺寸、SHA-256、编号重复与缺图：

```text
python -X utf8 scripts/audit_images.py /path/to/manifest.json --output /path/to/audit.json
```
