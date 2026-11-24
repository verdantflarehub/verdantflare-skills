# Google Flow 渠道调用

## 1. 默认路由与模型上限

**默认路由规则**：凡调用 Google 系列视频模型（Veo / Omni），**默认一律走 Google Flow（`route=google-flow`）**；除非用户显式指定 Google AI Studio（`route=google`），否则不考虑 Google AI Studio。

通过 Studio Video MCP 显式选择 `route=google-flow`，先查 `video.capabilities` 与 `video.preflight` 再提交。下表为当前 MCP 接入范围：

- **`veo-lite`**：`text_to_video` / `reference_to_video`；最多 3 张参考图（`ref_cap=3`）；4 / 6 / 8s；16:9 / 9:16；原生 720p（不支持 1080p 超分）。
- **`veo-fast`**：`text_to_video` / `reference_to_video`；最多 3 张参考图（`ref_cap=3`）；4 / 6 / 8s；16:9 / 9:16；可选 `upscale="1080p"`。
- **`veo-quality`**：**仅 `text_to_video`**；**不支持参考图（`ref_cap=0`）**；4 / 6 / 8s；16:9 / 9:16；可选 `upscale="1080p"`。
- **`omni-1.2-flash`**：`text_to_video` / `reference_to_video` / `video_editing`；最多 7 图 + 1 段 `.mp4` 原片（≥ 3s）；3–15s；16:9 / 9:16；可选 `upscale="1080p"`。

### 1.1 Omni Flash 1.2 的订阅时长与原片编辑

Google AI Ultra 单段支持 3–15 秒。用户指定 15 秒时，使用 `model=omni-1.2-flash` 和 `duration_seconds=15` 预检，并沿用该时长提交。Plus、Pro 同为 3–15 秒，未订阅账号为 3–10 秒。用户指定较短镜头时沿用镜头时长。[Google Flow 官方模型能力表](https://support.google.com/flow/answer/16352836?hl=en)

`omni-1.2-flash` 已接通 `video_editing` 原片编辑，支持在 `references.videos` 中传入 1 段 ≥ 3 秒的 `.mp4` 原片（上限 128 MiB）并在 `references.images` 中附最多 7 张人物/服饰参考图。[Google Flow 视频编辑说明](https://support.google.com/flow/answer/16935718?hl=en)

### 1.2 生成时可选超分导出（`upscale`）

`video.preflight` / `video.create` / `video.generate` 支持可选参数 `upscale="1080p"`（默认关闭，不传保持原生 720p 出片速度，开启后 0 积分免费超分，单次约增加 15–40 秒后处理导出时间）：

- 支持模型：`omni-1.2-flash`、`veo-fast`、`veo-quality`；`veo-lite` 不支持 1080p 超分，传入时预检直接拒绝（`unsupported_upscale` / `invalid_field: upscale`）。
- 失败降级：若生成已成功扣分、仅最后一步超分偶发超时或不可用，网关自动降级保留并返回原生 720p 成片，并在结果中标注 `upscale_status="fallback_original"`。

## 2. 禁止提交与选型约束

- **严禁向 `veo-quality` 提交参考图或 `mode=reference_to_video`**：`veo-quality` 在 Google Flow 侧 `ref_cap=0`，Video MCP 与 `google-flow-channel` 网关均在前置校验阶段直接拒绝带参考图的 `veo-quality` 请求。
- **需要参考图锁脸、锁服装或锁场景时**：选用 `veo-fast`（最多 3 图）、`veo-lite`（最多 3 图）或 `omni-1.2-flash`（最多 7 图，支持 3–15s）；需要原片视频编辑时仅选用 `omni-1.2-flash`（1 段 `.mp4` 原片 + 最多 7 图），其余模型不得传参考视频，全部模型不支持参考音频。
- **单张/多张参考图说明**：`google-flow` 当前 `reference_to_video` 不接受 `purpose=first_frame` 或 `purpose=last_frame`；参考图须为同项目已导入图片 Artifact，并在 `references.images` 中显式填写语义 `purpose`（如 `character`、`outfit`、`scene`）。
- **失败不静默切换**：预检或提交返回 `unsupported_references`、`mode_reference_mismatch`、`authorization_required` 或 `quota_exhausted` 时保留原错误并停止，不自动换模型或换收费渠道重试。
