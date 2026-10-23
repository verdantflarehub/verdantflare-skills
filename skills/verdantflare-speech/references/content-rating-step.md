# Speech 生成后内容分级评估步骤（K09）

## 1. 当前依据

- [18+ 评估规范（rubric v1.0）](content-rating-rubric.md)：判定类别（S1–S4、V1）、音频与文本判定标准及输出 JSON 格式。
- [逐句任务与交付](tasks-and-delivery.md)：`speech.result` 产物获取、`report.json` v2 与 Project 归档流程。

## 2. 步骤位置

评估放在“取得生成产物（`speech.result`）”之后、“交付摘要与项目同步”之前：

1. 生成任务完成，调用 `speech.result` 取得生成的音频产物与 `report.json`、`generation.json`（此时尚未评估）。
2. 按第 3 节准备评估输入，对照 [18+ 评估规范（rubric v1.0）](content-rating-rubric.md) 判定，得到评估 JSON。
3. 调用 `speech.rate_content` 提交评估结果；判定为 `general` 的产物也必须提交，以留下“已评估”记录。
4. 按第 5 节处理交付摘要、Studio 项目归档与 World 声线登记。

批量逐句配音生成多个 take 时，逐个 take 评估并逐个调用 `speech.rate_content` 提交。评估未完成前，不得将 take 同步到 Studio 项目，也不得在交付摘要中展示。只读查询或恢复已完成且已评估的 take 时，不重复评估。

## 3. 音频与语音评估输入（含转写缺失处理）

- **评估输入**：
  - **转写文本**：优先读取 `report.json` 中的 `asr` 回写文本，或使用当前可用的语音转写能力取得实际音频文本。
  - **文本与提示词**：同时评估配音原文（`text.source`）、发音替换文本（`text.sent`）、情绪描述（`emotion_text`）及标题。
  - **非语言声音**：检查是否存在可识别的、以性兴奋为目的的非语言声音（如模拟性行为的声音，计入 S1、S2）；普通语气、音色本身（如低沉、气声）不构成 `nsfw`。
- **转写能力缺失时的处理**：当运行环境没有可用转写能力且结果中无 ASR 转写文本时，不作凭空推断，仅依据台词文本、提示词与已掌握信息判定，并**必须在 `reasons` 中明确注明 `"未转写音频"`**（例如 `["未触发类别", "未转写音频"]`）。
- **不确定从严（O2）**：无法取得必要输入或含义模糊无法排除 18+ 时，按规范第 5.1 节**“不确定即标 nsfw”**处理，并在 `reasons` 中写明原因。

## 4. 规范版本与 `speech.rate_content` 调用

- **规范版本**：`rubric_version` 固定取自同目录 [content-rating-rubric.md](content-rating-rubric.md) 文首声明的版本号（当前为 `"rubric v1.0"`），不得自行编造版本号。
- **调用参数**：向 `speech.rate_content` 传入 `task_id` 或 `content_ref`（二选一）及评估字段：

```json
{
  "task_id": "<语音生成任务ID>",
  "rating": "general",
  "rubric_version": "rubric v1.0",
  "evaluator_model": "<执行评估的模型标识>",
  "reasons": ["未触发类别"],
  "evaluated_at": "2026-10-24T00:00:00Z"
}
```

- **幂等与超时重试**：`speech.rate_content` 是幂等的。若提交超时或响应未知，用相同标识和相同评估 JSON 重试，**严禁因此重新生成语音 take**。

## 5. 交付摘要与项目同步约束

- **交付摘要脱敏**：判定为 `nsfw` 的语音产物，交付摘要只写“N 个产物已标记为 NSFW 并隐藏”，**严禁展示音频试听链接、`ContentRef`、文件路径或露骨台词原文**；`general` 产物照常交付。
- **项目同步与 World 阻断（H1 已确认）**：判定为 `nsfw` 的产物**不同步到 Studio 项目（不提交 `project.commit`），也不登记到 World（不调用 `world.register`）**。短剧对白交接遇到 `nsfw` 产物时，在阶段记录中只写“已隐藏产物 N 个”，由用户决定是否改写台词或提示词后重新生成。
- **不提供撤销**：MCP 与本技能均不提供、也不尝试撤销标记；若存在误标，提示用户可在 Studio 设置页开启本机“显示 NSFW 内容”开关（24 小时有效）后人工取消 NSFW 标记。
