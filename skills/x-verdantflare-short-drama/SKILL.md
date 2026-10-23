---
name: x-verdantflare-short-drama
description: VF 短剧、影视段落与宣传片的工作入口，完成剧本、导演分镜、执行方案、分批制作、审核返修及已有项目续作；实际图像、视频与声音调用交由对应媒体 Skill。
metadata:
  version: "0.2.0"
---

# VerdantFlare 短剧

命名约定：`x-` 是作者标记；目录名、frontmatter 的 `name`、调用名及后续重构均须保留此前缀。

按用户当前任务选阶段，只读取该阶段资料。写剧本不要求先读视频流程；用户已授权返修时直接改稿并同步配套设定，复审后交付。已有版本、素材及人工选择持续有效；结构整理不重新生成媒体，也不重提状态未知的任务。

## 1. 阶段入口

| 当前交付 | 读取入口 |
| --- | --- |
| 新短剧全流程立项、完整制作顺序与交接 | [全流程](references/production/series-workflow-template.md) |
| 宣传片执行方案、多版本续作与分批交付 | [执行方案](references/production/execution-planning.md) |
| 写作、改稿、补齐完整剧本包 | [编剧](references/writing/guide.md) |
| 只审核、复审、核对问题 | [审核](references/review/guide.md) |
| 导演方案、分镜、相机和时间线 | [导演](references/directing/guide.md) |
| 资产候选、镜头制作与验片 | [制作](references/production/guide.md) |
| 单镜动作、情绪、对白表演 | [表演](references/performance/guide.md) |
| 主要角色资产分拆、评分状态与一致性参考包 | [角色一致性包](references/production/character-consistency-package.md) |
| 角色卡微表情、克制情绪与表情僵硬返修 | [微表情](references/performance/microexpressions.md) |
| 参考声线、独立对白、旁白与单句返修 | [对白交接](references/performance/dialogue.md) |
| 生成后 18+ 内容分级评估与标记 | [评估步骤](references/content-rating-step.md)、[评估规范](references/content-rating-rubric.md) |
| 明确要求本机 Ollama 提取原文事实 | [本机拆解](references/local-breakdown/guide.md) |
| 品牌片、产品广告的声明与制作适配 | [广告适配](references/commercial/guide.md) |

## 2. 完整剧本交付

完整剧本包必须交付六类独立文档：剧本正文、人物小传、地点与场次、道具、服装 Look、逐镜连续性；另附固定版本索引和审核返修记录。正文要有可表演动作、完整对白、时空、站位、氛围及音乐/环境声意图。字段与模板分别见[剧本包规范](references/writing/script-package-standard.md)和[交付模板](references/writing/delivery-templates.md)。

只要求梗概、单场或局部润色时遵循请求范围；不能将局部成果冒称完整剧本包。细节可自行决定的直接写明制作设定，实际媒体才能验证的项记录待验，不以文本检查代替人工批准或市场留存数据。

## 3. 模块与工具的边界

模块是此 Skill 内按需读取的说明，不是七个需要分别安装或调用的 Skill。角色身份、Look、地点本体、场次与道具实物各自保留稳定 ID；导演拆合镜头时维护映射，不能切断连续性引用。

本 Skill 目录是脚本命令的工作目录：

- 导演计划校验：`python3 scripts/validate_plan.py <plan.json>`。
- 本机单场提取：`python3 scripts/breakdown.py --input <scene.txt> --output <breakdown.json>`。仅在用户要求本机提取时使用；模型已安装且服务可用才执行，不自动下载模型。

媒体调用仍使用当前宿主的 Image / Video / Music / Speech 能力和对应 Skill。项目保存与不可变内容传输使用现行 Project / Artifact 契约；AI 短剧阶段成果按下节同步到 Studio；项目归档不代表创作批准或远端公开发行。技术校验、创作选版和正式发行分别记录。

### 3.1 短剧白模制作要求

本工作流按用户确定的制作标准，为每个场景、全部镜头与复杂道具交付 Blender 白模。进入空间设计或镜头制作时读取[白模规范](references/production/blender-whitebox.md)。仅写剧本或制作人物身份图不强制先完成建模；已有白模按固定版本复用，每镜定位相机与帧范围，不能把预览图当成可编辑工程。当场景白模或精模需供网页片场查看器（`viewer`）三维审片与核对机位时，按[白模规范第 2.2 节](references/production/blender-whitebox.md#22-片场包set-package-v10导出与机位对齐校验)导出 `sets/<package_id>/`（含 `set.json`、`scene.glb`、`poster.png`、`cameras/CAM_*.png`），并通过 `validate_set_package.py` 与 `check_camera_alignment.py` 校验后归档为 Project 固定修订。

场景照片、正反打、人物站位、关键帧确认和视频返修遵循[场景—关键帧—视频规范](references/production/scene-keyframe-video.md)。Blender 默认只固定完整空间、人物关键位置／朝向、接触锚点与摄影机运镜；正式关键帧锁定身份、状态和动作起落，视频模型完成自然连续动作与表情。精细骨骼动画和逐帧肢体预演不是生成前置要求；只有明确需要精确动作控制且模型实际支持时才追加。白模不保证生成结果严格遵守几何，视频仍须动态验片。

### 3.2 Studio 阶段归档与生成后内容分级评估

AI 短剧项目内容默认同步到同一 Studio 项目，读取[阶段同步与恢复](references/studio-sync.md)。用户明确要求仅本地、只讨论或仅审阅而不产生项目成果时遵循该范围。写作、返修、素材候选、白模、镜头与交付分别在形成可保存版本后归档；同步候选无需等待创作选版。

调用 Image、Speech、Music、Video 生成新媒体产物后，在交付摘要展示与 Studio 项目同步前，必须先按[评估步骤](references/content-rating-step.md)与[评估规范](references/content-rating-rubric.md)完成 18+ 内容分级评估并调用对应域的 `*.rate_content`（`general` 与 `nsfw` 均须提交）。判定为 `nsfw` 的产物严禁同步到 Studio 项目或登记到 World，在阶段记录与交付摘要中只写“已隐藏产物 N 个”，不展示预览、链接、`ContentRef` 或提示词全文，由用户决定是否改写提示词后重新生成。

### 3.3 制作准备与用户授权

制作阶段按需读取[生成准备与恢复](references/production/generation-review.md)，固定本轮范围与参考。MCP Token 即用户调用授权，不要求另走生成审批；人物、服装、道具、场景、声音、配乐完整保留，按用户当前任务与明确限制执行。素材质量审核和最终选用不替代服务端用户权限，也不新增调用前置确认。

## 4. 旧入口迁移

旧 `x-verdantflare-short-drama-*` 名称对应的能力均已迁入本包，见[迁移表](references/migration.md)。收到旧名称时按迁移表选择模块。旧目录不再作为可发现技能分发；历史文档/已安装客户端的旧路径不会自动重定向，更新安装或将路径改为迁移表中的新路径。无需同时加载新旧两套技能。

## 5. 安装与运行依赖

编剧、审核和文字设计可独立使用本包。运行校验器需要 Python 3.10 或更高版本；本机拆解另外需要 Ollama 及已安装的 `gemma3:12b`，不自动拉取。图像、视频、音乐和对白执行按实际任务使用对应已安装 Skill 与宿主能力；模块中的外部链接固定到可追溯源码版本，作为依赖说明，不是自动安装或调用授权。
