---
name: x-verdantflare-short-drama-film-production
description: 从剧本或场景推进 VF 影视段落的资产制作、视频候选与验片；仅需导演方案或分镜计划时使用 x-verdantflare-short-drama-director，不用于广告或歌曲制作。
---

# VerdantFlare Film Production

以用户指定的交付阶段为范围；已有剧本、视觉圣经、批准资产和 Shot 版本是事实源。新项目先确定目标片长、画幅、故事约束和实际项目上下文，缺失但不影响当前阶段的选项写为假设。不要把编剧建议当作用户已经批准的剧本改动。

| 当前任务 | 执行入口 |
| --- | --- |
| 从单场剧本提取有原文证据的人物、道具与动作 | [Local Scene Breakdown Skill](../x-verdantflare-short-drama-local-scene-breakdown/SKILL.md)；人工核对后再规划 |
| 从剧本到场景、Shot 和资产需求 | [制作流程](references/workflow.md)；使用 [短剧 Director Skill](../x-verdantflare-short-drama-director/SKILL.md) 的计划契约与校验器 |
| 角色、造型、场景或分镜图像候选 | [Image Skill](../verdantflare-image/SKILL.md) |
| 已冻结 Generation Unit 的视频提示词、提交、查询和恢复 | [Video Skill](../verdantflare-video/SKILL.md) |
| 已批准歌曲的 30 秒高潮 MV | [Music MV Skill](../verdantflare-music-mv/SKILL.md) |

交付前期方案时输出 `treatment.md`、`plan.json`、资产需求和待决事项；运行 Director 校验器。实际制作时逐阶段交付候选及引用、任务 ID 和检查结果，不把候选写为获批成片。视频调用只通过 Video MCP，模型、route、输入模式和时长以当前宿主能力及 Video Skill 为准。
