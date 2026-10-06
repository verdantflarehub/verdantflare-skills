# H3 Shot Workflow

## 提交前

1. 从当前 Studio Project 上下文取得 `project_id`，打开最近已提交修订；读取冻结的 Generation Unit 并验证模型、时长、画幅、Prompt 和引用。
2. 若输入来自 World，先调用 `world.get(asset_id, asset_version_id)`，检查授权并解析固定版本中的具体图片、视频或音频 `ContentRef`，再编译为 Video MCP 的 Artifact 引用。不得使用 `latest` 或展示名。
3. 计算规范化输入摘要。若同一 `generation_unit_id` 已有不同摘要，停止并报告版本冲突。
4. 创建不可变 `attempt_id` 和幂等键，先将 Attempt 以 `ready` 状态持久化。
5. 确认宿主提供 `video.generate/status/result`。任一工具缺失时不得直接调用 Runtime。

## 提交与恢复

调用 `video.generate` 前将 Attempt 置为 `submitting`。调用成功后先保存 `video_task_id`，再进入轮询。

显式选择 fal 且需要命令行执行时，从 Skill 根目录使用：

```text
python3 scripts/video_client.py check
python3 scripts/video_client.py generate \
  --model minimax-h3-ref2va \
  --route fal \
  --project-id <project_id> \
  --idempotency-key <generation_unit/attempt> \
  --prompt "<single-shot prompt>" \
  --image-ref <artifact_id>=identity
python3 scripts/video_client.py resume --route fal <video_task_id>
```

引用必须先通过 Project 提交登记为当前项目可用的 Artifact，或从已授权 World 固定版本解析后显式导入/关联到当前项目。需要从 HTTPS 对象导入时使用同一客户端的 `import` 子命令并提供 SHA-256；fal 路线不接收本地裸路径、URL/Base64 参考、fal endpoint 或 `FAL_KEY`。只有同时显式选择 `model=minimax-h3-ref2va`、`route=fal` 才进入 MCP fal 路线，提交响应不确定时将本地 Attempt 保持为 `submission_unknown`，不得改用其他渠道重放。

如果提交响应丢失，使用同一幂等键恢复或查询；不得生成新幂等键重放。若当前 MCP 尚无按幂等键恢复不确定提交的能力，将 Attempt 保持为不确定失败并停止，不能猜测未创建任务。

轮询 `video.status`：

- `queued`、`running`：继续等待并保存最近时间戳；
- `succeeded`：调用 `video.result`；
- `failed`、`cancelled`：保存结构化错误并结束 Attempt；
- 未知状态：视为协议错误，不映射为已知状态。

## 结果处理

`video.result` 必须返回 Artifact 引用而不是大文件正文。登记模型、运行时版本、输入摘要和媒体元数据，执行 `validation.md` 中的全部技术检查，然后提取首帧、时间中点附近关键帧和尾帧。

创建 `ShotCandidate` 后把 Attempt 置为 `candidate_ready` 并返回上层。该状态不包含人工批准。

## 重试

- 下载或本地校验故障：先重取同一结果并修复本地问题，不重新生成；
- 已确认的生成失败且授权预算允许重做：保留 Generation Unit，创建新的 Attempt；提交状态未知时只恢复查询，不创建新任务；
- Prompt、引用、时长、画幅或模型变化：要求上层创建新的 Generation Unit Version；
- 身份、表演、连续性或节奏未通过：这是创作审核失败，由上层决定是否产生新版本，本 Skill 不擅自修改输入；
- 取消只作用于明确的 `video_task_id`，不得删除 Artifact、其他 Attempt 或项目输出。

## 完成与记录

保存 generation_unit_id、attempt_id、输入摘要、幂等键、video_task_id、原始 MCP 状态、Artifact、运行时版本和检查结果。结果登记且 validation.md 技术检查通过后，先通过 `project.commit` 归档到当前 Video Project，再返回 ShotCandidate；保留原生音轨供审核，最终 MV 由上层替换为批准 Master。技术完成不代表创作批准，也不自动调用 `world.register`。
