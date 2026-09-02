# Music MV 制作工作流

## 阶段 0：音乐输入

读取批准的 Music Asset Version，验证 Master WAV、批准歌词、LRC、BPM 和可用 Stems。测量 Master 实际时长并将其写入项目，不采用用户口述时长覆盖媒体事实。

若音乐未通过 Music Gate，停止 MV 正式生产；可以调用 `verdantflare-music` 完成歌曲，但不得用临时 Demo 建立正式 Shot Timeline。

## 阶段 1：导演方案

分析歌曲段落、歌词区间、拍点、强拍、转折、能量、人声出现区间和可剪辑事件，形成 `song-timeline.yaml`。

根据用户意图、目标观众、平台、画幅和批准资产形成：

- `treatment.md`：核心概念、叙事或表演策略、段落视觉职责和情绪推进；
- `visual-bible.md`：人物身份、造型、场景、色彩、光线、摄影、母题、连续性和禁用内容。

通过 Director Gate 后再进入正式故事板。

## 阶段 2：故事板与 Animatic

先规划覆盖 Master 全长的 Shot 草案。每个 Shot 指定时间区间、镜头类型、画面意图、歌词与节拍上下文、连续性状态和预计素材来源。

用故事板关键帧、批准素材或明确标记的临时占位生成全曲 Animatic，并挂载批准 Master。检查所有时段有画面、段落变化成立、高潮得到视觉响应、表演镜头分布合理且重复使用有导演依据。

通过 Animatic Gate 后冻结 Shot Timeline。修改 Shot 边界、视觉意图或连续性关系会产生新的 Timeline Version。

## 阶段 3：生成计划

将需要 H3 的连续素材需求合并为 4 至 15 秒 `GenerationUnit`。冻结 `unit_form`：人物连续动作优先 `continuous_single_shot`；广告化对照或短蒙太奇可用最多 2–3 个切点的 `internal_multi_shot`。一个 Generation Unit 可以服务多个最终 Shot；不要把每个编辑切点机械地变成一次 H3 调用，也不要让一个单元跨场景、换装或跨时间状态。

按以下顺序确定优先级：

1. 创作者身份和演唱可信度代表镜头；
2. 核心视觉母题和关键叙事转折；
3. 跨镜连续性依赖的上游镜头；
4. 普通覆盖和 Insert。

冻结 Generation Unit 后交给 `verdantflare-video-h3`。改变 Prompt、参考资产、时长、画幅或模型时创建新版本，不修改运行中的单元。

## 阶段 4：逐镜审核与返工

每个 H3 结果首先是 `ShotCandidate`，不能直接进入成片。技术验证通过后，在 Shot Gate 检查身份、表演、动作、连续性、构图、节奏和合规。

返工路由：

- 身份漂移：调整身份参考或降低镜头复杂度，创建新 Generation Unit Version；
- 表演不可信：更换表演参考、缩短生成单元或调整景别；
- 动作断裂：使用批准的上一镜尾帧或重设动作匹配点；
- 服装、道具或空间不连续：修正 Visual Bible 或连续性状态后重编受影响单元；
- 节奏不命中：优先调整 Shot 的素材入出点，素材不足时才重生；
- 技术失败：保留创作输入，由 H3 Skill 创建新 Attempt；
- 导演概念不成立：返回 Treatment 和 Animatic，不继续逐镜重试。

## 阶段 5：Picture Lock 与交付

所有 Shot 均有批准来源后形成 Picture Lock。未经 Picture Lock Gate 批准，不进行正式发布包装。

最终装配必须：

1. 按 Shot Timeline 裁切和拼接批准素材；
2. 静音生成素材的内生音轨；
3. 从零时码挂载批准 Master WAV；
4. 从批准歌词与 LRC 生成字幕，不从生成视频重新识别歌词；
5. 校验黑帧、重复帧、音画时长、帧率、编码、字幕安全区和结尾；
6. 记录最终 Artifact、素材来源、模型版本、权利和审核结论。

Release Gate 通过后输出主视频、无字幕版、字幕版、封面、发布元数据和 provenance 清单。
