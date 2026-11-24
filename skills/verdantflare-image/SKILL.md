---
name: verdantflare-image
description: 通过 VerdantFlare Image MCP 生成或编辑图像候选（默认出图模型为 GPT Image 2.6 / gpt-image-2.6-sunburst），覆盖文生图与视觉规格、图生图与局部重绘、多参考编辑以及 Google Flow / Google AI Studio / Grok / MOSS-Image 渠道适配。
---

# VerdantFlare Image

## 1. 范围与边界

本技能通过 Studio Image MCP（现有网关 `image.create/edit/inpaint/status/result/rate_content` 及 `image.import*`）生成、编辑、恢复与下载具有不可变 Artifact 与 SHA-256 的图像候选。已绑定 Studio 项目的创作与返修默认完成素材传输、原项目保存及角色关联核验，沿用真实 `project_id`；仅本地、只讨论或暂不上传的明确限定优先。不负责 MV 导演或视频剪辑，仅解释或修改代码时不启动生图，不绕过 MCP/受控客户端直连供应商。

**默认模型与路由红线**：未指定引擎或模型时默认使用 **GPT Image 2.6**（`engine="codex"`，默认 `model="gpt-image-2.6-sunburst"`，可选 `gpt-image-2.6-flare` 及向下兼容 `2.5`）。凡调用 Google 系列图像模型（Nano Banana 2 / 2.1 / Pro），**默认一律走 Google Flow（`engine="google-flow"`，默认 `model="nano2"`，可选 `nano21` / `nano-pro`，支持 `upscale="2k" | "4k"`）**，除非用户显式指定，否则不考虑 Google AI Studio（`engine="gemini"`）。生成产物在摘要展示与 Project/World 同步前必须完成 K09 内容分级评估并调用 `image.rate_content`。

用户要求人物五视图、人物半身照或 16:9 人物视觉档案卡时，先读[人物设定技能](../verdantflare-image-character/SKILL.md)确定版式，再由本技能出图；Project 归属、换电脑恢复、Artifact 保存及 World 固定资产引用遵循[共同接入规则](../_shared/project-world.md)，连接与认证排查见[环境说明](../ENVIRONMENT.md)。

## 2. 能力清单与按任务读取

按要执行的原子能力直达对应入口；跨能力契约、质感控制与 K09 分级步骤见 `00.common.rules.md`：

| 原子能力           | 入口文档                                                              |
| :----------------- | :-------------------------------------------------------------------- |
| 文生图与视觉规格   | [01.t2i](references/01.t2i.entrypoint.md)                             |
| 图生图、重绘与返修 | [02.i2i-edit](references/02.i2i-edit.entrypoint.md)                   |
| 多渠道适配与路由   | [03.provider-adapters](references/03.provider-adapters.entrypoint.md) |

先读目标能力的 `entrypoint.md`（输入与输出 → 分类 → 工作流 → 规范 → 验收），无需通读其他能力。

跨能力公共文档：[00.common.rules](references/00.common.rules.md)（由各能力入口引用）。

## 3. 工具与命令

主客户端为 [image_client.py](scripts/image_client.py)（已适配 Studio `/mcp` JSON-RPC 网关与 `/apps/image/artifacts/{artifact_id}/content` 受控下载及真实宽高/MIME/SHA-256 核验），环境加载脚本为 [load_env.py](scripts/load_env.py)。完整参数与恢复流程见 [02.i2i-edit.entrypoint.md](references/02.i2i-edit.entrypoint.md)。

```text
python3 scripts/image_client.py stats
python3 scripts/image_client.py generate --project-id <project_id> --idempotency-key <key> --prompt "<prompt>"
python3 scripts/image_client.py status <task_id>
python3 scripts/image_client.py download <artifact_id> --output <path>
python3 scripts/image_client.py rate <task_id> --rating general --rubric-version "rubric v1.0"
```
