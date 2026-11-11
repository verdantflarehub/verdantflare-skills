# Google 图像调用（Nano Banana 系列）

## 1. 发现与渠道归属

通过 Studio MCP 调用 `image.capabilities`，确认 `engine="gemini"`（对应渠道应用 `channel_id="google"`，渠道展示名 `Google`）已启用并配置完整。新任务显式传入真实 `project_id` 与固定 `idempotency_key`。

Google 渠道图像模型遵循 **`display_name`（展示名）与 `model_id`（真实调用编码）分离** 规范，所有模型共享香蕉标识 `nano-banana.png`：

| 展示名（`display_name`） | 模型编码（`model_id`）                                               |
| :----------------------- | :------------------------------------------------------------------- |
| `Nano Banana 2.1`        | `gemini-nano-banana-2.1`（默认模型，Flash 级高保真，`$0.0336`/张）   |
| `Nano Banana Pro`        | `gemini-3-pro-image`（兼容 `-preview`，旗舰光学摄影感，`$0.134`/张） |
| `Nano Banana 2`          | `gemini-3.1-flash-image`（兼容 `-preview`，极速锚定，`$0.0672`/张）  |
| `Nano Banana 2 Lite`     | `gemini-3.1-flash-lite-image`（轻量高性价比批量草图，`$0.0336`/张）  |
| `Nano Banana`            | `gemini-2.5-flash-image`（经典兼容底座，按既有任务或显式指定使用）   |

## 2. 请求参数与边界

- **支持操作**：`image.create`（或已注册的 `image.generate`）支持文生图（T2I）及携带 `source_artifact_id` / `reference_artifact_ids` 的多参考生图；`image.edit` 支持图生图（I2I）与多参考融合编辑（R2I），主参考图 `source_artifact_id` 必填，连同 `reference_artifact_ids` 合计 **1–6 张参考图**，单任务参考图总容量 `<= 24MB`。
- **画幅比例（`aspect_ratio`）**：支持 `1:1`、`2:3`、`3:2`、`3:4`、`4:3`、`9:16`、`16:9`（通过 `generationConfig.imageConfig.aspectRatio` 透传）。
- **分辨率与编码格式（`resolution` -> `imageSize`）**：`image-mcp-server-v0.2.58` 与 `google-channel-v0.1.29` 已将 `resolution`（`1k` / `2k` / `4k`）映射为 `generationConfig.imageConfig.imageSize`（`1K` / `2K` / `4K`，对不支持 `imageSize` 的旧模型如 `gemini-2.5-flash-image` 自动回退 `1K` 档位；修复记录见 `docs/archive/bugs/bug_20261111_image_chatgpt_google_2k_resolution_mismatch.md`）。请求 `resolution="2k"` 时上游按 `2K` 档位 16 像素对齐网格返回（例如 `9:16` 返回 `1536×2752`、`16:9` 返回 `2752×1536`、`1:1` 返回 `2048×2048`；`1k` 档位下 `9:16` 返回 `768×1376`），且上游原生返回可能为 `image/jpeg` 或 `image/png`（如 `gemini-nano-banana-2.1` 常返回原生 JPEG）。下载后必须按文件头检查真实 MIME 与像素尺寸：若需转存为 PNG 或裁切为精确 `9:16`，须保留原始 JPEG 字节与 SHA-256 并单独登记转码／裁切副本，严禁对 `1K` 回退图像做插值放大后冒充原生 2K。
- **渠道与预算参数**：`channel_account_id`（可选）指定 Google 渠道账户，省略时使用渠道默认账户；`budget_limit`（可选）为单任务美元预算上限（十进制正数字符串，如 `"0.20"`），宿主网关未自动注入时显式传入。
- **写实人物参考提示词**（仅适用于 Studio 已登记的 AI 角色或已获授权的形象）：优先调用 `image.edit`（主参考图走 `source_artifact_id`），用英文自然语言描述场景、服装与角色外观特征，人物身份交由参考图传达，不写“严格复制参考图面部身份”一类祈使句；上游返回安全拦截或未出图时如实向用户报告 `reason_code`（如 `image_safety`、`prohibited_content`、`no_image_returned`），不自动换幂等键重提，也不反复改写措辞试探过滤边界。
- **显式拒绝的边界**：Google 渠道当前声明 `max_Reference_masks: 0`，**不支持遮罩局部重绘**（不调用 `image.inpaint`，不传 `mask_artifact_id` 或 `mask`）；不传 `moss-image` 专用的 `layout`、`steps`、`guidance` 参数；不将自适应原生 1K 尺寸伪造或插值冒充为 2K / 4K。

## 3. 恢复、分级评估与交付

1. 提交后立即保存 `task_id` 与原 `idempotency_key`。遇到等待超时或 `submission_unknown` 先按原键核对已有任务，不自动换键重提。
2. 下载产物后校验实际字节数、宽高、真实 MIME（JPEG / PNG）与 SHA-256，核对输出像素是否符合请求的 `1k` / `2k` / `4k` 档位。
3. 按 [生成后内容分级评估步骤](content-rating-step.md) 与 [18+ 评估规范（rubric v1.0）](content-rating-rubric.md) 执行 K09 分级评估并调用 `image.rate_content` 提交结果，仅 `general` 产物归档至当前 Studio Project。
