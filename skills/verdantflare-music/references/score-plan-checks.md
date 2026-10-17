# 配乐计划检查

## 1. 用途

[贴片配乐](picture-scoring.md)形成线性编排后，在技能目录运行：

```sh
python3 scripts/validate_score_plan.py score-plan.json
```

脚本仅用 Python 标准库，检查声明的实测元数据和**不变速、无重叠**的时间线。它不读媒体、不渲染、不证明远端归属或听感。交叉淡化或变速编排使用实际剪辑契约，不删掉重叠段骗过检查。

## 2. 输入

文件含 `schema_version: background-score-v1`、全片 `duration_seconds`、`cues` 数组、`silences` 数组。cue 字段：

- `cue_id`：稳定标识；同一源的不同选段使用不同 cue ID。
- `timeline_start/timeline_end`：全片秒数。
- `source_start/source_end/source_duration_seconds`：源选段及实际源时长，不能填生成目标时长。
- `source_sha256`：实际字节摘要；Artifact/ContentRef 在项目资产清单保留。
- `vocal_review`：`pending/clear/present`；只有实际听审才用 clear，并在 `vocal_review_evidence` 写对应审核记录定位。没有证据仍报告 pending。
- 可选 `required_bpm/measured_bpm/bpm_tolerance`：项目要求核验 BPM 时一起记录实测与容差；偏差或缺实测会失败。

silence 包含 `start/end/reason`，记录真实停乐安排。没有停乐则空数组，不能拿静默填补意外源短缺而不披露。持续源中间停乐时，将可听部分拆成多个 cue 并保留正确源偏移，把停乐区间单独写入 silences。来源与时间线长度容差为一个 48 kHz 样本。

此文件是技能检查输入，不是新的后台 Edit 契约。输入中的来源、时长和听审结论须来自真实产物与记录，脚本不签发批准。

## 3. 输出

源越界、覆盖缺口、静默冲突、重复 ID、非有限时间、隐含变速或缺少要求的 BPM 测量会使技术检查失败。`technical_pass`、`review_pending`、`vocal_issues` 分开报告。

- 退出 0：检查通过，且输入引用了无人声听审证据。
- 退出 1：技术失败或存在唱声。
- 退出 2：技术通过但听审缺失。

退出 0 也不是最终音质、叙事、选用或发布批准。按[贴片配乐](picture-scoring.md)继续实际音频检查和项目归档。
