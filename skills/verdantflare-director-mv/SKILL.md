---
name: verdantflare-director-mv
description: 音乐 MV 作品统筹导演技能（Director），以已批准 Master WAV 为唯一时间线事实源，统筹 MV 全局视觉母题、角色/空间一致性、多工种隔离派单、逐镜审核与成片装配（支持 30 秒高潮版与 90 秒+ 全长 TV Version 素材池模式）；不用于单张角色图、单镜独立视频或歌曲制作。
---

# 音乐 MV 导演（verdantflare-director-mv）

## 1. 范围与边界

本技能位于电影工业技能体系顶层（`director`），以已批准 Master WAV 为唯一音频与时间线事实源，负责音乐 MV 的全局导演企划、视觉母题与一致性锁定、多工种隔离派单、逐镜审核门禁与成片装配，覆盖两种作品形态：**30 秒高潮故事版**（按 Block 逐段精修）与 **90 秒+ 全长 TV Version 素材池模式**（按 `2.5x–3x` 剪辑冗余比拆分为 `10s` 生成单元，支持 `9:16` 全屏与 `16:9` 漫画三联叠画混合画幅）。

**导演层上下文与 Session 隔离红线**：本技能只管全局总盘、节拍对位、联合一致性基线、派单交接与终审装配，严禁陷入底层模型参数、LoRA 权重或单镜细碎返修泥潭——角色视觉设定与多角参考库交由[角色设计专员](../verdantflare-crew-character/SKILL.md)，单镜出图与视频渲染按能力入口委托[图像技能](../verdantflare-image/SKILL.md)与[视频技能](../verdantflare-video/SKILL.md)，歌曲修复交由[音乐技能](../verdantflare-music/SKILL.md)。AI 短剧与 MV 阶段成果默认同步到原 Studio 项目（沿用真实 `project_id`）；根目录 `project.json` 为本领域时钟与编排文档，`.vf/project.json` 为服务维护的通用 ProjectManifest 投影。MCP 能力缺失不自动换模型，也不操作 GPU/部署。

## 2. 能力清单与按任务读取

按目标 MV 作品形态直达对应入口：

| 原子能力  | 入口文档                                                              |
| :-------- | :-------------------------------------------------------------------- |
| 30s MV    | [01.mv-30s.entrypoint.md](references/01.mv-30s.entrypoint.md)         |
| TV 素材池 | [02.mv-tv-pool.entrypoint.md](references/02.mv-tv-pool.entrypoint.md) |

先读目标形态的 `entrypoint.md`（输入与输出 → 分类 → 工作流 → 规范 → 验收），无需通读其他形态；跨模式共享的目录契约、实战铁律与 K09 分级见 [00.common.rules.md](references/00.common.rules.md)，两级审核体系与门禁见 [00.common.acceptance.md](references/00.common.acceptance.md)（由各能力入口引用），配置排查见 [ENVIRONMENT.md](../ENVIRONMENT.md)。

## 3. 工具与命令

本技能经宿主 Studio MCP 调度 Project / Artifact / Image / Video / Music 工具链；本地环境加载辅助脚本见 [load_env.py](scripts/load_env.py)，各单元确定性拼图与切片脚本位于项目内 `units/<Block_ID>/source/build/`（如 `compose_inputs.go`、`split_wav.go`）。JSON 机器契约样例见 [schemas.md](references/schemas.md)。
