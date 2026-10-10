# VerdantFlare Film Production

以用户指定的交付阶段为范围；已有剧本、视觉圣经、批准资产和 Shot 版本是事实源。新项目先确定目标片长、画幅、故事约束和实际项目上下文，缺失但不影响当前阶段的选项写为假设。不要把编剧建议当作用户已经批准的剧本改动。

| 当前任务 | 执行入口 |
| --- | --- |
| 新短剧立项、全流程顺序和跨阶段输入输出 | [短剧端到端制作模板](series-workflow-template.md)；正式项目 ID 与本地引用分开记录 |
| 完整剧本包创作、修订或配套设定补齐 | [完整剧本 Skill](../writing/guide.md)；六类文字文档、版本索引和审核记录先于下游设计交接 |
| 剧本或人物小传的锁稿前审核与返修 | [剧本审核 Skill](../review/guide.md)；发现与人工决定绑定具体版本 |
| 从单场剧本提取有原文证据的人物、道具与动作 | [Local Scene Breakdown Skill](../local-breakdown/guide.md)；人工核对后再规划 |
| 从剧本到场景、Shot 和资产需求 | [制作流程](workflow.md)；使用 [短剧 Director Skill](../directing/guide.md) 的计划契约与校验器 |
| 场景、道具、服装分别沉淀为跨集一致性资产 | [数字资产圣经模板](asset-bible-template.md)；地点本体、场次变体、道具状态、角色 Look 和逐镜引用分别记录 |
| 角色、造型、场景或分镜图像候选 | [Image Skill](https://github.com/verdantflarehub/verdantflare-skills/blob/3c5a282fc4e8c30bda3bbfeef173f43d543bf93d/skills/verdantflare-image/SKILL.md) |
| 已冻结 Generation Unit 的视频提示词、提交、查询和恢复 | [Video Skill](https://github.com/verdantflarehub/verdantflare-skills/blob/3c5a282fc4e8c30bda3bbfeef173f43d543bf93d/skills/verdantflare-video/SKILL.md) |
| 已批准歌曲的 30 秒高潮 MV | [Music MV Skill](https://github.com/verdantflarehub/verdantflare-skills/blob/3c5a282fc4e8c30bda3bbfeef173f43d543bf93d/skills/verdantflare-music-mv/SKILL.md) |

交付前期方案时输出 `treatment.md`、`plan.json`、资产需求和待决事项；运行 Director 校验器。剧本审核的 P0/P1 只阻断受影响的锁稿、Shot 和生成任务，不妨碍无依赖部分的整理。实际制作时逐阶段交付候选及引用、任务 ID 和检查结果，不把候选写为获批成片。视频调用只通过 Video MCP，模型、route、输入模式和时长以当前宿主能力及 Video Skill 为准。
