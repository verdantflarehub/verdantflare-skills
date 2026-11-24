---
name: verdantflare-video
description: 通过 Studio Video MCP 执行文生视频（T2V）、图生视频（I2V）、首尾帧视频（FL2V）与多参考全能视频（REF2V），支持按场景指定逻辑模型（H3、SD2、Veo、Omni 1.2 Flash、Grok、Wan 3.0 等），内部按已注册能力映射渠道并核实参数、报价与 K09 内容分级。
metadata:
  version: "0.5.7"
---

# VerdantFlare Video

## 1. 范围与边界

本技能通过 Studio MCP 统一网关（`video.create/status/result/rate_content/capabilities/preflight/import*` 及 `video.regenerate_2k/interpolate/character_swap`）生成、恢复与下载视频；不直连底层 Runtime 或供应商，不持有或发送供应商密钥。AI 短剧阶段成果默认同步至原 Studio 项目（沿用真实 `project_id`）；仅本地或只读讨论的限定优先。Project、Artifact 与 World 生命周期遵循[共同接入规则](https://github.com/verdantflarehub/verdantflare-skills/blob/verdantflare-video-v0.4.1/skills/_shared/project-world.md)，认证与旧令牌排查见[环境说明](https://github.com/verdantflarehub/verdantflare-skills/blob/verdantflare-video-v0.4.0/skills/ENVIRONMENT.md)。

**模型选型与自动路由**：上层专员或调用方通过 `model`（及可选 `route`）选引擎，渠道参数与 GPU 租约由本技能内部管理（失败不静默换渠道）——5090 本地 H3 默认 `h3-fasth3`（`fasth3-nvfp4`），冷备 `h3-singularity`（`du-0`）；云端 H3 Ref2VA 与 Seedance 2.0 走 `fal`；Google 系列（Veo / Omni 1.2 Flash）默认一律走 **Google Flow**（`route=google-flow`），仅显式指定时用 Google AI Studio（`route=google`）；另有 Grok（`route=grok`）与 Higgsfield（`wan3_0` / `seedance_2_5_draft`）。新视频交付与同步前必须完成抽帧与 K09 `video.rate_content`。本地 GPU 排队返回 `queued`（`waiting_gpu_lease`）属正常等待，严禁取消重建或手动关停其他 GPU 实例。

## 2. 能力清单与按任务读取

按要执行的视频原子能力直达对应入口（调用时通过 `model` 入参指定逻辑模型）：

| 原子能力   | 入口文档                                      |
| :--------- | :-------------------------------------------- |
| 文生视频   | [01.t2v](references/01.t2v.entrypoint.md)     |
| 图生视频   | [02.i2v](references/02.i2v.entrypoint.md)     |
| 首尾帧视频 | [03.fl2v](references/03.fl2v.entrypoint.md)   |
| 多参考视频 | [04.ref2v](references/04.ref2v.entrypoint.md) |

跨能力 MCP 契约、模型/渠道路由与验收门禁见 [00.common.rules](references/00.common.rules.md)、[00.common.acceptance](references/00.common.acceptance.md) 及渠道附录 [channels](references/channels.md)（由各能力入口引用）。

## 3. 工具与命令

主客户端为 [video_client.py](scripts/video_client.py)（需 Python 3.10+），H3 提示词静态检查器为 [validate_h3_prompt.py](scripts/validate_h3_prompt.py)，Higgsfield 管理员私有 CLI 为 [higgsfield.py](scripts/higgsfield.py)。完整参数与恢复流程见 [04.ref2v.entrypoint.md](references/04.ref2v.entrypoint.md)。

```sh
python scripts/video_client.py check
python scripts/video_client.py import --project-id <project_id> --file <path> --purpose identity
python scripts/video_client.py generate --project-id <project_id> --idempotency-key <key> --route <route> --prompt "<prompt>"
python scripts/video_client.py result <task_id> --output <out.mp4>
python scripts/validate_h3_prompt.py request.json preflight.json
```
