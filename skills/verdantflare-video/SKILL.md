---
name: verdantflare-video
description: 通过 Studio Video MCP 的真实动态渠道生成、恢复和下载视频，按已注册能力使用 H3、SD2、Grok、Google、Google Flow 与 Higgsfield 模型；覆盖文生视频、单图生视频、首尾帧视频、多参考全能视频与渠道配置指南，核实参数、素材限制和报价并保留具体服务错误。
metadata:
  version: "0.5.7"
---

# VerdantFlare Video

## 1. 范围与边界

本技能通过 Studio MCP 统一网关（`video.create/status/result/rate_content/capabilities/preflight/import*` 及 `video.regenerate_2k/interpolate/character_swap`）生成、恢复与下载视频；不直连底层 Runtime 或供应商，不持有或发送供应商密钥。AI 短剧阶段成果默认同步至原 Studio 项目（沿用真实 `project_id`）；仅本地或只读讨论的限定优先。Project、Artifact 与 World 生命周期遵循[共同接入规则](https://github.com/verdantflarehub/verdantflare-skills/blob/verdantflare-video-v0.4.1/skills/_shared/project-world.md)，认证与旧令牌排查见[环境说明](https://github.com/verdantflarehub/verdantflare-skills/blob/verdantflare-video-v0.4.0/skills/ENVIRONMENT.md)。

**默认路由与合规红线**：未指定模型或渠道时解析当前宿主配置并核实可用性（5090 本地 H3 默认 `h3-fasth3` 唯一档位 `fasth3-nvfp4`，冷备 `h3-singularity` 唯一档位 `du-0`），失败不静默换渠道；已有任务沿原模型、渠道与任务 ID 恢复。凡调用 Google 系列视频模型（Veo / Omni 1.2 Flash），**默认一律走 Google Flow（`route=google-flow`）**，仅显式指定时使用 Google AI Studio（`route=google`）。每次取得新生成视频后、在摘要展示与 Project/World 同步前，必须完成本机临时抽帧与 K09 内容分级并调用 `video.rate_content`。本地 GPU 路由排队返回 `queued`（`waiting_gpu_lease`）属正常调度等待，严禁取消重建或手动关停其他 GPU 实例。

## 2. 能力清单与按任务读取

按要执行的原子能力直达对应入口；跨能力 MCP 契约与验收门禁见 `00.common.*`：

| 原子能力             | 入口文档                                                        |
| :------------------- | :-------------------------------------------------------------- |
| 文生视频与提示词编译 | [01.t2v](references/01.t2v.entrypoint.md)                       |
| 单图生视频与首帧条件 | [02.i2v](references/02.i2v.entrypoint.md)                       |
| 首尾帧条件视频       | [03.fl2v](references/03.fl2v.entrypoint.md)                     |
| 多参考全能视频与 SD2 | [04.ref2v](references/04.ref2v.entrypoint.md)                   |
| 渠道差异与配置指南   | [05.channel-guides](references/05.channel-guides.entrypoint.md) |

先读目标能力的 `entrypoint.md`（输入与输出 → 分类 → 工作流 → 规范 → 验收），无需通读其他能力。

跨能力公共文档：[00.common.rules](references/00.common.rules.md)、[00.common.acceptance](references/00.common.acceptance.md)（由各能力入口引用）。

## 3. 工具与命令

主客户端为 [video_client.py](scripts/video_client.py)（需 Python 3.10+），H3 提示词静态检查器为 [validate_h3_prompt.py](scripts/validate_h3_prompt.py)，Higgsfield 管理员私有 CLI 为 [higgsfield.py](scripts/higgsfield.py)。完整参数与恢复流程见 [04.ref2v.entrypoint.md](references/04.ref2v.entrypoint.md)。

```sh
python scripts/video_client.py check
python scripts/video_client.py import --project-id <project_id> --file <path> --purpose identity
python scripts/video_client.py generate --project-id <project_id> --idempotency-key <key> --route <route> --prompt "<prompt>"
python scripts/video_client.py result <task_id> --output <out.mp4>
python scripts/validate_h3_prompt.py request.json preflight.json
```
