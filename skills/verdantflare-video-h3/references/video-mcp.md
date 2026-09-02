# Video MCP 契约

这是上层领域契约，不是 H3 Runtime 的原始 `/v1/videos` HTTP 契约。Skill 不负责把两者直接桥接。

## `video.generate`

```json
{
  "idempotency_key": "gen_007_v1/attempt_01",
  "model": "minimax-h3-ref2va",
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
  "runtime_version": "music-minimax-h3-api-v0.1.1",
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
