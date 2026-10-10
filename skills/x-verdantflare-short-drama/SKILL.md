---
name: x-verdantflare-short-drama
description: VF 短剧与影视段落的统一工作入口，按任务完成完整剧本包、审核返修、导演分镜、资产与镜头制作、角色表演或本机单场拆解；也支持广告段落适配，实际图像和视频调用交由对应媒体 Skill。
---

# VerdantFlare 短剧

命名约定：`x-` 是作者标记；目录名、frontmatter 的 `name`、调用名及后续重构均须保留此前缀。

按用户当前任务选阶段，只读取该阶段资料。写剧本不要求先读视频流程；用户已授权返修时直接改稿并同步配套设定，复审后交付。已有版本、素材及人工选择持续有效；结构整理不重新生成媒体，也不重提状态未知的任务。

## 1. 阶段入口

| 当前交付 | 读取入口 |
| --- | --- |
| 新项目、制作顺序与交接 | [全流程](references/production/series-workflow-template.md) |
| 写作、改稿、补齐完整剧本包 | [编剧](references/writing/guide.md) |
| 只审核、复审、核对问题 | [审核](references/review/guide.md) |
| 导演方案、分镜、相机和时间线 | [导演](references/directing/guide.md) |
| 资产候选、镜头制作与验片 | [制作](references/production/guide.md) |
| 单镜动作、情绪、对白表演 | [表演](references/performance/guide.md) |
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

媒体调用仍使用当前宿主的 Image / Video / Music 能力和对应 Skill。项目保存与不可变内容传输使用现行 Project / Artifact 契约；本地文档交付不自动授权远端发布。技术校验、创作选版和正式发行分别记录。

## 4. 旧入口迁移

旧 `x-verdantflare-short-drama-*` 名称对应的能力均已迁入本包，见[迁移表](references/migration.md)。收到旧名称时按迁移表选择模块。旧目录不再作为可发现技能分发；历史文档/已安装客户端的旧路径不会自动重定向，更新安装或将路径改为迁移表中的新路径。无需同时加载新旧两套技能。
