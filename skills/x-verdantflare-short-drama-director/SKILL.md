---
name: x-verdantflare-short-drama-director
description: 将剧本、故事概念或广告 brief 编成 VF 导演方案、分镜时间线、资产需求和生成单元提案；用于前期策划与分镜返修，不负责调用生图、生视频或音乐工具。
---

# VerdantFlare Director

交付可审阅的 `treatment.md` 与可校验的 `plan.json`。先保留用户给定的故事、品牌事实、目标时长和画幅；缺失且会改变叙事或交付的决定才提问，其余写入假设。已有项目以批准版本为事实源，返修只改受影响的 Shot 和引用，不宣称新版本已获批准。

按任务读取：

| 任务 | 参考 |
| --- | --- |
| 剧本、梗概、场景改成分镜 | [导演拆解](references/storyboard.md) |
| 广告 brief、TVC 或品牌短片 | [广告导演](references/advertising.md) |
| 编写或检查机器交接文件 | [计划契约](references/plan-contract.md) |

先确定每个 Shot 的叙事目的、主要动作、起落状态和连续性，再写相机与声音。Shot 是剪辑镜头；Generation Unit 是模型请求提案，只有已知目标模型和调用约束时才编排。提交 Image / Video MCP 前交由相应 Skill 处理参数、授权、生成和恢复；音乐 MV 的既有审核与时间线由 `verdantflare-music-mv` 管理。

交付前运行 `python3 scripts/validate_plan.py <plan.json>`，修复结构、时码和引用错误。校验通过只表示计划内部一致；画面可行性、品牌声明和艺术选择仍需按项目流程审阅。不要把第三方 Skill 文件或平台专用模型指令直接作为 VF 的执行契约。
