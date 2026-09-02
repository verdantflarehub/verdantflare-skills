# H3 Shot Workflow

## 提交前

1. 读取冻结的 Generation Unit 并验证模型、时长、画幅、Prompt 和引用。
2. 计算规范化输入摘要。若同一 `generation_unit_id` 已有不同摘要，停止并报告版本冲突。
3. 创建不可变 `attempt_id` 和幂等键，先将 Attempt 以 `ready` 状态持久化。
4. 确认宿主提供 `video.generate/status/result`。任一工具缺失时不得直接调用 Runtime。

## 提交与恢复

调用 `video.generate` 前将 Attempt 置为 `submitting`。调用成功后先保存 `video_task_id`，再进入轮询。

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

- Runtime、队列、下载或媒体校验失败：保留 Generation Unit，创建新的 Attempt；
- Prompt、引用、时长、画幅或模型变化：要求上层创建新的 Generation Unit Version；
- 身份、表演、连续性或节奏未通过：这是创作审核失败，由上层决定是否产生新版本，本 Skill 不擅自修改输入；
- 取消只作用于明确的 `video_task_id`，不得删除 Artifact、其他 Attempt 或项目输出。
