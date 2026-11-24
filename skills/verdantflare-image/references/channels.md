# verdantflare-image：模型与渠道参数附录（channels）

本附录承接跨能力共享的模型到渠道自动映射、渠道专属参数与自托管 GPU 租约规则，由 [公共契约](00.common.rules.md) 及各能力入口按需引用。

## 1. 逻辑模型与渠道映射

| 逻辑模型 / 引擎  | 内部渠道与详细附录                                                  |
| :--------------- | :------------------------------------------------------------------ |
| GPT Image 2.6    | `codex`（默认），见[公共契约](00.common.rules.md)                   |
| Nano Banana 2    | `google-flow`（默认，支持 `2k`/`4k` 超分）                          |
| Google AI Studio | `gemini`（仅显式指定），见[google.md](google.md)                    |
| Grok             | `grok`（1K/2K 文生图与单图编辑），见[grok.md](grok.md)              |
| MOSS-Image       | `moss-image`（文字海报/坐标排版），见[moss-image.md](moss-image.md) |

## 2. 各渠道参数边界与 GPU 租约

1. **能力发现**：先调用 `image.capabilities` 确认目标 `engine` 已启用、配置完整且支持所需 `model`、画幅、参考数量与 `rate_content`。
2. **渠道专属参数**：
   - **Google Flow（`engine="google-flow"`）**：默认 `nano2`，可选 `nano21`、`nano-pro`；画幅 `1:1`、`16:9` 或 `9:16`，最多 3 张参考图；可选 `upscale="2k" | "4k"`（默认关闭，0 积分免费超分，`4k` 需 Ultra 账号，偶发失败降级保留 1K 原图并标 `upscale_status="fallback_original"`）；可通过 `--budget-limit` 指定整数积分上限。
   - **Google AI Studio（`engine="gemini"`）**：仅显式指定时使用，区分展示名与 `model_id`（默认 `gemini-nano-banana-2.1`），支持 1–6 张参考图（`<=24MB`），不支持遮罩重绘，详见[google.md](google.md)。
   - **Grok（`engine="grok"`）**：模型取 `image.capabilities` 声明，`image.edit` 仅接受 1 张同项目 `source_artifact_id` 且继承画幅，不走 REST CLI，详见[grok.md](grok.md)。
   - **MOSS-Image（`engine="moss-image"`）**：传入显式 `width`/`height`（16 倍数、`[512, 2048]`、总像素 `<=2M`）、可选 `layout`（`meta/items`，`box_2d=[x1,y1,x2,y2]`，最多 32 项）、`steps`/`cfg_scale`/`seed` 与独立 `negative_prompt`，详见[moss-image.md](moss-image.md)。
3. **GPU 排队与恢复**：自托管本地 GPU 引擎（`moss-image` 及 CUDA 版 `face-fusion`）由 Image MCP 自动向 Station GPU 调度器（`:5056`）申请显存租约并执行按需冷启动（`0 -> 1`）；云渠道与 CPU 模式 `face-fusion` 不占用本地 GPU 租约。返回 `queued`（`waiting_gpu_lease`）或 `ENGINE_NOT_READY` 属正常排队，继续轮询原 `task_id`，严禁重提或手动卸载其他 GPU 应用。

## 3. 防混用与失败红线

- **不混用专属字段**：`gemini` 不传 `mask` / `image.inpaint` 或 `moss-image` 字段；`grok` 不传 `quality`、`instructions`、遮罩、多参考、透明背景或 4K；`moss-image` 严禁传 `channel_account_id`、`budget_limit`、`prefer_4k`、`resolution="4k"`、`mask` 或 `image.inpaint`（传入必报 `INVALID_ARGUMENT`）。
- **角色一致性边界**：`moss-image` 参考图（1–3 张实验性 R2I）不保证人物身份一致，角色 IP 一致性出图不选 `moss-image`；用户明确指定时如实说明限制，不自动切换收费渠道。
- **失败不静默切渠道**：上游返回 `image_safety`、`prohibited_content`、`no_image_returned` 或预算/配额错误时保留原 `reason_code` 并停止；`moss-image` 无人场景 `moss_mode="edit"` 另须核对 `execution.mode=edit`、`conditioning_mode=moss_edit_reference_latents` 与 `weight_sha256_checked`。
