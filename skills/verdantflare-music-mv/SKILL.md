---
name: verdantflare-music-mv
description: 基于已批准歌曲，导演、审核并装配 30 秒高潮 MV；不用于单张角色图、单镜视频或整首长片。
---

# VerdantFlare Music MV

AI 短剧任务形成阶段成果后默认同步到原 Studio 项目，沿用真实 project_id；用户指定仅本地或只读讨论时遵循其范围。候选、返修与最终结果保留固定版本、修改原因、来源设定和任务记录，按当前 Project / Artifact 契约提交并回读修订；只上传或本地保存不算已同步。响应未知先对账恢复，不重复收费生成。同步不自动批准、选用或发布 World。

范围为 30 秒高潮故事 MV。批准 Master 是音频与时间线事实源；已有授权素材直接复用。只请求某阶段时完成该阶段，不自动重做音乐或整支 MV。

工程归属和固定引用遵循[共同接入规则](../_shared/project-world.md)。根目录 `project.json` 是本领域时钟与编排文档，`.vf/project.json` 是服务维护的通用 ProjectManifest 投影；两者职责不同，均使用 JSON。旧 `project.yaml` 只允许导入，不再生成或作为活跃事实源。人物形象、批准 Master 和动作参考在提交前解析为固定的 World 资产版本或当前 Project 文件。

| 当前操作                                  | 按需读取                                                                                                 |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| 企划、分段、推进、返工或恢复              | [工作流](references/workflow.md)                                                                         |
| 项目结构、B/S/F/GU 命名、时间线或机器清单 | [契约](references/contracts.md)                                                                          |
| 人物四视图、服装、角色卡、参考网格        | [角色资产](references/character-assets.md)；需要生图时再读 [Image Skill](../verdantflare-image/SKILL.md) |
| 到达审核点、收到批准或候选选择            | [审核门](references/review-gates.md)                                                                     |
| 执行已冻结视频生成单元                    | [H3 Skill](../verdantflare-video/SKILL.md)                                                               |
| 生成后 18+ 内容分级评估与标记             | [评估步骤](references/content-rating-step.md)、[评估规范](references/content-rating-rubric.md)           |
| 确实需要制作或修复歌曲                    | [Music Skill](../verdantflare-music/SKILL.md)                                                            |
| 配置问题                                  | [环境说明](../ENVIRONMENT.md)                                                                            |

人物身份、故事/分镜、渲染批次、Shot、Picture Lock/Release 审核按具体资产版本记录；既有批准可复用，不能伪造新的艺术批准。未到门禁前自主完成草案、确定性组装、检查和技术修复。MCP 能力缺失不自动换模型，也不操作 GPU/部署。

生成后内容分级评估（K09 强制步骤）：凡经 Studio MCP 生成新媒体产物（角色视图、服装人台、分镜格、视频 Shot 或歌曲候选），取得产物后、在写入 `review.md`、展示交付摘要或同步到 Project/World 之前，必须按[评估步骤](references/content-rating-step.md)与[评估规范](references/content-rating-rubric.md)完成评估（视频须在本机临时目录按至少每 2 秒 1 帧且全片不少于 8 帧加切镜关键帧抽帧，评估后删除临时帧并在 `reasons` 中记录抽帧数），并调用对应域的 `*.rate_content` 提交分级结果（`general` 与 `nsfw` 均须提交）。判定为 `nsfw` 的产物在摘要与 `review.md` 中只写“已隐藏产物 N 个”，不展示预览、接触表、`ContentRef` 或提示词全文，也不同步到 Studio 项目或登记到 World。

输入清单记录不可变资产与哈希，保留来源和批准版本。候选失败时只重做相关阶段，收费尝试遵循已批准预算；诊断素材和用户资产不作为临时垃圾自动删除。

完整交付要求各审核门通过、批准 Shot 覆盖目标音频区间、成片使用批准 Master，且媒体与字幕检查通过。交付最终视频、审核记录和清单；只完成候选时明确其状态。
