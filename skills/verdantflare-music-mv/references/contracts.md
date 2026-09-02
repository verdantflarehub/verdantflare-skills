# Music MV 项目契约

## Shot Timeline

所有区间采用左闭右开 `[start_ms, end_ms)`，按 `start_ms` 升序，不允许重叠或空洞；第一镜从 `0` 开始，最后一镜的 `end_ms` 等于 Master 实际时长。

```yaml
mv_id: mv_20260831_001
music_asset_version_id: music_version_001
master_artifact_id: artifact_master_001
master_duration_ms: 200000
timeline_version: 1
status: draft
shots:
  - shot_id: shot_010
    start_ms: 42000
    end_ms: 47500
    shot_type: performance
    lyric_context: "批准歌词及演唱状态"
    beat_events_ms: [42000, 43500, 45000, 46500]
    visual_intent: "副歌首次进入，正面近景建立歌手身份"
    continuity_state_id: continuity_chorus_a
    source_plan:
      type: generated_take
      generation_unit_id: gen_007_v1
      in_ms: 1000
      out_ms: 6500
    review_status: pending
```

允许的 `shot_type` 为 `performance`、`narrative`、`concept`、`insert` 和 `editorial`。`source_plan.type` 必须明确为 `generated_take`、`approved_asset` 或 `storyboard_placeholder`；冻结时间线和最终装配不得包含 `storyboard_placeholder`。

## Generation Unit

```yaml
generation_unit_id: gen_007_v1
mv_id: mv_20260831_001
model: minimax-h3-ref2va
status: frozen
duration_ms: 12000
frame_rate: 24
aspect_ratio: "9:16"
unit_form: continuous_single_shot
prompt: "由批准 Treatment、Visual Bible、单元形态和镜头意图编译的 H3 六段式提示"
references:
  images:
    - asset_version_id: appearance_version_003
      artifact_id: artifact_portrait_front
      purpose: identity
  videos:
    - artifact_id: artifact_motion_reference_021
      purpose: performance
  audios:
    - artifact_id: artifact_master_excerpt_42000_54000
      purpose: rhythm_and_performance
continuity:
  state_id: continuity_chorus_a
  previous_tail_frame_artifact_id: artifact_gen_006_tail
candidate_count: 1
```

`unit_form` 只允许 `continuous_single_shot` 或 `internal_multi_shot`。连续单镜以动作 beat 组织且不得包含 cut；内部多镜最多 2–3 个 Shot，切点严格递增并位于单元时长内。

提交前验证：状态为 `frozen`；时长为 4000 至 15000 毫秒；模型为 `minimax-h3-ref2va`；单元形态已冻结；引用均为不可变 Artifact；动作参考视频覆盖目标时长；音频片段来自批准 Master；Prompt 使用 H3 Ref2VA 六段式结构且不含基础设施参数。

## Artifact 关系

每个 Shot Candidate 至少关联：

- `generation_unit_id` 和 `attempt_id`；
- `video_task_id` 和输出 `artifact_id`；
- 模型、运行时版本和输入摘要；
- 容器、时长、宽高、帧率、视频编码和音频编码；
- 首帧、关键帧、尾帧 Artifact；
- 技术检查和人工审核结论。

候选和批准关系均为不可变历史。选择新候选时更新 Shot 的选择记录，不覆盖旧文件或旧结论。
