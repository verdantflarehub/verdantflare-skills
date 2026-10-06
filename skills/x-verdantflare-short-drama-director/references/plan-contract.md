# 本地计划契约 v1

`plan.json` 是前期策划交接件，不是 Station API 请求、WorldStore 记录或人工审核决定。使用 UTF-8 JSON，整数毫秒；`schema_version` 固定为 `1`。保存后运行 `python3 scripts/validate_plan.py plan.json`。

根对象包含：`project_ref`、`kind`（`film`、`advertisement`、`music_video`）、`duration_ms`、`aspect_ratio`、`source`、`assets`、`shots`。`project_ref` 是本地策划引用，不是 Video MCP 所需的正式 `project_id`；提交生成前由 Video Skill 解析真实项目 ID。`source` 包含来源 `type` 和可追溯的 `reference`。`assets` 包含 `characters`、`looks`、`locations`、`props` 四个数组；每项有唯一 `id` 和可读 `description`。Look 另有 `character_id`。已存在的批准资产可附 `asset_version_id`；资产尚未制作时省略此字段，不填占位版本号。

`shots` 按时间排序且从 `0` 连续覆盖 `duration_ms`。每项包含：

- `id`、`start_ms`、`end_ms`、`purpose`、`action`、`start_state`、`end_state`、`audio`、`continuity_group`（同一地点与连续时间的本地分组，如 `B01`）；
- `location_id`、`character_ids`、`look_ids`、`prop_ids`，引用 `assets` 中的 ID；
- `camera`：`framing`、`position`、`movement`，固定机位写 `static`。

可选 `generation_units` 数组包含 `id`、`start_ms`、`end_ms`、按时间顺序排列的 `shot_ids`、`form`（`continuous_single_shot` 或 `internal_multi_shot`）、`model`。同一 Shot 只能进入一个单元，单元不得跨连续性组、地点或同一人物的不同 Look，也不得跨越未覆盖的时间段。`model=h3` 或 `minimax-h3-ref2va` 时单元须为 4–15 秒，连续单镜只含一镜，内部多镜含 2–3 镜。未知模型不猜测能力：可以省略整个 `generation_units`。

广告计划另有 `brief`：非空的 `benefit`、`evidence`、`cta` 与字符串数组 `prohibited_claims`。可选 `assumptions` 和 `open_questions` 都是字符串数组，用于说明资料缺口。校验器检查时码、引用和模型单元的确定性约束，不评价创意质量或证据真假。
