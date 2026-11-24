---
name: verdantflare-image
description: 通过 VerdantFlare Image MCP 执行文生图（T2I）、图生图/多参考编辑（I2I）与遮罩局部重绘（Inpaint），支持按场景指定逻辑模型（默认 GPT Image 2.6，可选 Nano Banana 2 / Grok / MOSS-Image），内部自动映射对应渠道并执行像素核验与 K09 内容分级。
---

# VerdantFlare Image

## 1. 范围与边界

本技能通过 Studio Image MCP（`image.create/edit/inpaint/status/result/rate_content` 及 `image.import*`）生成、编辑、恢复与下载具有不可变 Artifact 与 SHA-256 的图像候选。已绑定 Studio 项目的创作与返修默认完成素材传输、原项目保存及角色关联核验，沿用真实 `project_id`；仅本地、只讨论或暂不上传的明确限定优先。不负责 MV 导演或视频剪辑，不绕过 MCP/受控客户端直连供应商。

**模型选型与自动路由**：上层专员或调用方通过逻辑模型入参 `model` 选引擎，渠道网关由本技能内部自动映射（失败不静默切渠道）——默认 **GPT Image 2.6**（`engine="codex"`，`gpt-image-2.6-sunburst`）；指定 **Nano Banana 2 / 2.1 / Pro** 时默认一律走 **Google Flow**（`engine="google-flow"`，`nano2` / `nano21` / `nano-pro`，支持免费 `2k`/`4k` 超分），仅显式指定时用 Google AI Studio（`gemini`）；另有 **Grok**（单图轻编辑）与 **MOSS-Image**（文字海报/坐标排版，不保人脸一致）。生成产物在展示与同步前必须完成 K09 `image.rate_content`。

人物五视图、半身照或 16:9 档案卡先读[人物设定技能](../verdantflare-image-character/SKILL.md)；Project/World 接入遵循[共同接入规则](../_shared/project-world.md)，认证排查见[环境说明](../ENVIRONMENT.md)。

## 2. 能力清单与按任务读取

按要执行的图像原子能力直达对应入口（调用时通过 `model` 入参指定逻辑模型）：

| 原子能力 | 入口文档                                            |
| :------- | :-------------------------------------------------- |
| 文生图   | [01.t2i](references/01.t2i.entrypoint.md)           |
| 图生图   | [02.i2i-edit](references/02.i2i-edit.entrypoint.md) |
| 局部重绘 | [03.inpaint](references/03.inpaint.entrypoint.md)   |

跨能力公共契约、模型/渠道自动路由与 K09 分级见 [00.common.rules](references/00.common.rules.md) 及渠道附录 [channels](references/channels.md)（由各能力入口引用）。

## 3. 工具与命令

主客户端为 [image_client.py](scripts/image_client.py)（支持 Studio `/mcp` JSON-RPC 网关与受控下载校验），环境加载脚本为 [load_env.py](scripts/load_env.py)。完整参数与恢复流程见 [02.i2i-edit.entrypoint.md](references/02.i2i-edit.entrypoint.md)。

```text
python3 scripts/image_client.py stats
python3 scripts/image_client.py generate --project-id <project_id> --idempotency-key <key> --prompt "<prompt>"
python3 scripts/image_client.py status <task_id>
python3 scripts/image_client.py download <artifact_id> --output <path>
python3 scripts/image_client.py rate <task_id> --rating general --rubric-version "rubric v1.0"
```
