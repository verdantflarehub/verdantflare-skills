# 逐句任务与交付

## 1. 固定输入、发音词典与情绪控制

回读用户指定的剧本修订或现有录制表，保留稳定句子 ID（`utterance_id`）、角色、镜头（`shot_id`）、原文和声线版本。实际送入模型的文本与原文分开保存；人物、场景或声音 ID 来自真实项目，不能将本地名称伪装成服务 ID。

当项目包含易错人名、古地名、专有名词或多音字时，先在项目修订内维护发音词典（如 `speech/lexicon.json`）：

- 结构：`{"schema_version": 1, "language": "zh-CN", "entries": [{"term": "解县", "replacement": "懈县", "note": "古地名读法"}]}`。
- 提交 `speech.create` 时传入该词典文件的 `lexicon_ref`（ContentRef）。服务端按最长词条优先匹配生成 `text.sent`，保留原文 `text.source` 并记录 `text.lexicon_hits`，同时将 `text.source` 作为 `expected_text` 传给运行时用于 ASR 比对，不改动剧本正文。

有多参考方向、长句拼接或固定剪辑窗口时，按[旁白方向](narration-direction.md)区分音色、语气与节奏，并在 `speech.create` 传入 `shot_window_seconds` 记录目标镜头窗口；实测候选长度后处理超窗（`window_overrun`），不能用整体响度通过代替自然度和接缝检查。

把情绪、停顿和镜头时间窗口写成制作要求，再对照 `speech.capabilities` 的 `engines` 数组。当前 `indextts2` 支持 `zh-CN`、300–500 字以内文本，以及三选一（或省略）的情绪输入：

- `emotion_vector`：八维数组 `[happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]`，每项 `0..0.8`、总和 `<= 0.8`。
- `emotion_text`：简短情绪描述（1–200 字，如 `"克制隐忍，尾音略微发紧"`），由引擎 QwenEmotion 模块解析为八维向量并写入 `report.json` 与 `generation.json` 的 `resolved_emotion_vector`。
- `emotion_reference`：`{"content_ref": <同修订 WAV ContentRef>, "alpha": 0.1..1.0}`，使用参考片段传递情绪语气。
- 三种情绪输入彼此互斥；普通旁白可全部省略，避免替用户强加表演要求。语速与精确时长参数当前不支持。

不把哭泣、喘息、笑声或精确时长控制假映射为普通文本或任意参数。视频消费音频和口型能力另查实际 Video 入口。不能完成的要求明确保留缺项。

## 2. 提交与恢复

通过 Studio 实际发现的工具执行；缺工具时保持准备记录，不造任务 ID，也不使用内部 HTTP 路径补洞。

1. `project.open` 取得当前可写项目及固定 `revision_id`，再调用 `speech.capabilities` 检查 `engines` 就绪状态、模型清单摘要和限制。
2. `speech.voices(project_id, revision_id)` 列出该修订可用的两类声线：
   - `voice_source: "world"`：项目 `asset_refs` 引用的 World `reference-voice` 资产，创建任务时传 `world_voice: {"asset_id": ..., "version_id": ...}`。
   - `voice_source: "project_candidate"`：项目内 `speech/voices/<voice_id>/profile.json` 候选声线，创建任务时传 `voice_profile`（ContentRef）。
   - `world_voice` 与 `voice_profile` 严格互斥；检查 `compatible: true` 后再提交。没有配置时按[参考接收](voice-reference.md)保存后重新打开修订。
3. `speech.create` 传入 `project_id`、`revision_id`、`world_voice` 或 `voice_profile`、稳定 `utterance_id`、完整 `text`、`language: "zh-CN"`、固定 `idempotency_key`，以及可选同修订 `script_ref`、`lexicon_ref`、`shot_window_seconds` 和至多一种情绪输入。
4. 保存业务 `task_id`，用 `speech.status(project_id, task_id)` 查询；`runtime_task_id` 是内部原任务来源，不替代业务任务 ID。`phase: loading_model` 或 `error.code: model_load_timeout` 表示 GPU 按需唤醒冷启动中，继续轮询原任务，不换键重提。
5. 成功后调用 `speech.result(project_id, task_id)`，取得 `original.wav`、`editing.wav`、`report.json`、`generation.json` 四份中央 ContentRef；使用 `artifact.read` 获取受控下载路径并校验。

提交前保存固定输入、声线版本和稳定幂等键。收到任务后先持久保存原 `task_id`／`runtime_task_id`，再轮询；请求 ID、模型版本和任务 ID 使用各自真实返回值。

