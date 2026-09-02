---
name: verdantflare-video-h3
description: 使用领域级 video MCP 可靠执行 MiniMax H3 Ref2VA 原子视频生成；当用户或上层 Skill 提供已冻结的 4 至 15 秒 Generation Unit，要求编译 H3 Prompt、提交、查询、恢复、下载、校验或重试 Shot Candidate 时使用。不负责完整 MV 的 Treatment、故事板、跨单元导演、候选批准或最终剪辑。
---

# VerdantFlare Video H3

将一个已冻结的 `GenerationUnit` 可靠执行为可审核的 `ShotCandidate`。区分首帧、首尾帧和全参考需求时阅读 [references/input-modes.md](references/input-modes.md)；提交、恢复或重试时阅读 [references/workflow.md](references/workflow.md)；构造和解释领域 MCP 请求时阅读 [references/video-mcp.md](references/video-mcp.md)；设计或评审 Ref2VA 动态提示词时阅读 [references/prompting.md](references/prompting.md)；判断某个控制项是否可信时阅读 [references/control-evidence.md](references/control-evidence.md)；校验输入限制和输出媒体时阅读 [references/validation.md](references/validation.md)。

## 职责边界

- 只处理一个 H3 Generation Unit，不理解整首歌曲、Verse、Chorus、完整 MV 或跨 Generation Unit 的导演意图。一个单元可以是连续单镜，也可以是最多 2–3 个明确切点的内部多镜，但必须由上层冻结其形态。
- 通过宿主提供的 `video.generate`、`video.status` 和 `video.result` 调用 `minimax-h3-ref2va`。不直接访问 H3 Runtime 的 `/v1/videos`，不管理 Kubernetes、GPU、模型权重或部署参数。
- 不修改已冻结的 Prompt、时长、画幅或参考资产。创作输入变化必须由上层创建新的 Generation Unit Version。
- MCP 工具缺失或契约不满足时停在提交前，报告缺少的能力和已校验输入。不得把 Runtime 冒烟接口当作 MCP，不能改用 `verdantflare-video`、其他模型或远端 Provider。
- 任务完成只产生候选，不代表镜头批准。身份、表演、连续性、节奏和创作质量由 `verdantflare-music-mv` 或调用方审核。

## 执行状态

为每个 Attempt 持久化 `generation_unit_id`、`attempt_id`、输入摘要、幂等键、`video_task_id`、MCP 状态、Artifact 引用、媒体检查和错误。状态机为：

```text
ready -> submitting -> queued -> running -> validating -> candidate_ready
submitting | queued | running | validating -> failed
ready | queued | running -> cancelled
```

失败和拒绝不原地回退。相同创作输入需要技术重试时创建新 Attempt；输入变化时拒绝重试并要求新的 Generation Unit Version。

## 核心规则

1. 只接受状态为 `frozen`、时长 4 至 15 秒、模型为 `minimax-h3-ref2va` 的 Generation Unit。
2. 音频参考不能作为唯一输入；每个请求必须至少包含一张图片或一段视频。引用必须是受控 Artifact，不接受宿主机绝对路径或未经登记的临时 URL。
3. 使用稳定幂等键 `generation_unit_id/attempt_id` 调用 `video.generate`。同一 Attempt 不得再次创建任务。
4. 获得 `video_task_id` 后立即持久化。进程重启、连接超时或状态不确定时只查询 `video.status`，不得重新提交。
5. 当前没有可信的细粒度进度时只报告 `queued` 或 `running`，不估算完成百分比。
6. `video.result` 成功后登记不可变 Artifact，再校验媒体、逐秒动态接触表和完整播放。只看首帧、关键帧、尾帧不能证明视频成立；校验失败是 Attempt 失败，不把损坏文件交给上层审核。
7. 保留生成视频的原生音轨用于候选审核，但明确标记为非最终歌曲音轨；最终 MV 必须由上层静音并重新挂载批准 Master。

## 完成条件

只有任务状态成功、结果 Artifact 已登记、媒体校验通过、Provenance 完整且三类审核帧已生成，才能返回 `ShotCandidate`。返回 Generation Unit、Attempt、Video Task、Artifact、运行时版本、输入摘要、媒体参数和技术检查；不得声称镜头或 MV 已批准。
