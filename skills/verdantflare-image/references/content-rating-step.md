# Image 生成后内容分级评估步骤（K09）

## 1. 当前依据

- [18+ 评估规范（rubric v1.0）](content-rating-rubric.md)：判定类别（S1–S4、V1）、图像与文本评估标准及输出 JSON 格式。
- [调用契约](contracts.md)：`image.rate_content` 工具与 Project 归档边界。

## 2. 步骤位置

评估放在“取得生成产物”之后、“交付摘要与项目同步”之前：

1. 生成、编辑或局部重绘任务完成，按原流程下载并校验产物（此时尚未评估，Agent 可读取图像内容）。
2. 按第 3 节准备评估输入，对照 [18+ 评估规范（rubric v1.0）](content-rating-rubric.md) 逐项判定，得到评估 JSON。
3. 调用 `image.rate_content` 提交评估结果；判定为 `general` 的产物也必须提交，以留下“已评估”记录。
4. 按第 5 节处理交付摘要、Studio 项目同步与 World 登记。

一次生成多张图像候选时，必须逐张独立评估、逐张调用 `image.rate_content` 提交。评估与提交未完成前，不得把产物同步到 Studio 项目，也不得在交付摘要中展示。只读查询或恢复已完成且已评估的任务时，不重复评估。

## 3. 评估输入与不确定处理

- **图像输入**：读取生成的图像文件本身，同时评估本次请求的正向提示词（`prompt`）、版式文字（如 `moss-image` 的 `layout` 文本）与标题。
- **反向提示词豁免**：`negative_prompt` 或反向排除描述用于排除相关内容，不作为判定 `nsfw` 的依据。
- **意图与结果（O3）**：若正向提示词明确要求生成成人内容（S1–S4、V1），即使生成的图像未完全呈现，也一律判为 `nsfw`。
- **不确定从严（O2）**：无法取得完整图像输入或无法完成判定时，按规范第 5.1 节**“不确定即标 nsfw”**处理，并在 `reasons` 中写明具体原因。

## 4. 规范版本与 `image.rate_content` 调用

- **规范版本**：`rubric_version` 固定取自同目录 [content-rating-rubric.md](content-rating-rubric.md) 文首声明的版本号（当前为 `"rubric v1.0"`），不得自行编造版本号。
- **调用参数**：向 `image.rate_content` 传入产物定位标识（`task_id` 或 `content_ref`，二选一）及评估字段：

```json
{
  "task_id": "<生成任务ID>",
  "rating": "general",
  "rubric_version": "rubric v1.0",
  "evaluator_model": "<执行评估的模型标识>",
  "reasons": ["未触发类别"],
  "evaluated_at": "2026-10-24T00:00:00Z"
}
```

- **幂等与超时重试**：`image.rate_content` 是幂等的。若调用超时或响应未知，使用相同产物标识和相同评估 JSON 重试，**严禁因标记提交超时而重新生成图像**。

## 5. 交付摘要与项目同步约束

- **交付摘要脱敏**：判定为 `nsfw` 的图像产物，交付摘要只写“N 个产物已标记为 NSFW 并隐藏”，**严禁展示预览图、下载链接、`ContentRef`、原生 `artifact_id` 或提示词全文**；判定为 `general` 的产物照常交付。
- **项目同步与 World 阻断（H1 已确认）**：判定为 `nsfw` 的产物**不同步到 Studio 项目（不写入 `project.commit`），也不登记到 World（不调用 `world.register`）**。
- **不提供撤销**：MCP 与本技能均不提供、也不尝试撤销标记；若用户认为存在误标，在摘要中提示用户可在 Studio 设置页开启本机“显示 NSFW 内容”开关（24 小时有效）后，在产物详情中人工取消 NSFW 标记。
