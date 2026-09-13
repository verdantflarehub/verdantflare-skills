---
name: verdantflare-music-mv
description: 基于已批准歌曲，导演、审核并装配 30 秒高潮 MV；不用于单张角色图、单镜视频或整首长片。
---

# VerdantFlare Music MV

范围为 30 秒高潮故事 MV。批准 Master 是音频与时间线事实源；已有授权素材直接复用。只请求某阶段时完成该阶段，不自动重做音乐或整支 MV。

| 当前操作 | 按需读取 |
| --- | --- |
| 企划、分段、推进、返工或恢复 | [工作流](references/workflow.md) |
| 项目结构、B/S/F/GU 命名、时间线或机器清单 | [契约](references/contracts.md) |
| 人物四视图、服装、角色卡、参考网格 | [角色资产](references/character-assets.md)；需要生图时再读 [Image Skill](../verdantflare-image/SKILL.md) |
| 到达审核点、收到批准或候选选择 | [审核门](references/review-gates.md) |
| 执行已冻结视频生成单元 | [H3 Skill](../verdantflare-video-h3/SKILL.md) |
| 确实需要制作或修复歌曲 | [Music Skill](../verdantflare-music/SKILL.md) |
| 配置问题 | [环境说明](../ENVIRONMENT.md) |

人物身份、故事/分镜、渲染批次、Shot、Picture Lock/Release 审核按具体资产版本记录；既有批准可复用，不能伪造新的艺术批准。未到门禁前自主完成草案、确定性组装、检查和技术修复。MCP 能力缺失不自动换模型，也不操作 GPU/部署。

输入清单记录不可变资产与哈希，保留来源和批准版本。候选失败时只重做相关阶段，收费尝试遵循已批准预算；诊断素材和用户资产不作为临时垃圾自动删除。

完整交付要求各审核门通过、批准 Shot 覆盖目标音频区间、成片使用批准 Master，且媒体与字幕检查通过。交付最终视频、审核记录和清单；只完成候选时明确其状态。