- 同键同输入恢复原任务；同键异参冲突时保留旧请求，返修另建 take。
- 网络超时先查询原记录；首次响应丢失而尚无 `task_id` 时，按保存的完全相同 create 请求和幂等键恢复。查不到不等于未执行，不换键自动重发。
- 已生成但上传／Project 保存失败，只恢复传输或提交，不重新推理。
- 服务明确返回 `recovery_required` 时保留原因，新的尝试与旧任务建立对应关系，不无限自动重试。

`phase: archive` 的暂时故障由服务按原 `write_id` 恢复。权限丢失先解决既有项目权限，不能更换主体来绕过。`model_version_changed` 或 `unverified_voice_engine` 保留旧配置和任务，重新核对实际模型后再决定新 take。

## 3. 过期 take 检出与听审返修

每条生成的 `generation.json` 持久保存：

- `input_digest`：由 `sent_text`、`voice_source`、`model_manifest_sha256` 与情绪输入计算的规范化 SHA-256。
- `voice_source`：实际使用的 `world`（`asset_id`、`version_id`、`reference_sha256`）或 `project_candidate`（`voice_profile`、`profile_sha256`）。
- `resolved_emotion_vector`：实际生效的八维情绪向量。
- `text`：`{source, sent, lexicon_hits}`。
- `timings`：`{queue_seconds, load_seconds, inference_seconds}`。

在短剧合入或声线升级后，回读已选 take 的 `generation.json` 并与当前目标声线版本、当前词典计算出的 `sent_text` 及当前 `model_manifest_sha256` 比对：

- 若 `voice_source` 版本／摘要不同、`text.sent` 变化或 `model_manifest_sha256` 变化，标记该 take 为过期（`stale: true`），列出具体原因（如 `world_voice_version_changed`、`voice_profile_changed`、`text_changed`）。
- 只对过期或听审标记为 `needs_revision` 的 `utterance_id` 生成新 take（递增幂等键后缀如 `-take-2`），保留其他已选 take 的字节与固定引用，不重跑整集。
- 读取 `speech.status`／Studio Speech Dashboard 中的听审结论（`review`、`review_note`）和 `report.json` 的 `warnings`：若因读音错误返修，先更新 `speech/lexicon.json` 并提交新修订，再带新 `lexicon_ref` 重录该句；若因情绪或超窗返修，调整 `emotion_text`／`emotion_vector` 后重录该句。

## 4. 音频与技术报告（`report.json` v2）

下载返回的原生音频、剪辑副本和报告，按返回的大小与 SHA-256 核验。`report.json`（`schema_version: 2`）记录：

- `original`（22.05 kHz PCM16）与 `editing`（48 kHz PCM24 单声道，遵循 -16 LUFS / -1.0 dBTP 交付规范）各自的时长、采样率、声道、峰值、真峰值（`true_peak_dbfs`）、综合响度（`integrated_lufs`）、满幅采样数、首尾静音与语音活动区间。
- `asr`：SenseVoiceSmall 回写文本、标准化文本、字错率 `cer` 与 `transcript_mismatch`。
- `speaker_similarity`：WeSpeaker 参考声线余弦相似度。
- `shot_window_seconds` 与 `window_diff_seconds`：镜头时间窗偏差。
- `warnings`：`clipping`、`true_peak_over`、`transcript_mismatch`、`window_overrun`。

满幅或警告输出保留原件、指标和问题状态。客观指标只作初筛，用户试听决定角色一致性、情绪和自然度；技术通过不替代听审。

## 5. 项目归档与短剧交接

服务返回中央 ContentRef 时直接复用固定引用。Speech 生成的四份文件已经是 `task_output/service_id=speech` 原生产物，不重复上传。结果的 `project_archive: pending` 表示仍需 Skill 显式提交；保存 Project 回执，不能把服务生成成功当成项目提交完成。

原始音频、剪辑副本、报告和生成记录以最小 Project changes 提交（如 `speech/episodes/<ep>/takes/<utterance_id>/<take_id>/` 及汇总表 `speech/episodes/<ep>/dialogue-delivery.json`）。`commit_id`／`write_id` 在请求前固定，使用真实 `expected_revision_id`；冲突先合并，未知结果查询原提交。

AI 短剧按当前领域契约将每句对白候选关联到真实角色、声线来源（`world_voice` 或 `voice_profile`）、场景／镜头（`shot_id`）、时间窗口、客观 QC 指标、听审状态与 `file_refs`。交付可试听文件、实际任务及版本、技术问题与听审状态、固定项目修订。
