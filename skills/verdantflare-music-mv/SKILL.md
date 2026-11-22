---
name: verdantflare-music-mv
description: 基于已批准歌曲，导演、审核并装配音乐 MV（支持 30 秒高潮版与 90 秒+ 全长 TV Version 素材池模式）；不用于单张角色图或单镜独立视频。
---

# VerdantFlare Music MV

AI 短剧与 MV 任务形成阶段成果后默认同步到原 Studio 项目，沿用真实 project_id；用户指定仅本地或只读讨论时遵循其范围。候选、返修与最终结果保留固定版本、修改原因、来源设定和任务记录，按当前 Project / Artifact 契约提交并回读修订；只上传或本地保存不算已同步。响应未知先对账恢复，不重复收费生成。同步不自动批准、选用或发布 World。

支持两种制作模式：**30 秒高潮故事版**（按 Block 逐段精修）与 **全长 TV Version 素材池模式**（如 `90s+` 整曲，按 `2.5x–3x` 剪辑冗余比拆分为 `10s` 生成单元，支持 `9:16` 竖屏全屏与 `16:9` 漫画三横屏竖排叠画 Triptych 混合画幅）。批准 Master 是音频与时间线事实源；已有授权素材直接复用。只请求某阶段时完成该阶段，不自动重做音乐或整支 MV。

工程归属和固定引用遵循[共同接入规则](../_shared/project-world.md)。根目录 `project.json` 是本领域时钟与编排文档，`.vf/project.json` 是服务维护的通用 ProjectManifest 投影；两者职责不同，均使用 JSON。旧 `project.yaml` 只允许导入，不再生成或作为活跃事实源。人物形象、批准 Master 和场景/动作参考在提交前解析为固定的 World 资产版本或当前 Project 文件。

## 核心实战铁律（全模式强制）

1. **人物五视图与多套造型分工**：采用 `verdantflare-image-character` 统一五视图（上三格无头全身正/90°侧/背 + 下两格正脸/60°侧脸）冻结 `visualbible.md`。同一人物因剧情换装（如校园日常装 `CHAR-02A` / 球场短装 `CHAR-02B`）时，必须以已冻结主造型为基准派生新五视图并单独审定；严禁未经批准引入额外角色，多角色剧情严禁双正脸同框（防串脸，改用单正脸、OTS 正反打、远景背影或漫画三联分格）。
2. **动漫/影视翻唱 MV 原作分镜致敬**：凡翻唱动漫、电影或剧集主题曲，必须在 `storyboard.md` 中显式检索并规划 **3–6 个原作 OP/ED 或漫画/剧集经典分镜致敬锚点**，禁止拍成泛泛的风景街拍。
3. **Google Omni Flash 1.2 多参考范式与零遗漏 7 槽位矩阵**：采用 `route=google-flow`、`model=omni-1.2-flash`（`upscale="1080p"`）制作时，**彻底禁止设计“人物悬空/失衡动态静态关键帧”**，且不得传 `first_frame/last_frame` 或参考音频；必须建立**共享纯场景空景母图库（`scenes/`，同空间不同时段由母图 `image.edit` 派生以锁死建筑几何）**，并按 7 槽位逐项核对确保零遗漏，详见 [Omni 逐镜规范](references/omni-shot-spec.md)。
4. **景别节奏与局部特写铁律**：**严禁整镜从头到尾只用全身/全景平拍**！10s 动作或情绪单元必须编排局部特写与景别起伏（如球鞋急停特写、运动腿部发力特写、胸部以上运球出击近景、压腕/手部道具特写等），兼顾电影张力与人脸清晰度。
5. **逐镜专属审核文档（Shot Review Sheet）**：每个生成单元独立维护 `shots/<GU_ID>/review.md` 与 `spec.json`，完整呈现**① 输入参考（图片/视频直显与 Artifact ID）+ ② 完整提示词 + ③ 机器参数 + ④ 输出视频与逐秒接触表 + ⑤ 追加式人工审核记录**；输入侧过审后再调用视频模型渲染，成片过审后再入剪辑池。

| 当前操作                                  | 按需读取                                                                                                 |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Omni 1.2 多参考、7 槽位与逐镜审核单模板   | [Omni 逐镜规范](references/omni-shot-spec.md)                                                            |
| 企划、分段、推进、返工或恢复              | [工作流](references/workflow.md)                                                                         |
| 项目结构、B/S/F/GU 命名、时间线或机器清单 | [契约](references/contracts.md)                                                                          |
| 人物五视图、服装、角色卡、参考网格        | [角色资产](references/character-assets.md)；需要生图时再读 [Image Skill](../verdantflare-image/SKILL.md)（默认出图模型 GPT Image 2.6 / `gpt-image-2.6-sunburst`） |
| 到达审核点、收到批准或候选选择            | [审核门](references/review-gates.md)                                                                     |
| 执行已冻结视频生成单元（Omni 1.2 / H3）   | [Video Skill](../verdantflare-video/SKILL.md)（Google Omni Flash 1.2 必读 `references/google-flow.md`）  |
| 生成后 18+ 内容分级评估与标记             | [评估步骤](references/content-rating-step.md)、[评估规范](references/content-rating-rubric.md)           |
| 确实需要制作或修复歌曲                    | [Music Skill](../verdantflare-music/SKILL.md)                                                            |
| 配置问题                                  | [环境说明](../ENVIRONMENT.md)                                                                            |

人物身份、故事/分镜、渲染批次、Shot、Picture Lock/Release 审核按具体资产版本记录；既有批准可复用，不能伪造新的艺术批准。未到门禁前自主完成草案、确定性组装、检查和技术修复。MCP 能力缺失不自动换模型，也不操作 GPU/部署。

生成后内容分级评估（K09 强制步骤）：凡经 Studio MCP 生成新媒体产物（角色视图、服装人台、分镜格、视频 Shot 或歌曲候选），取得产物后、在写入 `review.md`、展示交付摘要或同步到 Project/World 之前，必须按[评估步骤](references/content-rating-step.md)与[评估规范](references/content-rating-rubric.md)完成评估（视频须在本机临时目录按至少每 2 秒 1 帧且全片不少于 8 帧加切镜关键帧抽帧，评估后删除临时帧并在 `reasons` 中记录抽帧数），并调用对应域的 `*.rate_content` 提交分级结果（`general` 与 `nsfw` 均须提交）。判定为 `nsfw` 的产物在摘要与 `review.md` 中只写“已隐藏产物 N 个”，不展示预览、接触表、`ContentRef` 或提示词全文，也不同步到 Studio 项目或登记到 World。

输入清单记录不可变资产与哈希，保留来源和批准版本。候选失败时只重做相关阶段，收费尝试遵循已批准预算；诊断素材和用户资产不作为临时垃圾自动删除。

完整交付要求各审核门通过、批准 Shot 覆盖目标音频区间、成片使用批准 Master，且媒体与字幕检查通过。交付最终视频、审核记录和清单；只完成候选时明确其状态。
