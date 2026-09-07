# Music MV 项目契约

## 1. 目录结构契约

项目存储在 `.output/music-mv/<创作者>/<MV项目>/`，分为“项目全局层”与“单元执行层”：

```text
.output/music-mv/<创作者>/<MV项目>/
├── project.yaml                 # 项目全局时钟与元数据
├── treatment.md                 # 顶层导演方案与叙事脉络
├── visualbible.md              # 视觉圣经与人物原案
├── review.md                    # 【项目全局总审核：审核 Block 规划列表、跨段连续性与成片交付】
├── master.wav                   # 批准 Master 音频（唯一时钟源）
├── .archive/                    # 历史淘汰版本与旧实验归档
└── units/                       # 执行单元集合（以 Block 命名的子目录）
    └── B01/                     # 故事段落单元目录（如 B01、B01-v1）
        ├── review.md            # 【Block 单元审核：微观审核角色服装、本段剧情、分镜条带与生成批次】
        ├── source/              # 【原子源素材与构建代码】
        │   ├── character/       # 01-front-neutral.png, 02-front-smile.png, 03-left-15deg.png, 04-right-15deg.png, 05-wardrobe-turnaround.png
        │   ├── scene/           # scene-design.png (纯景图原文件)
        │   ├── storyboard/      # F01.png ~ F08.png (独立无标签高清单格分镜)
        │   ├── audio/           # master-clip.wav (单元对应的 Master 切片原音频)
        │   └── build/           # compose_inputs.go (确定性合成与切片脚本)
        ├── input/               # 【对齐模型协议的最终打包输入】
        │   ├── 01-character-card.png    # Picture 1: 人物设计(上区) + 角色设计(下区) 合成设定卡
        │   ├── 02-storyboard-grid.png   # Picture 2: 多格分镜排版图
        │   ├── 03-scene-design.png      # Picture 3: 纯场景设计图
        │   ├── 04-audio-excerpt.wav     # Picture 4: 单元音频切片
        │   └── manifest.json            # 机器不可变输入契约
        └── output/              # 【审阅条带与产物】
            ├── review/          # Shot-1-S01-F1-F2.png (供人工审核的首尾帧条带图)
            ├── generated/       # 模型生成的原始视频与尝试记录
            └── baseline/        # 上一版本优秀基线对照
```

> [!IMPORTANT]
> **文件命名契约红线（严禁中文文件名）**：
> 所有契约文件、脚本、输入输出图像、音频与视频的文件名，**必须统一使用标准英文小写短横线命名规范（ASCII kebab-case，如 `review.md`、`01-character-card.png`）**。
> 严禁使用中文命名任何 `.md`、`.png`、`.wav`、`.mp4` 或 `.json` 文件（如 `制作审核.md`、`角色设定卡.md` 均属不合格）。文档正文内容可使用中文表述，但系统文件名必须为纯英文，以杜绝跨平台编码错乱、脚本异常与 Git 乱码。

## 2. B-S-F 代号命名契约

统一采用 **Block - Shot - Frame** 三级层级代号体系：

| 代号层级                 | 格式            | 含义           | 作用与示例                                                                                                             |
| :----------------------- | :-------------- | :------------- | :--------------------------------------------------------------------------------------------------------------------- |
| **Story (故事单元)**     | -               | **业务情境**   | 由**同一角色、同一地点、同一时间**组成的有机视听单元。<=15s 必须整体组合，>15s 拆分为多个 Block。                      |
| **B (Block / Beat)**     | `B01`, `B02`... | **故事段落**   | 8~15 秒独立叙事/情绪段落。**对齐单元目录名**（`units/B01/`）与 `unit_id`（`B01` 或 `B01-v1`）。                        |
| **S (Shot)**             | `S01`, `S02`... | **分镜镜头**   | 剪辑中最终裁切的单镜头（通常 1~5 秒）。段落内简写为 `S01`，跨段落全称 `B01-S01`。                                      |
| **F (Frame)**            | `F01`, `F02`... | **分镜关键格** | 多格分镜图中的具体格（如八格图 `F01`~`F08`）；亦用于 Shot 起落幅标识（`S01-F1` 首帧、`S01-F2` 尾帧）。                 |
| **GU (Generation Unit)** | `B01-GU01`      | **生成单元**   | 依据三同原则组合提交给模型的批次。单次时长 <=15s，承载 2~3 个内部切点。超出时切分子单元（如 `B01-GU01`、`B01-GU02`）。 |

### 分镜组合黄金原则（三同定律）

由于**没有任何视频生成模型支持单次生成 30 秒以上的分镜**，AI 必须依据视听连续性将分镜组合为批次：

- **组合前提（三同）**：**同一个角色**（同一人脸+同一服装）、**同一个地点**（同一场景光影）、**同一个时间**（时序连续）。三者构成一个 **Story**；
- **<= 15 秒**：**必须组合**为一个生成单元（Generation Unit）一次性提交渲染，最大化利用模型内生连续性；
- **> 15 秒**：**必须切分**为多个 8~15 秒的 Block（如 `B01`、`B02`），受限于模型单次 15 秒生成物理极限；
- 不满足三同条件的镜头严禁强行拼入同一生成单元。

## 3. 机器输入契约 (`input/manifest.json`)

`input/manifest.json` 是提交给模型前唯一的机器可读不可变契约，记录输入包的哈希、版式及与审核文档的关联：

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

## 4. 构建脚本契约 (`source/build/`)

- 构建脚本（如 `compose_inputs.go`、`split_wav.go`）必须是**确定性、幂等的纯函数组装器**；
- 脚本只读取 `source/` 下的原素材，直接输出到 `input/`（如合成后的多格分镜、角色设定卡）或 `output/review/`（首尾帧条带图）；
- 严禁在执行过程中向 `source/` 写入派生图像，严禁向工作区根目录丢弃临时切片文件；
- 拼图布局参数、字模尺寸、行高和 padding 在脚本中作为命名常量固化，杜绝手工拼图带来的坐标漂移。

## 5. Shot Timeline

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

## 5. Generation Unit

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

提交前验证：状态为 `frozen`；包含的内部 Shot 严格满足三同定律（同一角色、同一地点、同一时间）；时长严格介于 4000 至 15000 毫秒（<=15s）；模型为 `minimax-h3-ref2va`；单元形态已冻结；引用均为不可变 Artifact；动作参考视频覆盖目标时长；音频片段来自批准 Master；Prompt 使用 H3 Ref2VA 六段式结构且不含基础设施参数。

## 6. Artifact 关系与不可变历史

每个 Shot Candidate 至少关联：

- `generation_unit_id` 和 `attempt_id`；
- `video_task_id` 和输出 `artifact_id`；
- 模型、运行时版本和输入摘要；
- 容器、时长、宽高、帧率、视频编码和音频编码；
- 首帧、关键帧、尾帧 Artifact；
- 技术检查和人工审核结论。

候选和批准关系均为不可变历史。选择新候选时更新 Shot 的选择记录，不覆盖旧文件或旧结论。
