---
name: x-verdantflare-short-drama
description: VF 短剧、影视段落与宣传片的工作入口，完成剧本、导演分镜、动作拆解、执行检查、分批制作及审核返修；实际媒体调用交给专业媒体 Skill。
metadata:
  version: "0.3.0"
---

# VerdantFlare 短剧

## 1. 适用范围

按当前交付进入写作、导演或制作，不要求每次加载全流程。已有项目先恢复固定身份、Look、空间、道具、声音、已批准成果及被否定引用；局部返修不解除其余约束。`x-` 是作者标记，重构与调用保留此前缀。

先读[分类与边界](references/01.base-category.md)，再按需读[工作流](references/01.base-workflow.md)、[规则](references/01.base-rules.md)和[验收](references/01.base-acceptance.md)。正确原则必须进入实际请求与产物检查，不能只写在计划里。

## 2. 按任务读取

- 完整制作与续作：[全流程](references/production/series-workflow-template.md)、[执行方案](references/production/execution-planning.md)
- 场次、完整对白及听者反应：[编剧](references/writing/guide.md)、[剧本包](references/writing/script-package-standard.md)
- 分镜、运镜与生成单元：[导演](references/directing/guide.md)、[白模与相机](references/production/blender-whitebox.md)
- 任意复杂动作判断与拆解：[通用动作方法](references/performance/action-analysis.md)、[表演](references/performance/performance.md)
- 身份、服装与参考：[角色一致性包](references/production/character-consistency-package.md)、[场景与关键帧](references/production/scene-keyframe-video.md)
- 制作、实际请求检查及恢复：[制作](references/production/guide.md)、[执行入口](references/production/execution-gate.md)
- 自审、局部修复与接画：[返修证据](references/performance/rework-evidence.md)、[验收](references/01.base-acceptance.md)
- 微表情／对白：[微表情](references/performance/microexpressions.md)、[对白](references/performance/dialogue.md)
- 文稿审核／广告：[审核](references/review/guide.md)、[广告](references/commercial/guide.md)
- 同步与内容分级：[同步](references/studio-sync.md)、[分级步骤](references/content-rating-step.md)、[分级规范](references/content-rating-rubric.md)
- 用户要求本机原文提取：[本机拆解](references/local-breakdown/guide.md)
- 旧名称迁移：[迁移表](references/migration.md)

## 3. 工具与依赖

文字设计可独立完成。脚本需Python 3.10+，相对于本技能目录运行：

- 分镜结构：`python3 scripts/validate_plan.py <plan.json>`。
- 离线实际输入检查：`python3 scripts/production_gate.py check --packet <packet.json> --basis <basis.json> --basis-sha256 <上游摘要>`。
- 提交、恢复和接画：使用[执行入口](references/production/execution-gate.md)的Python接口；提交消费冻结请求，复用Video客户端，不另造媒体API。
- 参考／产物专项：`validate_reference_reuse.py`、`validate_visual_review.py`，详见[返修证据](references/performance/rework-evidence.md)。

Image／Video／Speech／Music负责真实能力、参数及媒体调用；Blender负责指定实例与空间／相机证据；Project／Artifact负责归档传输。遵循当前用户授权及限制，不增加审批，不以Token取消用户的次数、渠道或上传限制。本机拆解仅在明确要求且Ollama及模型已可用时运行，不自动下载。安装整个技能包，不复制模块为新技能。

生成后按分级步骤调用对应域的`*.rate_content`，分级与选用／发布分别记录。
