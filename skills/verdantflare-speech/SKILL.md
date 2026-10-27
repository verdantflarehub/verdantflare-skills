---
name: verdantflare-speech
description: 通过 VerdantFlare Studio 从 World 声线资产或项目参考录音生成对白、旁白和新文稿，支持发音词典、情绪控制、逐句换声、过期 take 检出、听审返修和归档。也用于基于参考特征规划音色变化、准备新声线候选；歌曲与 RVC 换声交由 Music。
metadata:
  version: "0.4.0"
---

# VerdantFlare Speech

把固定台词、参考声音和每次实际生成绑定为可追溯的 take。沿用当前项目、声线版本和用户授权；只改目标台词或词典无需重新录制声音。

## 1. 能力发现

用户入口统一为宿主 Studio MCP，读取 `STUDIO_MCP_URL` 与 `STUDIO_MCP_BEARER_TOKEN`，从实际工具目录确认本次需要的能力和 Schema。Bearer 由服务端解析身份，不附加用户／组织头，不索取内部服务凭据或新增生成审批令牌。

已通过 Studio 接通 Speech 0.2.0 → IndexTTS2 0.2.0 → Artifact 原生产物 → Project 固定修订。实际工具为 `speech.capabilities`、`speech.voices`、`speech.create`、`speech.status`、`speech.result`、`speech.rate_content`。`speech.capabilities` 返回按引擎的能力声明数组 `engines`；`speech.voices` 同时列出项目引用的 World `reference-voice` 资产（`world_voice`）与项目候选配置（`voice_profile`）。每次执行重新发现 Schema 和就绪状态；缺工具时报告当前入口缺口，继续可独立完成的准备和已有结果整理。

本技能不启动 GPU 服务、不读取 Kubernetes Secret，也不直连内部运行时。Music 歌曲接口或视频自带音轨不自动替代独立对白生成。

## 2. 选择工作

- 用户要短录制文稿、提交录音或复用声线（含 World `reference-voice` 与项目 `voice_profile`）：读取[参考接收与版本](references/voice-reference.md)。
- 用户要“用参考生成新声线”“录入新声音”或保存成可反复调用的声线：读取[新声线登记与试音](references/new-voice.md)，完成参考、配置、固定修订与回读。
- 用户明确要融合两种参考的音色：读取[实验性声谱融合](references/voice-morph.md)，使用同文源制作新的参考候选，再登记与跨文本试音；不宣称身份比例或稳定音色设计模型。
- 用户要基于参考改变年龄、性别、明亮度或设计不同音色：读取[音色设计与候选复用](references/voice-design.md)，固定参考特征、修改要求和目标描述；按实际后端执行，不把文字设计说成波形身份编辑。用户两种都需要时同时保留原参考复用与设计候选。
- 用户要维护发音词典、逐句配音、检出过期已选 take、恢复任务、按听审返修或保存试听：读取[任务与交付](references/tasks-and-delivery.md)。
- 生成完成后执行 18+ 内容分级评估与标记：读取[评估步骤](references/content-rating-step.md)与[评估规范](references/content-rating-rubric.md)。
- 用户要参考旁白风格、新音色方向、连贯表演或锁定窗口配音：读取[旁白方向与时间窗口](references/narration-direction.md)。
- 仅讨论文本或明确仅本地时遵循该范围；不因缺少生成工具而停止独立准备。

## 3. 制作边界

先固定原台词与角色，不自行改写、拆换说话人或把表演说明念进正文。人名、地名与多音字通过项目发音词典（`lexicon_ref`）作用于 `text.sent`，保留 `text.source` 原文不篡改。只传实际后端支持的参数；情绪控制从 `emotion_vector`、`emotion_text`、`emotion_reference` 中至多选一；不承诺精确时长、非语言表演或音频驱动口型。换文本、词典、参考版本或有效参数形成新 take，保留旧结果，并通过 `generation.json` 的 `input_digest` 与 `voice_source` 检出过期已选 take。

参考音频是声线的版本化组成，不能伪称为新训练权重；RVC 权重不能直接加载到 IndexTTS2。原始录音、权重、生成媒体和凭据不进入技能包或 Git。

新参考可建立新声线；仅调整情绪不等于设计不同身份。当前 IndexTTS2 接口不支持年龄、性别和身份控制；VoxCPM2 候选运行器处于开发验证，尚非 Studio 已开放能力。工具缺失时完成设计输入与已有候选登记准备，不捏造生成结果，不把情绪参数或后期变调包装为音色设计。

生成后内容分级评估（K09 强制步骤）：每次 `speech.result` 取得新生成的语音 take 后、在交付摘要展示与 Project/World 归档之前，必须按[评估步骤](references/content-rating-step.md)与[评估规范](references/content-rating-rubric.md)评估转写文本（如 `report.json` 的 `asr`）、台词、正向提示词与可识别非语言声音（缺少转写能力时只依据文本资料判定，并在 `reasons` 中注明 `"未转写音频"`），调用 `speech.rate_content` 提交分级结果（`general` 与 `nsfw` 均须提交，`rubric_version` 取自规范副本文首；不确定即标 `nsfw`；提交超时用相同内容幂等重试，不重跑生成）。判定为 `nsfw` 的产物在摘要中只写“N 个产物已标记为 NSFW 并隐藏”，不展示试听链接、`ContentRef` 或台词全文，也不同步到 Studio 项目或登记到 World。

生成成功、客观质量测量（`report.json` v2）、内容分级评估（`speech.rate_content`）、项目归档、声线选用与人耳听审分别报告。满幅或明显失真保留问题标记，不能靠后期降音量宣称已恢复削平波形。候选归档不自动批准或发布 World。
