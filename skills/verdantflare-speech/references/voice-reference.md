# 参考接收与版本

## 1. 最短录制文稿

用户需要尽量短的普通话参考时，可让不同声线分别读一次：

> 清晨的风吹过竹林，远处传来几声鸟鸣。你先坐下，喝杯热茶，我们慢慢说。

正文 29 字，按自然语速约 8–12 秒；实际时长以录音为准。每种声音独立文件、单人近讲，保留原件；不加配乐、混响或变声。手机 M4A 可直接接收，优先原始 WAV，无需用户先转码。不要把声线名称念进录音。

已经收到可用录音时直接接收，不要求重读这份文稿。实际转写未知就记录未知，不把建议文稿填写成已核验台词。

## 2. 接收与处理

保存原件及 SHA-256，核验可解码、实际时长、采样率、声道、位深／编码、峰值与满幅；单一说话人、噪声和台词完整性需要试听或相应证据，信号指标不能代替听审。

IndexTTS2 0.1.3 已验证的参考范围：PCM16／PCM24 单声道 WAV，16／22.05／24／44.1／48 kHz，1–15 秒，不超过 2 MiB。调用时复核实际能力声明，不能将本范围当作对所有模型的固定要求。

确需单声道转换、选段或重采样时另存派生文件，记录来源、精确选段和处理。不覆盖原件，不静默截断长录音，不默认降噪、归一化或做生成式增强。双声道合并后核对相位抵消、峰值和可懂度。

## 3. 可复用声线配置

需要把新录音实际登记为可调用声线时，执行[新声线登记与试音](new-voice.md)。其中配置准备脚本可做确定性核验，但只有固定修订 `speech.voices` 回读才证明登记成功。

声线配置是版本化领域文件，不要求先有独立声音模型数据库。保存真实可得的信息：

- 声线标签、原项目及来源／使用范围。
- 原件和有效参考的文件、大小、摘要、实际处理与固定 ContentRef；未上传的引用保留缺项。
- 实际模型、运行时版本及模型清单摘要，不能把计划版本写成已运行。
- 实际朗读文字或未知状态、试听任务与质量结论、候选或已选状态。

每次生成固定此配置和参考。换参考或选段另建版本；运行时升级记录新的实际绑定，旧配置和任务保留。仅改台词无需重录参考。没有训练过程、权重包和加载证据时，不创建虚假训练 run 或 World voice-model。

用户要求结合两个参考时，先按[旁白方向](narration-direction.md)区分借鉴表演与融合音色；不把单参考配置或本地派生音频描述为后端已支持多声线融合。

当前 Speech 可发现配置的项目路径为 `speech/voices/<voice_id>/profile.json`。配置与参考 WAV 必须同时属于生成时指定的项目修订；原件、详细处理和来源记录可另存领域文件，不把未声明字段塞入服务配置。

配置字段为 `schema_version: 1`、稳定小写 `voice_id`、`label`、`engine: indextts2`、实际 `model_manifest_sha256`、`reference.content_ref`、`reference.sha256`、`candidate_state` 和 `listening_review`。模型摘要从实际能力读取并核对，不能抄示例值。未听审使用 `candidate`／`pending`；`selected` 或 `approved` 只引用真实选择和听审。保存后使用 `speech.voices` 回读，生成时传入该文件的完整 ContentRef。

既有 Mengsk 等 RVC `.pth/.index` 是音色转换模型，不能直接用作 IndexTTS2 文本生成权重。可复用用途明确的原始说话录音；“先生成再 RVC 换声”需要独立请求与验证，不能悄悄增加转换步骤。

## 4. 保存与依赖

已授权归档的原件、派生参考与配置通过 Studio Artifact／Project 保存，沿用真实项目。AI 短剧阶段成果默认归原项目；用户指定仅本地时保留本地。音色或表演尚未选用时保持候选。

已有宿主 Project／Artifact 工具时按实际 Schema 使用；涉及传输或提交恢复可读取已安装的同名技能。未安装时以下固定源码说明可供按需阅读，不要求自动安装：

- [Artifact 受控传输](https://github.com/verdantflarehub/verdantflare-skills/blob/e51e12f52023bbca00c706ef818efca6d7faba9d/skills/verdantflare-artifact/references/transfer-and-recovery.md)。
- [Project 修订恢复](https://github.com/verdantflarehub/verdantflare-skills/blob/e51e12f52023bbca00c706ef818efca6d7faba9d/skills/verdantflare-project/references/revisions.md)。
