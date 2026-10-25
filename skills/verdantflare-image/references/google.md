# Google 图像调用（Nano Banana 系列）

## 1. 发现与渠道归属

通过 Studio MCP 调用 `image.capabilities`，确认 `engine="gemini"`（对应渠道应用 `channel_id="google"`，渠道展示名 `Google`）已启用并配置完整。新任务显式传入真实 `project_id` 与固定 `idempotency_key`。

Google 渠道图像模型遵循 **`display_name`（展示名）与 `model_id`（真实调用编码）分离** 规范，所有模型共享香蕉标识 `nano-banana.png`：

| 展示名（`display_name`） | 模型编码（`model_id`） |
| --- | --- |
| `Nano Banana 2.1` | `gemini-nano-banana-2.1`（`engine="gemini"` 默认模型，Flash 级高保真与多轮一致性，`$0.0336`/张） |
| `Nano Banana Pro` | `gemini-3-pro-image`（兼容 `-preview`，旗舰光学摄影感与复杂构图，`$0.134`/张） |
| `Nano Banana 2` | `gemini-3.1-flash-image`（兼容 `-preview`，极速现实世界锚定，`$0.0672`/张） |
| `Nano Banana 2 Lite` | `gemini-3.1-flash-lite-image`（最轻量高性价比批量草图，`$0.0336`/张） |
| `Nano Banana` | `gemini-2.5-flash-image`（经典兼容底座，仅按既有任务或显式指定使用） |

## 2. 请求参数与边界

- **支持操作**：
  - `image.create`（或已注册的 `image.generate`）：文生图（T2I），也可携带 `source_artifact_id` / `reference_artifact_ids` 执行多参考生图。
  - `image.edit`：图生图（I2I）与多参考融合编辑（R2I），主参考图 `source_artifact_id` 必填，连同 `reference_artifact_ids` 合计 **1–6 张参考图**，单任务参考图总容量 `<= 24MB`。
- **画幅比例（`aspect_ratio`）**：支持 `1:1`、`2:3`、`3:2`、`3:4`、`4:3`、`9:16`、`16:9`，输出格式为 `PNG`（自适应原生尺寸）。
- **渠道与预算参数**：
  - `channel_account_id`（可选）：指定 Google 渠道账户，省略时使用渠道默认账户。
  - `budget_limit`（可选）：单任务美元预算上限（十进制正数字符串，如 `"0.20"`）；宿主网关未自动注入时显式传入。
- **写实人物参考提示词**（仅适用于 Studio 已登记的 AI 角色或已获授权的形象）：
  - 优先调用 `image.edit`（主参考图走 `source_artifact_id`），用英文自然语言描述场景、服装与角色外观特征，人物身份交由参考图传达；不写“严格复制参考图面部身份”一类祈使句。
  - 上游返回安全拦截或未出图时如实向用户报告 `reason_code`（如 `image_safety`、`prohibited_content`、`no_image_returned`），不自动换幂等键重提，也不反复改写措辞试探过滤边界；仍需出图时改用其他已授权渠道或调整创作内容。
- **显式拒绝的边界**：
  - Google 渠道当前声明 `max_Reference_masks: 0`，**不支持遮罩局部重绘**（不调用 `image.inpaint`，不传 `mask_artifact_id` 或 `mask`）。
  - 不传 `moss-image` 专用的 `layout`、`steps`、`guidance` 参数；不将自适应原生尺寸伪造或插值冒充为 4K。

## 3. 恢复、分级评估与交付

1. 提交后立即保存 `task_id` 与原 `idempotency_key`。遇到等待超时或 `submission_unknown` 先按原键核对已有任务，不自动换键重提。
2. 下载产物后校验实际字节数、宽高、PNG MIME 与 SHA-256。
3. 按 [生成后内容分级评估步骤](content-rating-step.md) 与 [18+ 评估规范（rubric v1.0）](content-rating-rubric.md) 执行 K09 分级评估并调用 `image.rate_content` 提交结果，仅 `general` 产物归档至当前 Studio Project。
