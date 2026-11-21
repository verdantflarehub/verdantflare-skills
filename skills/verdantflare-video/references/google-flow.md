# Google Flow 渠道调用

## 1. 模型与参考图限制

通过 Studio Video MCP 显式选择 `route=google-flow`，先查 `video.capabilities` 与 `video.preflight` 再提交。下表为当前 MCP 接入范围，提交参数以 `gflow models --json` 与服务端模型目录为准：

| 模型             | 支持模式与上限                                                                                  |
| :--------------- | :---------------------------------------------------------------------------------------------- |
| `veo-lite`       | `text_to_video` / `reference_to_video`；最多 3 张参考图（`ref_cap=3`）；4 / 6 / 8s；16:9 / 9:16 |
| `veo-fast`       | `text_to_video` / `reference_to_video`；最多 3 张参考图（`ref_cap=3`）；4 / 6 / 8s；16:9 / 9:16 |
| `veo-quality`    | **仅 `text_to_video`**；**不支持参考图（`ref_cap=0`）**；4 / 6 / 8s；16:9 / 9:16                |
| `omni-1.2-flash` | `text_to_video` / `reference_to_video`；最多 7 张参考图（`ref_cap=7`）；3–15s；16:9 / 9:16      |

### 1.1 Omni Flash 1.2 的订阅时长

Google AI Ultra 单段支持 3–15 秒。用户指定 15 秒时，使用 `model=omni-1.2-flash` 和 `duration_seconds=15` 预检，并沿用该时长提交。Plus、Pro 同为 3–15 秒，未订阅账号为 3–10 秒。用户指定较短镜头时沿用镜头时长。[Google Flow 官方模型能力表](https://support.google.com/flow/answer/16352836?hl=en)

Flow 网页支持首尾帧、参考视频和原片编辑。当前 Video MCP 的 Google Flow 渠道可传参考图，视频输入和首尾帧仍以渠道预检结果为准。[Google Flow 视频编辑说明](https://support.google.com/flow/answer/16935718?hl=en)

## 2. 禁止提交与选型约束

- **严禁向 `veo-quality` 提交参考图或 `mode=reference_to_video`**：`veo-quality` 在 Google Flow 侧 `ref_cap=0`，Video MCP 与 `google-flow-channel` 网关均在前置校验阶段直接拒绝带参考图的 `veo-quality` 请求。
- **需要参考图锁脸、锁服装或锁场景时**：选用 `veo-fast`（最多 3 图）、`veo-lite`（最多 3 图）或 `omni-1.2-flash`（最多 7 图，支持 3–15s），不得传参考视频或参考音频。
- **单张/多张参考图说明**：`google-flow` 当前 `reference_to_video` 不接受 `purpose=first_frame` 或 `purpose=last_frame`；参考图须为同项目已导入图片 Artifact，并在 `references.images` 中显式填写语义 `purpose`（如 `character`、`outfit`、`scene`）。
- **失败不静默切换**：预检或提交返回 `unsupported_references`、`mode_reference_mismatch`、`authorization_required` 或 `quota_exhausted` 时保留原错误并停止，不自动换模型或换收费渠道重试。
