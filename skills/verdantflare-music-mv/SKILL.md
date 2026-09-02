---
name: verdantflare-music-mv
description: 将已批准歌曲导演并制作成可审核、可恢复、可局部重做的完整音乐视频；当用户要求为歌曲创作 MV Treatment、Visual Bible、故事板、Animatic、Shot Timeline，调度逐镜视频生成，审核跨镜连续性，或使用批准母带装配约 200 秒 MV 时使用。不用于创作歌曲，也不直接操作 H3 Runtime。
---

# VerdantFlare Music MV

以已批准的 Music Asset Version 为音乐输入，将完整歌曲导演、拆镜、生成、审核并装配为音乐视频。需要推进阶段、返工或恢复项目时阅读 [references/workflow.md](references/workflow.md)；创建或校验项目文件与时间线时阅读 [references/contracts.md](references/contracts.md)；设计人物身份卡、服装或 H3 参考资产时阅读 [references/character-assets.md](references/character-assets.md)；到达审核点或收到批准、修改、重做、选择候选等指令时阅读 [references/review-gates.md](references/review-gates.md)。

## 职责边界

- 负责 MV Treatment、Visual Bible、歌曲结构分析、故事板、全曲 Animatic、Shot Timeline、跨镜连续性、候选选择、Picture Lock、字幕和交付包。
- 歌曲尚未批准时，使用 `verdantflare-music` 完成音乐生产；只接受通过 Music Gate 的 Master WAV、歌词、LRC、BPM 和可用 Stems 进入正式 MV 制作。
- 将批准的连续素材需求冻结为 `GenerationUnit`，再使用 `verdantflare-video-h3` 执行 H3 原子素材生成。MV Skill 决定单元是连续单镜还是最多 2–3 个内部切镜，但不拼接 H3 HTTP 请求，不管理 `video_task_id`、GPU、模型下载或 Kubernetes。
- H3 不是所有镜头的必选来源。允许使用批准的已有素材、静帧运动、同一 Take 的不同裁切和传统剪辑，但每个最终 Shot 都必须有明确来源。
- 不把视频逻辑写回 `verdantflare-music`，不调用固定 SD2 契约的 `verdantflare-video` 代替 H3，也不在能力失败时静默更换模型。

## 项目状态

使用 `.output/music-mv/<创作者>/<MV项目>/` 保存文本清单和受控 Artifact 引用。音频、视频、图像和模型不得提交 Git；项目文件只记录不可变资产 ID、摘要、审核记录和可公开的交付元数据，不记录签名 URL、凭据或内部存储地址。

至少维护：

- `mv-project.yaml`：Music Asset Version、目标平台、画幅、当前阶段和项目状态；
- `treatment.md` 与 `visual-bible.md`；
- `song-timeline.yaml`、`shot-timeline.yaml` 和 `generation-units.yaml`；
- `制作审核记录.md`：所有审核决定和返工原因，追加记录而不覆盖历史；
- `artifacts.json`：故事板、Animatic、候选、批准镜头和交付物引用。

恢复项目时先核对记录、时间线和 Artifact 是否一致，从最后一个未批准的审核门继续。冲突时停止并报告，不猜测状态，不重复已经成功且批准的昂贵生成。

## 核心规则

1. Master WAV 是整支 MV 的全局时钟和最终歌曲音频唯一事实源。所有时码采用从 Master 起点开始的整数毫秒。
2. 区分最终剪辑 `Shot` 与 H3 `GenerationUnit`：Shot 通常 2 至 8 秒；Generation Unit 必须为 4 至 15 秒，一个生成素材可裁出多个 Shot。
3. 约 200 秒 MV 先规划完整覆盖，再决定生成数量。建议以 25 至 40 个 Shot 起草，但最终数量由歌曲、Treatment 和批准 Animatic 决定，不设置生成配额。
4. 先完成全曲故事板和 Animatic，再投入批量 H3。Treatment 或 Animatic 未批准时不得批量生成正式候选。
5. 优先验证身份、演唱表演和风格风险最高的代表镜头；代表镜头通过后才批量执行同类镜头。
6. 候选技术成功不等于镜头通过。逐镜检查身份、表演、动作、连续性、构图、节奏、技术媒体参数和合规。
7. 只返工失败镜头或受影响的下游装配。Treatment 概念不成立时回到导演阶段，不能继续消耗原子视频生成掩盖问题。
8. Picture Lock 后移除或静音所有生成镜头内生音轨，统一挂载批准 Master WAV；环境声或拟音必须作为独立可审核音轨。

## 完成条件

只有 Director、Animatic、Shot、Picture Lock 和 Release 五个审核门均明确通过，Shot Timeline 完整覆盖 Master 时长，所有最终 Shot 均解析到批准素材，成片使用批准 Master 且媒体、字幕、权利检查通过，才能声明 MV 完成。返回最终视频 Artifact、实际时长、画幅、Master Music Asset Version、审核记录和交付清单。
