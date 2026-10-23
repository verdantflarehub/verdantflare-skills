# MOSS-Image 自托管引擎调用说明

## 1. 当前依据

- [调用契约](contracts.md)：Studio Image MCP 工具发现、任务轮询与 Artifact 归档。
- [生成后内容分级评估步骤](content-rating-step.md)：MOSS-Image 产物生成后的 18+ 评估与 `image.rate_content` 标记。

## 2. 引擎定位与能力发现

`moss-image` 是 Image 应用的自托管文生图引擎（基于 `Comfy-Org/MOSS-Image` INT8-ConvRot 权重与 `flux2-vae_small`），主打中英文密集文字渲染（海报、包装、信息图）与原生 JSON 版式控制。

- **统一入口**：经 Studio MCP 的 `image.create`（或已注册的 `image.generate`）调用，传入 `engine: "moss-image"`；底层 `image-moss-api` 仅在集群内受控可达，客户端不直连。
- **能力核验**：调用前通过 `image.capabilities` 核验 `engine="moss-image"` 已声明、支持 `rate_content` 且尺寸与版式规则匹配。
- **非渠道引擎**：`moss-image` 是自托管 GPU 引擎，不经过渠道应用，没有上游 Key 或美元预算。

## 3. 请求参数与拒绝边界

### 3.1 支持的参数

- **`project_id` / `idempotency_key`**：必填，使用真实 Studio 项目 ID 与固定幂等键。
- **`engine`**：固定传 `"moss-image"`。
- **`prompt`**：自然语言或标签串，最长 4096 个编码器 token；与 `layout` 至少提供一个，同时提供时由服务端合并为同一提示词。
- **`layout`**（可选，实验性）：版式 JSON 对象，遵循官方 `yxyx` + `abs_centralized` 规范：
  - `summary.caption` 必填，`summary.meta` 与 `summary.canvas` 可选。
  - `items` 数组最多 **32 项**，每项包含 `label`（`text` 或 `object`）、`caption`、`bbox`（`[y_min, x_min, y_max, x_max]`，原点在画布中心，`y` 范围 `[-H/2, H/2]`、`x` 范围 `[-W/2, W/2]`），文字项另含 `text`。越界直接拒绝，不自动裁剪。
- **`width` / `height`**：默认 `1024` × `1024`；必须为 16 的正整数倍，总像素在 `0.25M`–`4M` 之间（推荐 `1M`、`1.5M`、`2M`）。
- **`steps` / `guidance` / `seed`**：默认采用官方 50 步、CFG `3.0`、Euler、shift `5`（跳第 22 层）；`seed` 省略时由服务端生成并写入结果。
- **`negative_prompt`**：可选，默认为空；`moss-image` 有独立负向分支，不套用 Codex 通道的负向词剥离清洗。

### 3.2 显式拒绝的参数

`moss-image` 仅支持文生图与版式文生图（每次生成 1 张），不支持编辑、局部重绘或渠道计费。以下参数或操作一律返回 `INVALID_ARGUMENT`，调用时不得传入：

- `reference_images`、`source_artifact_id`、`reference_artifact_ids`、`mask`
- `image.edit`、`image.inpaint`、`action="edit"`
- `prefer_4k`、`channel_account_id`、`budget_limit`

## 4. GPU 租约、超时与恢复

- **按需启停与租约**：`moss-image` 使用按需启停租约 `gpu-task-moss-image`（空闲 10 分钟缩回 0，等待 Music3 空闲）。冷启动或排队时状态可能返回 `ENGINE_NOT_READY` 或 `queued`，继续按原 `task_id` 轮询，不换键重提。
- **超时上限**：单任务推理超时上限为 `300 s`（含冷启动等待最高 `600 s`）。
- **重启与恢复**：若底层 Pod 重启导致运行中作业丢失，任务状态记为 `ENGINE_RESTARTED` 并失败，服务端不自动重跑；由调用方核对记录后决定是否以新幂等键重新提交。已生成 PNG 但 Artifact 上传未完成的任务，由服务端按原确定性 `write_id` 恢复上传。

## 5. 生成后分级评估（K09）

取得 `moss-image` 生成结果后，必须先按 [生成后内容分级评估步骤](content-rating-step.md) 与 [18+ 评估规范（rubric v1.0）](content-rating-rubric.md) 完成图像、正向提示词与版式文字的 18+ 评估，并调用 `image.rate_content` 提交结果（`general` 与 `nsfw` 均提交），再决定交付摘要展示与 Project 同步。
