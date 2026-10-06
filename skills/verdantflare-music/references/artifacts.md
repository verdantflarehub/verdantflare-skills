# 输入输出与质量标准

## 输入

完整原创的输入为（只做企划、歌词或其他单阶段工作时，仅要求该阶段输入）：

1. 一句音乐灵感。
2. 人声来源：Music3 原始人声、一个或多个获授权录音，或一个已批准的人声模型。指定人物音色才需要录音或模型。

原始录音和已批准模型作为指定人物音色来源时二选一。原始录音先准备、经人工审核后用于训练并进入创作者人声库；已批准模型直接复用，不重复训练。进入对应执行阶段前必须具备：

既有歌曲重新演绎是独立输入分支：需要用户有权处理的完整歌曲和一个已批准人声模型，不要求音乐灵感、原创企划或双候选。客户媒体先进入批准的外部 S3，再以对象 URL、文件名和 SHA-256 调用 `asset.import`；项目提交只保存导入后的 Artifact 引用和完整性元数据，不保存签名 URL。旧工程中的 `assets.json` 仅作为导入兼容输入。

| 阶段 | 必需输入 |
| --- | --- |
| S3 资产导入 | 已批准 S3/CDN origin 的 HTTPS 对象 URL、安全音频文件名、SHA-256 |
| 候选生成 | 已批准的 `完整词曲企划.md`、`lyrics.txt`、`instructions.txt`、最大生成时长 |
| 分轨 | 已选定的完整歌曲资产 |
| 训练材料准备 | 1–20 个获授权的原始人声音频 Artifact |
| 音色训练 | 人工批准的 `voice-training.wav` Artifact、新的模型 ID |
| 音色转换 | 原始干声、已批准的人声模型 |
| 歌词对齐 | 转换后干声、不含时间戳和段落标签的已批准逐行歌词 |
| 混音母带 | 伴奏、转换后干声、实际对齐的 LRC、BPM |

不得使用伪造企划、歌词、录音或 LRC 代替缺失的真实输入。不得根据文件名推断审核已经通过。

## 标准交付物

```text
<music-workspace-root>/<创作者>/
├── voice/
│   ├── recordings/
│   │   ├── <原始人声>.*
│   │   └── recording-notes.md
│   ├── prepared/
│   │   └── <准备批次>/
│   │       ├── prepared-XX.wav
│   │       ├── voice-training.wav
│   │       ├── voice-segments.zip
│   │       └── voice-preparation-report.md
│   └── models/
│       └── <模型ID>/
│           ├── <模型ID>.pth
│           ├── <模型ID>.index
│           ├── validation.wav
│           └── voice-model.json
└── <项目>/
    ├── 完整词曲企划.md
    ├── lyrics.txt
    ├── instructions.txt
    ├── 创作预审报告.md
    ├── Demo_Candidate_1.*
    ├── Demo_Candidate_2.*
    ├── Demo_Redraw_<修订号>.wav
    ├── Demo_Selected.*
    ├── instrumental.wav
    ├── vocal_dry_original.wav
    ├── vocal_dry_cloned.wav
    ├── vocal_wet_original.wav        # 服务返回时保留；非独立和声
    ├── vocal_reverb_original.wav     # 服务返回时保留；非独立和声
    ├── Aligned_Lyrics.lrc
    ├── Final_Song_Master.wav
    ├── <创作者显示名>-<歌曲名>.mp3
    ├── <创作者显示名>-<歌曲名>.lrc
    └── 制作审核记录.md
```

每个服务端 Project 工作副本还包含：

```text
<music-workspace-root>/<创作者>/<项目>/.vf/
├── project.json   # 服务端 ProjectManifest 的本地投影
└── local.json     # 仅本机的连接别名、基础修订和下载状态
```

