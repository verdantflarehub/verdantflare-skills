---
name: verdantflare-music
description: 使用 VerdantFlare Music 制作歌曲、翻唱、个人音色或母带；也用于词曲企划和已有音乐项目续作。
---

# VerdantFlare Music

按用户要求完成当前音乐阶段，不将歌词、企划或单项修复扩展成完整生成、训练和母带任务。

| 当前阶段 | 按需读取 |
| --- | --- |
| 企划、原创候选、翻唱、分轨、转换、对齐或返工 | [工作流](references/workflow.md) 的对应阶段 |
| 编写原创企划 | [全局规范](references/全局-审核规范.md)、[歌词规范](references/歌词-审核规范.md)、[编曲规范](references/编曲-审核规范.md)，再按下方曲风路由 |
| 输入输出、命名、目录或质量检查 | [交付契约](references/artifacts.md) |
| 原始人声准备或训练 | [人声准备](references/voice-preparation.md) |
| 候选选择、批准、终审或恢复审核记录 | [审核门](references/review-gates.md) |
| 配置问题 | [环境说明](../ENVIRONMENT.md) |

曲风只加载当前需要的一组：[R&B 歌词](references/歌词-R&B-审核规范.md) / [编曲](references/编曲-R&B-审核规范.md)，[说唱歌词](references/歌词-说唱-审核规范.md) / [编曲](references/编曲-说唱-审核规范.md)，[华语流行摇滚歌词](references/歌词-华语流行摇滚-审核规范.md) / [编曲](references/编曲-华语流行摇滚-审核规范.md)。其他曲风使用通用规范并记录风格假设，不因缺少专属文件停止企划。

仅通过宿主 Music MCP 和受控 Artifact 执行；不管理 GPU、部署或供应商直连。训练使用有权处理且已批准的材料，已批准模型直接复用。技术预审失败自主修复后复验；既有批准不重复询问，缺少新的人声授权或艺术批准时只暂停依赖阶段。

本地准备、格式验证和技术修复可继续，收费重做受已批准次数/预算约束，不覆盖旧资产或伪造 LRC。完成当前请求的产物与验证才结束；完整歌曲须满足交付契约与五个人工审核点，不能用工具成功代替人工音质终审。
