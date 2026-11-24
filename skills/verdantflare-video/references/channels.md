# verdantflare-video：模型与渠道参数附录（channels）

本附录承接跨能力共享的模型到渠道映射、各渠道专属参数边界与配置校验，由 [公共 MCP 契约](00.common.rules.md) 及 `01~04` 能力入口按需引用。

## 1. 逻辑模型与渠道映射

| 渠道（`route`）                | 定位与专册                                                  |
| :----------------------------- | :---------------------------------------------------------- |
| `h3-fasth3` / `h3-singularity` | 5090 本地 H3，见[公共契约](00.common.rules.md)              |
| `fal`                          | 云端 H3 Ref2VA 与 SD2，见[04.ref2v](04.ref2v.entrypoint.md) |
| `google-flow`                  | Google 默认渠道，见[google-flow.md](google-flow.md)         |
| `google`                       | Google AI Studio（仅显式），见[google.md](google.md)        |
| `grok`                         | xAI Grok（T2V/I2V），见[grok.md](grok.md)                   |
| `higgsfield`                   | 会员渠道与私有 CLI，见[higgsfield.md](higgsfield.md)        |

各渠道核心规格：5090 本地 H3 默认 `h3-fasth3`（`fasth3-nvfp4` 8-NFE）、冷备 `h3-singularity`（`du-0` 4-NFE），支持 2K IC 与 RIFE 48 FPS；`fal` 含 `minimax-h3-ref2va`（480P、5–15s）与 `bytedance-seedance-2.0-reference-to-video`；`google-flow` 含 `veo-lite`/`veo-fast`/`veo-quality`/`omni-1.2-flash`；`grok` 支持 1–15s、`480p/720p`；`higgsfield` 含 `wan3_0`、`seedance_2_5_draft` 等。历史 SD2 接口另见 [api.md](api.md)。

## 2. 各渠道专属参数与校验

1. **渠道发现与匹配**：执行 `python scripts/video_client.py check` 或调用 `video.capabilities`，按 `route + model`（含 `models[]` 别名）`+ mode` 精确匹配能力行，确认 `configured=true`、`enabled` 与创建 `inputSchema`。
2. **渠道专属规则**：
   - **Google Flow（`route=google-flow`）**：凡调用 Veo / Omni 1.2 Flash 默认走此渠道；`veo-quality` 仅 `text_to_video`（`ref_cap=0`，**严禁传参考图或 `reference_to_video`**）；多图锁脸/场景用 `veo-fast`/`veo-lite`（最多 3 图）或 `omni-1.2-flash`（最多 7 图，3–15s，且唯一支持 `video_editing` 1 段 `>=3s` `.mp4` 原片 + 最多 7 图）；`omni-1.2-flash`/`veo-fast`/`veo-quality` 可选 `upscale="1080p"`（0 积分，失败降级 720p 并标 `upscale_status="fallback_original"`，`veo-lite` 不支持）；`--budget-limit` 为正整数积分。详见[google-flow.md](google-flow.md)。
   - **Google AI Studio（`route=google`）**：仅用户显式指定时使用，退役模型返回 `model_retired` 保留原错不自动切收费模型，详见[google.md](google.md)。
   - **Grok（`route=grok`）**：1–15s 整数时长、`480p/720p`，不传 `quality_profile`/种子/音频开关或 H3 标签，最长观测 24 小时，详见[grok.md](grok.md)。
   - **Higgsfield（`route=higgsfield`）**：无宿主默认模型必须显式 `--model`；`seedance_2_5_draft` 固定 `480p`、4–15s、最多 4 图 + 1 视频 R2V，不替换普通 2.5 或首尾帧；`--price-ceiling` 单独表示 Credits 上限；管理员 OAuth 与服务器 PVC 交接通过私有运行时 `scripts/higgsfield.py`（固定 CLI 1.1.26、DMIT 出口 `127.0.0.1:19081 -> openclash:1081`）执行，详见[higgsfield.md](higgsfield.md)。
3. **配置校验与预检**：校验本地环境状态可使用 `scripts/check-config.py` / `scripts/config.py`（历史 `install-config-*.sh/ps1` 不是 Studio 认证修复入口）。提交前调用 `video.preflight` 核对报价、积分/币种预算与参考限制，`can_submit=false` 立即停止。

## 3. 防混用、凭据与验收红线

- **严禁跨渠道参数混用**：不把 H3 的参考上限、24 FPS、`<Picture 1>` 标签或 `quality_profile` 套给 Grok、Google Flow、Google 或 Higgsfield；不把 Higgsfield 的 `--price-ceiling`（Credits）与其他渠道币种 `--budget-limit` 换算。
- **凭据隔离与报价区分**：所有供应商 Key、会员 Token 与刷新凭据留存服务端 Secret 或服务器 PVC，Skill 不读取、输出或发送任何供应商凭据；服务报价（`quoted_credits`）不等于实际扣费，未知价格保留未知。
- **错误透传与验收**：`model_retired`、`authorization_required`、`limits_unavailable`、`unsupported_references`、`mode_reference_mismatch`、`quota_exhausted`、`media_contract_mismatch` 均保留原码并停止；管理员执行 Higgsfield 私有 CLI 交接须通过 `reconcile` 核验脱敏回执且不提交 `.runtime/` 凭据。
