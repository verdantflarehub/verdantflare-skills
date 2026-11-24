# VerdantFlare Music MV：JSON 机器契约样例

## 1. 当前依据

- [公共规范](00.common.rules.md)：目录结构、B-S-F-GU 命名与 World 解析规则。
- [公共验收与审核门](00.common.acceptance.md)：冻结与渲染任务确认门禁。

## 2. `input/manifest.json` 样例

提交给模型前的领域机器可读不可变契约（Project 文件之一，不替代 `.vf/project.json`）：

```json
{
  "schema_version": 1,
  "generation_unit_id": "B01-v1",
  "status": "storyboard_and_four_internal_shots_approved_not_submitted",
  "created_at": "2026-09-07",
  "images": [
    {
      "picture": 1,
      "path": "01-character-card.png",
      "purpose": "identity_and_story_wardrobe",
      "width": 2048,
      "height": 2048,
      "sha256": "24bfa3b1743cd6f0acc7d8699eda78995050e1a6d8f22eb48e7d847f750ab0c0",
      "layout": "four approved bust-level face calibrations above a faceless front-left-back wardrobe turnaround"
    },
    {
      "picture": 2,
      "path": "02-storyboard-grid.png",
      "purpose": "ordered_story_beats",
      "width": 2592,
      "height": 4608,
      "sha256": "9dbdb5c021035f3395d568b63e2a11e495f3162426efd05c909880dbfc852111",
      "reading_order": "left_to_right_top_to_bottom",
      "labels_removed": false,
      "labels": "F01-F08",
      "storyboard_version": "current",
      "change": "The awkward skipping frame was removed. The former leaf-removal and kiss frames are renumbered F07 and F08."
    },
    {
      "picture": 3,
      "path": "03-scene-design.png",
      "purpose": "tower_bridge_environment",
      "width": 941,
      "height": 1672,
      "sha256": "e2d92255005b1bd43fdad31c50102d7773cb5a27f4be9df2aae0d28d6c060cbc",
      "contains_people": false
    }
  ],
  "audio": {
    "path": "04-audio-excerpt.wav",
    "purpose": "rhythm_and_performance",
    "sample_rate_hz": 48000,
    "channels": 2,
    "sha256": "5c54fccf3896dee98d7ea788aa53baa6a8082a36dc1d9e2d9774abdd6851fd56"
  },
  "shot_design": {
    "review_document": "../review.md",
    "status": "approved",
    "review_strips": [
      {
        "path": "../output/review/Shot-1-S01-F1-F2.png",
        "sha256": "0d5357b53605050daadcdac39626d3ef0576449d7e8a21cdc985cfbb8b32f4b2"
      }
    ],
    "production_numbering_strategy": "numbered eight-frame current grid; label leakage is an explicit experiment failure condition",
    "execution_structure": "pending; four internal shots exceed the current single-GenerationUnit limit of three"
  },
  "baseline": {
    "generation_unit_id": "B01-baseline",
    "candidate": "../output/baseline/B01-attempt_06-delivery-10s.mp4",
    "input_mode": "one identity image plus eight independent full-frame storyboard images"
  },
  "submission_policy": "The numbered eight-frame grid and four internal Shot designs are approved. Do not submit H3 until exact timings, machine prompts, and new Generation Units are compiled and frozen."
}
```

## 3. Shot Timeline 样例

所有区间采用左闭右开 `[start_ms, end_ms)`，按 `start_ms` 升序，无重叠或空洞；第一镜从 `0` 开始，最后一镜 `end_ms` 等于 Master 实际时长。允许的 `shot_type`：`performance`、`narrative`、`concept`、`insert`、`editorial`；允许的 `source_plan.type`：`generated_take`、`approved_asset`、`storyboard_placeholder`（冻结与最终装配不得含 `storyboard_placeholder`）。

```json
{
  "mv_id": "mv_20260831_001",
  "music_asset_version_id": "music_version_001",
  "master_artifact_id": "artifact_master_001",
  "master_duration_ms": 200000,
  "timeline_version": 1,
  "status": "draft",
  "shots": [
    {
      "shot_id": "shot_010",
      "start_ms": 42000,
      "end_ms": 47500,
      "shot_type": "performance",
      "lyric_context": "批准歌词及演唱状态",
      "beat_events_ms": [42000, 43500, 45000, 46500],
      "visual_intent": "副歌首次进入，正面近景建立歌手身份",
      "continuity_state_id": "continuity_chorus_a",
      "source_plan": {
        "type": "generated_take",
        "generation_unit_id": "gen_007_v1",
        "in_ms": 1000,
        "out_ms": 6500
      },
      "review_status": "pending"
    }
  ]
}
```

## 4. Generation Unit 样例

`unit_form` 只允许 `continuous_single_shot`（按动作 beat 组织，不含 cut）或 `internal_multi_shot`（最多 2–3 个 Shot，切点严格递增）。提交前验证：`status=frozen`、内部 Shot 满足三同定律、时长 `4000..15000ms`、模型为 `minimax-h3-ref2va`、引用均为不可变 Artifact、动作参考覆盖目标时长、音频片段来自批准 Master、Prompt 为 H3 Ref2VA 六段式且不含基础设施参数。

```json
{
  "generation_unit_id": "gen_007_v1",
  "mv_id": "mv_20260831_001",
  "model": "minimax-h3-ref2va",
  "status": "frozen",
  "duration_ms": 12000,
  "frame_rate": 24,
  "aspect_ratio": "9:16",
  "unit_form": "continuous_single_shot",
  "prompt": "由批准 Treatment、Visual Bible、单元形态和镜头意图编译的 H3 六段式提示",
  "references": {
    "images": [
      {
        "asset_version_id": "appearance_version_003",
        "artifact_id": "artifact_portrait_front",
        "purpose": "identity"
      }
    ],
    "videos": [
      {
        "artifact_id": "artifact_motion_reference_021",
        "purpose": "performance"
      }
    ],
    "audios": [
      {
        "artifact_id": "artifact_master_excerpt_42000_54000",
        "purpose": "rhythm_and_performance"
      }
    ]
  },
  "continuity": {
    "state_id": "continuity_chorus_a",
    "previous_tail_frame_artifact_id": "artifact_gen_006_tail"
  },
  "candidate_count": 1
}
```
