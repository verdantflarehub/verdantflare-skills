# Video MCP 契约

这是上层领域契约，不是 H3 Runtime 的原始 `/v1/videos` HTTP 契约。Skill 不负责把两者直接桥接。

## `video.generate`

```json
{
  "project_id": "project_anxi_mv",
  "idempotency_key": "gen_007_v1/attempt_01",
  "model": "minimax-h3-ref2va",
  "route": "fal",
  "prompt": "single-shot prompt",
  "duration_seconds": 12,
  "aspect_ratio": "9:16",
  "references": {
    "images": [{"artifact_id": "artifact_portrait_front", "purpose": "identity"}],
    "videos": [{"artifact_id": "artifact_motion_reference_021", "purpose": "performance"}],
    "audios": [{"artifact_id": "artifact_master_excerpt_42000_54000", "purpose": "rhythm_and_performance"}]
  }
}
```

`project_id` 与 `route` 都是新任务的必填字段：

- `project_id` 来自当前 Studio/项目上下文。所有引用 Artifact 必须属于同一项目；不得从文件名、Prompt 或其他任务猜测项目，也不得复用其他项目的 Artifact ID。
- `route` 是本次 Attempt 已解析并冻结的推理渠道。即使采用宿主默认渠道，也必须先解析为明确值再提交；当前规范值包括默认 `h3-vdn` 和显式 `fal`，其他值只在宿主能力声明支持时使用。
- `route=fal` 只对应 `model=minimax-h3-ref2va` 的 Reference-to-Video。它不开放 fal 的 T2V、I2V、FL2V、Prompt Expansion 或 LoRA，也不允许客户端传入 fal endpoint、供应商 request ID 或 `FAL_KEY`。

成功返回领域 `video_task_id`、接受时间和 `queued` 或 `running`。相同幂等键和相同输入返回原任务；相同幂等键和不同输入返回冲突。

## `video.status`

输入 `video_task_id`，返回：

```json
{
  "video_task_id": "video_task_01J...",
  "status": "queued | running | succeeded | failed | cancelled",
  "created_at": "ISO 8601",
  "updated_at": "ISO 8601",
  "error": null
}
```

失败时 `error` 只包含稳定的错误码和安全消息，不包含凭据、签名 URL、内部节点或原始供应商响应。

## `video.result`

只对 `succeeded` 返回：

```json
{
  "video_task_id": "video_task_01J...",
  "artifact_id": "artifact_video_01J...",
  "model": "minimax-h3-ref2va",
  "runtime_version": "video-minimax-h3-api-v0.3.0",
  "input_digest": "sha256:...",
  "media": {
    "duration_ms": 12000,
    "width": 768,
    "height": 1344,
    "frame_rate": 24,
    "video_codec": "h264",
    "audio_codec": "aac"
  }
}
```

领域 MCP 不暴露 Pod、节点、GPU、offload、SGLang 参数、内部文件路径或 Runtime Task ID。

## 推理路线选择


对用户区分以下两个 H3 选项；它们属于同一 MiniMax H3 模型族：

| 名称 | 含义 | 选择规则 |
| --- | --- | --- |
| MiniMax H3 | MiniMax H3 基础模型；用户所说的“原版”对应现有非 Sol 推理路线 | 用户明确指定原版时采用 |
| MiniMax H3 Sol（`h3-sol` / Sol-H3） | 基于 MiniMax H3、采用 NVIDIA Sol-H3 优化推理引擎的版本 | 未指定时默认采用 |

Sol-H3 沿用 MiniMax H3 基础权重，当前 Ref2VA 路线还使用 LightX2V Turbo 四步 LoRA；不能描述为 NVIDIA 重新训练的独立基础模型，也不能承诺画质完全一致或在所有硬件上更快。现有非 Sol 服务是否使用加速适配器须以实际配置为准，不能将“原版”自动等同于未经优化的 Base H3。

- 用户未指定推理路线时，使用宿主 MCP 的默认渠道配置（当前生产配置为 `h3-vdn`）。提交前核对宿主 MCP 能力声明，确认该路线已接入并满足当前 Generation Unit 的要求；缺少明确支持时停止并报告，不得自动回退。
- 用户明确指定 fal 时，使用 `model=minimax-h3-ref2va`、`route=fal`。只有宿主 MCP 声明该渠道可用时才能提交；fal 失败或未配置时不得改用默认渠道重放。
- 用户明确指定 MiniMax H3 原版时，选择宿主明确提供的非 Sol 路线；泛指“H3”且未限定版本时采用宿主配置的默认路线。两种路线都必须先确认能力与实际映射，不能仅凭同一个 `model` 标识判断版本。
- `h3-sol` 是推理路线名称；现有领域契约的 `model` 标识仍为 `minimax-h3-ref2va`。仅在宿主明确声明该标识映射至 Sol-H3 时，才能沿用此契约提交默认任务。不得将 `h3-sol` 擅自写入尚未支持它的 `model` 字段，也不得臆造引擎选择参数。
- `model` 与 runtime route identity 是两个字段：`model=minimax-h3-ref2va` 表示业务模型类型；`route=h3-vdn` 是当前默认渠道，`route=fal` 是已开放的显式 Ref2VA 渠道；历史 Sol 渠道仅在宿主明确重新接入时可选。route identity 不得替代 `model`，每次提交都必须显式传递 route 并留下运行时记录。宿主兼容历史别名 `minimax-h3-sol-ref2va` 与 `minimax-h3-sol-ref2va-4090`，新请求优先使用规范渠道名。
- 将实际推理路线及服务返回的运行时版本写入 Attempt 和候选来源记录，不能仅凭模型标识声称已使用 Sol-H3。已冻结输入或已有任务指定其他路线时，不得静默切换；已有任务沿原引用查询、恢复和下载。
