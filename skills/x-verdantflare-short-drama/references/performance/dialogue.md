# 对白与声线交接

## 1. 对白任务

独立对白、旁白、参考录音、发音词典维护、声线升级过期检出或单句返修交给 `verdantflare-speech`。先固定剧本修订、角色、句子（`utterance_id`）、镜头（`shot_id`）、镜头时间窗口（`shot_window_seconds`）与声线版本（World `reference-voice` 资产 `world_voice` 或项目候选 `voice_profile`）；单镜表演卡消费同一份台词、情绪要求和声音记录。歌曲、配乐与 RVC 换声仍交给 Music，视频是否支持音频参考或口型按实际 Video 能力决定。

有已安装的 Speech 技能时读取该入口；独立安装本短剧包时，可按需读取[固定 Speech 技能源码](https://github.com/verdantflarehub/verdantflare-skills/blob/edd08111ffe9722ea42018fcd02ae6b628667257/skills/verdantflare-speech/SKILL.md)。不依赖本地兄弟目录，也不自动安装或索取内部服务凭据。

## 2. 当前执行边界

Studio 已接通 `speech.capabilities`、`speech.voices`、`speech.create`、`speech.status`、`speech.result`（Speech 0.2.0 / IndexTTS2 0.2.0）：

- 提交前维护项目发音词典 `speech/lexicon.json`（人名、古地名、多音字），通过 `lexicon_ref` 作用于 `text.sent`，不篡改剧本原文 `text.source`。
- 声线源在 `world_voice: {asset_id, version_id}` 与 `voice_profile`（ContentRef）之间二选一互斥；情绪输入在 `emotion_vector`、`emotion_text`、`emotion_reference` 之间至多选一；传入 `shot_window_seconds` 以测量镜头窗口偏差。
- 实际提交前重新发现工具与 Schema；未开放时继续准备并报告当前入口缺口。独立配音使用 Speech，歌曲与视频音轨沿用各自任务边界。

用户明确要生成视频自带对白时沿用该任务范围，并按 Video 的实际能力执行；独立配音文件和可恢复语音任务仍分别验收。

## 3. 短剧回写、过期检出与听审返修

默认归档到同一 Studio 短剧项目，用户指定仅本地时遵循其范围。把原生音频（`original.wav`）、剪辑副本（`editing.wav`）、质量报告（`report.json` v2）、生成记录（`generation.json`）和声线配置与真实角色、场景／镜头、时间窗及制作版本关联；按当前 Project 领域契约维护固定 `file_refs` 与整集汇总清单。

仅将文件加入项目时报告“已归档，声音素材关联待完成”。服务已经完成提交就回读原修订，不重复导入。当角色声线升级、台词修改或词典更新时，通过比对已选 take 的 `generation.json`（`input_digest`、`voice_source`、`text.sent`）自动检出过期已选 take；读取 Studio Speech Dashboard 或项目听审记录（`selected`／`needs_revision`、`review_note`）后，只针对过期或 `needs_revision` 的单句发起新 take，不重跑整集。

满幅、真峰值、响度、ASR 字错率（`cer`）、参考声线余弦相似度（`speaker_similarity`）、超窗偏差（`window_diff_seconds`）与人工听审分别记录。客观测量通过不替代人工听审，归档候选不自动标记为终选；不裁切尾字或填满静音来伪造时长符合。
