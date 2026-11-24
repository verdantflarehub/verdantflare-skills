---
name: verdantflare-music-mv
description: 基于已批准歌曲，导演、审核并装配音乐 MV（支持 30 秒高潮版与 90 秒+ 全长 TV Version 素材池模式）；不用于单张角色图或单镜独立视频。
---

# VerdantFlare Music MV

## 1. 范围与边界

本技能以已批准 Master WAV 为唯一音频与时间线事实源，负责音乐 MV 的导演企划、角色与分镜视觉资产制作、生成单元编排、逐镜质检与成片装配，覆盖两种模式：**30 秒高潮故事版**（按 Block 逐段精修）与 **90 秒+ 全长 TV Version 素材池模式**（按 `2.5x–3x` 剪辑冗余比拆分为 `10s` 生成单元，支持 `9:16` 全屏与 `16:9` 漫画三联叠画混合画幅）。

AI 短剧与 MV 阶段成果默认同步到原 Studio 项目（沿用真实 `project_id`）；根目录 `project.json` 为本领域时钟与编排文档，`.vf/project.json` 为服务维护的通用 ProjectManifest 投影，旧 `project.yaml` 只允许一次性导入。只请求某阶段时完成该阶段，不自动重做音乐或整支 MV；不用于单张角色图（转 `verdantflare-image` / `verdantflare-crew-character`）、单镜独立视频（转 `verdantflare-video`）或歌曲制作（转 `verdantflare-music`）。MCP 能力缺失不自动换模型，也不操作 GPU/部署。

## 2. 能力清单与按任务读取

按要执行的原子能力直达对应入口；跨模式共享的目录契约、命名规则、核心实战铁律与 K09 分级见 `00.common.rules.md`，两级审核体系与门禁见 `00.common.acceptance.md`：

| 原子能力（能干什么活） | 入口文档                                                              |
| :--------------------- | :-------------------------------------------------------------------- |
| 30s MV                 | [01.mv-30s.entrypoint.md](references/01.mv-30s.entrypoint.md)         |
| TV 素材池              | [02.mv-tv-pool.entrypoint.md](references/02.mv-tv-pool.entrypoint.md) |

先读目标能力的 `entrypoint.md`（输入与输出 → 分类 → 工作流 → 规范 → 验收），无需通读其他能力。跨技能生图读 [verdantflare-image](../verdantflare-image/SKILL.md)（默认 GPT Image 2.6 / `gpt-image-2.6-sunburst`），视频渲染读 [verdantflare-video](../verdantflare-video/SKILL.md)，歌曲修复读 [verdantflare-music](../verdantflare-music/SKILL.md)，配置问题读 [ENVIRONMENT.md](../ENVIRONMENT.md)。

跨能力公共文档：[00.common.rules.md](references/00.common.rules.md)、[00.common.acceptance.md](references/00.common.acceptance.md)（由各能力入口引用）。

## 3. 工具与命令

本技能经宿主 Studio MCP 调用 Project / Artifact / Image / Video / Music 工具链；本地环境加载辅助脚本见 [load_env.py](scripts/load_env.py)，各单元确定性拼图与切片脚本位于项目内 `units/<Block_ID>/source/build/`（如 `compose_inputs.go`、`split_wav.go`）。JSON 机器契约样例见 [schemas.md](references/schemas.md)。