`voice-model.json` 是 World / Music 的领域 JSON，按 [VoiceModel Schema](../../../../docs/design/contracts/v2/voice-model.schema.json) 登记原生模型、固定文件用途、兼容能力和验证证据；它作为 Project 的 `domain_documents`，入库时与明确选定的模型文件一起提交。通用文件归属、Artifact `ContentRef`、歌曲候选、分轨、母带和任务引用由 `.vf/project.json` 代表的 ProjectManifest 管理。人声模型可在审核后登记到 World/music，被多个歌曲 Project 固定引用；歌曲候选、分轨和母带默认属于单个 Project。

旧 `assets.json`、`model.json`、`recordings.json` 只可作为历史工程导入或只读投影。迁移后不得继续编辑这些库存文件，也不得让它们与 ProjectManifest 形成第二套事实源；原始录音、训练素材和私有审核资料仍保留在来源 Project。

`创作预审报告.md` 用简要证据汇总适用的全局、歌词与编曲检查，以及真正会影响生成的阻断项和警告。它是当前草案的审核快照；修改企划、歌词或 instructions 后更新受影响结论，不重复无关检查。

制作审核记录保存当前阶段所需的入口信息、实际可用的预检结果或旧版兼容路径，以及关键决定和资产 ID。局部重绘仅在实际执行时记录源 Artifact ID、区间、修订号与输出 ID；其下游资产使用新来源重新生成，不覆盖旧版本。

换声细节返工产生的独立和声/氛围轨、预混轨和试听版本仅在实际存在时登记到来源 Project 的文件清单，记录来源、版本与审核状态；新母带使用新版本名，不覆盖待返工的旧母带。湿人声与混响残留可供诊断，但不得在清单中标为“独立和声”。

多人声部制作时为每位歌手记录独立干声、模型 ID、歌词职责、时间轴版本及审核结论。可选分轨模型返回的 `backing_vocals_unreviewed.wav` 必须标为待人工审核，不因文件名或 Artifact 创建成功改成“已批准和声”。

最终 MP3 和 LRC 必须使用相同基名，固定为 `<创作者显示名>-<歌曲名>.mp3` 与 `<创作者显示名>-<歌曲名>.lrc`。创作者显示名使用用户批准的拼写和大小写，歌曲名使用审核点 1 批准的标题；两者中不允许用于路径分隔的字符。母带工具返回内部文件名时，在项目交付目录中直接使用上述名称，不另存 `Final_Song.mp3` 或 `Final_Song.lrc`。

资产系统使用资源 ID 时，上述目录保存 MD、领域 JSON 和本地工作副本，媒体与模型由 Artifact 及项目受控存储持久化，不复制到 Git。任何情况下都不得把原始人声、模型或歌曲媒体提交 Git。World 登记只保留明确选中的模型包和来源修订，不把训练录音集合自动暴露给资产使用者。

## 成品规范

- 自然时长：候选以批准的最大生成时长为上限，保存 Music3 的自然结束结果；不得裁切、补静音或反复抽 seed 只为填满上限。候选和最终母带均测量并记录实际时长，最终 WAV 与解码后的 MP3 节目时长应一致，允许编码容器存在不可避免的采样级误差。
- 母带 WAV：立体声、48 kHz、24-bit PCM。
- MP3：立体声、320 kbps；解码后的节目时长与母带一致。
- 综合响度：目标 `-14 LUFS`，验收容差 `±0.5 LU`。
- True Peak：不得高于 `-1.0 dBTP`；测量工具分辨率造成的 `0.05 dB` 以内偏差可以记录后接受。
- LRC：`lyrics.align` 输出 UTF-8 `Aligned_Lyrics.lrc`，毫秒级时间戳严格递增且落在节目时长内，歌词与已批准逐行文字完全一致。不得使用原曲时间轴或线性缩放作 fallback。

格式与哈希检查只能证明文件可读取且传输完整。候选、转换干声和母带听感仍由适用的人工审核点确认；分轨漏声与残留能试听则检查，不能判断时并入转换干声审核披露。
